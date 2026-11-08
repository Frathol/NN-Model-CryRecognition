// =========================================================================
//   SMART INCUBATOR NEOSENTINEL - ESP32-S3 FULL INTEGRATED SENSOR & ALARM NODE
//   (AMBANG BATAS DISAMAKAN 100% DENGAN DASHBOARD & FSM.PY REKAN TIM)
//
//   1. Suhu Tubuh Bayi (NTC GPIO 3):
//      - Aman: 36.2 - 37.5 C (NHS West of Scotland & CMNRP)
//      - < 36.2 C atau > 37.5 C -> Langsung CODE BLUE tanpa tunda (Wajib Klik Button GPIO 5)
//   2. Detak Jantung (Pulse Sensor "Love" 3-Pin: S -> GPIO 7, + -> 3.3V, - -> GND):
//      - Normal: 100 - 160 BPM
//      - 161 - 180 BPM bertahan HR_HIGH_DELAY_MS (180 dtk paper / 30 dtk demo) -> WARN (Kuning)
//      - < 100 BPM bertahan 10 detik -> CODE BLUE (Merah + Buzzer Latched, Wajib Klik Button)
//      - > 180 BPM bertahan HR_HIGH_DELAY_MS -> CODE BLUE (Merah + Buzzer Latched, Wajib Klik Button)
//      - Sensor tidak menempel (contact == false) -> WARN (Kuning, "sensor not attached", BPM diabaikan)
//      - Status menangis dibaca dari incubator/cry/state (dikirim cry_service di Pi).
//        Tanpa data tangisan (MQTT putus / cry_service offline) dianggap TIDAK menangis.
//      - Lama tunda dikirim di alarm.hr_high_delay_s supaya dashboard & fsm.py memakai angka yang sama.
//   3. Suhu Udara Kabin (SHT31 GPIO 8/9):
//      - Aman: 32.0 - 34.0 C. Di luar rentang -> CRITICAL Kabin (Otomatis mati saat suhu pulih)
// =========================================================================

#include <Arduino.h>
#include <WiFi.h>
#include <Wire.h>
#include <PubSubClient.h>
#include <ArduinoJson.h>
#include <Adafruit_SHT31.h>
#include <DHT.h>
#include "HX711.h"
#include <Adafruit_GFX.h>
#include <Adafruit_SSD1306.h>
#include <Adafruit_NeoPixel.h>

// -------------------------------------------------------------------------
// 1. KONFIGURASI JARINGAN, MQTT & TUNDA WAKTU (DEMO VS PAPER)
// -------------------------------------------------------------------------
#define WIFI_SSID               "Nama jaringan"
#define WIFI_PASSWORD           "Frathol23091"

#define MQTT_HOST               "10.223.183.158"    // IP Statis Raspberry Pi 5
#define MQTT_PORT               1883
#define MQTT_USER               "esp32"
#define MQTT_PASS               "12345678"
#define MQTT_CLIENT_ID          "incubator-esp32s3"

#define TOPIC_SENSORS           "incubator/sensors"       // Publish JSON periodik
#define TOPIC_STATUS            "incubator/esp32/status"  // LWT online/offline
#define TOPIC_CRY_STATE         "incubator/cry/state"     // Subscribe: {"crying": true/false, ...} dari cry_service
#define TOPIC_CRY_STATUS        "incubator/cry/status"    // Subscribe: online / no_mic / offline

#define SENSOR_READ_MS          1000                      // Update layar OLED & FSM tiap 1 detik
#define PUBLISH_INTERVAL_MS     5000                      // Publish JSON ke MQTT tiap 5 detik
#define USE_DUMMY_SOC           false                     // false = baca Potensio 3.3V di GPIO 1

// Ubah DEMO_FAST_DELAY ke true (30 detik) saat demo, dan kembalikan ke false (180 detik) untuk paper!
#define DEMO_FAST_DELAY         true
#define HR_LOW_DELAY_MS         10000UL                   // 10 detik untuk BPM < 100 (ASU/CAHS)
#define HR_HIGH_DELAY_MS        (DEMO_FAST_DELAY ? 30000UL : 180000UL) // 30s Demo / 180s Paper (Erasmus MC)

// Ambang Batas Klinis Sesuai Kesepakatan Tim
#define NTC_SAFE_MIN            36.2f
#define NTC_SAFE_MAX            37.5f
#define CABIN_SAFE_MIN          32.0f
#define CABIN_SAFE_MAX          34.0f

// -------------------------------------------------------------------------
// 2. PEMETAAN PIN HARDWARE ESP32-S3
// -------------------------------------------------------------------------
#define I2C0_SDA                8
#define I2C0_SCL                9
#define I2C1_SDA                10
#define I2C1_SCL                11

#define NTC_PIN                 3
#define NTC_SAMPLES             16
#define NTC_VCC_MV              3300.0f
#define NTC_R_SERIES            10000.0f
#define NTC_R_NOMINAL           10000.0f
#define NTC_T_NOMINAL           25.0f
#define NTC_BETA                3950.0f

// Sensor Detak Jantung Bentuk "Love" 3-Pin (S -> GPIO 7, + -> 3.3V, - -> GND)
#define PULSE_SENSOR_PIN        7
#define HR_AVG_SIZE             5

#define DHT_PIN                 4
#define DHT_TYPE                DHT11
#define BUTTON_MUTE_PIN         5
#define BUZZER_PIN              6

#define LED_GREEN_PIN           12
#define LED_YELLOW_PIN          13
#define LED_RED_PIN             14

#define LC1_DT                  16
#define LC1_SCK                 17
#define LC2_DT                  18
#define LC2_SCK                 21

#define POT_SOC_PIN             1
#define MAINS_DETECT_PIN        2
#define NEOPIXEL_PIN            48
#define NUM_PIXELS              1

// -------------------------------------------------------------------------
// 3. INISIALISASI OBJEK & STATE
// -------------------------------------------------------------------------
WiFiClient        wifiClient;
PubSubClient      mqtt(wifiClient);

TwoWire           I2C_Bus0 = TwoWire(0);
TwoWire           I2C_Bus1 = TwoWire(1);

Adafruit_SHT31    sht31(&I2C_Bus0);
Adafruit_SSD1306  oled1(128, 64, &I2C_Bus0, -1);
Adafruit_SSD1306  oled2(128, 64, &I2C_Bus1, -1);

DHT               dht(DHT_PIN, DHT_TYPE);
HX711             scale1;
HX711             scale2;
Adafruit_NeoPixel rgbLed(NUM_PIXELS, NEOPIXEL_PIN, NEO_GRB + NEO_KHZ800);

bool shtOk   = false;
bool oled1Ok = false;
bool oled2Ok = false;
bool lc1Ok   = false;
bool lc2Ok   = false;

float cal_factor_1 = -226.34f;
float cal_factor_2 =  225.66f;

portMUX_TYPE hrMux = portMUX_INITIALIZER_UNLOCKED;
struct HeartData {
  float    bpm       = NAN;
  uint32_t rawSignal = 0;
  bool     contact   = false;
} hrShared;

struct IncubatorState {
  float       ntcTemp           = NAN;
  float       shtTemp           = NAN;
  float       shtHum            = NAN;
  float       dhtTemp           = NAN;
  float       dhtHum            = NAN;
  float       heartBpm          = NAN;
  uint32_t    heartSignal       = 0;
  bool        heartContact      = false;
  float       weightLeft        = 0.0f;
  float       weightRight       = 0.0f;
  float       weightTotal       = 0.0f;
  int         socPercent        = 100;
  bool        mainsOk           = true;
  bool        babyCrying        = false; // Info konteks dari AI cry detection via MQTT
  const char* fsm               = "NORMAL";
  const char* clinicalCode      = "IDLE_NORMAL";
  bool        codeBlueLatched   = false; // Mengunci CODE_BLUE sampai tombol fisik GPIO 5 diklik
  bool        manualBuzzerAck   = false;
  bool        manualBtnRequired = false;
  uint32_t    hrLowStartMs      = 0;
  uint32_t    hrMidStartMs      = 0;
  uint32_t    hrHighStartMs     = 0;
} state;

// -------------------------------------------------------------------------
// 4. HELPER JSON & PEMBACAAN NTC (PERSAMAAN BETA)
// -------------------------------------------------------------------------
static void putNum(JsonObject obj, const char* key, float v, int decimals = 2) {
  if (isnan(v)) { obj[key] = nullptr; return; }
  float p = powf(10.0f, decimals);
  obj[key] = roundf(v * p) / p;
}

float readNtcCelsius() {
  uint32_t sum = 0;
  for (int i = 0; i < NTC_SAMPLES; i++) {
    sum += analogReadMilliVolts(NTC_PIN);
    delayMicroseconds(200);
  }
  float mv = (float)sum / NTC_SAMPLES;
  if (mv <= 10.0f || mv >= NTC_VCC_MV - 10.0f) return NAN;

  float rNtc = NTC_R_SERIES * mv / (NTC_VCC_MV - mv);
  float invT = 1.0f / (NTC_T_NOMINAL + 273.15f) + logf(rNtc / NTC_R_NOMINAL) / NTC_BETA;
  return (1.0f / invT) - 273.15f;
}

// -------------------------------------------------------------------------
// 5. TASK FREERTOS SENSOR DETAK JANTUNG "LOVE" 3-PIN (ADC GPIO 7 ~100 Hz)
// -------------------------------------------------------------------------
void pulseSensorTask(void*) {
  float    rates[HR_AVG_SIZE] = {0};
  uint8_t  rateIdx = 0, rateCount = 0;
  uint32_t lastBeatMs = 0;
  float    baseline = 2048.0f;
  float    envelope = 0.0f;
  bool     aboveThreshold = false;

  for (;;) {
    uint16_t raw = analogRead(PULSE_SENSOR_PIN);
    uint32_t now = millis();

    baseline = 0.99f * baseline + 0.01f * (float)raw;
    float ac = (float)raw - baseline;
    envelope = 0.95f * envelope + 0.05f * fabsf(ac);

    // Deteksi kontak kulit terkalibrasi (bebas noise cahaya ruangan)
    bool contact = (raw > 200 && raw < 3900 && envelope > 25.0f);

    if (!contact) {
      rateCount = 0; rateIdx = 0;
    } else {
      float thresh = fmaxf(envelope * 0.70f, 30.0f);
      if (ac > thresh && !aboveThreshold && (now - lastBeatMs > 250)) {
        aboveThreshold = true;
        if (lastBeatMs != 0) {
          float bpm = 60000.0f / (float)(now - lastBeatMs);
          if (bpm >= 55.0f && bpm <= 220.0f) {
            rates[rateIdx] = bpm;
            rateIdx = (rateIdx + 1) % HR_AVG_SIZE;
            if (rateCount < HR_AVG_SIZE) rateCount++;
          }
        }
        lastBeatMs = now;
      } else if (ac < 0.0f) {
        aboveThreshold = false;
      }
    }

    bool stale = (lastBeatMs == 0) || ((now - lastBeatMs) > 4000);
    float avg = NAN;
    if (contact && !stale && rateCount > 0) {
      float s = 0;
      for (uint8_t i = 0; i < rateCount; i++) s += rates[i];
      avg = s / rateCount;
    }

    portENTER_CRITICAL(&hrMux);
    hrShared.bpm       = avg;
    hrShared.rawSignal = raw;
    hrShared.contact   = contact;
    portEXIT_CRITICAL(&hrMux);

    vTaskDelay(pdMS_TO_TICKS(10));
  }
}

// -------------------------------------------------------------------------
// 6. KONTROL LED RGB, 3 LED STATUS, TOMBOL MUTE & BUZZER
// -------------------------------------------------------------------------
void updateRgbStatus() {
  static uint32_t lastBlink = 0;
  static uint8_t  step      = 0;
  if (millis() - lastBlink < 350) return;
  lastBlink = millis();

  step = (step + 1) % 4;
  bool isOnline = (WiFi.status() == WL_CONNECTED && mqtt.connected());

  if (!isOnline) {
    if (step == 0 || step == 2) rgbLed.setPixelColor(0, rgbLed.Color(150, 0, 150));
    else rgbLed.clear();
  } else {
    if (step == 0) rgbLed.setPixelColor(0, rgbLed.Color(0, 0, 180));
    else if (step == 2) rgbLed.setPixelColor(0, rgbLed.Color(0, 180, 0));
    else rgbLed.clear();
  }
  rgbLed.show();
}

void updateStatusLeds() {
  digitalWrite(LED_GREEN_PIN,  strcmp(state.fsm, "NORMAL") == 0 ? HIGH : LOW);
  digitalWrite(LED_YELLOW_PIN, strcmp(state.fsm, "WARN") == 0 ? HIGH : LOW);
  digitalWrite(LED_RED_PIN,    strcmp(state.fsm, "CRITICAL") == 0 ? HIGH : LOW);
}

// Tombol Fisik GPIO 5: Khusus mematikan buzzer CODE_BLUE (NTC <36.2 / >37.5, BPM <100 / >180)
void checkMuteButton() {
  static bool lastBtnState = HIGH;
  static uint32_t lastDebounceTime = 0;
  bool reading = digitalRead(BUTTON_MUTE_PIN);
  if (reading != lastBtnState) lastDebounceTime = millis();

  if ((millis() - lastDebounceTime) > 50) {
    static bool confirmedState = HIGH;
    if (reading != confirmedState) {
      confirmedState = reading;
      if (confirmedState == LOW && state.manualBtnRequired) {
        state.manualBuzzerAck = true;
        state.codeBlueLatched = false;
        digitalWrite(BUZZER_PIN, LOW);
      }
    }
  }
  lastBtnState = reading;
}

void updateBuzzer() {
  if (strcmp(state.fsm, "NORMAL") == 0) {
    digitalWrite(BUZZER_PIN, LOW);
    return;
  }
  if (state.manualBtnRequired && state.manualBuzzerAck) {
    digitalWrite(BUZZER_PIN, LOW);
    return;
  }
  static uint32_t lastBeepToggle = 0;
  static bool beepPinState = false;
  uint32_t interval = (strcmp(state.fsm, "CRITICAL") == 0) ? 200 : 600;

  if (millis() - lastBeepToggle >= interval) {
    lastBeepToggle = millis();
    beepPinState = !beepPinState;
    digitalWrite(BUZZER_PIN, beepPinState ? HIGH : LOW);
  }
}

// -------------------------------------------------------------------------
// 7. PEMBACAAN SENSOR & EVALUASI AMBANG BARU (SEPAKAT DENGAN DASHBOARD)
// -------------------------------------------------------------------------
void readAllSensors() {
  uint32_t now = millis();

  // 1. NTC Suhu Tubuh Bayi (GPIO 3)
  state.ntcTemp = readNtcCelsius();

  // 2. SHT31 Suhu & RH Kabin (GPIO 8/9)
  if (!shtOk) shtOk = sht31.begin(0x44);
  if (shtOk) {
    state.shtTemp = sht31.readTemperature();
    state.shtHum  = sht31.readHumidity();
  } else {
    state.shtTemp = NAN; state.shtHum = NAN;
  }

  // 3. DHT11 Redundan (GPIO 4)
  state.dhtTemp = dht.readTemperature();
  state.dhtHum  = dht.readHumidity();

  // 4. Detak Jantung "Love" 3-Pin (GPIO 7)
  portENTER_CRITICAL(&hrMux);
  state.heartBpm     = hrShared.bpm;
  state.heartSignal  = hrShared.rawSignal;
  state.heartContact = hrShared.contact;
  portEXIT_CRITICAL(&hrMux);

  // 5. 2x Load Cell HX711
  if (lc1Ok && scale1.is_ready()) state.weightLeft = scale1.get_units(2);
  if (lc2Ok && scale2.is_ready()) state.weightRight = scale2.get_units(2);
  state.weightTotal = (state.weightLeft + state.weightRight) / 2.0f;
  if (abs(state.weightTotal) < 0.5f) state.weightTotal = 0.0f;

  // 6. Simulasi PSU (GPIO 2) & Baterai SoC (GPIO 1)
  state.mainsOk = (digitalRead(MAINS_DETECT_PIN) == HIGH);
  state.socPercent = USE_DUMMY_SOC ? 100 : constrain(map(analogReadMilliVolts(POT_SOC_PIN), 0, 3100, 0, 100), 0, 100);

  // =======================================================================
  // 7. EVALUASI ATURAN CODE BLUE & FSM (SESUAI FILE TXT REKAN TIM)
  // =======================================================================
  // A. Suhu tubuh bayi (NTC): Aman 36.2 - 37.5 C. Di luar itu -> Langsung CODE_BLUE tanpa tunda!
  bool ntcCodeBlue = !isnan(state.ntcTemp) && (state.ntcTemp < NTC_SAFE_MIN || state.ntcTemp > NTC_SAFE_MAX);

  // B. Detak Jantung (Pulse Sensor):
  //    Jika tidak menempel -> hanya peringatan "sensor not attached" (WARN), angka BPM diabaikan
  bool hrCodeBlueLow  = false;
  bool hrCodeBlueHigh = false;
  bool hrVitalAlert   = false;

  if (!state.heartContact || isnan(state.heartBpm)) {
    state.hrLowStartMs  = 0;
    state.hrMidStartMs  = 0;
    state.hrHighStartMs = 0;
  } else {
    // < 100 BPM bertahan 10 detik -> CODE BLUE
    if (state.heartBpm < 100.0f) {
      if (state.hrLowStartMs == 0) state.hrLowStartMs = now;
      if (now - state.hrLowStartMs >= HR_LOW_DELAY_MS) hrCodeBlueLow = true;
    } else {
      state.hrLowStartMs = 0;
    }

    // > 180 BPM bertahan 3 menit (atau 30 dtk demo) -> CODE BLUE (walaupun sedang menangis)
    if (state.heartBpm > 180.0f) {
      if (state.hrHighStartMs == 0) state.hrHighStartMs = now;
      if (now - state.hrHighStartMs >= HR_HIGH_DELAY_MS) hrCodeBlueHigh = true;
    } else {
      state.hrHighStartMs = 0;
    }

    // 161 - 180 BPM:
    // - Kalau menangis -> tidak ada alarm
    // - Kalau tidak menangis & bertahan 3 menit (30 dtk demo) -> PERINGATAN (Kuning / VITAL_SIGN_ALERT)
    if (state.heartBpm > 160.0f && state.heartBpm <= 180.0f && !state.babyCrying) {
      if (state.hrMidStartMs == 0) state.hrMidStartMs = now;
      if (now - state.hrMidStartMs >= HR_HIGH_DELAY_MS) hrVitalAlert = true;
    } else {
      state.hrMidStartMs = 0;
    }
  }

  // C. Suhu Udara Kabin (SHT31): Aman 32.0 - 34.0 C (Auto-Stage tanpa klik tombol)
  float refCabinTemp = !isnan(state.shtTemp) ? state.shtTemp : state.dhtTemp;
  bool cabinCritical = !isnan(refCabinTemp) && (refCabinTemp < CABIN_SAFE_MIN || refCabinTemp > CABIN_SAFE_MAX);
  bool powerCritical = (!state.mainsOk && state.socPercent < 20);

  // D. Kondisi Peringatan Kuning (WARN)
  bool sensorNotAttachedWarn = !state.heartContact;
  bool powerWarn = (!state.mainsOk || state.socPercent < 35);

  // Kategorisasi Buzzer: CODE_BLUE (Fisiologis Bayi) wajib klik tombol GPIO 5!
  bool codeBlueNow = (ntcCodeBlue || hrCodeBlueLow || hrCodeBlueHigh);
  if (codeBlueNow) {
    state.codeBlueLatched = true;
  }

  state.manualBtnRequired = (codeBlueNow || state.codeBlueLatched);
  if (!state.manualBtnRequired) {
    state.manualBuzzerAck = false;
  }

  if (codeBlueNow || state.codeBlueLatched) {
    state.fsm          = "CRITICAL";
    state.clinicalCode = "CODE_BLUE";
  } else if (cabinCritical || powerCritical) {
    state.fsm          = "CRITICAL";
    state.clinicalCode = cabinCritical ? "THERMAL_HAZARD" : "POWER_CRITICAL";
  } else if (hrVitalAlert || sensorNotAttachedWarn || powerWarn) {
    state.fsm          = "WARN";
    state.clinicalCode = hrVitalAlert ? "VITAL_SIGN_ALERT" : (sensorNotAttachedWarn ? "SENSOR_NOT_ATTACHED" : "POWER_WARNING");
  } else {
    state.fsm          = "NORMAL";
    state.clinicalCode = "IDLE_NORMAL";
  }

  updateStatusLeds();
}

// -------------------------------------------------------------------------
// 8. UPDATE 2x LAYAR OLED & PUBLISH JSON MQTT
// -------------------------------------------------------------------------
void updateOledDisplays() {
  if (oled1Ok) {
    oled1.clearDisplay();
    oled1.setTextSize(1);
    oled1.setTextColor(SSD1306_WHITE);
    oled1.setCursor(0, 0);
    oled1.printf("%s %s", state.clinicalCode, state.manualBtnRequired ? "[BTN]" : "[AUTO]");
    oled1.drawFastHLine(0, 10, 128, SSD1306_WHITE);
    oled1.setCursor(0, 14);
    oled1.printf("KABIN: %.1fC | %.0f%%", state.shtTemp, state.shtHum);
    oled1.setCursor(0, 26);
    oled1.printf("BAYI(NTC): %.2fC", state.ntcTemp);
    oled1.setCursor(0, 38);
    if (state.heartContact && !isnan(state.heartBpm)) oled1.printf("BPM: %.0f [MENEMPEL]", state.heartBpm);
    else oled1.print("BPM: --- [NOT ATTACH]");
    oled1.drawFastHLine(0, 49, 128, SSD1306_WHITE);
    oled1.setCursor(0, 53);
    oled1.printf("BERAT: %.1f gram", state.weightTotal);
    oled1.display();
  }

  if (oled2Ok) {
    oled2.clearDisplay();
    oled2.setTextSize(1);
    oled2.setTextColor(SSD1306_WHITE);
    oled2.setCursor(0, 0);
    oled2.printf("FSM:%s | %s", state.fsm, DEMO_FAST_DELAY ? "DEMO30s" : "180s");
    oled2.drawFastHLine(0, 10, 128, SSD1306_WHITE);
    oled2.setCursor(0, 15);
    oled2.printf("PSU: %s | SoC: %d%%", state.mainsOk ? "OK" : "FAIL!", state.socPercent);
    oled2.setCursor(0, 27);
    oled2.printf("LED: %s", strcmp(state.fsm, "NORMAL") == 0 ? "HIJAU" : (strcmp(state.fsm, "WARN") == 0 ? "KUNING" : "MERAH"));
    oled2.setCursor(0, 39);
    if (WiFi.status() == WL_CONNECTED) oled2.printf("WiFi: OK (%d dBm)", WiFi.RSSI());
    else oled2.print("WiFi: RECONNECTING..");
    oled2.setCursor(0, 51);
    oled2.printf("MQTT: %s", mqtt.connected() ? "ONLINE (RPi5)" : "OFFLINE");
    oled2.display();
  }
}

void publishSensors() {
  JsonDocument doc;
  JsonObject root = doc.to<JsonObject>();

  JsonObject ntc = root["ntc"].to<JsonObject>();
  putNum(ntc, "temp", state.ntcTemp, 2);

  JsonObject sht = root["sht31"].to<JsonObject>();
  putNum(sht, "temp", state.shtTemp, 2);
  putNum(sht, "hum", state.shtHum, 2);

  JsonObject dh = root["dht11"].to<JsonObject>();
  putNum(dh, "temp", state.dhtTemp, 1);
  putNum(dh, "hum", state.dhtHum, 1);

  JsonObject heart = root["heart"].to<JsonObject>();
  if (state.heartContact && !isnan(state.heartBpm)) {
    putNum(heart, "bpm", state.heartBpm, 0);
  } else {
    heart["bpm"] = nullptr; // Sesuai aturan: saat sensor tidak menempel, angka BPM diabaikan
  }
  heart["contact"]   = state.heartContact;
  heart["ir"]        = state.heartSignal;
  heart["sensor_ok"] = true;

  JsonObject weight = root["weight"].to<JsonObject>();
  putNum(weight, "total_g", state.weightTotal, 1);
  putNum(weight, "left_g", state.weightLeft, 1);
  putNum(weight, "right_g", state.weightRight, 1);
  weight["sensor_ok"] = (lc1Ok && lc2Ok);

  JsonObject pwr = root["power"].to<JsonObject>();
  pwr["soc_pct"]  = state.socPercent;
  pwr["mains_ok"] = state.mainsOk;
  pwr["status"]   = state.mainsOk ? "PSU:OK" : "MAINS:FAIL";

  JsonObject alarm = root["alarm"].to<JsonObject>();
  alarm["fsm_state"]     = state.fsm;
  alarm["clinical_code"] = state.clinicalCode;
  alarm["buzzer_mode"]   = state.manualBtnRequired ? "MANUAL_BUTTON_REQUIRED" : "AUTO_STAGE_DEPENDENT";
  alarm["buzzer_muted"]  = (state.manualBtnRequired && state.manualBuzzerAck);
  alarm["led_green"]     = (strcmp(state.fsm, "NORMAL") == 0);
  alarm["led_yellow"]    = (strcmp(state.fsm, "WARN") == 0);
  alarm["led_red"]       = (strcmp(state.fsm, "CRITICAL") == 0);
  alarm["hr_high_delay_s"] = HR_HIGH_DELAY_MS / 1000;   // dashboard & fsm.py ikut angka ini
  alarm["baby_crying"]     = state.babyCrying;         // status tangisan yang dipakai ESP

  root["fsm_state"] = state.fsm;
  root["uptime_s"]  = millis() / 1000;
  root["rssi"]      = WiFi.RSSI();

  char buf[960];
  size_t n = serializeJson(doc, buf, sizeof(buf));
  mqtt.publish(TOPIC_SENSORS, (const uint8_t*)buf, n, false);
}

// Pesan masuk dari broker: status tangisan dari cry_service
void onMqttMessage(char* topic, byte* payload, unsigned int length) {
  if (strcmp(topic, TOPIC_CRY_STATE) == 0) {
    JsonDocument doc;
    if (deserializeJson(doc, payload, length)) return;   // JSON rusak: abaikan
    state.babyCrying = doc["crying"] | false;
  } else if (strcmp(topic, TOPIC_CRY_STATUS) == 0) {
    // cry_service mati / mic tidak ada -> status tangisan lama tidak bisa dipercaya
    if (length != 6 || memcmp(payload, "online", 6) != 0) state.babyCrying = false;
  }
}

void ensureWifi() {
  static uint32_t lastTry = 0;
  if (WiFi.status() == WL_CONNECTED) return;
  if (millis() - lastTry < 10000 && lastTry != 0) return;
  lastTry = millis();
  WiFi.disconnect();
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
}

void ensureMqtt() {
  static uint32_t lastTry = 0;
  if (mqtt.connected()) return;
  state.babyCrying = false;   // tanpa koneksi tidak ada info tangisan -> anggap tidak menangis
  if (WiFi.status() != WL_CONNECTED) return;
  if (millis() - lastTry < 5000 && lastTry != 0) return;
  lastTry = millis();
  if (mqtt.connect(MQTT_CLIENT_ID, MQTT_USER, MQTT_PASS, TOPIC_STATUS, 1, true, "offline")) {
    mqtt.publish(TOPIC_STATUS, "online", true);
    mqtt.subscribe(TOPIC_CRY_STATE);
    mqtt.subscribe(TOPIC_CRY_STATUS);
  }
}

void setup() {
  Serial.begin(115200);
  delay(500);

  rgbLed.begin();
  rgbLed.setPixelColor(0, rgbLed.Color(150, 0, 150));
  rgbLed.show();

  pinMode(BUTTON_MUTE_PIN, INPUT_PULLUP);
  pinMode(BUZZER_PIN, OUTPUT);
  pinMode(LED_GREEN_PIN, OUTPUT);
  pinMode(LED_YELLOW_PIN, OUTPUT);
  pinMode(LED_RED_PIN, OUTPUT);
  pinMode(MAINS_DETECT_PIN, INPUT_PULLUP);

  analogReadResolution(12);
  analogSetPinAttenuation(NTC_PIN, ADC_11db);
  analogSetPinAttenuation(PULSE_SENSOR_PIN, ADC_11db);
  analogSetPinAttenuation(POT_SOC_PIN, ADC_11db);

  // Jalankan pembacaan Sensor Detak Jantung "Love" 3-Pin (GPIO 7) di Core 1
  xTaskCreatePinnedToCore(pulseSensorTask, "pulse", 4096, nullptr, 2, nullptr, 1);

  // Bus I2C-0 (GPIO 8/9): SHT31 + OLED 1
  I2C_Bus0.begin(I2C0_SDA, I2C0_SCL, 100000);
  shtOk   = sht31.begin(0x44);
  oled1Ok = oled1.begin(SSD1306_SWITCHCAPVCC, 0x3C);

  // Bus I2C-1 (GPIO 10/11): OLED 2 (0x3C)
  I2C_Bus1.begin(I2C1_SDA, I2C1_SCL, 100000);
  oled2Ok = oled2.begin(SSD1306_SWITCHCAPVCC, 0x3C);

  dht.begin();
  scale1.begin(LC1_DT, LC1_SCK);
  scale2.begin(LC2_DT, LC2_SCK);
  if ((lc1Ok = scale1.wait_ready_timeout(1000))) { scale1.set_scale(cal_factor_1); scale1.tare(); }
  if ((lc2Ok = scale2.wait_ready_timeout(1000))) { scale2.set_scale(cal_factor_2); scale2.tare(); }

  WiFi.mode(WIFI_STA);
  WiFi.setAutoReconnect(true);
  mqtt.setServer(MQTT_HOST, MQTT_PORT);
  mqtt.setCallback(onMqttMessage);
  mqtt.setBufferSize(1024);
  mqtt.setKeepAlive(15);
  ensureWifi();
}

void loop() {
  ensureWifi();
  ensureMqtt();
  mqtt.loop();

  updateRgbStatus();
  checkMuteButton();
  updateBuzzer();

  static uint32_t lastRead = 0;
  if (millis() - lastRead >= SENSOR_READ_MS) {
    lastRead = millis();
    readAllSensors();
    updateOledDisplays();
  }

  static uint32_t lastPub = 0;
  if (millis() - lastPub >= PUBLISH_INTERVAL_MS) {
    lastPub = millis();
    if (mqtt.connected()) publishSensors();
  }
  delay(5);
}