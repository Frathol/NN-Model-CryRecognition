// =====================================================================
//  Smart Incubator - ESP32-S3 sensor node
//  Sensor : NTC (ADC), SHT31 (I2C0), DHT11 (1-wire), MAX30102 (I2C1)
//  Output : JSON ke MQTT topic incubator/sensors setiap PUBLISH_INTERVAL_MS
// =====================================================================
#include <Arduino.h>
#include <WiFi.h>
#include <Wire.h>
#include <PubSubClient.h>
#include <ArduinoJson.h>
#include <Adafruit_SHT31.h>
#include <DHT.h>
#include <MAX30105.h>     // SparkFun library, kompatibel MAX30102
#include <heartRate.h>    // algoritma deteksi detak (PBA) dari SparkFun

#include "config.h"

// ---------------------------------------------------------------------
WiFiClient     wifiClient;
PubSubClient   mqtt(wifiClient);
Adafruit_SHT31 sht31(&Wire);
DHT            dht(DHT_PIN, DHT_TYPE);
MAX30105       pulseSensor;

bool shtOk = false;
bool hrOk  = false;

// Data detak jantung dibagi antara task HR dan loop() -> dilindungi spinlock
portMUX_TYPE hrMux = portMUX_INITIALIZER_UNLOCKED;
struct HeartData {
  float    bpm      = NAN;   // rata-rata BPM
  uint32_t ir       = 0;     // nilai IR terakhir (indikator kontak)
  bool     contact  = false; // sensor menempel di kulit
} hrShared;

// ---------------------------------------------------------------------
//  Helper JSON: NaN -> null, dibulatkan
// ---------------------------------------------------------------------
static void putNum(JsonObject obj, const char* key, float v, int decimals = 2) {
  if (isnan(v)) { obj[key] = nullptr; return; }
  float p = powf(10.0f, decimals);
  obj[key] = roundf(v * p) / p;
}

// ---------------------------------------------------------------------
//  NTC: rata-rata ADC (mV terkalibrasi) -> resistansi -> suhu (persamaan Beta)
// ---------------------------------------------------------------------
float readNtcCelsius() {
  uint32_t sum = 0;
  for (int i = 0; i < NTC_SAMPLES; i++) {
    sum += analogReadMilliVolts(NTC_PIN);
    delayMicroseconds(200);
  }
  float mv = (float)sum / NTC_SAMPLES;
  if (mv <= 10.0f || mv >= NTC_VCC_MV - 10.0f) return NAN;   // terbuka / short

  float rNtc   = NTC_R_SERIES * mv / (NTC_VCC_MV - mv);
  float invT   = 1.0f / (NTC_T_NOMINAL + 273.15f) + logf(rNtc / NTC_R_NOMINAL) / NTC_BETA;
  return 1.0f / invT - 273.15f;
}

// ---------------------------------------------------------------------
//  Task detak jantung: membaca FIFO MAX30102 terus-menerus (~100 Hz).
//  Interval detak dihitung dari indeks sampel, bukan millis(), sehingga
//  tetap akurat walau sampel dibaca per-batch dari FIFO.
// ---------------------------------------------------------------------
void heartRateTask(void*) {
  float    rates[HR_AVG_SIZE] = {0};
  uint8_t  rateIdx = 0, rateCount = 0;
  uint32_t sampleIdx = 0, lastBeatIdx = 0;

  for (;;) {
    pulseSensor.check();                       // tarik sampel baru dari FIFO
    while (pulseSensor.available()) {
      uint32_t ir = pulseSensor.getFIFOIR();
      pulseSensor.nextSample();
      sampleIdx++;

      bool contact = ir >= HR_IR_FINGER_THRESHOLD;
      if (!contact) {
        rateCount = 0; rateIdx = 0; lastBeatIdx = 0;
      } else if (checkForBeat((long)ir)) {
        if (lastBeatIdx != 0) {
          float sec = (float)(sampleIdx - lastBeatIdx) / HR_SAMPLE_RATE_HZ;
          float bpm = 60.0f / sec;
          if (bpm > 40.0f && bpm < 250.0f) {   // rentang neonatus bisa >180
            rates[rateIdx] = bpm;
            rateIdx = (rateIdx + 1) % HR_AVG_SIZE;
            if (rateCount < HR_AVG_SIZE) rateCount++;
          }
        }
        lastBeatIdx = sampleIdx;
      }

      // Tidak ada detak > 5 detik -> anggap data tidak valid
      bool stale = lastBeatIdx == 0 ||
                   (sampleIdx - lastBeatIdx) > (uint32_t)(5 * HR_SAMPLE_RATE_HZ);

      float avg = NAN;
      if (contact && !stale && rateCount > 0) {
        float s = 0; for (uint8_t i = 0; i < rateCount; i++) s += rates[i];
        avg = s / rateCount;
      }

      portENTER_CRITICAL(&hrMux);
      hrShared.bpm = avg; hrShared.ir = ir; hrShared.contact = contact;
      portEXIT_CRITICAL(&hrMux);
    }
    vTaskDelay(pdMS_TO_TICKS(10));
  }
}

// ---------------------------------------------------------------------
//  WiFi & MQTT (non-blocking reconnect)
// ---------------------------------------------------------------------
void ensureWifi() {
  static uint32_t lastTry = 0;
  if (WiFi.status() == WL_CONNECTED) return;
  if (millis() - lastTry < 10000 && lastTry != 0) return;
  lastTry = millis();
  Serial.printf("[WiFi] menghubungkan ke %s ...\n", WIFI_SSID);
  WiFi.disconnect();
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
}

void ensureMqtt() {
  static uint32_t lastTry = 0;
  if (WiFi.status() != WL_CONNECTED || mqtt.connected()) return;
  if (millis() - lastTry < 5000 && lastTry != 0) return;
  lastTry = millis();

  Serial.printf("[MQTT] menghubungkan ke %s:%d ...\n", MQTT_HOST, MQTT_PORT);
  // Last Will: broker otomatis publish "offline" jika ESP32 putus
  if (mqtt.connect(MQTT_CLIENT_ID, MQTT_USER, MQTT_PASS,
                   TOPIC_STATUS, 1, true, "offline")) {
    Serial.println("[MQTT] terhubung");
    mqtt.publish(TOPIC_STATUS, "online", true);
  } else {
    Serial.printf("[MQTT] gagal, rc=%d\n", mqtt.state());
  }
}

// ---------------------------------------------------------------------
void initSensors() {
  // SHT31 di bus I2C #0
  Wire.begin(I2C0_SDA, I2C0_SCL, 100000);
  shtOk = sht31.begin(0x44);
  Serial.printf("[SHT31] %s\n", shtOk ? "OK" : "TIDAK TERDETEKSI (cek alamat 0x44/0x45)");

  // DHT11
  dht.begin();

  // NTC
  analogReadResolution(12);
  analogSetPinAttenuation(NTC_PIN, ADC_11db);   // rentang ~0-3.1 V

  // MAX30102 di bus I2C #1
  Wire1.begin(I2C1_SDA, I2C1_SCL, 400000);
  hrOk = pulseSensor.begin(Wire1, I2C_SPEED_FAST);
  if (hrOk) {
    // ledBrightness, sampleAverage, ledMode(2=Red+IR), sampleRate, pulseWidth, adcRange
    pulseSensor.setup(0x1F, 4, 2, 400, 411, 4096);
    xTaskCreatePinnedToCore(heartRateTask, "hr", 4096, nullptr, 2, nullptr, 1);
  }
  Serial.printf("[MAX30102] %s\n", hrOk ? "OK" : "TIDAK TERDETEKSI");
}

void publishSensors() {
  float ntcT  = readNtcCelsius();

  float shtT = NAN, shtH = NAN;
  if (!shtOk) shtOk = sht31.begin(0x44);       // coba lagi jika sempat lepas
  if (shtOk) { shtT = sht31.readTemperature(); shtH = sht31.readHumidity(); }

  float dhtT = dht.readTemperature();
  float dhtH = dht.readHumidity();

  HeartData hr;
  portENTER_CRITICAL(&hrMux);
  hr = hrShared;
  portEXIT_CRITICAL(&hrMux);

  JsonDocument doc;
  JsonObject root = doc.to<JsonObject>();
  JsonObject ntc  = root["ntc"].to<JsonObject>();
  putNum(ntc, "temp", ntcT);

  JsonObject sht  = root["sht31"].to<JsonObject>();
  putNum(sht, "temp", shtT); putNum(sht, "hum", shtH);

  JsonObject dh   = root["dht11"].to<JsonObject>();
  putNum(dh, "temp", dhtT, 1); putNum(dh, "hum", dhtH, 1);

  JsonObject heart = root["heart"].to<JsonObject>();
  putNum(heart, "bpm", hr.bpm, 0);
  heart["contact"] = hr.contact;
  heart["ir"]      = hr.ir;
  heart["sensor_ok"] = hrOk;

  root["uptime_s"] = millis() / 1000;
  root["rssi"]     = WiFi.RSSI();

  char buf[512];
  size_t n = serializeJson(doc, buf, sizeof(buf));
  bool ok = mqtt.publish(TOPIC_SENSORS, (const uint8_t*)buf, n, false);
  Serial.printf("[PUB %s] %s\n", ok ? "ok" : "GAGAL", buf);
}

// ---------------------------------------------------------------------
void setup() {
  Serial.begin(115200);
  delay(500);
  Serial.println("\n=== Smart Incubator - ESP32-S3 ===");

  WiFi.mode(WIFI_STA);
  WiFi.setAutoReconnect(true);
  mqtt.setServer(MQTT_HOST, MQTT_PORT);
  mqtt.setBufferSize(768);
  mqtt.setKeepAlive(15);

  initSensors();
  ensureWifi();
}

void loop() {
  ensureWifi();
  ensureMqtt();
  mqtt.loop();

  static uint32_t lastPub = 0;
  if (millis() - lastPub >= PUBLISH_INTERVAL_MS) {
    lastPub = millis();
    if (mqtt.connected()) publishSensors();
  }
  delay(5);
}
