# Pumpkin Firmware Progress

## Latest checkpoints (Oct 27 2025)
- Pumpkin server now speaks English by default and batches >=64 ms of audio before asking OpenAI to reply, avoiding the `input_audio_buffer_commit_empty` errors we saw earlier.
- Firmware manifest now overrides `espressif/esp_websocket_client` to reuse the copy that ships with `examples/openai-realtime` (path: `../../openai-realtime/components/esp-protocols/components/esp_websocket_client`). This keeps the build working even if the registry mirror is unreachable and removes the bad `override_path` warning we hit last night.
- `idf.py reconfigure` reaches the dependency-resolution stage; it still needs the ESP-IDF shell (or `~/.espressif` Python env) plus network access for the remaining ESP components (`esp_io_expander`, `esp_lcd_*`, etc.).

## Immediate to-do list
- Run `source ~/esp/esp-idf/export.sh` (or activate `idf5.2_py3.13_env`) before calling any `idf.py` target so Python resolves `click` from the IDF environment instead of the pumpkin server venv.
- Re-run `idf.py reconfigure`/`idf.py build` from a network-enabled shell so the component manager can download the remaining Espressif components (`esp_io_expander`, `esp_lcd_*`, etc.). The WebSocket client now comes from the local `openai-realtime` checkout, so the build should no longer stop with the `override_path` error even if you are offline.
- Flash the updated firmware and confirm the device still streams 20 ms PCM frames; the server log should now print `Committing N frames (...)` once per 100-120 ms burst.
- Capture a short audio exchange and verify that the OpenAI transcript/voice comes back in English.

## Near-term enhancements
- Firmware: keep an eye on `pumpkin_audio_read` underflows. If underruns persist, raise the RX queue depth beyond the current 24 slots.
- Server: plumb persona wake phrases into a lightweight VAD so we only forward audio when the pumpkin is "awake".
- Tooling: add a smoke test that fakes a watcher session and asserts we stream PCM all the way through `_handle_openai_audio`.

## Reference snippets
- Use the managed component: `espressif/esp_websocket_client` is declared in `examples/pumpkin-firmware/src/idf_component.yml`; no extra `EXTRA_COMPONENT_DIRS` entry is required.
- Realtime request payload (for quick diffing):
  ```json
  {
    "type": "response.create",
    "response": {
      "modalities": ["audio"],
      "instructions": "<persona objective> Respond in English.",
      "audio": {
        "voice": "alloy",
        "format": "pcm16",
        "language": "en"
      }
    }
  }
  ```
