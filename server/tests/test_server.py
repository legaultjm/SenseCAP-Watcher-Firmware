import asyncio
import json
import sys
import urllib.error
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:  # pragma: no cover - dependency shim when PyYAML is unavailable
    import yaml  # type: ignore
except ModuleNotFoundError:  # pragma: no cover
    import types

    yaml = types.SimpleNamespace(  # type: ignore[assignment]
        safe_load=lambda data: json.loads(data),
    )
    sys.modules.setdefault("yaml", yaml)

try:  # pragma: no cover - dependency shim when websockets isn't installed
    import websockets  # type: ignore
except ModuleNotFoundError:  # pragma: no cover
    import types

    websockets = types.SimpleNamespace(  # type: ignore[assignment]
        ConnectionClosedOK=type("ConnectionClosedOK", (Exception,), {}),
        ConnectionClosedError=type("ConnectionClosedError", (Exception,), {}),
    )

    async def _noop_serve(*args, **kwargs):
        class _DummyServer:
            def close(self):
                return None

            async def wait_closed(self):
                return None

        return _DummyServer()

    websocket_server = types.SimpleNamespace(WebSocketServerProtocol=object, serve=_noop_serve)
    async_server = types.SimpleNamespace(ServerConnection=object, serve=_noop_serve)

    websockets.server = websocket_server  # type: ignore[attr-defined]
    websockets.asyncio = types.SimpleNamespace(server=async_server)  # type: ignore[attr-defined]
    websockets.legacy = types.SimpleNamespace(server=websocket_server)  # type: ignore[attr-defined]

    sys.modules.setdefault("websockets", websockets)  # type: ignore[arg-type]
    sys.modules.setdefault("websockets.server", websocket_server)
    sys.modules.setdefault("websockets.asyncio", types.SimpleNamespace(server=async_server))
    sys.modules.setdefault("websockets.asyncio.server", async_server)
    sys.modules.setdefault("websockets.legacy", types.SimpleNamespace(server=websocket_server))
    sys.modules.setdefault("websockets.legacy.server", websocket_server)

from server.main import PumpkinSession, RuntimeConfig, ValidationError


class StubWebSocket:
    def __init__(self, incoming):
        self._incoming = asyncio.Queue()
        for item in incoming:
            self._incoming.put_nowait(item)
        self.sent = []
        self.remote_address = ("stub", 0)

    async def recv(self):
        return await self._incoming.get()

    async def send(self, data):
        self.sent.append(data)

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._incoming.empty():
            raise StopAsyncIteration
        return await self.recv()


@pytest.fixture
def runtime_config(tmp_path, monkeypatch):
    config_file = tmp_path / "persona.yaml"
    config_file.write_text(
        json.dumps(
            {
                "persona": {
                    "name": "Pumpkin",
                    "role": "Friendly pumpkin",
                    "objective": "Delight visitors",
                    "humour_level": "goofy",
                    "voice": "alloy",
                    "speaking_style": "playful",
                    "wake_phrases": ["hello"],
                    "goodbye_phrases": ["bye"],
                    "silence_timeout_s": 10,
                },
                "openai": {
                    "model": "gpt-speech-latest",
                    "audio_format": "pcm16",
                    "output_voice": "alloy",
                },
                "server": {
                    "awake_animation_ms": 800,
                    "sleep_animation_ms": 2000,
                    "max_listen_seconds": 30,
                    "reconnect_backoff_s": [1, 2, 3],
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    runtime = RuntimeConfig.from_filesystem(config_file)
    return runtime


def test_runtime_config_missing_file(tmp_path):
    missing = tmp_path / "missing.yaml"
    with pytest.raises(FileNotFoundError):
        RuntimeConfig.from_filesystem(missing)


def test_runtime_config_loads_api_key(tmp_path, monkeypatch):
    config_file = tmp_path / "persona.yaml"
    config_file.write_text(
        json.dumps(
            {
                "persona": {
                    "name": "Pumpkin",
                    "role": "Friendly",
                    "objective": "Fun",
                    "humour_level": "goofy",
                    "voice": "alloy",
                    "speaking_style": "playful",
                },
                "openai": {"model": "gpt-speech-latest"},
                "server": {
                    "awake_animation_ms": 100,
                    "sleep_animation_ms": 200,
                    "max_listen_seconds": 10,
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("OPENAI_API_KEY", "abc123")

    runtime = RuntimeConfig.from_filesystem(config_file)

    assert runtime.api_key == "abc123"


def test_runtime_config_warns_without_api_key(tmp_path, caplog, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    config_file = tmp_path / "persona.yaml"
    config_file.write_text(
        json.dumps(
            {
                "persona": {
                    "name": "Pumpkin",
                    "role": "Friendly",
                    "objective": "Fun",
                    "humour_level": "goofy",
                    "voice": "alloy",
                    "speaking_style": "playful",
                },
                "openai": {"model": "gpt-speech-latest"},
                "server": {
                    "awake_animation_ms": 800,
                    "sleep_animation_ms": 2000,
                    "max_listen_seconds": 30,
                },
            }
        ),
        encoding="utf-8",
    )

    with caplog.at_level("WARNING"):
        runtime = RuntimeConfig.from_filesystem(config_file)

    assert "OPENAI_API_KEY is not set" in caplog.text
    assert runtime.api_key is None


def test_runtime_config_invalid_schema(tmp_path):
    config_file = tmp_path / "persona.yaml"
    config_file.write_text(json.dumps({"bad": "data"}), encoding="utf-8")

    with pytest.raises(ValidationError):
        RuntimeConfig.from_filesystem(config_file)


def test_verify_openai_reachability_success(runtime_config):
    events = {}

    def requester(req):
        events["headers"] = dict(req.header_items())
        events.setdefault("methods", []).append(req.get_method())
        return 200, b""

    runtime_config.verify_openai_reachability(requester)
    assert events["headers"]["Authorization"] == "Bearer test-key"
    assert events["methods"] == ["HEAD"]


def test_verify_openai_reachability_failure(runtime_config):
    with pytest.raises(RuntimeError):
        runtime_config.verify_openai_reachability(lambda req: (401, b""))


def test_verify_openai_reachability_head_retry(runtime_config):
    methods = []

    def requester(req):
        methods.append(req.get_method())
        if req.get_method() == "HEAD":
            raise urllib.error.HTTPError(req.full_url, 405, "", hdrs=None, fp=None)
        return 200, b""

    runtime_config.verify_openai_reachability(requester)
    assert methods == ["HEAD", "GET"]


@pytest.mark.asyncio
async def test_handshake_sends_configuration(runtime_config):
    stub = StubWebSocket([json.dumps({"type": "hello"})])
    session = PumpkinSession(stub, runtime_config)

    await session._perform_handshake()

    assert len(stub.sent) == 1
    payload = json.loads(stub.sent[0])
    assert payload["type"] == "config"
    assert payload["persona"]["name"] == "Pumpkin"
    assert payload["openai"]["model"] == "gpt-speech-latest"


@pytest.mark.asyncio
async def test_handle_audio_echo(runtime_config):
    stub = StubWebSocket([])
    session = PumpkinSession(stub, runtime_config)

    await session._handle_audio_echo(b"sample")

    assert len(stub.sent) == 2
    metadata = json.loads(stub.sent[0])
    assert metadata["type"] == "audio_out"
    assert stub.sent[1] == b"sample"


@pytest.mark.asyncio
async def test_handshake_rejects_binary(runtime_config):
    stub = StubWebSocket([b"\x00\x01"])
    session = PumpkinSession(stub, runtime_config)

    with pytest.raises(ValueError):
        await session._perform_handshake()
