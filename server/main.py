"""Entrypoint for the SenseCAP pumpkin WebSocket server.

The current implementation focuses on configuration exchange and structured
logging so the firmware team can begin integrating against a stable protocol.
Upcoming milestones will replace the placeholder audio handlers with real
OpenAI speech-to-speech streaming.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import contextlib
import json
import logging
import wave
from array import array
import os
import signal
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, Optional, Tuple
import urllib.error
import urllib.request
from datetime import datetime, timezone

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

SUPPORTED_OPENAI_VOICES = {
    "alloy",
    "ash",
    "ballad",
    "coral",
    "echo",
    "sage",
    "shimmer",
    "verse",
    "marin",
    "cedar",
}


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
    output_sample_rate_hz: int = Field(
        24000, ge=8000, le=96000, description="Expected sample rate emitted by the OpenAI model"
    )
    target_sample_rate_hz: int = Field(
        16000,
        ge=8000,
        le=48000,
        description="Sample rate required by the watcher firmware (resampling target)",
    )


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


def compose_persona_instructions(persona: PersonaSettings) -> str:
    """Construct the instruction string sent to the realtime API."""
    role = (persona.role or "").strip()
    objective = (persona.objective or "").strip()
    humour = (persona.humour_level or "").strip()
    style = (persona.speaking_style or "").strip()

    tone_clause = ""
    if humour and style:
        tone_clause = f"Keep the tone {humour} and {style}."
    elif humour:
        tone_clause = f"Keep the tone {humour}."
    elif style:
        tone_clause = f"Keep the delivery {style}."

    parts: list[str] = []
    if role:
        parts.append(role)
    if objective:
        parts.append(objective)
    parts.append(f"Stay in character as {persona.name}.")
    if tone_clause:
        parts.append(tone_clause)
    parts.append(
        "Always respond in English, keep replies upbeat, speak with lively fast-paced energy, and wrap up sentences quickly before listening again."
    )
    parts.append(
        "Keep every reply to one or two crisp sentences (no more than a dozen words each) unless the guest begs for more detail."
    )
    parts.append(
        "After finishing a reply, leave a noticeable beat of silence so visitors can jump in; if someone starts speaking, yield immediately."
    )
    parts.append(
        "When you first wake, give a quick playful greeting then hand the turn back to the guest."
    )
    parts.append(
        "If the guest falls quiet, stay attentive rather than filling the space with chatter."
    )
    return " ".join(parts)


class OpenAIRealtimeSession:
    """Maintains a realtime OpenAI websocket and streams audio responses."""

    _MIN_COMMIT_DURATION_MS = 140
    _TARGET_SAMPLE_RATE = 16000

    def __init__(
        self,
        runtime: RuntimeConfig,
        audio_callback: Callable[[str, bytes, int], Awaitable[None]],
        resume_callback: Optional[Callable[[int, str], None]] = None,
    ) -> None:
        if not runtime.api_key:
            raise RuntimeError("OPENAI_API_KEY must be configured for realtime streaming")

        self.runtime = runtime
        self.audio_callback = audio_callback
        self._resume_callback = resume_callback
        self.ws: Optional[websockets.WebSocketClientProtocol] = None
        self._send_lock = asyncio.Lock()
        self._reader_task: Optional[asyncio.Task[None]] = None
        self._awaiting_response = False
        self._closed = False
        self._connected = False
        self._response_instructions = compose_persona_instructions(self.runtime.persona)
        self._bytes_since_commit = 0
        self._pending_commit_bytes = 0
        self._session_logged = False
        self._response_logged = False
        self._resample_logged = False
        self._input_resample_logged = False
        self._commit_after_response = False
        self._speech_active = False
        self._mic_paused = False
        self._voice_warning_emitted = False
        self._transcript_buffers: Dict[str, str] = {}
        self._transcript_logged: set[str] = set()
        self._user_transcripts: Dict[str, str] = {}
        self._user_transcripts_logged: set[str] = set()
        self._TARGET_SAMPLE_RATE = int(
            getattr(runtime.openai, "target_sample_rate_hz", self._TARGET_SAMPLE_RATE)
        )
        self._fallback_response_sample_rate = int(
            getattr(runtime.openai, "output_sample_rate_hz", max(self._TARGET_SAMPLE_RATE, 24000))
        )
        self._device_sample_rate = self._TARGET_SAMPLE_RATE
        self._api_input_sample_rate = int(
            getattr(runtime.openai, "output_sample_rate_hz", self._fallback_response_sample_rate)
        )
        if self._api_input_sample_rate <= 0:
            self._api_input_sample_rate = self._fallback_response_sample_rate or self._device_sample_rate
        if self._api_input_sample_rate <= 0:
            self._api_input_sample_rate = self._TARGET_SAMPLE_RATE
        commit_rate = max(1, self._api_input_sample_rate)
        self._min_commit_bytes = max(
            1,
            int(
                round(
                    commit_rate
                    * 2
                    * (self._MIN_COMMIT_DURATION_MS / 1000.0)
                )
            ),
        )
        LOGGER.info(
            "OpenAI realtime persona: name=%s voice=%s device_sample_rate=%s api_input_sample_rate=%s target_sample_rate=%s fallback_response_sample_rate=%s min_commit_bytes=%s instructions=\"%s\"",
            self.runtime.persona.name,
            self._output_voice,
            self._device_sample_rate,
            self._api_input_sample_rate,
            self._TARGET_SAMPLE_RATE,
            self._fallback_response_sample_rate,
            self._min_commit_bytes,
            self._response_instructions,
        )

    @property
    def _output_voice(self) -> str:
        candidates = [
            (self.runtime.openai.output_voice or "").strip(),
            (self.runtime.persona.voice or "").strip(),
        ]
        for candidate in candidates:
            if not candidate:
                continue
            if candidate in SUPPORTED_OPENAI_VOICES:
                return candidate
            if not self._voice_warning_emitted:
                LOGGER.warning(
                    "Requested voice '%s' is not supported by gpt-realtime; falling back to default.",
                    candidate,
                )
                self._voice_warning_emitted = True
        return "alloy"

    def _resample_pcm(self, pcm_bytes: bytes, source_rate: int, target_rate: int) -> bytes:
        if source_rate <= 0 or source_rate == target_rate:
            return pcm_bytes
        sample_count = len(pcm_bytes) // 2
        if sample_count == 0:
            return pcm_bytes

        ratio = target_rate / source_rate
        output_samples = max(1, int(round(sample_count * ratio)))

        src = array("h")
        src.frombytes(pcm_bytes)
        dst = array("h", [0] * output_samples)

        for out_index in range(output_samples):
            source_pos = out_index / ratio
            left_index = int(source_pos)
            frac = source_pos - left_index

            if left_index >= sample_count - 1:
                sample = int(src[sample_count - 1])
            else:
                s1 = int(src[left_index])
                s2 = int(src[left_index + 1])
                sample = int(s1 + (s2 - s1) * frac)

            if sample > 32767:
                sample = 32767
            elif sample < -32768:
                sample = -32768

            dst[out_index] = sample

        return dst.tobytes()

    @staticmethod
    def _extract_text_payload(source: Any) -> str:
        """Normalize various realtime payload shapes into a text string."""
        if isinstance(source, str):
            return source.strip()
        if isinstance(source, dict):
            candidate = source.get("text")
            if isinstance(candidate, str):
                return candidate.strip()
        if isinstance(source, list):
            parts: list[str] = []
            for entry in source:
                text = OpenAIRealtimeSession._extract_text_payload(entry)
                if text:
                    parts.append(text)
            return " ".join(parts).strip()
        return ""

    def _extend_resume_deadline(self, duration_ms: int, response_id: str) -> None:
        if not self._resume_callback:
            return
        try:
            self._resume_callback(duration_ms, response_id)
        except Exception:
            LOGGER.exception("Failed to extend resume deadline")

    def set_mic_paused(self, paused: bool) -> None:
        self._mic_paused = paused

    def _log_user_transcript(self, item_id: str, text: str) -> None:
        if not item_id:
            return
        cleaned = (text or "").strip()
        if item_id in self._user_transcripts_logged:
            return
        self._user_transcripts_logged.add(item_id)
        display = cleaned if cleaned else "[no transcript]"
        LOGGER.info("Realtime user said (%s): %s", item_id, display)

    def _flush_user_transcripts(self, require_text: bool = False) -> None:
        if not self._user_transcripts:
            return
        for item_id, text in list(self._user_transcripts.items()):
            if require_text and not (text or "").strip():
                continue
            self._log_user_transcript(item_id, text)

    async def enqueue_audio(self, pcm_bytes: bytes) -> None:
        await self._ensure_connected()
        if not self.ws:
            return

        payload = pcm_bytes
        raw_length = len(pcm_bytes)
        if self._device_sample_rate != self._api_input_sample_rate:
            try:
                payload = self._resample_pcm(
                    pcm_bytes,
                    self._device_sample_rate,
                    self._api_input_sample_rate,
                )
                if not self._input_resample_logged:
                    LOGGER.info(
                        "Realtime input resampler engaged: %s Hz -> %s Hz",
                        self._device_sample_rate,
                        self._api_input_sample_rate,
                    )
                    self._input_resample_logged = True
            except Exception:
                LOGGER.exception("Failed to resample input audio; falling back to device rate")
                payload = pcm_bytes
        audio_b64 = base64.b64encode(payload).decode("ascii")
        if LOGGER.isEnabledFor(logging.DEBUG):
            denom = max(1, self._api_input_sample_rate) * 2
            duration_ms = int(round(len(payload) / denom * 1000))
            LOGGER.debug(
                "Session input chunk: raw=%d bytes resampled=%d bytes (~%d ms @ %s Hz)",
                len(pcm_bytes),
                len(payload),
                duration_ms,
                self._api_input_sample_rate,
            )
        append_msg = {
            "type": "input_audio_buffer.append",
            "audio": audio_b64,
        }
        async with self._send_lock:
            await self.ws.send(json.dumps(append_msg))

        self._bytes_since_commit += len(payload)
        chunk_ms = int(round(raw_length / (self._device_sample_rate * 2) * 1000))
        if chunk_ms > 0 and self._resume_callback:
            try:
                self._resume_callback(chunk_ms, "streaming")
            except Exception:
                LOGGER.exception("Failed to extend resume deadline during audio enqueue")

        # If we've buffered substantial *spoken* audio with no response in flight, commit as a failsafe.
        if (
            self._speech_active
            and not self._mic_paused
            and not self._awaiting_response
            and self._bytes_since_commit >= self._min_commit_bytes * 12
        ):
            LOGGER.debug(
                "Buffered spoken audio exceeded safety threshold (%d bytes); proactive commit",
                self._bytes_since_commit,
            )
            await self._commit_and_request()

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._reader_task:
            self._reader_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._reader_task
        if self.ws:
            await self.ws.close()
            self.ws = None
        self._connected = False

    async def _ensure_connected(self) -> None:
        if self._connected and self.ws is not None:
            return

        headers = [
            ("Authorization", f"Bearer {self.runtime.api_key}"),
            ("OpenAI-Beta", "realtime=v1"),
        ]
        url = f"wss://api.openai.com/v1/realtime?model={self.runtime.openai.model}"
        try:
            self.ws = await websockets.connect(
                url, additional_headers=headers, max_size=4 * 1024 * 1024
            )
        except TypeError:
            # Older websockets packages use "extra_headers"
            self.ws = await websockets.connect(
                url, extra_headers=headers, max_size=4 * 1024 * 1024
            )

        session_update = {
            "type": "session.update",
            "session": {
                "model": self.runtime.openai.model,
                "voice": self._output_voice,
                "modalities": ["audio", "text"],
                "instructions": self._response_instructions,
                "input_audio_format": self.runtime.openai.audio_format,
                "output_audio_format": self.runtime.openai.audio_format,
            },
        }
        if not self._session_logged:
            LOGGER.info(
                "Realtime session.update payload: %s",
                json.dumps(session_update["session"], indent=2, ensure_ascii=False),
            )
            self._session_logged = True
        async with self._send_lock:
            await self.ws.send(json.dumps(session_update))

        self._reader_task = asyncio.create_task(self._reader_loop())
        self._awaiting_response = False
        self._bytes_since_commit = 0
        self._pending_commit_bytes = 0
        self._connected = True

    async def _commit_and_request(self) -> None:
        if self._awaiting_response:
            self._commit_after_response = True
            LOGGER.debug(
                "Deferred commit (%d bytes) until current response completes",
                self._bytes_since_commit,
            )
            return

        if (
            not self.ws
            or self._bytes_since_commit == 0
            or self._pending_commit_bytes > 0
        ):
            return
        if self._bytes_since_commit < self._min_commit_bytes:
            return

        bytes_to_commit = self._bytes_since_commit
        self._pending_commit_bytes = bytes_to_commit
        self._commit_after_response = False
        response_payload: Dict[str, Any] = {
            "modalities": ["audio", "text"],
            "instructions": self._response_instructions,
            "voice": self._output_voice,
            "output_audio_format": self.runtime.openai.audio_format,
        }
        if not self._response_logged:
            LOGGER.info(
                "Realtime response template: %s",
                json.dumps(response_payload, indent=2, ensure_ascii=False),
            )
            self._response_logged = True

        commands = [
            {"type": "input_audio_buffer.commit"},
            {
                "type": "response.create",
                "response": response_payload,
            },
        ]

        denom = max(1, self._api_input_sample_rate) * 2
        duration_ms = int(round(bytes_to_commit / denom * 1000))
        LOGGER.debug(
            "Committing %d bytes (~%d ms at %s Hz; minimum %d bytes) to realtime session",
            bytes_to_commit,
            duration_ms,
            self._api_input_sample_rate,
            self._min_commit_bytes,
        )
        try:
            async with self._send_lock:
                for message in commands:
                    await self.ws.send(json.dumps(message))
        except Exception:
            self._pending_commit_bytes = 0
            self._bytes_since_commit = bytes_to_commit
            self._awaiting_response = False
            raise
        else:
            self._awaiting_response = True
            self._bytes_since_commit = 0
            LOGGER.info(
                "Committed %.2f ms (bytes=%d) of device audio to realtime session; awaiting response",
                duration_ms,
                bytes_to_commit,
            )

    async def clear_input_buffer(self) -> None:
        await self._ensure_connected()
        if not self.ws:
            return
        try:
            async with self._send_lock:
                await self.ws.send(json.dumps({"type": "input_audio_buffer.clear"}))
        except Exception:
            LOGGER.exception("Failed to clear realtime input buffer")
        else:
            self._bytes_since_commit = 0
            self._pending_commit_bytes = 0

    async def _reader_loop(self) -> None:
        assert self.ws is not None
        sample_rate = self._fallback_response_sample_rate
        try:
            async for raw in self.ws:
                try:
                    message = json.loads(raw)
                except json.JSONDecodeError:
                    LOGGER.debug("Realtime message parse failure: %r", raw)
                    continue

                msg_type = message.get("type")
                if msg_type in {
                    "response.output_audio.delta",
                    "response.audio.delta",
                }:
                    audio_obj = message.get("audio")
                    delta_b64: Optional[str] = message.get("delta")
                    if isinstance(audio_obj, dict):
                        if not delta_b64:
                            delta_b64 = (
                                audio_obj.get("delta")
                                or audio_obj.get("chunk")
                                or audio_obj.get("data")
                            )
                        sr_candidate = (
                            audio_obj.get("sample_rate_hz")
                            or audio_obj.get("sample_rate")
                        )
                        if sr_candidate is not None:
                            try:
                                sample_rate = int(sr_candidate)
                            except (TypeError, ValueError):
                                LOGGER.debug(
                                    "Unexpected sample rate payload %r", sr_candidate
                                )
                    response_id = (
                        message.get("response_id")
                        or (audio_obj.get("response_id") if isinstance(audio_obj, dict) else None)
                        or "unknown"
                    )
                    root_sr_candidate = message.get("sample_rate_hz") or message.get("sample_rate")
                    if root_sr_candidate is not None:
                        try:
                            sample_rate = int(root_sr_candidate)
                        except (TypeError, ValueError):
                            LOGGER.debug("Unexpected root sample rate payload %r", root_sr_candidate)
                    if delta_b64:
                        try:
                            chunk = base64.b64decode(delta_b64)
                        except (ValueError, TypeError):
                            LOGGER.debug("Failed to decode audio delta payload: %s", message)
                        else:
                            try:
                                sample_rate_int = int(sample_rate)
                            except (TypeError, ValueError):
                                sample_rate_int = self._fallback_response_sample_rate
                            send_chunk = chunk
                            send_rate = sample_rate_int
                            if sample_rate_int != self._TARGET_SAMPLE_RATE and sample_rate_int > 0:
                                try:
                                    send_chunk = self._resample_pcm(
                                        chunk,
                                        sample_rate_int,
                                        self._TARGET_SAMPLE_RATE,
                                    )
                                    send_rate = self._TARGET_SAMPLE_RATE
                                    if not self._resample_logged:
                                        LOGGER.info(
                                            "Realtime audio resampler engaged: %s Hz -> %s Hz",
                                            sample_rate_int,
                                            self._TARGET_SAMPLE_RATE,
                                        )
                                        self._resample_logged = True
                                    LOGGER.debug(
                                        "Resampled audio chunk from %s Hz to %s Hz (%d -> %d bytes)",
                                        sample_rate_int,
                                        self._TARGET_SAMPLE_RATE,
                                        len(chunk),
                                        len(send_chunk),
                                    )
                                except Exception:
                                    LOGGER.exception("Failed to resample audio chunk; using raw data")
                                    send_chunk = chunk
                                    send_rate = sample_rate_int
                            if self._pending_commit_bytes:
                                self._pending_commit_bytes = 0
                            await self.audio_callback(response_id, send_chunk, send_rate)
                    else:
                        LOGGER.debug("Realtime audio delta without payload: %s", message)
                elif msg_type in {
                    "response.audio.done",
                    "response.completed",
                    "response.done",
                    "response.error",
                }:
                    self._awaiting_response = False
                    if self._pending_commit_bytes:
                        self._pending_commit_bytes = 0
                    if self._bytes_since_commit >= self._min_commit_bytes:
                        await self._commit_and_request()
                    response_id = message.get("response_id") or (
                        message.get("item_id") if "item_id" in message else None
                    )
                    if msg_type in {
                        "response.audio.done",
                        "response.completed",
                        "response.done",
                    }:
                        try:
                            sample_rate_int = int(sample_rate)
                        except (TypeError, ValueError):
                            sample_rate_int = self._TARGET_SAMPLE_RATE
                        await self.audio_callback(
                            response_id or "unknown", b"", sample_rate_int
                        )
                        key = response_id or "unknown"
                        transcript = self._transcript_buffers.pop(key, "").strip()
                        final_text = (
                            (message.get("text") or "").strip()
                            if isinstance(message.get("text"), str)
                            else ""
                        )
                        chosen = final_text or transcript
                        if chosen and key not in self._transcript_logged:
                            LOGGER.info(
                                "Realtime response transcript (%s): %s",
                                key,
                                chosen,
                            )
                        self._transcript_logged.discard(key)
                    if msg_type == "response.error":
                        LOGGER.warning("Realtime response error: %s", message)
                    if not self._awaiting_response and self._commit_after_response:
                        self._commit_after_response = False
                        if self._bytes_since_commit >= self._min_commit_bytes:
                            await self._commit_and_request()
                elif msg_type == "response.audio_transcript.delta":
                    transcript_delta = message.get("delta") or message.get("text")
                    response_id = (
                        message.get("response_id")
                        or message.get("item_id")
                        or "unknown"
                    )
                    if transcript_delta:
                        key = response_id or "unknown"
                        existing = self._transcript_buffers.get(key, "")
                        self._transcript_buffers[key] = existing + transcript_delta
                        LOGGER.debug("Realtime transcript delta (%s): %s", key, transcript_delta)
                elif msg_type == "response.audio_transcript.done":
                    response_id = (
                        message.get("response_id")
                        or message.get("item_id")
                        or "unknown"
                    )
                    key = response_id or "unknown"
                    combined = self._transcript_buffers.get(key, "")
                    final_text = (
                        (message.get("text") or "").strip()
                        if isinstance(message.get("text"), str)
                        else ""
                    )
                    chosen = final_text or combined.strip()
                    if chosen:
                        LOGGER.info("Realtime transcript final (%s): %s", key, chosen)
                        self._transcript_logged.add(key)
                        self._transcript_buffers[key] = chosen
                elif msg_type == "conversation.item.created":
                    item = message.get("item") or {}
                    item_id = item.get("id")
                    if not isinstance(item_id, str) or not item_id:
                        continue
                    item_type = item.get("type")
                    if item_type == "input_audio":
                        self._user_transcripts.setdefault(item_id, "")
                    elif item_type == "input_audio_transcription":
                        text_body = self._extract_text_payload(
                            item.get("content") or item.get("text")
                        )
                        status = (item.get("status") or "").lower()
                        if status in {"completed", "done"}:
                            if text_body:
                                self._user_transcripts_logged.discard(item_id)
                                self._log_user_transcript(item_id, text_body)
                            self._user_transcripts.pop(item_id, None)
                            self._user_transcripts_logged.discard(item_id)
                        else:
                            if text_body:
                                self._user_transcripts[item_id] = text_body
                                LOGGER.debug(
                                    "Realtime user transcript seed (%s): %s",
                                    item_id,
                                    text_body,
                                )
                            else:
                                self._user_transcripts.setdefault(item_id, "")
                    elif item_type == "message":
                        role = (item.get("role") or "").lower()
                        text_body = self._extract_text_payload(
                            item.get("content") or item.get("text")
                        )
                        if role == "user":
                            if text_body:
                                self._user_transcripts_logged.discard(item_id)
                                self._log_user_transcript(item_id, text_body)
                                self._user_transcripts[item_id] = text_body
                            else:
                                self._user_transcripts.setdefault(item_id, "")
                        else:
                            self._user_transcripts.pop(item_id, None)
                            self._user_transcripts_logged.discard(item_id)
                    else:
                        existing = self._extract_text_payload(
                            item.get("content") or item.get("text")
                        )
                        if existing:
                            LOGGER.debug(
                                "Conversation item created (%s type=%s role=%s): %s",
                                item_id,
                                item_type,
                                item.get("role"),
                                existing,
                            )
                elif msg_type == "conversation.item.completed":
                    item = message.get("item") or {}
                    item_id = item.get("id")
                    if not isinstance(item_id, str) or not item_id:
                        continue
                    item_type = item.get("type")
                    if item_type == "input_audio_transcription":
                        final_text = self._extract_text_payload(
                            item.get("content") or item.get("text")
                        )
                        if not final_text:
                            final_text = self._user_transcripts.get(item_id, "").strip()
                        if final_text:
                            self._user_transcripts_logged.discard(item_id)
                            self._log_user_transcript(item_id, final_text)
                        self._user_transcripts.pop(item_id, None)
                        self._user_transcripts_logged.discard(item_id)
                    elif item_type == "message":
                        role = (item.get("role") or "").lower()
                        final_text = self._extract_text_payload(
                            item.get("content") or item.get("text")
                        )
                        if role == "user":
                            if not final_text:
                                final_text = self._user_transcripts.get(item_id, "").strip()
                            if final_text:
                                self._user_transcripts_logged.discard(item_id)
                                self._log_user_transcript(item_id, final_text)
                        self._user_transcripts.pop(item_id, None)
                        self._user_transcripts_logged.discard(item_id)
                    else:
                        self._user_transcripts.pop(item_id, None)
                        self._user_transcripts_logged.discard(item_id)
                elif msg_type == "conversation.item.delta":
                    item = message.get("item") or {}
                    item_id = item.get("id")
                    if not isinstance(item_id, str) or not item_id:
                        continue
                    item_type = item.get("type")
                    if item_type == "message":
                        role = (item.get("role") or "").lower()
                        delta_body = self._extract_text_payload(
                            item.get("delta") or item.get("content") or item.get("text")
                        )
                        if role == "user" and delta_body:
                            existing = self._user_transcripts.get(item_id, "")
                            combined = f"{existing}{delta_body}"
                            self._user_transcripts[item_id] = combined
                            LOGGER.debug(
                                "Realtime user transcript delta (%s): %s",
                                item_id,
                                delta_body,
                            )
                    elif item_type == "input_audio_transcription":
                        delta_body = self._extract_text_payload(
                            item.get("delta") or item.get("content") or item.get("text")
                        )
                        if delta_body:
                            existing = self._user_transcripts.get(item_id, "")
                            combined = f"{existing}{delta_body}"
                            self._user_transcripts[item_id] = combined
                            LOGGER.debug(
                                "Realtime user transcript delta (%s): %s",
                                item_id,
                                delta_body,
                            )
                elif msg_type == "conversation.item.deleted":
                    item = message.get("item") or {}
                    item_id = item.get("id")
                    if isinstance(item_id, str) and item_id:
                        self._user_transcripts.pop(item_id, None)
                        self._user_transcripts_logged.discard(item_id)
                elif msg_type == "input_audio_transcription.delta":
                    item_id = message.get("item_id")
                    if not isinstance(item_id, str) or not item_id:
                        continue
                    delta_text = self._extract_text_payload(
                        message.get("delta") or message.get("text")
                    )
                    if delta_text:
                        existing = self._user_transcripts.get(item_id, "")
                        combined = f"{existing}{delta_text}"
                        self._user_transcripts[item_id] = combined
                        LOGGER.debug(
                            "Realtime user transcript delta (%s): %s",
                            item_id,
                            delta_text,
                        )
                elif msg_type in {
                    "input_audio_transcription.completed",
                    "input_audio_transcription.done",
                }:
                    item_id = message.get("item_id")
                    if not isinstance(item_id, str) or not item_id:
                        continue
                    final_text = self._extract_text_payload(
                        message.get("transcription")
                        or message.get("text")
                        or message.get("delta")
                    )
                    if not final_text:
                        final_text = self._user_transcripts.get(item_id, "").strip()
                    if final_text:
                        self._user_transcripts_logged.discard(item_id)
                        self._log_user_transcript(item_id, final_text)
                    self._user_transcripts.pop(item_id, None)
                    self._user_transcripts_logged.discard(item_id)
                elif msg_type == "input_audio_buffer.speech_started":
                    self._speech_active = True
                    LOGGER.info("Realtime detected speech start")
                elif msg_type == "input_audio_buffer.speech_stopped":
                    self._speech_active = False
                    LOGGER.info(
                        "Realtime detected speech stop (buffered_bytes=%d awaiting_response=%s)",
                        self._bytes_since_commit,
                        self._awaiting_response,
                    )
                    self._flush_user_transcripts(require_text=True)
                    self._extend_resume_deadline(0, "unknown")
                    if self._mic_paused:
                        LOGGER.debug("Speech stopped while mic paused; deferring commit")
                    elif self._bytes_since_commit >= self._min_commit_bytes:
                        if self._awaiting_response:
                            self._commit_after_response = True
                        else:
                            await self._commit_and_request()
                    else:
                        LOGGER.debug(
                            "Speech stopped but only %d bytes buffered (< %d); waiting for more audio.",
                            self._bytes_since_commit,
                            self._min_commit_bytes,
                        )
                elif msg_type == "session.updated":
                    session_info = message.get("session", {})
                    input_format = session_info.get("input_audio_format")
                    input_rate = (
                        session_info.get("sample_rate")
                        or session_info.get("sample_rate_hz")
                        or session_info.get("sample_rate")
                    )
                    output_format = session_info.get("output_audio_format")
                    output_rate = (
                        session_info.get("output_audio_sample_rate")
                        or session_info.get("output_sample_rate")
                        or session_info.get("sample_rate")
                    )
                    LOGGER.info(
                        "Realtime session acknowledged voice=%s input=%s@%sHz output=%s@%sHz modalities=%s instructions_preview=%r",
                        session_info.get("voice"),
                        input_format,
                        input_rate,
                        output_format,
                        output_rate,
                        session_info.get("modalities"),
                        (session_info.get("instructions") or "")[:120],
                    )
                elif msg_type == "error":
                    LOGGER.error("Realtime session error: %s", message)
                    error_info = message.get("error") or {}
                    LOGGER.debug(
                        "Realtime error details: code=%s message=%s bytes_since_commit=%d pending_commit_bytes=%d awaiting_response=%s",
                        error_info.get("code"),
                        (error_info.get("message") or "").strip(),
                        self._bytes_since_commit,
                        self._pending_commit_bytes,
                        self._awaiting_response,
                    )
                    was_waiting = self._awaiting_response
                    if self._pending_commit_bytes:
                        self._bytes_since_commit += self._pending_commit_bytes
                        self._pending_commit_bytes = 0
                    code = error_info.get("code")
                    if code == "input_audio_buffer_commit_empty":
                        LOGGER.debug(
                            "Realtime session reported empty audio commit; waiting for additional buffered audio before retrying."
                        )
                        self._bytes_since_commit = 0
                        self._pending_commit_bytes = 0
                        self._awaiting_response = was_waiting
                        if was_waiting:
                            self._commit_after_response = True
                    elif code == "conversation_already_has_active_response":
                        # A response is still streaming; keep waiting for completion before committing again.
                        self._awaiting_response = True
                    else:
                        self._awaiting_response = False
                        LOGGER.debug(
                            "Realtime session error code=%s message=%s",
                            code,
                            (error_info.get("message") or "").strip(),
                        )
                else:
                    LOGGER.debug("Unhandled realtime message: %s", message.get("type"))
        except asyncio.CancelledError:  # pragma: no cover - shutdown path
            raise
        except websockets.ConnectionClosed:
            LOGGER.info("Realtime session websocket closed")
        except Exception:  # pragma: no cover
            LOGGER.exception("Realtime reader loop crashed")
        finally:
            self.ws = None
            self._awaiting_response = False
            self._bytes_since_commit = 0
            self._pending_commit_bytes = 0
            self._commit_after_response = False
            self._speech_active = False
            self._connected = False
            self._flush_user_transcripts()
            self._transcript_buffers.clear()
            self._transcript_logged.clear()
            self._user_transcripts.clear()
            self._user_transcripts_logged.clear()

class PumpkinSession:
    """Tracks one Watcher connection."""

    def __init__(
        self,
        websocket: WebSocketConnection,
        config: RuntimeConfig,
        capture_dir: Optional[Path] = None,
    ):
        self.websocket = websocket
        self.config = config
        self.id = f"client-{id(self):x}"
        self.openai: Optional[OpenAIRealtimeSession] = None
        self._current_response_id: Optional[str] = None
        self._response_sequence = 0
        self._mic_paused = False
        self._resume_deadline_s: float = 0.0
        self._resume_task: Optional[asyncio.Task[None]] = None
        self._resume_extra_buffer_s = 0.4
        self._resume_event = asyncio.Event()
        self._resume_in_progress = False
        self._capture_dir = capture_dir
        self._capture_writer: Optional[wave.Wave_write] = None
        self._capture_path: Optional[Path] = None
        self._capture_bytes = 0
        self._capture_sample_rate = int(getattr(self.config.openai, "target_sample_rate_hz", 16000))
        if self._capture_sample_rate <= 0:
            self._capture_sample_rate = 16000
        self._capture_failed = False
        if self._capture_dir:
            self._start_capture()

    def _start_capture(self) -> None:
        try:
            timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            filename = f"{self.id}-{timestamp}.wav"
            path = self._capture_dir / filename
            writer = wave.open(str(path), "wb")
            writer.setnchannels(1)
            writer.setsampwidth(2)
            writer.setframerate(self._capture_sample_rate)
        except Exception:
            LOGGER.exception(
                "Session %s failed to start capture in %s", self.id, self._capture_dir
            )
            if "writer" in locals():
                with contextlib.suppress(Exception):
                    writer.close()
            self._capture_writer = None
            self._capture_path = None
            self._capture_failed = True
        else:
            self._capture_writer = writer
            self._capture_path = path
            self._capture_bytes = 0
            LOGGER.info("Session %s capturing device audio to %s", self.id, path)

    def _record_incoming_audio(self, chunk: bytes) -> None:
        if not chunk or not self._capture_writer:
            return
        try:
            self._capture_writer.writeframes(chunk)
            self._capture_bytes += len(chunk)
        except Exception:
            self._capture_failed = True
            LOGGER.exception("Session %s failed to write capture chunk", self.id)
            self._stop_capture(log_success=False)

    def _stop_capture(self, log_success: bool = True) -> None:
        writer = self._capture_writer
        path = self._capture_path
        if writer:
            self._capture_writer = None
            try:
                writer.close()
            except Exception:
                LOGGER.exception("Session %s failed to finalize capture file", self.id)
                path = None
        if log_success and path and not self._capture_failed:
            duration_s = 0.0
            if self._capture_sample_rate > 0:
                duration_s = self._capture_bytes / (self._capture_sample_rate * 2)
            LOGGER.info(
                "Session %s saved %.2f s (bytes=%d) of device audio to %s",
                self.id,
                duration_s,
                self._capture_bytes,
                path,
            )
        self._capture_path = None
        self._capture_writer = None

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
            self._resume_event.set()
            if self._resume_task and not self._resume_task.done():
                self._resume_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await self._resume_task
                self._resume_task = None
            self._resume_event.clear()
            if self._mic_paused:
                LOGGER.info("Session %s resuming mic during shutdown", self.id)
                with contextlib.suppress(Exception):
                    await self._send(
                        {
                            "type": "command",
                            "name": "resume_mic",
                            "payload": {"reason": "session_shutdown"},
                        }
                    )
                self._mic_paused = False
                if self.openai:
                    self.openai.set_mic_paused(False)
            if self.openai:
                await self.openai.close()
            self._stop_capture(log_success=not self._capture_failed)
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
        """Process messages from the watcher firmware."""
        async for incoming in self.websocket:
            if isinstance(incoming, bytes):
                if self._mic_paused:
                    LOGGER.debug(
                        "Session %s dropping %d audio bytes while mic paused",
                        self.id,
                        len(incoming),
                    )
                    continue
                if not incoming:
                    continue
                LOGGER.debug("Session %s received %d audio bytes", self.id, len(incoming))
                self._record_incoming_audio(incoming)
                if not self.openai and self.config.api_key:
                    try:
                        self.openai = OpenAIRealtimeSession(
                            self.config,
                            self._handle_openai_audio,
                            resume_callback=self._extend_resume_deadline,
                        )
                    except Exception:  # pragma: no cover - initialization failure
                        LOGGER.exception("Failed to initialize OpenAI realtime session; falling back to echo")
                        self.openai = None
                if self.openai:
                    await self.openai.enqueue_audio(incoming)
                else:
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

    def _extend_resume_deadline(self, duration_ms: int, response_id: str) -> None:
        loop = asyncio.get_running_loop()
        chunk_seconds = max(0.0, duration_ms / 1000.0)
        deadline = loop.time() + chunk_seconds + self._resume_extra_buffer_s
        if deadline > self._resume_deadline_s:
            self._resume_deadline_s = deadline
        if self._mic_paused:
            self._resume_event.set()
            if self._resume_task is None:
                self._resume_task = asyncio.create_task(self._resume_mic_scheduler(response_id))

    async def _resume_mic_scheduler(self, response_id: str) -> None:
        loop = asyncio.get_running_loop()
        try:
            while self._mic_paused:
                delay = max(0.0, self._resume_deadline_s - loop.time())
                self._resume_event.clear()
                try:
                    await asyncio.wait_for(self._resume_event.wait(), timeout=delay)
                    if not self._mic_paused:
                        break
                    continue
                except asyncio.TimeoutError:
                    if not self._mic_paused:
                        break
                    if self._resume_in_progress:
                        continue
                    self._resume_in_progress = True
                    try:
                        if self._mic_paused and not getattr(self.websocket, "closed", False):
                            LOGGER.info(
                                "Session %s resuming mic after response_id=%s", self.id, response_id
                            )
                            await self._send(
                                {
                                    "type": "command",
                                    "name": "resume_mic",
                                    "payload": {"response_id": response_id},
                                }
                            )
                            self._mic_paused = False
                            if self.openai:
                                self.openai.set_mic_paused(False)
                            self._resume_deadline_s = 0.0
                    finally:
                        self._resume_in_progress = False
        except asyncio.CancelledError:
            raise
        except Exception:
            LOGGER.exception("Failed while attempting to resume microphone")
        finally:
            self._resume_task = None
            self._resume_event.clear()

    async def _handle_audio_echo(self, audio_bytes: bytes) -> None:
        """Echo incoming audio back to the device as a temporary stand-in."""
        await self._send({"type": "audio_out", "format": "pcm16", "duration_ms": 0})
        await self.websocket.send(audio_bytes)

    async def _handle_openai_audio(
        self, response_id: str, pcm_chunk: bytes, sample_rate: int
    ) -> None:
        if getattr(self.websocket, "closed", False):
            return

        if not pcm_chunk:
            if response_id == "unknown" and self._current_response_id:
                response_id = self._current_response_id
            if self._current_response_id == response_id:
                self._current_response_id = None
                self._response_sequence = 0
            if response_id:
                self._extend_resume_deadline(0, response_id)
            return

        safe_rate = sample_rate if sample_rate > 0 else 16000
        duration_ms = int(len(pcm_chunk) / (safe_rate * 2) * 1000)
        device_response_id = response_id or "unknown"

        if response_id != self._current_response_id:
            self._current_response_id = response_id
            self._response_sequence = 0
            if not self._mic_paused:
                resume_ms = max(duration_ms + 120, 240)
                LOGGER.info(
                    "Session %s pausing mic for %.0f ms (response_id=%s)",
                    self.id,
                    resume_ms,
                    device_response_id,
                )
                await self._send(
                    {
                        "type": "command",
                        "name": "pause_mic",
                        "payload": {
                            "resume_after_ms": resume_ms,
                            "response_id": device_response_id,
                        },
                    }
                )
                self._mic_paused = True
                if self.openai:
                    self.openai.set_mic_paused(True)
                self._resume_event.set()
        self._extend_resume_deadline(duration_ms, device_response_id)
        await self._send(
            {
                "type": "audio_out",
                "format": "pcm16",
                "sample_rate": safe_rate,
                "duration_ms": duration_ms,
                "response_id": device_response_id,
                "sequence": self._response_sequence,
            }
        )
        await self.websocket.send(pcm_chunk)
        self._response_sequence += 1

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
    capture_dir: Optional[Path]


async def run_server(options: ServerOptions) -> None:
    runtime = RuntimeConfig.from_filesystem(options.config_path)
    persona_instructions = compose_persona_instructions(runtime.persona)
    LOGGER.info(
        "Loaded persona config: name=%s voice=%s humour=%s style=%s instructions=\"%s\"",
        runtime.persona.name,
        (runtime.persona.voice or runtime.openai.output_voice),
        runtime.persona.humour_level,
        runtime.persona.speaking_style,
        persona_instructions,
    )
    LOGGER.info(
        "OpenAI realtime model=%s audio_format=%s voice=%s",
        runtime.openai.model,
        runtime.openai.audio_format,
        runtime.openai.output_voice,
    )
    capture_dir = options.capture_dir
    if capture_dir:
        capture_dir = capture_dir.expanduser()
        capture_dir.mkdir(parents=True, exist_ok=True)
        capture_dir = capture_dir.resolve()
        LOGGER.info("Device audio captures will be stored in %s", capture_dir)

    ssl_context = None
    if options.cert and options.key:
        import ssl

        ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ssl_context.load_cert_chain(certfile=str(options.cert), keyfile=str(options.key))
        LOGGER.info("TLS enabled using certificate %s", options.cert)
    else:
        LOGGER.warning("Starting server without TLS; use only for local development.")

    async def handler(websocket: WebSocketConnection) -> None:
        session = PumpkinSession(websocket, runtime, capture_dir=capture_dir)
        await session.run()

    server = await serve(
        handler,
        host=options.host,
        port=options.port,
        ssl=ssl_context,
        max_size=4 * 1024 * 1024,  # allow generous audio frames
        ping_interval=None,  # ESP-IDF websocket client does not respond to protocol pings reliably
        ping_timeout=None,
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
    parser.add_argument(
        "--capture-dir",
        type=Path,
        help="Directory for saving incoming device audio as WAV files",
    )
    args = parser.parse_args(argv)
    return ServerOptions(
        host=args.host,
        port=args.port,
        cert=args.cert,
        key=args.key,
        config_path=args.config,
        log_level=args.log_level,
        capture_dir=args.capture_dir,
    )


def configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )


async def _async_main(options: ServerOptions) -> None:
    configure_logging(options.log_level)
    await run_server(options)


def main(argv: Optional[list[str]] = None) -> None:
    options = parse_args(argv)
    asyncio.run(_async_main(options))


if __name__ == "__main__":
    main()
