#include <stdint.h>

#include <peer.h>
#include "sensecap-watcher.h"
extern "C" {
#include "ui.h"
}

#define LOG_TAG "realtimeapi-sdk"
#define MAX_HTTP_OUTPUT_BUFFER 2048

bool oai_wifi(void);
void oai_wifi_init(void);
void oai_init_audio_capture(void);
void oai_init_audio_decoder(void);
void oai_init_audio_encoder();
void oai_send_audio(PeerConnection *peer_connection);
void oai_audio_decode(uint8_t *data, size_t size);
void oai_webrtc();
void oai_http_request(char *offer, char *answer);

int cmd_init(void);

size_t pumpkin_audio_read(int16_t *buffer, size_t samples);
void pumpkin_audio_write(const int16_t *buffer, size_t samples);
void pumpkin_audio_play_test_tone(void);
