#pragma once

#include <stdbool.h>
#include <stdint.h>

#include <string>

/**
 * High-level connection states reported by the pumpkin client.
 *
 * The state machine intentionally ignores Wi-Fi transitions (handled elsewhere)
 * and focuses on the lifecycle of the WebSocket connection to the pumpkin server.
 */
enum class PumpkinClientState {
  kIdle,             ///< Client task not started yet.
  kConnecting,       ///< TCP/TLS socket is being established.
  kAwaitingConfig,   ///< Handshake sent; waiting for the server's config payload.
  kReady,            ///< Config payload received and parsed successfully.
  kError,            ///< Terminal error; will retry after a backoff delay.
};

/**
 * Minimal runtime configuration derived from the server's config message.
 *
 * Future milestones will expand this into typed fields. For now we retain the
 * raw JSON payload for logging/debugging purposes.
 */
struct PumpkinRuntimeConfig {
  std::string raw_json;
};

struct PumpkinClientConfig {
  const char *host = nullptr;
  uint16_t port = 0;
  bool use_tls = false;
};

using PumpkinClientStateCallback = void (*)(PumpkinClientState state, void *user_data);

void pumpkin_client_register_state_callback(PumpkinClientStateCallback callback,
                                            void *user_data);

void pumpkin_client_start(const PumpkinClientConfig &config);
void pumpkin_client_stop(void);
bool pumpkin_client_ready(void);
const PumpkinRuntimeConfig *pumpkin_client_get_runtime_config(void);
PumpkinClientState pumpkin_client_get_state(void);
const char *pumpkin_client_state_string(PumpkinClientState state);
