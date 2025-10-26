"""Entrypoint for the SenseCAP pumpkin WebSocket server.

The current implementation focuses on configuration exchange and structured
logging so the firmware team can begin integrating against a stable protocol.
Upcoming milestones will replace the placeholder audio handlers with real
OpenAI speech-to-speech streaming.
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import logging
import os
import signal
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Tuple
import urllib.error
import urllib.request

import websockets
import yaml
from pydantic import BaseModel, Field, ValidationError

try:  # WebSockets 14+ (new asyncio API)
    from websockets.asyncio.server import ServerConnection as WebSocketConnection
    from websockets.asyncio.server import serve
except ImportError:  # pragma: no cover - exercised only with older websockets releases
    try:  # WebSockets 10–13 legacy namespace
        from websockets.server import WebSocketServerProtocol as WebSocketConnection
        from websockets.server import serve
    except ImportError:  # WebSockets <10 fallback
        from websockets.legacy.server import WebSocketServerProtocol as WebSocketConnection
        from websockets.legacy.server import serve

LOGGER = logging.getLogger("pumpkin.server")


class PersonaSettings(BaseModel):
    name: str = Field(..., description="Display name for logging and metrics")
    role: str = Field(..., description="System prompt describing the pumpkin's persona")
    objective: str = Field(..., description="Guidance for conversational goals")
    humour_level: str = Field(..., description="Qualitative humour setting (e.g. goofy, dry)")
    voice: str = Field(..., description="Preferred OpenAI output voice preset")
    speaking_style: str = Field(..., description="Additional voice/style hints sent to the API")
    wake_phrases: list[str] = Field(default_factory=list)
    goodbye_phrases: list[str] = Field(default_factory=list)
    silence_timeout_s: float = Field(12.0, ge=1.0)


class OpenAISettings(BaseModel):
    model: str = Field(..., description="Realtime speech-to-speech model name")
    audio_format: str = Field("pcm16", description="Audio format for both input and output")
    output_voice: str = Field("alloy", description="Voice preset requested from the model")


class ServerTuning(BaseModel):
    awake_animation_ms: int = Field(800, ge=0)
    sleep_animation_ms: int = Field(2000, ge=0)
    max_listen_seconds: int = Field(45, ge=1)
    reconnect_backoff_s: list[int] = Field(default_factory=lambda: [2, 4, 8, 16])


class PumpkinConfig(BaseModel):
    persona: PersonaSettings
    openai: OpenAISettings
    server: ServerTuning


@dataclass(slots=True)
class RuntimeConfig:
    persona: PersonaSettings
    openai: OpenAISettings
    server: ServerTuning
    api_key: Optional[str]

    @classmethod
    def from_filesystem(cls, path: Path) -> "RuntimeConfig":
        LOGGER.debug("Loading persona configuration from %s", path)
        if not path.exists():
            raise FileNotFoundError(
                f"Persona configuration {path} not found. Create it from persona.example.yaml."
            )
        with path.open("r", encoding="utf-8") as fh:
            raw_data = yaml.safe_load(fh)
        try:
            parsed = PumpkinConfig.model_validate(raw_data)
        except ValidationError as err:
            LOGGER.error("Invalid configuration: %s", err)
            raise
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            message = (
                "OPENAI_API_KEY is not set; OpenAI integration will be disabled until provided."
            )
            LOGGER.warning(message)
            logging.getLogger().warning(message)
        return cls(
            persona=parsed.persona,
            openai=parsed.openai,
            server=parsed.server,
            api_key=api_key,
        )

    def verify_openai_reachability(
        self,
        requester: Optional[Callable[[urllib.request.Request], Tuple[int, bytes]]] = None,
    ) -> None:
        """Probe the OpenAI API to ensure credentials are accepted.

        The default implementation performs a HEAD request against the models
        endpoint which does not incur usage costs. Tests can supply a stub
        ``requester`` to avoid network access.
        """

        if not self.api_key:
            raise RuntimeError("OPENAI_API_KEY is not configured; cannot contact OpenAI")

        requester = requester or self._default_openai_probe
        base_headers = {
            "Authorization": f"Bearer {self.api_key}",
            "User-Agent": "pumpkin-server-healthcheck/1.0",
        }

        for method in ("HEAD", "GET"):
            req = urllib.request.Request(
                "https://api.openai.com/v1/models",
                method=method,
                headers=base_headers,
            )

            try:
                status, _ = requester(req)
            except urllib.error.HTTPError as err:
                if err.code == 405 and method == "HEAD":
                    continue
                raise RuntimeError(
                    f"OpenAI credential probe failed with HTTP status {err.code}"
                ) from err
            except OSError as err:
                raise RuntimeError("OpenAI credential probe failed") from err

            if status < 400:
                return

        raise RuntimeError("OpenAI credential probe returned an error status")

    @staticmethod
    def _default_openai_probe(request: urllib.request.Request) -> Tuple[int, bytes]:
        with urllib.request.urlopen(request, timeout=5) as response:  # type: ignore[arg-type]
            return response.status, response.read()


class PumpkinSession:
    """Tracks one Watcher connection."""

    def __init__(self, websocket: WebSocketConnection, config: RuntimeConfig):
        self.websocket = websocket
        self.config = config
        self.id = f"client-{id(self):x}"

    async def run(self) -> None:
        LOGGER.info("Session %s connected from %s", self.id, self.websocket.remote_address)
        try:
            await self._perform_handshake()
            await self._receive_loop()
        except websockets.ConnectionClosedOK:
            LOGGER.info("Session %s closed cleanly", self.id)
        except websockets.ConnectionClosedError as exc:
            LOGGER.warning("Session %s closed with error: %s", self.id, exc)
        except Exception:  # pragma: no cover - log unexpected failures
            LOGGER.exception("Unhandled error in session %s", self.id)
        finally:
            LOGGER.debug("Session %s complete", self.id)

    async def _perform_handshake(self) -> None:
        """Wait for the initial hello frame and respond with config."""
        raw = await self.websocket.recv()
        if isinstance(raw, bytes):
            raise ValueError("Expected JSON handshake, received binary data")
        payload = json.loads(raw)
        msg_type = payload.get("type")
        if msg_type != "hello":
            raise ValueError(f"Expected hello message, received {msg_type!r}")
        LOGGER.debug("Session %s handshake payload: %s", self.id, payload)

        await self._send(
            {
                "type": "config",
                "persona": {
                    "name": self.config.persona.name,
                    "role": self.config.persona.role,
                    "objective": self.config.persona.objective,
                    "humour_level": self.config.persona.humour_level,
                    "wake_phrases": self.config.persona.wake_phrases,
                    "goodbye_phrases": self.config.persona.goodbye_phrases,
                    "silence_timeout_s": self.config.persona.silence_timeout_s,
                },
                "openai": {
                    "model": self.config.openai.model,
                    "audio_format": self.config.openai.audio_format,
                    "output_voice": self.config.openai.output_voice,
                },
                "server": self.config.server.model_dump(),
            }
        )

    async def _receive_loop(self) -> None:
        """Log messages from the device and echo audio back as a placeholder."""
        async for incoming in self.websocket:
            if isinstance(incoming, bytes):
                LOGGER.debug("Session %s received %d audio bytes", self.id, len(incoming))
                await self._handle_audio_echo(incoming)
                continue

            try:
                payload = json.loads(incoming)
            except json.JSONDecodeError:
                LOGGER.warning("Session %s received malformed JSON: %r", self.id, incoming)
                continue

            msg_type = payload.get("type")
            LOGGER.debug("Session %s received %s message", self.id, msg_type)

            if msg_type == "state":
                LOGGER.info("State update from device: %s", payload)
            elif msg_type == "command":
                LOGGER.info("Command acknowledgment from device: %s", payload)
            elif msg_type == "error":
                LOGGER.error("Device reported error: %s", payload)
            else:
                LOGGER.debug("Unhandled message: %s", payload)

    async def _handle_audio_echo(self, audio_bytes: bytes) -> None:
        """Echo incoming audio back to the device as a temporary stand-in."""
        await self._send({"type": "audio_out", "format": "pcm16", "duration_ms": 0})
        await self.websocket.send(audio_bytes)

    async def _send(self, message: Dict[str, Any]) -> None:
        LOGGER.debug("Session %s sending %s", self.id, message.get("type"))
        await self.websocket.send(json.dumps(message))


@dataclass(slots=True)
class ServerOptions:
    host: str
    port: int
    cert: Optional[Path]
    key: Optional[Path]
    config_path: Path
    log_level: str


async def serve(options: ServerOptions) -> None:
    runtime = RuntimeConfig.from_filesystem(options.config_path)

    ssl_context = None
    if options.cert and options.key:
        import ssl

        ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ssl_context.load_cert_chain(certfile=str(options.cert), keyfile=str(options.key))
        LOGGER.info("TLS enabled using certificate %s", options.cert)
    else:
        LOGGER.warning("Starting server without TLS; use only for local development.")

    async def handler(websocket: WebSocketConnection) -> None:
        session = PumpkinSession(websocket, runtime)
        await session.run()

    server = await serve(
        handler,
        host=options.host,
        port=options.port,
        ssl=ssl_context,
        max_size=4 * 1024 * 1024,  # allow generous audio frames
    )
    LOGGER.info("Pumpkin server listening on %s:%s", options.host, options.port)

    stop_event = asyncio.Event()

    def _shutdown() -> None:
        LOGGER.info("Shutdown signal received; closing server")
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, _shutdown)

    await stop_event.wait()
    server.close()
    await server.wait_closed()


def parse_args(argv: Optional[list[str]] = None) -> ServerOptions:
    parser = argparse.ArgumentParser(description="SenseCAP pumpkin realtime server")
    parser.add_argument("--host", default="0.0.0.0", help="Host/IP to bind")
    parser.add_argument("--port", type=int, default=8080, help="TCP port for the WebSocket server")
    parser.add_argument("--cert", type=Path, help="Path to TLS certificate (PEM)")
    parser.add_argument("--key", type=Path, help="Path to TLS private key (PEM)")
    parser.add_argument(
        "--config",
        type=Path,
        default=Path(os.getenv("PUMPKIN_CONFIG_PATH", "server/config/persona.yaml")),
        help="Persona configuration file",
    )
    parser.add_argument(
        "--log-level",
        default=os.getenv("PUMPKIN_LOG_LEVEL", "INFO"),
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        help="Logging verbosity",
    )
    args = parser.parse_args(argv)
    return ServerOptions(
        host=args.host,
        port=args.port,
        cert=args.cert,
        key=args.key,
        config_path=args.config,
        log_level=args.log_level,
    )


def configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )


async def _async_main(options: ServerOptions) -> None:
    configure_logging(options.log_level)
    await serve(options)


def main(argv: Optional[list[str]] = None) -> None:
    options = parse_args(argv)
    asyncio.run(_async_main(options))


if __name__ == "__main__":
    main()
