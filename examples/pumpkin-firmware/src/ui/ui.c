#include "ui.h"
#include "esp_lvgl_port.h"

// Assume that the images have been converted to C arrays and included
extern const lv_img_dsc_t speaking_A;
extern const lv_img_dsc_t speaking_B;
extern const lv_img_dsc_t speaking_C;
extern const lv_img_dsc_t speaking_D;
extern const lv_img_dsc_t speaking_E;

extern const lv_img_dsc_t listening_A;
extern const lv_img_dsc_t listening_B;
extern const lv_img_dsc_t listening_C;
extern const lv_img_dsc_t listening_D;
extern const lv_img_dsc_t listening_E;

// Image arrays
static const lv_img_dsc_t *speaking_images[] = {
    &speaking_A,
    &speaking_B,
    &speaking_C,
    &speaking_D,
    &speaking_E
};

static const lv_img_dsc_t *listening_images[] = {
    &listening_A,
    &listening_B,
    &listening_C,
    &listening_D,
    &listening_E
};
static lv_obj_t *label;
static lv_obj_t *img; // Image object
static uint8_t current_image_index = 0; // Index of the currently displayed image
static bool is_speaking = false; // Whether the speaking images are currently displayed
static lv_timer_t *timer1 = NULL; // Timer 1 (switches to listening after 2.5s)
static lv_timer_t *timer2 = NULL; // Timer 2 (500ms image polling)

static void ensure_label(void)
{
    if (!label) {
        label = lv_label_create(lv_scr_act()); // Create a label on the active screen
        lv_obj_set_width(label, LV_PCT(100));
        lv_label_set_long_mode(label, LV_LABEL_LONG_SCROLL_CIRCULAR);
    }
}

static void ui_set_label_text(const char *text)
{
    ensure_label();
    lv_label_set_text(label, text);
    lv_obj_clear_flag(label, LV_OBJ_FLAG_HIDDEN);
    lv_obj_align(label, LV_ALIGN_CENTER, 0, 0);
}

// Timer 2 callback function (500ms image polling)
static void timer2_callback(lv_timer_t *timer)
{
    const lv_img_dsc_t **images = is_speaking ? speaking_images : listening_images;
    current_image_index = (current_image_index + 1) % (sizeof(speaking_images) / sizeof(speaking_images[0]));
    lv_img_set_src(img, images[current_image_index]); // Update the image
}

// Timer 1 callback function (switches to listening after 2.5s)
static void timer1_callback(lv_timer_t *timer)
{
    is_speaking = false; // Switch to listening images
    lv_timer_reset(timer2); // Reset Timer 2
}

// Switch to speaking images
void ui_switch_speaking(void)
{
    lvgl_port_lock(0);
    if (img == NULL) {
        img = lv_img_create(lv_scr_act());
        lv_img_set_src(img, listening_images[current_image_index]);
        lv_obj_align(img, LV_ALIGN_CENTER, 0, 0);
    }
    if (!is_speaking) {
        // If not currently displaying speaking images, switch to speaking images
        is_speaking = true;
        current_image_index = 0;
        lv_img_set_src(img, speaking_images[current_image_index]); // Set the initial image

        // Start Timer 1 (switch to listening after 1s)
        if (timer1) {
            lv_timer_reset(timer1);
        } else {
            timer1 = lv_timer_create(timer1_callback, 1000, NULL); // 1s timer
        }

    } else {
        // If already displaying speaking images, reset Timer 1
        if (timer1) {
            lv_timer_reset(timer1);
        }
    }
    lvgl_port_unlock();
}

void ui_listening(void)
{
    lvgl_port_lock(0);
    if (label) {
        lv_obj_add_flag(label, LV_OBJ_FLAG_HIDDEN);
    }
    img = lv_img_create(lv_scr_act());
    lv_img_set_src(img, listening_images[current_image_index]); // Set the initial image to listening
    lv_obj_align(img, LV_ALIGN_CENTER, 0, 0); // Center the image
    timer2 = lv_timer_create(timer2_callback, 300, NULL); // 500ms timer
    lv_timer_set_repeat_count(timer2, -1);
    lvgl_port_unlock();
}

void ui_wifi_connecting(void)
{
    lvgl_port_lock(0);
    ui_set_label_text("Wi-Fi connecting...");
    lvgl_port_unlock();
}

void ui_wifi_connected(void)
{
    lvgl_port_lock(0);
    ui_set_label_text("Wi-Fi connected. Waiting for pumpkin server...");
    lvgl_port_unlock();
}

void ui_wifi_failed(void)
{
    lvgl_port_lock(0);
    ui_set_label_text("Wi-Fi failed. Check pumpkin_config.h and reboot.");
    lvgl_port_unlock();
}

void ui_server_connecting(void)
{
    lvgl_port_lock(0);
    ui_set_label_text("Pumpkin server connecting...");
    lvgl_port_unlock();
}

void ui_server_waiting_config(void)
{
    lvgl_port_lock(0);
    ui_set_label_text("Pumpkin server configuring...");
    lvgl_port_unlock();
}

void ui_server_ready(void)
{
    lvgl_port_lock(0);
    ui_set_label_text("Pumpkin server ready. Say hello!");
    lvgl_port_unlock();
}

void ui_server_error(void)
{
    lvgl_port_lock(0);
    ui_set_label_text("Pumpkin server unavailable. Retrying...");
    lvgl_port_unlock();
}

void ui_init(void)
{
    lvgl_port_lock(0);
    ensure_label();
    lv_label_set_text(label, "Configure Wi-Fi and OpenAI key via serial port.");
    lv_obj_align(label, LV_ALIGN_CENTER, 0, 0);
    lvgl_port_unlock();
}
