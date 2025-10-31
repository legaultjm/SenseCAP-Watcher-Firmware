#include "main.h"

#include <esp_event.h>
#include <esp_log.h>

#include "nvs_flash.h"
#include "pumpkin_config.h"

#ifndef LINUX_BUILD
#include "pumpkin_client.h"
#endif

extern "C" void board_init(void)
{
  bsp_io_expander_init();
  lv_disp_t *lvgl_disp = bsp_lvgl_init();
  assert(lvgl_disp != NULL);
  bsp_rgb_init();
  bsp_codec_init();
  bsp_codec_volume_set(100, NULL);
}
extern "C"  void long_press_event_cb(void)
{
  ESP_LOGI("", "long_press_event_cb");
  bsp_system_shutdown();
  bsp_lcd_brightness_set(0);
  bsp_codec_mute_set(true);
  vTaskDelay(pdMS_TO_TICKS(3000));
  esp_restart();
}

#ifndef LINUX_BUILD
static void pumpkin_state_callback(PumpkinClientState state, void *) {
  switch (state) {
    case PumpkinClientState::kConnecting:
      ESP_LOGI(LOG_TAG, "Connecting to pumpkin server...");
      ui_server_connecting();
      break;
    case PumpkinClientState::kAwaitingConfig:
      ESP_LOGI(LOG_TAG, "Waiting for pumpkin server config");
      ui_server_waiting_config();
      break;
    case PumpkinClientState::kReady:
      ui_listening();
      ESP_LOGI(LOG_TAG, "Pumpkin server ready");
      ui_server_ready();
      break;
    case PumpkinClientState::kError:
      ESP_LOGW(LOG_TAG, "Pumpkin server unavailable; retrying");
      ui_server_error();
      break;
    case PumpkinClientState::kIdle:
    default:
      break;
  }
}
#endif

extern "C" void app_main(void) {
  esp_err_t ret = nvs_flash_init();
  if (ret == ESP_ERR_NVS_NO_FREE_PAGES ||
      ret == ESP_ERR_NVS_NEW_VERSION_FOUND) {
    ESP_ERROR_CHECK(nvs_flash_erase());
    ret = nvs_flash_init();
  }
  ESP_ERROR_CHECK(ret);

  ESP_ERROR_CHECK(esp_event_loop_create_default());
  
  board_init();
  
  bsp_set_btn_long_press_cb(long_press_event_cb);

  ui_init();
  oai_wifi_init();
  cmd_init();

  ESP_LOGI(LOG_TAG, "Pumpkin server target: %s:%u (TLS=%s)", pumpkin_config::kServerHost,
           static_cast<unsigned>(pumpkin_config::kServerPort),
           pumpkin_config::kServerUseTls ? "yes" : "no");

  if (!oai_wifi()) {
    ESP_LOGE(LOG_TAG, "Wi-Fi connection failed; rebooting in 5 seconds");
    vTaskDelay(pdMS_TO_TICKS(5000));
    esp_restart();
  }

  oai_init_audio_capture();
  pumpkin_audio_play_test_tone();

#ifndef LINUX_BUILD
  ui_server_connecting();
  PumpkinClientConfig client_config{
      .host = pumpkin_config::kServerHost,
      .port = pumpkin_config::kServerPort,
      .use_tls = pumpkin_config::kServerUseTls,
  };
  pumpkin_client_register_state_callback(pumpkin_state_callback, nullptr);
  pumpkin_client_start(client_config);
#else
  ui_server_ready();
#endif

  while (true) {
    vTaskDelay(pdMS_TO_TICKS(1000));
  }
}
