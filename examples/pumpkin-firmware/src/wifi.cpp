#include <assert.h>
#include <esp_event.h>
#include <esp_log.h>
#include <esp_wifi.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "main.h"
#include "pumpkin_config.h"

static bool g_wifi_connected = false;
static bool g_wifi_failed = false;

static bool wifi_credentials_configured(void) {
  const bool ssid_configured = strlen(pumpkin_config::kWifiSsid) != 0 &&
                              strcmp(pumpkin_config::kWifiSsid, "REPLACE_WITH_WIFI_SSID") != 0;
  const bool password_configured =
      strcmp(pumpkin_config::kWifiPassword, "REPLACE_WITH_WIFI_PASSWORD") != 0;
  return ssid_configured && password_configured;
}

static void oai_event_handler(void *arg, esp_event_base_t event_base,
                              int32_t event_id, void *event_data) {
  static int s_retry_num = 0;
  if (event_base == WIFI_EVENT && event_id == WIFI_EVENT_STA_START) {
    ESP_ERROR_CHECK(esp_wifi_connect());
  } else if (event_base == WIFI_EVENT && event_id == WIFI_EVENT_STA_DISCONNECTED) {
    if (s_retry_num < pumpkin_config::kWifiMaxRetryCount) {
      s_retry_num++;
      ESP_LOGW(LOG_TAG, "Wi-Fi disconnected, retry %d/%d",
               s_retry_num, pumpkin_config::kWifiMaxRetryCount);
      ESP_ERROR_CHECK(esp_wifi_connect());
      ui_wifi_connecting();
    } else {
      ESP_LOGE(LOG_TAG, "Wi-Fi connection failed after %d retries",
               pumpkin_config::kWifiMaxRetryCount);
      g_wifi_failed = true;
      ui_wifi_failed();
    }
  } else if (event_base == IP_EVENT && event_id == IP_EVENT_STA_GOT_IP) {
    ip_event_got_ip_t *event = (ip_event_got_ip_t *)event_data;
    ESP_LOGI(LOG_TAG, "got ip:" IPSTR, IP2STR(&event->ip_info.ip));
    g_wifi_connected = true;
    g_wifi_failed = false;
    s_retry_num = 0;
    ui_wifi_connected();
  }
}

void oai_wifi_init(void) 
{
  ESP_ERROR_CHECK(esp_event_handler_register(WIFI_EVENT, ESP_EVENT_ANY_ID,
                                             &oai_event_handler, NULL));
  ESP_ERROR_CHECK(esp_event_handler_register(IP_EVENT, IP_EVENT_STA_GOT_IP,
                                             &oai_event_handler, NULL));

  ESP_ERROR_CHECK(esp_netif_init());
  esp_netif_t *sta_netif = esp_netif_create_default_wifi_sta();
  assert(sta_netif);

  wifi_init_config_t cfg = WIFI_INIT_CONFIG_DEFAULT();
  ESP_ERROR_CHECK(esp_wifi_init(&cfg));
}

bool oai_wifi(void) {
  g_wifi_connected = false;
  g_wifi_failed = false;

  if (!wifi_credentials_configured()) {
    ESP_LOGE(LOG_TAG,
             "Wi-Fi credentials missing. Update pumpkin_config.h before flashing.");
    ui_wifi_failed();
    return false;
  }

  wifi_config_t wifi_config = {};
  memset(&wifi_config, 0, sizeof(wifi_config));
  strlcpy(reinterpret_cast<char *>(wifi_config.sta.ssid),
          pumpkin_config::kWifiSsid, sizeof(wifi_config.sta.ssid));
  strlcpy(reinterpret_cast<char *>(wifi_config.sta.password),
          pumpkin_config::kWifiPassword, sizeof(wifi_config.sta.password));

  if (strlen(pumpkin_config::kWifiPassword) > 0) {
    wifi_config.sta.threshold.authmode = WIFI_AUTH_WPA2_PSK;
  } else {
    wifi_config.sta.threshold.authmode = WIFI_AUTH_OPEN;
  }
  wifi_config.sta.sae_pwe_h2e = WPA3_SAE_PWE_BOTH;

  ESP_LOGI(LOG_TAG, "Connecting to Wi-Fi SSID: %s", wifi_config.sta.ssid);
  ui_wifi_connecting();

  ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_STA));
  ESP_ERROR_CHECK(esp_wifi_set_config(WIFI_IF_STA, &wifi_config));
  ESP_ERROR_CHECK(esp_wifi_start());
  ESP_ERROR_CHECK(esp_wifi_set_max_tx_power(34));

  // Connection attempt triggered by WIFI_EVENT_STA_START handler.
  while (!g_wifi_connected && !g_wifi_failed) {
    vTaskDelay(pdMS_TO_TICKS(200));
  }

  return g_wifi_connected;
}
