# Step 2: Build the `openai-realtime` Example

Follow these steps from an ESP-IDF-enabled terminal on your Mac to compile the stock firmware. They assume the repository lives at `~/dev/SenseCAP-Watcher-Firmware` and that you have already completed the environment preparation guide.

1. Activate the ESP-IDF environment in the current shell:
   ```bash
   source ~/esp/esp-idf/export.sh
   ```
2. Change to the example directory:
   ```bash
   cd ~/dev/SenseCAP-Watcher-Firmware/examples/openai-realtime
   ```
3. Clear any stale build artifacts and configuration that may still reference the wrong target:
   ```bash
   rm -rf build sdkconfig sdkconfig.old
   ```
4. Select the correct SenseCAP Watcher target (ESP32-S3) and generate a fresh `sdkconfig`:
   ```bash
   idf.py set-target esp32s3
   ```
   You should see CMake configure the project without errors.
5. Optionally review or adjust project settings through the menu system:
   ```bash
   idf.py menuconfig
   ```
   *Choose **Save** and **Exit** when finished.*
6. Build the firmware:
   ```bash
   idf.py build
   ```
   The build is successful when it finishes with `Project build complete.` and lists the generated `.bin` images.

If the build fails, re-run `idf.py build` and capture the final 30–40 lines of output so we can troubleshoot the exact error.

# Step 3: Review the Example Layout

With the project building cleanly, take a tour of the source structure so we know where to modify code in later steps. Run the commands below from the same example directory unless noted otherwise.

1. List the top-level contents to see the major folders:
   ```bash
   ls
   ```
   Key entries:
   * `main/` — application source files.
   * `components/` — project-specific component overrides, if any.
   * `src/` — manifest describing extra dependencies pulled in through IDF's component manager.
   * `CMakeLists.txt` — project build definition.
2. Inspect the `main` directory:
   ```bash
   ls main
   ```
   You should find files such as `app_main.cpp`, `ui.c`, and configuration headers. We will modify these when adding pumpkin-specific logic.
3. Open the primary application source to familiarize yourself with the flow. Use whichever editor you prefer; from the terminal you can preview the first 200 lines with:
   ```bash
   sed -n '1,200p' main/app_main.cpp
   ```
   Take notes on:
   * How Wi-Fi is configured and connected.
   * Where the OpenAI connection is initialized.
   * How audio input/output pipelines are created.
4. Review the UI helper to understand display control:
   ```bash
   sed -n '1,200p' main/ui.c
   ```
   Look for functions that update the eyes and mouth animations—we will reuse them for the pumpkin personality.
5. Check the component manifest to see external dependencies:
   ```bash
   cat src/idf_component.yml
   ```
   This lists libraries such as `sensecap-watcher`, `esp_lvgl_port`, and any audio codecs included via the component manager.
6. (Optional) Generate a dependency tree to visualize linked components:
   ```bash
   idf.py reconfigure
   jq -r '
     if has("component_manager") then
       if (.component_manager | has("components")) then
         .component_manager.components | keys[]
       elif (.component_manager | has("resolved_components")) then
         [.component_manager.resolved_components[].name] | unique | .[]
       else empty
       end
     elif has("components") then
       .components | keys[]
     else empty
     end' build/project_description.json
   ```
   The command above covers every layout we have encountered: older IDF versions expose a top-level `components` map, newer ones tuck it under `component_manager.components`, and the freshest releases store component metadata in the `component_manager.resolved_components` array. Listing the names after `unique` prevents duplicates. Install `jq` with `brew install jq` if it is not already available.

Once you are comfortable with the structure, let me know and we can start designing the architecture changes for the pumpkin experience.

# Step 4: Design the Architecture & Conversation Flow

With the codebase fresh in your mind, capture a concrete design before we start modifying firmware or writing the companion server. This step produces a living document that guides future implementation choices and locks in key requirements such as using OpenAI's latest speech-to-speech API with configurable role/objective prompts.

1. **Create a design notes file**
   * From the repository root, run:
     ```bash
     mkdir -p docs
     cat <<'EOF' > docs/pumpkin_architecture.md
     # SenseCAP Pumpkin Architecture

     ## 1. Objectives
     _Summarize the experience we want for trick-or-treaters (tone, responsiveness, sleep behaviour)._ 

     ## 2. On-Device Responsibilities
     _List everything that must execute on the ESP32-S3: audio capture/playback, display states, Wi-Fi management, etc._

     ## 3. Mac Server Responsibilities
     _Detail the services that will run on the Mac (LLM prompting, TTS generation, session tracking). Outline how the server will broker OpenAI's latest speech-to-speech API, including session lifecycle management and audio streaming._

     ## 4. Communication Protocol
     _Describe how the firmware and server talk: transport (WebSocket/HTTP), message directions, payload fields, retry logic._

     ## 5. Conversation State Machine
     _Outline the states (asleep, greeting, chatting, winding down) and transitions, including LLM cues or timers that trigger them._

     ## 6. Configuration Points
     _List knobs we expose for you to tweak (prompt text, TTS voice, sleep timeout, humour level, etc.) and where they will live in code. Explain how to adjust the LLM role/persona and objective inputs—editing a config file and restarting the server or rebuilding firmware is fine so long as the workflow stays simple._

     ## 7. OpenAI Session Details
     _Capture the parameters required by the latest speech-to-speech API (model name, session role/objective, audio format, safety settings) and how they map to your configuration layer._

     ## 8. Open Questions / Risks
     _Capture anything uncertain that needs prototyping or research._
     EOF
     ```
   * Open `docs/pumpkin_architecture.md` in your editor. We will keep revising it as we make decisions.

2. **Fill in the "Objectives" and responsibility sections**
   * Pull ideas from the Step 1 plan and your own expectations.
   * Reference specific functions from `main/app_main.cpp` and `main/ui.c` when you identify how the current firmware handles similar tasks.
   * Call out that end-to-end audio interactions will flow through OpenAI's latest speech-to-speech API so we bake in the dependency early.

3. **Define the communication protocol outline**
   * Decide whether we’ll start with WebSocket or HTTP requests from the device.
   * List the primary message types (e.g., `audio_chunk`, `llm_response`, `display_state`).
   * Note any sequencing requirements (do we need session IDs, timestamps?).

4. **Sketch the conversation state machine**
   * Use a simple table or bullet list to show state transitions.
   * Include how we detect arrival (initial audio cues), active conversation, and goodbye/silence.

5. **Identify configuration points**
   * Record where Wi-Fi credentials, API keys, prompt strings, and TTS voice selections will reside, emphasising that the secrets are checked into source so the device works without opening a serial console.
   * Detail how you will surface the LLM role/persona and objective text so they can be tweaked by editing a config file and restarting the server (or, if necessary, rebuilding firmware) without any serial setup.
   * Flag any secrets that should remain on the Mac server rather than the device firmware.

6. **Document OpenAI session details**
   * Capture the exact speech-to-speech model/version you intend to call, required session fields (role, objective, voice), and how configuration values flow from storage to the API request payload.
   * Note any constraints around audio codecs, sampling rates, or token limits that the firmware/server hand-off must respect.

7. **List open questions**
   * Anything you are unsure about—latency expectations, how to map OpenAI's speech-to-speech session fields, preferred fallback behaviours—goes here so we can resolve it before coding.

Once you complete the first pass of the design doc, share the highlights (or the whole file) so we can confirm the plan before moving on to implementation.
