# SenseCAP Pumpkin Server

This directory contains the Mac-side service that orchestrates the "sentient pumpkin" experience. The server accepts a
TLS WebSocket connection from the SenseCAP Watcher, relays microphone audio to OpenAI's speech-to-speech API, and streams
responses (audio plus animation cues) back to the device.

The first milestone is a minimal signalling pipeline that lets the firmware connect, exchange configuration, and echo audio
chunks. Later iterations will swap the echo path for real OpenAI integration, richer state handling, and persona-specific
behaviour.

## Prerequisites

* macOS with Python 3.11 or newer (the ESP-IDF environment already ships with Python 3.11).
* An OpenAI API key with access to the realtime speech models.
* `openssl` (system-provided) for generating a local development certificate.

## Creating this directory with Git (for first-time setup)

If you are brand new to Git and need help creating the `server/` folder structure, follow the step-by-step guide in
[`docs/server_module_setup.md`](../docs/server_module_setup.md). It walks through branching, making the directories, adding the
starter files (including the persona configuration), staging the changes, and committing them to your repository. Complete that
guide before returning here to run the commands below.

## Quick start

Change into the `server/` directory in the repository root before running the setup commands:

```bash
cd server
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp config/persona.example.yaml config/persona.yaml
python main.py --host 0.0.0.0 --port 8080 --cert certs/dev.pem --key certs/dev-key.pem
```

The requirements file pins `websockets` 14.x to match the server code. If you previously installed a different version in this
virtual environment, rerun the `pip install` command so the dependency is upgraded and the deprecation warnings disappear.

During development you can run without TLS by omitting `--cert/--key` (the handler automatically falls back to a plain
WebSocket in that case). The production firmware should use TLS.

## Configuration

* `config/persona.yaml` – human-editable persona details (role, objective, humour level, voice, wake/goodbye phrases).
* Environment variables (set them in the same shell session **before** launching `python main.py`):
  * `OPENAI_API_KEY` – required before enabling the speech-to-speech bridge. Example:

    ```bash
    export OPENAI_API_KEY="sk-your-key"
    ```

  * `PUMPKIN_CONFIG_PATH` – optional override for the persona file. Point it at an absolute path if you keep personas elsewhere:

    ```bash
    export PUMPKIN_CONFIG_PATH="/Users/you/dev/pumpkin/personas/spooky.yaml"
    ```

  If you prefer the variables to persist across terminals, add the `export` lines to your shell profile (for example, `~/.zshrc`) and restart the shell.

The `openai` section in `persona.yaml` also lets you tune audio sample rates:

* `output_sample_rate_hz` describes the raw rate produced by the OpenAI model (24 kHz today).
* `target_sample_rate_hz` is what the SenseCAP Watcher expects (16 kHz PCM). The server automatically resamples whenever those numbers differ.

### Verifying environment variables and persona configuration

Run these checks from the `server/` directory **after** activating your virtual environment:

```bash
cd server
source .venv/bin/activate  # if not already active
python - <<'PY'
import os, pathlib, yaml

api_key = os.getenv("OPENAI_API_KEY")
if api_key:
    print("✅ OPENAI_API_KEY is set (length:", len(api_key), ")")
else:
    print("❌ OPENAI_API_KEY is missing")

config_path = pathlib.Path(os.getenv("PUMPKIN_CONFIG_PATH", "config/persona.yaml"))
print("Using persona file:", config_path)

try:
    data = yaml.safe_load(config_path.read_text(encoding="utf-8"))
except FileNotFoundError:
    print("❌ Persona file not found")
else:
    required_fields = {"persona", "openai", "server"}
    missing = required_fields.difference(data)
    if missing:
        print("❌ Persona file missing keys:", ", ".join(sorted(missing)))
    else:
        print("✅ Persona file structure looks good")
PY
```

The script keeps the API key hidden while confirming it is present, reports which persona file is being used, and validates that it contains the top-level sections required by the server. Rerun the script whenever you change the persona path or environment variables.

Reload the server after editing the persona file to apply changes.

## Development roadmap

1. **Session bootstrap** – _implemented in this commit_: accepts the device handshake, loads persona config, and sends a
   `config` message with key timing values.
2. **Audio bridging** – _implemented now_: microphone frames are forwarded to OpenAI's realtime API and the generated audio is streamed back to the watcher.
3. **Animation cues** – coordinate viseme/phoneme metadata (or an energy proxy) to drive the Watcher's eye/mouth sprites.
4. **Persona switching** – extend the config loader to support multiple personas and remote switching.

## Testing

Run the automated test suite before making larger changes so you catch regressions in the handshake and audio stubs:

```bash
pytest server/tests
```

The tests rely on `pytest` and `pytest-asyncio`, which are included in `requirements.txt`. Activate your virtual environment
and reinstall requirements if you have not done so since pulling the latest changes.
