#pragma once

#include <stdint.h>

/**
 * Default configuration values compiled into the firmware. Users can override
 * these at runtime via the serial console; values are persisted to NVS so the
 * device remembers them after reboot.
 */
namespace pumpkin_config {

constexpr char kWifiSsid[] = "fullmoon";
constexpr char kWifiPassword[] = "casperlegault";

constexpr char kServerHost[] = "192.168.1.148";
constexpr uint16_t kServerPort = 8080;
constexpr bool kServerUseTls = false;

constexpr int kWifiMaxRetryCount = 5;

}  // namespace pumpkin_config
