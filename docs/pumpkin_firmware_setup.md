# Pumpkin Firmware Setup (Step-by-Step)

These instructions guide you through creating the custom firmware project that will talk to the pumpkin server. Every command assumes you already cloned the repository into `~/dev/SenseCAP-Watcher-Firmware` and that your ESP-IDF environment is ready (you can build the stock `openai-realtime` example without errors).

## Status snapshot (Oct 2025)

- Milestone 1 (connectivity) and Milestone 2 (handshake) are implemented in `examples/pumpkin-firmware`.
- Milestone 3 loopback path upgraded: the firmware now streams live microphone audio to the Mac server, which forwards frames to OpenAI and relays the generated PCM straight back to the device.
- Still outstanding: persona-driven command handling, UI polish, and automated tests for the new pumpkin client and server bridge.

**Immediate next actions**
1. Provision your `OPENAI_API_KEY`, start the pumpkin server (`python main.py ...`), and watch the console for `Pumpkin server ready`.
2. Flash the firmware, open a serial monitor, and confirm:
   - `Pumpkin client` switches through `connecting -> awaiting_config -> ready`.
   - `Audio TX sent frame` lines appear (20 ms cadence).
   - `Audio RX played frame` lines arrive without underrun warnings. If underruns appear, keep the device close to the Wi-Fi router or collect the log for troubleshooting.
3. Capture a 30-second clip of microphone input and note whether the pumpkin plays back the OpenAI response; report anomalies along with timestamps from the logs.

> 👶 Written for beginners. Follow each step in order.

## 1. Open the right terminal
1. Launch a fresh terminal on your Mac.
2. If your shell prompt currently shows `(.venv)` from the pumpkin server virtual environment, run `deactivate` first. (The ESP-IDF tools rely on their own Python environment and will complain about missing modules such as `click` if another venv is active.)
3. Activate ESP-IDF for this session:
   ```bash
   source ~/esp/esp-idf/export.sh
   ```
   *If you prefer to leave your `.venv` active, install `click` inside it before running the command: `pip install click==8.1.7`. Either approach prevents the `Cannot import module "esp_idf_monitor"` warning.*
4. Move into the repository:
   ```bash
   cd ~/dev/SenseCAP-Watcher-Firmware
   ```

## 2. Sync with the assistant
1. Make sure you are on the shared branch:
   ```bash
   git switch feature/pumpkin-server
   git pull --rebase origin feature/pumpkin-server
   ```
2. If Git reports local changes, decide whether to stash them (`git stash push`) or discard them (`git reset --hard HEAD` followed by `git clean -fd`). Ask for help if unsure.

## 3. Copy the firmware example
We will clone the existing example so the original stays untouched.

1. From the repo root, run:
   ```bash
   cp -R examples/openai-realtime examples/pumpkin-firmware
   ```
2. Enter the new directory:
   ```bash
   cd examples/pumpkin-firmware
   ```
3. Remove the old build outputs (if any) so we start clean:
   ```bash
   rm -rf build sdkconfig sdkconfig.old
   ```

## 4. Set the correct target and configure
1. Select the SenseCAP Watcher chip (ESP32-S3):
   ```bash
   idf.py set-target esp32s3
   ```
2. Run the configuration menu once so ESP-IDF creates a base `sdkconfig`:
   ```bash
   idf.py menuconfig
   ```
   * Use the arrow keys and press **Save** (usually `S`).
   * Accept the default file name.
   * Exit twice to return to the terminal.

## 5. Prepare for custom code
We will modify the existing C++ sources in upcoming steps. For quick comparison later, make lightweight backups of the files we expect to change most:

```bash
cd ~/dev/SenseCAP-Watcher-Firmware/examples/pumpkin-firmware
cp src/main.cpp src/main.cpp.bak
cp src/ui/ui.c src/ui/ui.c.bak
cp src/wifi.cpp src/wifi.cpp.bak
```

If you want to double-check the path before copying, run `ls src/ui` to see the UI helper files—`ui.c` lives inside that folder.

Leave the originals in place—we will edit `src/main.cpp`, `src/ui/ui.c`, and related helpers directly once the new design is ready.

## 6. Commit the new project skeleton
1. Return to the repository root:
   ```bash
   cd ~/dev/SenseCAP-Watcher-Firmware
   ```
2. See what changed:
   ```bash
   git status
   ```
3. Stage and commit the new example:
   ```bash
   git add examples/pumpkin-firmware
   git commit -m "examples: scaffold pumpkin firmware project"
   git push origin feature/pumpkin-server
   ```
4. Tell the assistant the push succeeded so we can begin editing the firmware files together.

## 7. Next steps (firmware roadmap)
The scaffold is in Git. From here we will iterate through the firmware features in stages so we always have something testable.

### Milestone 1 - Connectivity baseline
1. Review `server/main.py` to document the handshake (`hello` ➜ `config`) and message types (`audio_out`, `state`, `command`, `error`).
2. Add a firmware configuration header (for example `src/pumpkin_config.h`) that captures:
   * Wi-Fi SSID/password for the SenseCAP Watcher development network.
   * Server address, TCP port, and TLS toggle to match the Mac host.
3. Update `src/main.cpp` and `src/wifi.cpp` to:
   * Pull credentials from the new config header.
   * Show explicit UI states for `wifi_connecting`, `wifi_failed`, and `wifi_connected`.
   * Remove unused WebRTC initialization so we do not start the old OpenAI realtime stack.

### Milestone 2 - WebSocket client + handshake
1. Introduce a `pumpkin_client` component (new `src/pumpkin_client.cpp`/`.h`):
   * Wrap the ESP-IDF WebSocket client and manage reconnection backoff.
   * Send the `hello` JSON payload with device metadata (firmware version, board ID placeholder, available capabilities).
   * Receive the `config` payload and stash persona/server timing values for later use.
2. Add a lightweight state dispatcher so other modules (UI, audio) can subscribe to connection changes.
3. Extend the UI helpers to display `connecting`, `configuring`, and `ready` screens once the handshake succeeds.

### Milestone 3 - Audio pipeline (loopback)
1. Reuse the existing I2S capture/playback helpers to stream PCM16 audio frames over the WebSocket (binary frames).
2. Handle `audio_out` messages by queueing the following binary frame for playback; fall back to silence if no frame arrives.
3. Keep the server echo path as-is initially so we can validate timing and latency with a hardware loopback test.
4. Current firmware streams 20 ms PCM16 frames to the server and replays the echoed audio locally to exercise the loopback path.

### Milestone 4 - Command/state protocol
1. Define firmware-side structs/enums for the JSON `state` and `command` messages to keep parsing centralized.
2. Periodically publish device state (battery placeholder, UI mode, audio levels) so the server can make decisions.
3. Execute simple commands from the server (e.g., `sleep`, `wake`, `play_animation`) to prove the control channel works.

### Milestone 5 - Polish and validation
1. Add reconnection logic tied to the persona-specified backoff values.
2. Expose logging toggles via the command shell (`cmd.cpp`) so we can inspect WebSocket events without rebuilding.
3. Build a manual test checklist:
   * Start the Mac server (plain WS first, TLS later).
   * Flash the firmware, confirm Wi-Fi and handshake.
   * Speak into the mic, verify echo audio, and watch UI transitions.

When a milestone is complete, commit the changes and sync with the assistant so we can plan the next slice of work.
