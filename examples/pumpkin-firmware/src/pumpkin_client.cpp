#include "pumpkin_client.h"

#include <algorithm>
#include <array>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <deque>
#include <memory>
#include <string>
#include <vector>

extern "C" {
#include "ui.h"
}

#include "main.h"

#include "cJSON.h"
#include "esp_app_desc.h"
#include "esp_event.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "esp_websocket_client.h"
#include "freertos/FreeRTOS.h"
#include "freertos/event_groups.h"
#include "freertos/queue.h"
#include "freertos/task.h"

namespace {
constexpr EventBits_t BIT_CONNECTED = BIT0;
constexpr EventBits_t BIT_DISCONNECTED = BIT1;
constexpr EventBits_t BIT_ERROR = BIT2;
constexpr EventBits_t BIT_STOP_REQUEST = BIT3;
constexpr EventBits_t BIT_CONFIG_RECEIVED = BIT4;

constexpr TickType_t kDefaultDelayMs = 100;
constexpr uint32_t kPlaybackHoldoffMs = 360;
constexpr uint32_t kResumeFailsafeMs = 2000;
constexpr std::array<int, 4> kBackoffSeconds = {2, 4, 8, 16};
constexpr size_t kAudioSamplesPerFrame = 320;  // 20 ms @ 16 kHz
constexpr size_t kAudioQueueDepth = 64;
constexpr int kHelloSendTimeoutMs = 2000;
constexpr int kAudioSendTimeoutMs = 5000;
constexpr size_t kAudioPrefillFrames = 24;

constexpr int kVadSpeechTriggerFrames = 2;
constexpr int kVadSilenceTriggerFrames = 14;
constexpr size_t kVadPrerollFrames = 6;
constexpr uint32_t kVadInitialNoiseFloor = 120;
constexpr uint32_t kVadMinNoiseFloor = 40;
constexpr uint32_t kVadMaxNoiseFloor = 6000;
constexpr uint32_t kVadActivationMargin = 180;
constexpr uint32_t kVadNoiseAdaptShift = 4;  // 1/16 smoothing
constexpr uint32_t kVadActivationRatioNumerator = 4;
constexpr uint32_t kVadActivationRatioDenominator = 3;
constexpr int kVadSilenceTailFrames = 8;
constexpr uint32_t kVadAbsoluteActivation = 420;
constexpr uint32_t kPostResponseHoldMs = 900;
constexpr uint32_t kConversationSleepTimeoutMs = 45000;

struct AudioFrame {
  size_t samples = 0;
  std::array<int16_t, kAudioSamplesPerFrame> data{};
};

enum class ConversationState {
  kSleeping,
  kGreeting,
  kListening,
  kResponding,
  kPostResponse,
};
struct AudioQueueItem {
  std::vector<uint8_t>* payload = nullptr;
};

struct PumpkinClientContext {
  PumpkinClientConfig config{};
  PumpkinRuntimeConfig runtime_config{};
  PumpkinClientState state = PumpkinClientState::kIdle;
  PumpkinClientStateCallback state_callback = nullptr;
  void* state_callback_user_data = nullptr;

  TaskHandle_t task_handle = nullptr;
  TaskHandle_t audio_tx_task = nullptr;
  TaskHandle_t audio_rx_task = nullptr;
  EventGroupHandle_t event_group = nullptr;
  QueueHandle_t audio_queue = nullptr;

  bool running = false;
  bool audio_tasks_running = false;
  std::string connection_url;
  std::string partial_text;
  std::vector<uint8_t> partial_binary;
  esp_websocket_client_handle_t client = nullptr;
  bool client_started = false;
  volatile bool playback_active = false;
  volatile TickType_t playback_release_tick = 0;
  volatile bool capture_paused = false;
  volatile TickType_t capture_resume_tick = 0;
  volatile bool waiting_for_resume = false;
  std::string last_resume_response_id;
  uint32_t reported_sample_rate_hz = 16000;
  uint32_t reported_frame_duration_ms = 0;
  uint32_t audio_rx_underruns = 0;
  bool vad_voice_active = false;
  uint32_t vad_noise_floor = kVadInitialNoiseFloor;
  int vad_speech_run = 0;
  int vad_silence_run = 0;
  std::deque<AudioFrame> vad_preroll;
  ConversationState conversation_state = ConversationState::kSleeping;
  TickType_t post_response_release_tick = 0;
  TickType_t last_user_speech_tick = 0;
  TickType_t idle_since_tick = 0;
  bool expecting_greeting = true;
  bool conversation_active = false;
};

PumpkinClientContext g_ctx;

const char* TAG = "pumpkin_client";

TickType_t MsToTicksCeil(uint32_t ms) {
  if (ms == 0) {
    return 1;
  }
  TickType_t ticks = pdMS_TO_TICKS(ms);
  return ticks == 0 ? 1 : ticks;
}

bool DeadlineExpired(TickType_t deadline, TickType_t now) {
  if (deadline == 0) {
    return true;
  }
  return static_cast<int32_t>(deadline - now) <= 0;
}

void DispatchState(PumpkinClientState new_state);
void StartAudioTasks();
void StopAudioTasks();
void FlushAudioQueue();
void ClearVadPreroll();
void ResetVadState();
bool ProcessVadFrame(const AudioFrame& frame, bool* out_start, bool* out_end, uint32_t* out_avg_abs);
void PushVadPreroll(const AudioFrame& frame);
void SetConversationState(ConversationState new_state);

std::string BuildUrl(const PumpkinClientConfig& config) {
  std::string url = config.use_tls ? "wss://" : "ws://";
  if (config.host && std::strlen(config.host) > 0) {
    url.append(config.host);
  } else {
    url.append("127.0.0.1");
  }
  if (config.port != 0) {
    url.push_back(':');
    url.append(std::to_string(config.port));
  }
  url.push_back('/');
  return url;
}

std::string BuildHelloPayload() {
  const esp_app_desc_t* app_desc = esp_app_get_description();
  const char* version =
      (app_desc && std::strlen(app_desc->version) > 0) ? app_desc->version : "unknown";
  char buffer[256];
  std::snprintf(
      buffer, sizeof(buffer),
      R"({"type":"hello","device":{"model":"sensecap-watcher","firmware":"pumpkin-firmware","version":"%s"},"capabilities":["audio_in","audio_out","display"]})",
      version);
  return std::string(buffer);
}

void FlushAudioQueue() {
  if (!g_ctx.audio_queue) {
    return;
  }
  AudioQueueItem item;
  while (xQueueReceive(g_ctx.audio_queue, &item, 0) == pdTRUE) {
    delete item.payload;
  }
}

const char* ConversationStateName(ConversationState state) {
  switch (state) {
    case ConversationState::kSleeping:
      return "sleeping";
    case ConversationState::kGreeting:
      return "greeting";
    case ConversationState::kListening:
      return "listening";
    case ConversationState::kResponding:
      return "responding";
    case ConversationState::kPostResponse:
      return "post_response";
    default:
      return "unknown";
  }
}

void ClearVadPreroll() {
  g_ctx.vad_preroll.clear();
}

void ResetVadState() {
  g_ctx.vad_voice_active = false;
  g_ctx.vad_noise_floor = kVadInitialNoiseFloor;
  g_ctx.vad_speech_run = 0;
  g_ctx.vad_silence_run = 0;
  ClearVadPreroll();
}

void PushVadPreroll(const AudioFrame& frame) {
  if (!frame.samples) {
    return;
  }
  if (g_ctx.vad_preroll.size() >= kVadPrerollFrames) {
    g_ctx.vad_preroll.pop_front();
  }
  g_ctx.vad_preroll.push_back(frame);
}

bool ProcessVadFrame(const AudioFrame& frame, bool* out_start, bool* out_end, uint32_t* out_avg_abs) {
  bool start = false;
  bool end = false;
  const bool was_active = g_ctx.vad_voice_active;
  uint64_t sum_abs = 0;
  for (size_t i = 0; i < frame.samples; ++i) {
    sum_abs += static_cast<uint32_t>(std::abs(frame.data[i]));
  }
  const uint32_t avg_abs = frame.samples > 0 ? static_cast<uint32_t>(sum_abs / frame.samples) : 0;
  if (out_avg_abs) {
    *out_avg_abs = avg_abs;
  }

  if (!was_active) {
    const uint32_t clamped = std::min(std::max(avg_abs, kVadMinNoiseFloor), kVadMaxNoiseFloor);
    const uint32_t decay = g_ctx.vad_noise_floor - (g_ctx.vad_noise_floor >> kVadNoiseAdaptShift);
    const uint32_t growth = clamped >> kVadNoiseAdaptShift;
    g_ctx.vad_noise_floor = std::min(std::max(decay + growth, kVadMinNoiseFloor), kVadMaxNoiseFloor);
  }

  const uint32_t threshold_linear = g_ctx.vad_noise_floor + kVadActivationMargin;
  const bool loud_linear = avg_abs >= threshold_linear;
  const bool loud_ratio =
      static_cast<uint64_t>(avg_abs) * kVadActivationRatioDenominator >=
      static_cast<uint64_t>(g_ctx.vad_noise_floor) * kVadActivationRatioNumerator;
  uint32_t absolute_threshold = kVadAbsoluteActivation;
  if (g_ctx.expecting_greeting) {
    absolute_threshold += 80;
  }
  const bool loud = (avg_abs >= absolute_threshold) || loud_linear || loud_ratio;

  if (loud) {
    g_ctx.vad_speech_run = std::min(g_ctx.vad_speech_run + 1, 255);
    g_ctx.vad_silence_run = 0;
  } else {
    g_ctx.vad_silence_run = std::min(g_ctx.vad_silence_run + 1, 255);
    g_ctx.vad_speech_run = 0;
  }

  if (!was_active && loud && g_ctx.vad_speech_run >= kVadSpeechTriggerFrames) {
    start = true;
    g_ctx.vad_voice_active = true;
    g_ctx.vad_silence_run = 0;
  }

  if (g_ctx.vad_voice_active && !loud && g_ctx.vad_silence_run >= kVadSilenceTriggerFrames) {
    end = true;
    g_ctx.vad_voice_active = false;
    g_ctx.vad_speech_run = 0;
  }

  if (out_start) {
    *out_start = start;
  }
  if (out_end) {
    *out_end = end;
  }
  return g_ctx.vad_voice_active || start;
}

void SetConversationState(ConversationState new_state) {
  if (g_ctx.conversation_state == new_state) {
    return;
  }
  g_ctx.conversation_state = new_state;
  const TickType_t now = xTaskGetTickCount();
  g_ctx.idle_since_tick = now;
  ESP_LOGI(TAG, "Conversation state -> %s", ConversationStateName(new_state));
  switch (new_state) {
    case ConversationState::kSleeping:
      g_ctx.expecting_greeting = true;
      g_ctx.conversation_active = false;
      g_ctx.post_response_release_tick = 0;
      g_ctx.last_user_speech_tick = 0;
      ResetVadState();
      break;
    case ConversationState::kGreeting:
      g_ctx.expecting_greeting = false;
      g_ctx.conversation_active = true;
      g_ctx.post_response_release_tick = 0;
      ui_switch_speaking();
      break;
    case ConversationState::kListening:
      g_ctx.conversation_active = true;
      g_ctx.post_response_release_tick = 0;
      g_ctx.last_user_speech_tick = now;
      ui_listening();
      break;
    case ConversationState::kResponding:
      g_ctx.conversation_active = true;
      g_ctx.post_response_release_tick = 0;
      ui_switch_speaking();
      break;
    case ConversationState::kPostResponse:
      g_ctx.conversation_active = true;
      break;
  }
}

void DispatchState(PumpkinClientState new_state) {
  if (g_ctx.state == new_state) {
    return;
  }
  g_ctx.state = new_state;
  if (g_ctx.state_callback) {
    g_ctx.state_callback(new_state, g_ctx.state_callback_user_data);
  }
}

void HandleCommand(const cJSON* root) {
  const cJSON* name = cJSON_GetObjectItemCaseSensitive(root, "name");
  if (!cJSON_IsString(name) || name->valuestring == nullptr) {
    ESP_LOGW(TAG, "Command payload missing name");
    return;
  }
  const char* name_str = name->valuestring;
  if (std::strcmp(name_str, "pause_mic") == 0) {
    uint32_t resume_ms = 0;
    const cJSON* payload = cJSON_GetObjectItemCaseSensitive(root, "payload");
    if (payload && cJSON_IsObject(payload)) {
      const cJSON* duration = cJSON_GetObjectItemCaseSensitive(payload, "resume_after_ms");
      if (cJSON_IsNumber(duration) && duration->valuedouble > 0) {
        resume_ms = static_cast<uint32_t>(duration->valuedouble);
      }
    }
    ESP_LOGI(TAG, "Capture pause requested (resume_ms=%u)", static_cast<unsigned>(resume_ms));
    g_ctx.capture_paused = true;
    g_ctx.waiting_for_resume = true;
    const TickType_t now_ticks = xTaskGetTickCount();
    uint32_t guard_ms = resume_ms + kPlaybackHoldoffMs;
    guard_ms = std::max(guard_ms, kResumeFailsafeMs);
    g_ctx.capture_resume_tick = now_ticks + MsToTicksCeil(guard_ms);
    g_ctx.last_resume_response_id.clear();
    FlushAudioQueue();
    ResetVadState();
    if (g_ctx.expecting_greeting) {
      SetConversationState(ConversationState::kGreeting);
    } else {
      SetConversationState(ConversationState::kResponding);
    }
    ESP_LOGI(TAG, "Capture paused by server command (resume_ms=%u)",
             static_cast<unsigned>(resume_ms));
    return;
  }
  if (std::strcmp(name_str, "resume_mic") == 0) {
    std::string response_id;
    const cJSON* payload = cJSON_GetObjectItemCaseSensitive(root, "payload");
    if (payload && cJSON_IsObject(payload)) {
      const cJSON* response = cJSON_GetObjectItemCaseSensitive(payload, "response_id");
      if (cJSON_IsString(response) && response->valuestring) {
        response_id.assign(response->valuestring);
      }
    }
    g_ctx.capture_paused = false;
    g_ctx.capture_resume_tick = 0;
    g_ctx.waiting_for_resume = false;
    ResetVadState();
    g_ctx.post_response_release_tick =
        xTaskGetTickCount() + MsToTicksCeil(kPostResponseHoldMs);
    g_ctx.idle_since_tick = xTaskGetTickCount();
    SetConversationState(ConversationState::kPostResponse);
    if (!response_id.empty()) {
      g_ctx.last_resume_response_id = response_id;
      ESP_LOGI(TAG, "Capture resumed by server command (response_id=%s)", response_id.c_str());
    } else {
      g_ctx.last_resume_response_id.clear();
      ESP_LOGI(TAG, "Capture resumed by server command");
    }
    return;
  }
  ESP_LOGW(TAG, "Unhandled command: %s", name_str);
}

void HandleTextFrame(const esp_websocket_event_data_t& event) {
  if (!event.data_ptr) {
    return;
  }

  if (event.payload_offset == 0) {
    g_ctx.partial_text.assign(event.data_ptr, event.data_len);
  } else {
    g_ctx.partial_text.append(event.data_ptr, event.data_len);
  }

  const size_t accumulated = event.payload_offset + event.data_len;
  if (accumulated < static_cast<size_t>(event.payload_len)) {
    return;
  }

  cJSON* root = cJSON_Parse(g_ctx.partial_text.c_str());
  if (!root) {
    ESP_LOGW(TAG, "Failed to parse JSON frame");
    g_ctx.partial_text.clear();
    return;
  }

  const cJSON* type = cJSON_GetObjectItemCaseSensitive(root, "type");
  if (cJSON_IsString(type) && type->valuestring) {
    const char* type_str = type->valuestring;
    if (std::strcmp(type_str, "config") == 0) {
      g_ctx.runtime_config.raw_json = g_ctx.partial_text;
      ESP_LOGI(TAG, "Config payload received (%zu bytes)", g_ctx.partial_text.size());
      xEventGroupSetBits(g_ctx.event_group, BIT_CONFIG_RECEIVED);
    } else if (std::strcmp(type_str, "command") == 0) {
      HandleCommand(root);
    } else {
      ESP_LOGD(TAG, "Unhandled JSON type: %s", type_str);
    }
  }

  cJSON_Delete(root);
  g_ctx.partial_text.clear();
}

void HandleBinaryFrame(const esp_websocket_event_data_t& event) {
  const auto* bytes = reinterpret_cast<const uint8_t*>(event.data_ptr);
  if (!bytes || event.data_len == 0) {
    return;
  }

  if (event.payload_offset == 0) {
    g_ctx.partial_binary.assign(bytes, bytes + event.data_len);
  } else {
    g_ctx.partial_binary.insert(g_ctx.partial_binary.end(), bytes, bytes + event.data_len);
  }

  const size_t accumulated = event.payload_offset + event.data_len;
  if (accumulated < static_cast<size_t>(event.payload_len)) {
    return;
  }

  if (!g_ctx.audio_queue) {
    g_ctx.partial_binary.clear();
    return;
  }

  auto* payload = new std::vector<uint8_t>(g_ctx.partial_binary.begin(), g_ctx.partial_binary.end());
  AudioQueueItem item{payload};
  if (xQueueSend(g_ctx.audio_queue, &item, pdMS_TO_TICKS(10)) != pdTRUE) {
    delete payload;
  }
  g_ctx.partial_binary.clear();
}

void WebsocketEventHandler(void*, esp_event_base_t, int32_t event_id, void* event_data) {
  auto* data = static_cast<esp_websocket_event_data_t*>(event_data);

  switch (event_id) {
    case WEBSOCKET_EVENT_CONNECTED:
      xEventGroupSetBits(g_ctx.event_group, BIT_CONNECTED);
      break;
    case WEBSOCKET_EVENT_DATA:
      if (!data) {
        break;
      }
      if (data->op_code == 0x1) {
        HandleTextFrame(*data);
      } else if (data->op_code == 0x2) {
        HandleBinaryFrame(*data);
      }
      break;
    case WEBSOCKET_EVENT_DISCONNECTED:
      xEventGroupSetBits(g_ctx.event_group, BIT_DISCONNECTED);
      break;
    case WEBSOCKET_EVENT_ERROR:
      xEventGroupSetBits(g_ctx.event_group, BIT_ERROR);
      break;
    default:
      break;
  }
}

void PumpkinAudioTxTask(void*) {
  uint32_t zero_reads = 0;
  uint32_t frame_count = 0;

  ResetVadState();

  const auto send_frame = [&](const AudioFrame& frame) -> bool {
    if (frame.samples == 0) {
      return true;
    }
    const size_t bytes = frame.samples * sizeof(int16_t);
    int sent = esp_websocket_client_send_bin(
        g_ctx.client, reinterpret_cast<const char*>(frame.data.data()), bytes, kAudioSendTimeoutMs);
    if (sent <= 0) {
      ESP_LOGW(TAG, "Audio TX send failed (%d)", sent);
      return false;
    }
    if (sent != static_cast<int>(bytes)) {
      ESP_LOGW(TAG, "Audio TX partial send: %d of %zu bytes", sent, bytes);
      return false;
    }
    frame_count++;
    if ((frame_count % 50) == 0) {
      ESP_LOGI(TAG, "Audio TX sent frame #%u (%d bytes)", frame_count, sent);
    }
    return true;
  };

  const TickType_t sleep_timeout_ticks = MsToTicksCeil(kConversationSleepTimeoutMs);

  while (g_ctx.running && g_ctx.audio_tasks_running) {
    TickType_t now_ticks = xTaskGetTickCount();
    if (g_ctx.state != PumpkinClientState::kReady || g_ctx.client == nullptr ||
        !esp_websocket_client_is_connected(g_ctx.client)) {
      vTaskDelay(pdMS_TO_TICKS(20));
      continue;
    }

    if (g_ctx.capture_paused) {
      ResetVadState();
      vTaskDelay(pdMS_TO_TICKS(10));
      continue;
    }

    bool hold_active = false;
    if (g_ctx.conversation_state == ConversationState::kPostResponse) {
      if (DeadlineExpired(g_ctx.post_response_release_tick, now_ticks) || g_ctx.capture_paused) {
        g_ctx.post_response_release_tick = 0;
        if (!g_ctx.capture_paused) {
          SetConversationState(ConversationState::kListening);
          now_ticks = xTaskGetTickCount();
        }
      } else {
        hold_active = true;
      }
    }

    if (g_ctx.conversation_state == ConversationState::kListening && g_ctx.conversation_active &&
        !g_ctx.vad_voice_active && g_ctx.last_user_speech_tick != 0 &&
        DeadlineExpired(g_ctx.last_user_speech_tick + sleep_timeout_ticks, now_ticks)) {
      SetConversationState(ConversationState::kSleeping);
      vTaskDelay(pdMS_TO_TICKS(20));
      continue;
    }

    if (g_ctx.playback_active) {
      if (static_cast<int32_t>(g_ctx.playback_release_tick - now_ticks) > 0) {
        vTaskDelay(pdMS_TO_TICKS(10));
        continue;
      }
      g_ctx.playback_active = false;
    }

    AudioFrame frame{};
    frame.samples = pumpkin_audio_read(frame.data.data(), kAudioSamplesPerFrame);
    if (frame.samples == 0) {
      if (++zero_reads % 100 == 0) {
        ESP_LOGW(TAG, "Audio TX read returned 0 samples (x%u)", zero_reads);
      }
      vTaskDelay(pdMS_TO_TICKS(5));
      continue;
    }
    zero_reads = 0;

    bool start_of_speech = false;
   bool end_of_speech = false;
   uint32_t avg_abs = 0;
   const bool active_for_frame = ProcessVadFrame(frame, &start_of_speech, &end_of_speech, &avg_abs);
    TickType_t activity_tick = xTaskGetTickCount();

    if (start_of_speech && g_ctx.conversation_state != ConversationState::kListening) {
      g_ctx.post_response_release_tick = 0;
      SetConversationState(ConversationState::kListening);
      hold_active = false;
    } else if (hold_active && active_for_frame) {
      g_ctx.post_response_release_tick = 0;
      SetConversationState(ConversationState::kListening);
      hold_active = false;
    }

    if (active_for_frame) {
      g_ctx.last_user_speech_tick = activity_tick;
    }

    if (hold_active) {
      PushVadPreroll(frame);
      continue;
    }

    if (!active_for_frame && !start_of_speech) {
      PushVadPreroll(frame);
      continue;
    }

    if (start_of_speech) {
      ESP_LOGD(TAG, "VAD: speech detected (avg_abs=%u)", static_cast<unsigned>(avg_abs));
      for (const auto& buffered : g_ctx.vad_preroll) {
        if (!send_frame(buffered)) {
          break;
        }
      }
      ClearVadPreroll();
    }

    if (!send_frame(frame)) {
      vTaskDelay(pdMS_TO_TICKS(10));
      continue;
    }

    if (end_of_speech) {
      ESP_LOGD(TAG, "VAD: speech ended");
      g_ctx.last_user_speech_tick = activity_tick;
      if (!g_ctx.capture_paused && g_ctx.conversation_active) {
        g_ctx.post_response_release_tick =
            activity_tick + MsToTicksCeil(kPostResponseHoldMs);
        SetConversationState(ConversationState::kPostResponse);
      }
      AudioFrame silence{};
      silence.samples = frame.samples;
      silence.data.fill(0);
      for (int i = 0; i < kVadSilenceTailFrames; ++i) {
        if (!send_frame(silence)) {
          break;
        }
      }
    }
  }

  g_ctx.audio_tx_task = nullptr;
  vTaskDelete(nullptr);
}

void PumpkinAudioRxTask(void*) {
  uint32_t frame_count = 0;
  bool prefill_complete = false;

  while (g_ctx.running && g_ctx.audio_tasks_running) {
    if (!prefill_complete) {
      if (uxQueueMessagesWaiting(g_ctx.audio_queue) < kAudioPrefillFrames) {
        vTaskDelay(pdMS_TO_TICKS(5));
        continue;
      }
      prefill_complete = true;
    }

    AudioQueueItem item;
    if (xQueueReceive(g_ctx.audio_queue, &item, pdMS_TO_TICKS(40)) != pdTRUE) {
      if (prefill_complete) {
        g_ctx.audio_rx_underruns++;
        if (g_ctx.audio_rx_underruns < 5 || (g_ctx.audio_rx_underruns % 25) == 0) {
          ESP_LOGW(TAG, "Audio RX underrun detected (count=%u)", g_ctx.audio_rx_underruns);
        }
      }
      prefill_complete = false;
      continue;
    }

    std::unique_ptr<std::vector<uint8_t>> payload(item.payload);
    if (!payload || payload->empty()) {
      prefill_complete = false;
      continue;
    }

    const size_t samples = payload->size() / sizeof(int16_t);
    if (samples == 0) {
      prefill_complete = false;
      continue;
    }

    pumpkin_audio_write(reinterpret_cast<const int16_t*>(payload->data()), samples);
    if (g_ctx.reported_sample_rate_hz == 0) {
      g_ctx.reported_sample_rate_hz = 16000;
    }
    const uint32_t frame_ms =
        static_cast<uint32_t>((samples * 1000) / g_ctx.reported_sample_rate_hz);
    const TickType_t extend_ticks =
        MsToTicksCeil(frame_ms) + MsToTicksCeil(kPlaybackHoldoffMs);
    const TickType_t candidate_release = xTaskGetTickCount() + extend_ticks;
    if (!g_ctx.playback_active ||
        static_cast<int32_t>(candidate_release - g_ctx.playback_release_tick) > 0) {
      g_ctx.playback_release_tick = candidate_release;
    }
    if (g_ctx.capture_paused && g_ctx.waiting_for_resume && g_ctx.capture_resume_tick != 0 &&
        static_cast<int32_t>(candidate_release - g_ctx.capture_resume_tick) > 0) {
      g_ctx.capture_resume_tick = candidate_release;
    }
    g_ctx.playback_active = true;
    ui_switch_speaking();
    frame_count++;
    if ((frame_count % 50) == 0) {
      ESP_LOGI(TAG, "Audio RX played frame #%u (%zu bytes)", frame_count, payload->size());
    }
  }

  g_ctx.audio_rx_task = nullptr;
  vTaskDelete(nullptr);
}

void StartAudioTasks() {
  if (g_ctx.audio_tx_task || g_ctx.audio_rx_task) {
    return;
  }

  if (!g_ctx.audio_queue) {
    g_ctx.audio_queue = xQueueCreate(kAudioQueueDepth, sizeof(AudioQueueItem));
    if (!g_ctx.audio_queue) {
      ESP_LOGE(TAG, "Failed to allocate audio queue");
      return;
    }
  }

  g_ctx.audio_tasks_running = true;
  g_ctx.audio_rx_underruns = 0;
  g_ctx.playback_active = false;
  g_ctx.playback_release_tick = 0;
  g_ctx.capture_paused = false;
  g_ctx.capture_resume_tick = 0;
  g_ctx.waiting_for_resume = false;
  g_ctx.last_resume_response_id.clear();
  ResetVadState();
  g_ctx.post_response_release_tick = 0;
  g_ctx.last_user_speech_tick = 0;
  g_ctx.idle_since_tick = xTaskGetTickCount();
  g_ctx.expecting_greeting = true;
  g_ctx.conversation_active = false;
  SetConversationState(ConversationState::kSleeping);
  ESP_LOGI(TAG, "Starting audio TX/RX tasks");

  if (xTaskCreatePinnedToCore(PumpkinAudioTxTask, "pumpkin_audio_tx", 4096, nullptr, 5,
                              &g_ctx.audio_tx_task, tskNO_AFFINITY) != pdPASS) {
    ESP_LOGE(TAG, "Failed to create audio TX task");
    g_ctx.audio_tasks_running = false;
    g_ctx.audio_tx_task = nullptr;
    return;
  }

  if (xTaskCreatePinnedToCore(PumpkinAudioRxTask, "pumpkin_audio_rx", 4096, nullptr, 5,
                              &g_ctx.audio_rx_task, tskNO_AFFINITY) != pdPASS) {
    ESP_LOGE(TAG, "Failed to create audio RX task");
    g_ctx.audio_tasks_running = false;
    g_ctx.audio_rx_task = nullptr;
    vTaskDelete(g_ctx.audio_tx_task);
    g_ctx.audio_tx_task = nullptr;
    vQueueDelete(g_ctx.audio_queue);
    g_ctx.audio_queue = nullptr;
    return;
  }
}

void StopAudioTasks() {
  g_ctx.audio_tasks_running = false;
  if (g_ctx.audio_queue) {
    AudioQueueItem item{};
    xQueueSend(g_ctx.audio_queue, &item, 0);
    xQueueSend(g_ctx.audio_queue, &item, 0);
  }

  while (g_ctx.audio_tx_task || g_ctx.audio_rx_task) {
    vTaskDelay(pdMS_TO_TICKS(20));
  }

  FlushAudioQueue();
  if (g_ctx.audio_queue) {
    vQueueDelete(g_ctx.audio_queue);
    g_ctx.audio_queue = nullptr;
  }
  g_ctx.playback_active = false;
  g_ctx.playback_release_tick = 0;
  g_ctx.capture_paused = false;
  g_ctx.capture_resume_tick = 0;
  g_ctx.waiting_for_resume = false;
  g_ctx.last_resume_response_id.clear();
  SetConversationState(ConversationState::kSleeping);
  ESP_LOGI(TAG, "Audio tasks stopped");
}

void CleanupClient() {
  if (!g_ctx.client) {
    return;
  }

  esp_websocket_client_handle_t client = g_ctx.client;
  g_ctx.client = nullptr;
  g_ctx.client_started = false;

  if (esp_websocket_client_is_connected(client)) {
    esp_websocket_client_close(client, pdMS_TO_TICKS(1000));
  }
  esp_websocket_client_stop(client);
  esp_websocket_client_destroy(client);
}

bool AwaitConfigMessage(TickType_t timeout_ticks) {
  if (!g_ctx.event_group) {
    return false;
  }
  EventBits_t bits = xEventGroupWaitBits(
      g_ctx.event_group, BIT_CONFIG_RECEIVED | BIT_DISCONNECTED | BIT_ERROR | BIT_STOP_REQUEST,
      pdTRUE, pdFALSE, timeout_ticks);
  if (bits & BIT_CONFIG_RECEIVED) {
    return true;
  }
  return false;
}

TickType_t BackoffDelayTicks(size_t index) {
  const size_t clamped = std::min(index, kBackoffSeconds.size() - 1);
  return pdMS_TO_TICKS(kBackoffSeconds[clamped] * 1000);
}

void PumpkinClientTask(void*) {
  size_t backoff_index = 0;
  const std::string hello_payload = BuildHelloPayload();

  while (g_ctx.running) {
    DispatchState(PumpkinClientState::kConnecting);
    g_ctx.partial_text.clear();
    g_ctx.partial_binary.clear();
    FlushAudioQueue();
    if (g_ctx.event_group) {
      xEventGroupClearBits(g_ctx.event_group, BIT_CONNECTED | BIT_DISCONNECTED | BIT_ERROR |
                                               BIT_STOP_REQUEST | BIT_CONFIG_RECEIVED);
    }

    esp_websocket_client_config_t ws_cfg = {};
    ws_cfg.uri = g_ctx.connection_url.c_str();
    ws_cfg.disable_auto_reconnect = true;
    ws_cfg.network_timeout_ms = 5000;
    ws_cfg.reconnect_timeout_ms = 5000;
    ws_cfg.ping_interval_sec = 20;

    g_ctx.client = esp_websocket_client_init(&ws_cfg);
    if (!g_ctx.client) {
      ESP_LOGE(TAG, "Failed to initialise websocket client");
      DispatchState(PumpkinClientState::kError);
      vTaskDelay(BackoffDelayTicks(backoff_index));
      backoff_index = std::min(backoff_index + 1, kBackoffSeconds.size() - 1);
      continue;
    }

    esp_websocket_register_events(g_ctx.client, WEBSOCKET_EVENT_ANY, WebsocketEventHandler,
                                  nullptr);

    if (esp_websocket_client_start(g_ctx.client) != ESP_OK) {
      ESP_LOGE(TAG, "Failed to start websocket client");
      CleanupClient();
      DispatchState(PumpkinClientState::kError);
      vTaskDelay(BackoffDelayTicks(backoff_index));
      backoff_index = std::min(backoff_index + 1, kBackoffSeconds.size() - 1);
      continue;
    }

    g_ctx.client_started = true;

    EventBits_t bits = xEventGroupWaitBits(g_ctx.event_group,
                                           BIT_CONNECTED | BIT_DISCONNECTED | BIT_ERROR |
                                               BIT_STOP_REQUEST,
                                           pdTRUE, pdFALSE, pdMS_TO_TICKS(8000));
    if (!(bits & BIT_CONNECTED)) {
      ESP_LOGW(TAG, "Failed to connect to websocket endpoint");
      CleanupClient();
      DispatchState(PumpkinClientState::kError);
      vTaskDelay(BackoffDelayTicks(backoff_index));
      backoff_index = std::min(backoff_index + 1, kBackoffSeconds.size() - 1);
      continue;
    }

    DispatchState(PumpkinClientState::kAwaitingConfig);

    if (esp_websocket_client_send_text(g_ctx.client, hello_payload.c_str(),
                                       hello_payload.length(), kHelloSendTimeoutMs) <= 0) {
      ESP_LOGE(TAG, "Failed to send hello handshake");
      CleanupClient();
      DispatchState(PumpkinClientState::kError);
      vTaskDelay(BackoffDelayTicks(backoff_index));
      backoff_index = std::min(backoff_index + 1, kBackoffSeconds.size() - 1);
      continue;
    }

    if (!AwaitConfigMessage(pdMS_TO_TICKS(8000))) {
      ESP_LOGW(TAG, "Timed out waiting for config payload");
      CleanupClient();
      DispatchState(PumpkinClientState::kError);
      vTaskDelay(BackoffDelayTicks(backoff_index));
      backoff_index = std::min(backoff_index + 1, kBackoffSeconds.size() - 1);
      continue;
    }

    DispatchState(PumpkinClientState::kReady);
    StartAudioTasks();
    backoff_index = 0;

    while (g_ctx.running && esp_websocket_client_is_connected(g_ctx.client) &&
           g_ctx.state == PumpkinClientState::kReady) {
      EventBits_t loop_bits = xEventGroupWaitBits(
          g_ctx.event_group, BIT_DISCONNECTED | BIT_ERROR | BIT_STOP_REQUEST, pdTRUE, pdFALSE,
          pdMS_TO_TICKS(200));
      if (loop_bits & (BIT_DISCONNECTED | BIT_ERROR)) {
        ESP_LOGW(TAG, "Websocket connection lost");
        break;
      }
      if (loop_bits & BIT_STOP_REQUEST) {
        break;
      }

      if (g_ctx.capture_paused && g_ctx.waiting_for_resume && g_ctx.capture_resume_tick != 0) {
        const TickType_t now = xTaskGetTickCount();
        if (static_cast<int32_t>(g_ctx.capture_resume_tick - now) <= 0) {
          ESP_LOGW(TAG, "Auto-resuming microphone after resume failsafe");
          g_ctx.capture_paused = false;
          g_ctx.capture_resume_tick = 0;
          g_ctx.waiting_for_resume = false;
          g_ctx.last_resume_response_id.clear();
        }
      }
    }

    StopAudioTasks();
    CleanupClient();
    DispatchState(PumpkinClientState::kError);
    vTaskDelay(BackoffDelayTicks(backoff_index));
    backoff_index = std::min(backoff_index + 1, kBackoffSeconds.size() - 1);
  }

  StopAudioTasks();
  CleanupClient();
  DispatchState(PumpkinClientState::kIdle);
  g_ctx.task_handle = nullptr;
  vTaskDelete(nullptr);
}
}  // namespace

void pumpkin_client_register_state_callback(PumpkinClientStateCallback callback,
                                            void* user_data) {
  g_ctx.state_callback = callback;
  g_ctx.state_callback_user_data = user_data;
}

void pumpkin_client_start(const PumpkinClientConfig& config) {
  if (g_ctx.running) {
    ESP_LOGW(TAG, "pumpkin_client_start called while already running");
    return;
  }

  g_ctx.config = config;
  g_ctx.connection_url = BuildUrl(config);

  if (!g_ctx.event_group) {
    g_ctx.event_group = xEventGroupCreate();
    if (!g_ctx.event_group) {
      ESP_LOGE(TAG, "Failed to create event group");
      return;
    }
  }

  g_ctx.running = true;
  DispatchState(PumpkinClientState::kIdle);

  if (xTaskCreatePinnedToCore(PumpkinClientTask, "pumpkin_client", 6144, nullptr, 5,
                              &g_ctx.task_handle, tskNO_AFFINITY) != pdPASS) {
    ESP_LOGE(TAG, "Failed to create pumpkin client task");
    g_ctx.running = false;
    g_ctx.task_handle = nullptr;
    return;
  }
}

void pumpkin_client_stop() {
  if (!g_ctx.running) {
    return;
  }

  g_ctx.running = false;
  if (g_ctx.event_group) {
    xEventGroupSetBits(g_ctx.event_group, BIT_STOP_REQUEST);
  }

  if (g_ctx.task_handle) {
    while (g_ctx.task_handle) {
      vTaskDelay(pdMS_TO_TICKS(50));
    }
  }

  StopAudioTasks();
  CleanupClient();
  if (g_ctx.event_group) {
    vEventGroupDelete(g_ctx.event_group);
    g_ctx.event_group = nullptr;
  }
  g_ctx.state = PumpkinClientState::kIdle;
}

bool pumpkin_client_ready() {
  return g_ctx.state == PumpkinClientState::kReady;
}

const PumpkinRuntimeConfig* pumpkin_client_get_runtime_config() {
  return &g_ctx.runtime_config;
}

PumpkinClientState pumpkin_client_get_state() {
  return g_ctx.state;
}

const char* pumpkin_client_state_string(PumpkinClientState state) {
  switch (state) {
    case PumpkinClientState::kIdle:
      return "idle";
    case PumpkinClientState::kConnecting:
      return "connecting";
    case PumpkinClientState::kAwaitingConfig:
      return "awaiting_config";
    case PumpkinClientState::kReady:
      return "ready";
    case PumpkinClientState::kError:
      return "error";
    default:
      return "unknown";
  }
}
