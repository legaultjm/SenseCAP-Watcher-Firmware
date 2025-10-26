# SenseCAP Pumpkin Architecture

## 1. Objectives
- Deliver a whimsical "sentient pumpkin" greeter that wakes up when trick-or-treaters approach, chats with them in a playful voice, and returns to sleep when the interaction ends.
- Provide responsive speech-to-speech conversations with minimal latency by delegating heavy AI workloads to the Mac server while the ESP32-S3 handles real-time I/O.
- Make persona, humour level, and interaction goals easy to edit (for example by tweaking a server config file or rebuilding the firmware) without requiring any runtime serial setup.

## 2. On-Device Responsibilities
- Boot, join Wi-Fi using baked-in credentials, and establish a persistent TLS WebSocket connection to the Mac server.
- Capture near-real-time microphone audio (16 kHz mono PCM) and stream it upstream when the state machine requests live inference.
- Buffer and play back server-sent audio responses using the built-in speaker with smooth transitions between clips.
- Drive the LCD to display animated eye/mouth sprites for awake, talking, listening, and sleeping states. Animations should be triggered by server cues or local timers.
- Maintain lightweight state (awake/asleep, last activity timestamp) and send heartbeat/ping frames to keep the session alive.
- Persist minimal configuration such as device identifier and animation timing, but rely on server commands for conversation-specific tuning.

## 3. Mac Server Responsibilities
- Expose a WebSocket endpoint that the Watcher connects to for bidirectional audio/data streaming.
- Manage OpenAI's latest speech-to-speech API session lifecycle: create sessions with configurable role/objective prompts, stream inbound audio frames, and forward generated audio output back to the device.
- Convert audio formats as needed (e.g., PCM to 16-bit WAV for API requests, and API output to Opus/PCM compatible with the Watcher playback pipeline).
- Orchestrate a higher-level conversation state machine, deciding when to wake/sleep based on audio energy, recognised phrases ("trick or treat", "goodbye"), and LLM intents.
- Provide a simple configuration surface (e.g., JSON/YAML file or CLI flags) whose values take effect after a quick server restart so persona, objective, humour level, wake phrases, and TTS voice can be edited without touching the device.
- Log interactions, maintain metrics, and gracefully recover from dropped device connections.

## 4. Communication Protocol
- Transport: TLS-secured WebSocket initiated by the Watcher to the Mac server (e.g., `wss://192.168.1.103:8080/ws`).
- Message framing: JSON envelopes with optional binary attachments for audio chunks. Key message types:
  - `hello`: device → server handshake containing firmware version and desired persona profile.
  - `config`: server → device with current animation timings, sleep timeout, and any overrides.
  - `audio_in`: device → server streaming PCM audio frames with timestamps.
  - `audio_out`: server → device streaming audio segments plus viseme/animation cues.
  - `state`: either direction to signal state transitions (`awake`, `listening`, `speaking`, `sleep`).
  - `command`: server → device instructions such as `play_animation`, `adjust_volume`, or `sleep_now`.
  - `ack` / `error`: confirm receipt or report protocol problems.
- Heartbeats: WebSocket ping/pong every 15 seconds with failover reconnect logic (backoff up to 60 seconds).

## 5. Conversation State Machine
- **Sleep**: default idle; display slow breathing animation. Transition to `Listening` when server detects sustained ambient noise or receives explicit wake command.
- **Listening**: device streams microphone audio continuously; server analyses energy levels and transcripts. Transition to `Speaking` when OpenAI returns a response. If silence persists beyond configurable timeout, fall back to `Sleep`.
- **Speaking**: device plays server audio and displays talking animation. After playback finishes, transition to `Listening` unless goodbye intent detected.
- **Goodbye**: optional intermediate when the server detects farewell phrases; instruct device to play closing animation/sound, then move to `Sleep`.
- The server owns most transition logic, while the device enforces animation changes and local fallbacks (e.g., return to `Sleep` if connection drops).

## 6. Configuration Points
- Persona configuration (role, objective, humour level) stored in a server-side YAML/JSON file that is reloaded when the service restarts; the server pushes the active persona to the device via `config` messages so no serial tweaks are required.
- TTS voice selection and parameters (pitch, pace, timbre) adjustable per persona, mapped to OpenAI session settings.
- Wake/sleep timeouts, silence thresholds, and audio gain stored on the server with defaults compiled into firmware for safety.
- Device-specific identifiers, Wi-Fi credentials, and API keys compiled directly into firmware source so the unit boots ready to connect without any serial provisioning, while server URL/port can still be overridden via Kconfig if needed.
- Logging verbosity and debug tracing toggles available both server-side (CLI flag) and device-side (menuconfig option) to aid troubleshooting.

## 7. OpenAI Session Details
- Model: target the latest `gpt-4o-realtime-preview` (or successor) supporting full duplex speech-to-speech streaming.
- Session initialisation: send `role` and `objective` strings captured from persona config; include `style` modifiers for humour.
- Audio input: 16 kHz 16-bit PCM packaged as incremental WebRTC-compatible frames; ensure the API is configured for streaming latency optimisation.
- Audio output: request the desired voice preset and format (e.g., 16 kHz PCM or Opus). Provide phoneme/viseme metadata if available to drive mouth animations.
- Safety: configure content filters and fallback instructions (e.g., defuse inappropriate language) within the objective prompt.
- Session renewal: handle token expiry by recycling sessions transparently and notifying the device if a brief pause is required.

## 8. Open Questions / Risks
- Does the OpenAI speech-to-speech API expose viseme timing needed for precise mouth animations, or do we approximate based on audio energy?
- Latency budget: can the Wi-Fi + server pipeline maintain sub-500 ms round trips, or do we need local buffering tricks?
- How robust is LLM-based presence detection compared to explicit audio RMS thresholds—do we need a hybrid approach?
- Offline handling: acceptable to remain in the Sleep state with idle animation until connectivity returns; manual restart is fine once service is back.
- Security: minimal hardening required for this project; plan to revoke the OpenAI API key manually after the event.
