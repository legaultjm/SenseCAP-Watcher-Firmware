#include <driver/i2s.h>
#include <opus.h>

#include <algorithm>
#include <cmath>
#include <math.h>
#include <vector>

#include "main.h"


#define OPUS_OUT_BUFFER_SIZE 1276  // 1276 bytes is recommended by opus_encode
#define SAMPLE_RATE  16000
#define CHANNELS     1

#define BUFFER_SAMPLES (640)
#define BUFFER_SAMPLES_CNT (BUFFER_SAMPLES/2)

#define OPUS_ENCODER_BITRATE 30000
#define OPUS_ENCODER_COMPLEXITY 0

static esp_codec_dev_handle_t play_dev_handle;
static esp_codec_dev_handle_t record_dev_handle;

void oai_init_audio_capture() {
  bsp_codec_mute_set(true);
  bsp_codec_mute_set(false);
  bsp_codec_volume_set(120, NULL);

  play_dev_handle = bsp_codec_speaker_get();
  record_dev_handle = bsp_codec_microphone_get();
  return;
}

opus_int16 *output_buffer = NULL;
OpusDecoder *opus_decoder = NULL;

void oai_init_audio_decoder() {
  int decoder_error = 0;
  opus_decoder = opus_decoder_create(SAMPLE_RATE, CHANNELS, &decoder_error);
  if (decoder_error != OPUS_OK) {
    printf("Failed to create OPUS decoder");
    return;
  }
  output_buffer = (opus_int16 *)malloc(BUFFER_SAMPLES_CNT * sizeof(opus_int16));
}

void oai_audio_decode(uint8_t *data, size_t size) {
  
  int decoded_size =
      opus_decode(opus_decoder, data, size, output_buffer, BUFFER_SAMPLES_CNT, 0);
  
  if( size > 26) {
    printf("size: %d, decode size: %d\r\n", size, decoded_size);
    ui_switch_speaking();
  }
  if (decoded_size > 0) {
    esp_codec_dev_write(play_dev_handle, output_buffer, BUFFER_SAMPLES_CNT * sizeof(opus_int16));           
  }
}

OpusEncoder *opus_encoder = NULL;
opus_int16 *encoder_input_buffer = NULL;
uint8_t *encoder_output_buffer = NULL;

void oai_init_audio_encoder() {
  int encoder_error;
  opus_encoder = opus_encoder_create(SAMPLE_RATE, CHANNELS, OPUS_APPLICATION_VOIP,
                                     &encoder_error);
  if (encoder_error != OPUS_OK) {
    printf("Failed to create OPUS encoder");
    return;
  }

  if (opus_encoder_init(opus_encoder, SAMPLE_RATE, CHANNELS, OPUS_APPLICATION_VOIP) !=
      OPUS_OK) {
    printf("Failed to initialize OPUS encoder");
    return;
  }

  opus_encoder_ctl(opus_encoder, OPUS_SET_BITRATE(OPUS_ENCODER_BITRATE));
  opus_encoder_ctl(opus_encoder, OPUS_SET_COMPLEXITY(OPUS_ENCODER_COMPLEXITY));
  opus_encoder_ctl(opus_encoder, OPUS_SET_SIGNAL(OPUS_SIGNAL_VOICE));
  encoder_input_buffer = (opus_int16 *)malloc(BUFFER_SAMPLES);
  encoder_output_buffer = (uint8_t *)malloc(OPUS_OUT_BUFFER_SIZE);
}

void oai_send_audio(PeerConnection *peer_connection) {

  esp_codec_dev_read(record_dev_handle, encoder_input_buffer, BUFFER_SAMPLES);

  auto encoded_size =
      opus_encode(opus_encoder, encoder_input_buffer, BUFFER_SAMPLES_CNT,
                  encoder_output_buffer, OPUS_OUT_BUFFER_SIZE);

  // printf("size: %d, encoded_size : %ld\r\n", BUFFER_SAMPLES, encoded_size);
  peer_connection_send_audio(peer_connection, encoder_output_buffer,
                             encoded_size);
}

size_t pumpkin_audio_read(int16_t *buffer, size_t samples) {
  if (!record_dev_handle || buffer == NULL || samples == 0) {
    return 0;
  }
  size_t bytes_to_read = samples * sizeof(int16_t);
  esp_err_t ret = bsp_get_feed_data(false, buffer, bytes_to_read);
  if (ret != ESP_OK) {
    return 0;
  }
  return bytes_to_read / sizeof(int16_t);
}

void pumpkin_audio_write(const int16_t *buffer, size_t samples) {
  if (!play_dev_handle || buffer == NULL || samples == 0) {
    return;
  }
  size_t bytes_to_write = samples * sizeof(int16_t);
  esp_codec_dev_write(play_dev_handle, const_cast<int16_t *>(buffer), bytes_to_write);
}

void pumpkin_audio_play_test_tone(void) {
  if (!play_dev_handle) {
    return;
  }

  constexpr float kAmplitude = 0.4f;
  constexpr float kFrequencyHz = 880.0f;
  constexpr uint32_t kDurationMs = 500;
  constexpr float kTwoPi = 6.28318530717958647692f;

  const size_t total_samples = (SAMPLE_RATE * kDurationMs) / 1000;
  std::vector<int16_t> waveform(total_samples);

  for (size_t i = 0; i < total_samples; ++i) {
    const float t = static_cast<float>(i) / static_cast<float>(SAMPLE_RATE);
    const float value = sinf(kTwoPi * kFrequencyHz * t);
    waveform[i] = static_cast<int16_t>(value * kAmplitude * 32767.0f);
  }

  constexpr size_t kChunkSamples = 320;
  size_t offset = 0;
  while (offset < waveform.size()) {
    const size_t chunk = std::min(kChunkSamples, waveform.size() - offset);
    pumpkin_audio_write(waveform.data() + offset, chunk);
    offset += chunk;
  }
}
