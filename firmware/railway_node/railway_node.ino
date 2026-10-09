/*
  Railway Animal Detection — Node Firmware (ESP32-CAM + PIR)

  Two wake sources, checked at the top of setup() since deep sleep restarts
  execution from scratch every time (there is no persistent loop()):

    1. PIR on GPIO13 goes HIGH  -> capture a frame, POST it to /api/v1/infer
    2. Timer fires (every HEARTBEAT_INTERVAL_SEC) -> POST a lightweight
       heartbeat to /api/v1/heartbeat, no camera involved

  Before uploading:
    1. Copy config.h.example to config.h and fill in your WiFi + server details
    2. Board: "AI Thinker ESP32-CAM", Upload Speed: 115200, Partition Scheme: "Huge APP"
    3. Wire GPIO0 to GND, upload, then remove that jumper before running normally
    4. Wire per the flashing-bridge instructions if you don't have the USB shield yet
*/

#include <WiFi.h>
#include <HTTPClient.h>
#include "esp_camera.h"
#include "esp_sleep.h"
#include "camera_pins.h"
#include "config.h"

#define PIR_PIN GPIO_NUM_13
#define HEARTBEAT_INTERVAL_SEC 300      // 5 minutes between "I'm alive" pings
#define WIFI_CONNECT_TIMEOUT_MS 15000
#define PIR_SETTLE_TIMEOUT_MS 60000     // max time to wait for PIR to go LOW before re-arming

// Persists across deep sleep (RTC memory survives, regular RAM doesn't)
RTC_DATA_ATTR int boot_count = 0;

bool init_camera() {
  camera_config_t config;
  config.ledc_channel = LEDC_CHANNEL_0;
  config.ledc_timer = LEDC_TIMER_0;
  config.pin_d0 = Y2_GPIO_NUM;
  config.pin_d1 = Y3_GPIO_NUM;
  config.pin_d2 = Y4_GPIO_NUM;
  config.pin_d3 = Y5_GPIO_NUM;
  config.pin_d4 = Y6_GPIO_NUM;
  config.pin_d5 = Y7_GPIO_NUM;
  config.pin_d6 = Y8_GPIO_NUM;
  config.pin_d7 = Y9_GPIO_NUM;
  config.pin_xclk = XCLK_GPIO_NUM;
  config.pin_pclk = PCLK_GPIO_NUM;
  config.pin_vsync = VSYNC_GPIO_NUM;
  config.pin_href = HREF_GPIO_NUM;
  config.pin_sccb_sda = SIOD_GPIO_NUM;
  config.pin_sccb_scl = SIOC_GPIO_NUM;
  config.pin_pwdn = PWDN_GPIO_NUM;
  config.pin_reset = RESET_GPIO_NUM;
  config.xclk_freq_hz = 20000000;
  config.pixel_format = PIXFORMAT_JPEG;
  config.frame_size = FRAMESIZE_VGA;   // 640x480 — good enough for the model, keeps upload fast
  config.jpeg_quality = 12;            // lower number = higher quality, larger file
  config.fb_count = 1;

  esp_err_t err = esp_camera_init(&config);
  if (err != ESP_OK) {
    Serial.printf("Camera init failed: 0x%x\n", err);
    return false;
  }
  return true;
}

bool connect_wifi() {
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);

  unsigned long start = millis();
  while (WiFi.status() != WL_CONNECTED) {
    if (millis() - start > WIFI_CONNECT_TIMEOUT_MS) {
      Serial.println("WiFi connect timed out");
      return false;
    }
    delay(250);
  }
  Serial.printf("WiFi connected, IP: %s\n", WiFi.localIP().toString().c_str());
  return true;
}

// Builds and sends a multipart/form-data POST with the image + sensor fields.
// HTTPClient has no built-in multipart helper, so this constructs the body by hand.
bool send_detection(camera_fb_t *fb) {
  HTTPClient http;
  String url = String("http://") + SERVER_HOST + ":" + String(SERVER_PORT) + "/api/v1/infer";
  http.begin(url);

  String boundary = "----RailwayNodeBoundary7MA4YWx";
  http.addHeader("Content-Type", "multipart/form-data; boundary=" + boundary);

  auto formField = [&](const String &name, const String &value) {
    return "--" + boundary + "\r\n"
           "Content-Disposition: form-data; name=\"" + name + "\"\r\n\r\n" +
           value + "\r\n";
  };

  String head =
    formField("node_id", NODE_ID) +
    formField("location_km", String(LOCATION_KM, 2)) +
    formField("pir_triggered", "true") +
    formField("geophone_triggered", "false") +
    formField("animal_count", "1") +
    "--" + boundary + "\r\n"
    "Content-Disposition: form-data; name=\"image\"; filename=\"frame.jpg\"\r\n"
    "Content-Type: image/jpeg\r\n\r\n";

  String tail = "\r\n--" + boundary + "--\r\n";

  size_t totalLen = head.length() + fb->len + tail.length();
  uint8_t *body = (uint8_t *)malloc(totalLen);
  if (!body) {
    Serial.println("Failed to allocate multipart body buffer");
    http.end();
    return false;
  }

  size_t pos = 0;
  memcpy(body + pos, head.c_str(), head.length()); pos += head.length();
  memcpy(body + pos, fb->buf, fb->len);            pos += fb->len;
  memcpy(body + pos, tail.c_str(), tail.length());

  int httpCode = http.POST(body, totalLen);
  Serial.printf("POST /infer -> %d\n", httpCode);
  if (httpCode > 0) {
    Serial.println(http.getString());
  }

  free(body);
  http.end();
  return httpCode > 0 && httpCode < 300;
}

bool send_heartbeat() {
  HTTPClient http;
  String url = String("http://") + SERVER_HOST + ":" + String(SERVER_PORT) + "/api/v1/heartbeat";
  http.begin(url);
  http.addHeader("Content-Type", "application/x-www-form-urlencoded");

  String body = "node_id=" + String(NODE_ID) + "&location_km=" + String(LOCATION_KM, 2);
  int httpCode = http.POST(body);
  Serial.printf("POST /heartbeat -> %d\n", httpCode);

  http.end();
  return httpCode > 0 && httpCode < 300;
}

// Waits for the PIR output to drop LOW before we go back to sleep, so the
// same motion event that just woke us doesn't immediately wake us again.
void wait_for_pir_to_settle() {
  unsigned long start = millis();
  while (digitalRead(PIR_PIN) == HIGH) {
    if (millis() - start > PIR_SETTLE_TIMEOUT_MS) {
      Serial.println("PIR still HIGH after timeout, sleeping anyway");
      break;
    }
    delay(200);
  }
}

void go_back_to_sleep() {
  esp_sleep_enable_ext0_wakeup(PIR_PIN, 1);                              // wake on PIR HIGH
  esp_sleep_enable_timer_wakeup((uint64_t)HEARTBEAT_INTERVAL_SEC * 1000000ULL); // wake on timer
  Serial.println("Going to sleep...");
  Serial.flush();
  esp_deep_sleep_start();
}

void setup() {
  Serial.begin(115200);
  delay(200);

  pinMode(PIR_PIN, INPUT);
  boot_count++;

  esp_sleep_wakeup_cause_t wakeup_reason = esp_sleep_get_wakeup_cause();
  Serial.printf("Boot #%d, wakeup reason: %d\n", boot_count, wakeup_reason);

  bool wifi_ok = connect_wifi();

  if (wakeup_reason == ESP_SLEEP_WAKEUP_EXT0) {
    // Real motion trigger: capture and send for inference
    Serial.println("Wake cause: PIR motion");
    if (wifi_ok && init_camera()) {
      camera_fb_t *fb = esp_camera_fb_get();
      if (fb) {
        send_detection(fb);
        esp_camera_fb_return(fb);
      } else {
        Serial.println("Frame capture failed");
      }
    }
    wait_for_pir_to_settle();

  } else {
    // Timer wake, or first power-on: just a heartbeat, no camera needed
    Serial.println("Wake cause: timer / power-on");
    if (wifi_ok) {
      send_heartbeat();
    }
  }

  go_back_to_sleep();
}

void loop() {
  // Never reached -- setup() always ends in deep sleep, which restarts
  // execution from setup() on the next wake.
}
