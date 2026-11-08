// =========================================================================
//   SMART INCUBATOR - ESP32-S3 FULL INTEGRATED SENSOR & DISPLAY NODE
//   Hardware : ESP32-S3 DevKitC-1 (N16R8)
//   Indikator RGB (GPIO 48):
//     - MQTT Offline : Kedip Ungu
//     - MQTT Online  : Kedip Biru seling Kedip Hijau
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
// 1. KONFIGURASI JARINGAN & MQTT (RASPBERRY PI 5)
// -------------------------------------------------------------------------
#define WIFI_SSID           "Nama jaringan"
#define WIFI_PASSWORD       "Frathol23091"

#define MQTT_HOST           "10.223.183.158"    // IP Raspberry Pi 5
#define MQTT_PORT           1883
#define MQTT_USER           "esp32"
#define MQTT_PASS           "12345678"
#define MQTT_CLIENT_ID      "incubator-esp32s3"

#define TOPIC_SENSORS       "incubator/sensors"       // Publish JSON periodik
#define TOPIC_STATUS        "incubator/esp32/status"  // LWT online/offline
#define TOPIC_COMMAND       "incubator/command"       // Subscribe perintah ("tare")

#define SENSOR_READ_MS      1000                      // Update OLED & sensor tiap 1 detik
#define PUBLISH_INTERVAL_MS 5000                      // Kirim JSON ke MQTT tiap 5 detik

// -------------------------------------------------------------------------
// 2. PEMETAAN PIN HARDWARE ESP32-S3
// -------------------------------------------------------------------------
#define I2C0_SDA            8
#define I2C0_SCL            9

#define I2C1_SDA            10
#define I2C1_SCL            11

#define DHT_PIN             4
#define DHT_TYPE            DHT11

#define LC1_DT              16
#define LC1_SCK             17
#define LC2_DT              18
#define LC2_SCK             21

#define POT_SOC_PIN         1   // ADC1_CH0 untuk pembacaan % baterai (SoC)
#define MAINS_DETECT_PIN    2   // HIGH = PSU:OK, LOW (GND) = MAINS:FAIL

#define NEOPIXEL_PIN        48
#define NUM_PIXELS          1

#define SCREEN_WIDTH        128
#define SCREEN_HEIGHT       64
#define OLED_RESET          -1

// -------------------------------------------------------------------------
// 3. INISIALISASI OBJEK & VARIABEL GLOBAL
// -------------------------------------------------------------------------
WiFiClient        wifiClient;
PubSubClient      mqtt(wifiClient);

TwoWire           I2C_Bus0 = TwoWire(0);
TwoWire           I2C_Bus1 = TwoWire(1);

Adafruit_SHT31    sht31    = Adafruit_SHT31(&I2C_Bus0);
Adafruit_SSD1306  oled1(SCREEN_WIDTH, SCREEN_HEIGHT, &I2C_Bus0, OLED_RESET);
Adafruit_SSD1306  oled2(SCREEN_WIDTH, SCREEN_HEIGHT, &I2C_Bus1, OLED_RESET);

DHT               dht(DHT_PIN, DHT_TYPE);
HX711             scale1;
HX711             scale2;
Adafruit_NeoPixel rgbLed(NUM_PIXELS, NEOPIXEL_PIN, NEO_GRB + NEO_KHZ800);

bool shtOk   = false;
bool oled1Ok = false;
bool oled2Ok = false;
bool lc1Ok   = false;
bool lc2Ok   = false;

float cal_factor_1 = -226.34;
float cal_factor_2 =  225.66;

struct IncubatorState {
  float shtTemp     = NAN;
  float shtHum      = NAN;
  float dhtTemp     = NAN;
  float dhtHum      = NAN;
  float weightLeft  = 0.0;
  float weightRight = 0.0;
  float weightTotal = 0.0;
  int   socPercent  = 100;
  bool  mainsOk     = true;
  const char* fsm   = "NORMAL";
} state;

// -------------------------------------------------------------------------
// 4. HELPER JSON & INDIKATOR LED RGB BERKEDIP
// -------------------------------------------------------------------------
static void putNum(JsonObject obj, const char* key, float v, int decimals = 2) {
  if (isnan(v)) { obj[key] = nullptr; return; }
  float p = powf(10.0f, decimals);
  obj[key] = roundf(v * p) / p;
}

void updateRgbStatus() {
  static uint32_t lastBlink = 0;
  static uint8_t  step      = 0;

  // Berganti fase kedipan setiap 350 ms
  if (millis() - lastBlink < 350) return;
  lastBlink = millis();

  step = (step + 1) % 4; // Siklus: 0 -> 1 -> 2 -> 3

  bool isOnline = (WiFi.status() == WL_CONNECTED && mqtt.connected());

  if (!isOnline) {
    // JIKA MQTT MASIH OFFLINE: Kedip UNGU
    if (step == 0 || step == 2) {
      rgbLed.setPixelColor(0, rgbLed.Color(150, 0, 150)); // Ungu menyala
    } else {
      rgbLed.clear(); // Mati
    }
  } else {
    // JIKA MQTT ONLINE: Kedip BIRU seling Kedip HIJAU
    if (step == 0) {
      rgbLed.setPixelColor(0, rgbLed.Color(0, 0, 180));   // Biru menyala
    } else if (step == 2) {
      rgbLed.setPixelColor(0, rgbLed.Color(0, 180, 0));   // Hijau menyala
    } else {
      rgbLed.clear(); // Mati (jeda di antara kedipan)
    }
  }

  rgbLed.show();
}

// -------------------------------------------------------------------------
// 5. FUNGSI TARE & KALIBRASI LOAD CELL
// -------------------------------------------------------------------------
void tareLoadCells() {
  Serial.println("\n>>> [TARE] Menolkan kedua Load Cell...");
  if (lc1Ok) scale1.tare();
  if (lc2Ok) scale2.tare();
  Serial.println(">>> [TARE] Selesai! Berat baki direset ke 0.0 g\n");
}

void calibrateLoadCellsInteractive() {
  Serial.println("\n==========================================");
  Serial.println("     MODE KALIBRASI INTERAKTIF LOAD CELL");
  Serial.println("==========================================");
  Serial.println("1. Kosongkan baki inkubator...");
  scale1.set_scale(1.0f);
  scale2.set_scale(1.0f);
  scale1.tare();
  scale2.tare();
  Serial.println("2. Letakkan beban referensi (contoh: 200 gram).");
  Serial.println("3. Ketik berat beban (dalam gram) di Serial Monitor lalu Enter:");

  unsigned long startWait = millis();
  while (Serial.available() == 0) {
    updateRgbStatus(); // Tetap jalankan kedipan LED selama menunggu input
    if (millis() - startWait > 30000) {
      Serial.println("[TIMEOUT] Kalibrasi dibatalkan.");
      scale1.set_scale(cal_factor_1);
      scale2.set_scale(cal_factor_2);
      return;
    }
    delay(10);
  }

  float knownWeight = Serial.parseFloat();
  while (Serial.available() > 0) Serial.read();

  if (knownWeight <= 0) {
    Serial.println("[BATAL] Input berat tidak valid.");
    scale1.set_scale(cal_factor_1);
    scale2.set_scale(cal_factor_2);
    return;
  }

  long raw1 = scale1.read_average(15);
  long raw2 = scale2.read_average(15);

  cal_factor_1 = (float)raw1 / knownWeight;
  cal_factor_2 = (float)raw2 / knownWeight;

  scale1.set_scale(cal_factor_1);
  scale2.set_scale(cal_factor_2);

  Serial.printf("[SUKSES] Faktor Kalibrasi Baru -> LC1: %.2f | LC2: %.2f\n\n", cal_factor_1, cal_factor_2);
}

// -------------------------------------------------------------------------
// 6. PEMBACAAN SENSOR & EVALUASI FSM
// -------------------------------------------------------------------------
void readAllSensors() {
  // 1. SHT31
  if (!shtOk) shtOk = sht31.begin(0x44);
  if (shtOk) {
    state.shtTemp = sht31.readTemperature();
    state.shtHum  = sht31.readHumidity();
  } else {
    state.shtTemp = NAN;
    state.shtHum  = NAN;
  }

  // 2. DHT11
  state.dhtTemp = dht.readTemperature();
  state.dhtHum  = dht.readHumidity();

  // 3. Load Cell 1 & 2
  if (lc1Ok && scale1.is_ready()) {
    state.weightLeft = scale1.get_units(2);
  }
  if (lc2Ok && scale2.is_ready()) {
    state.weightRight = scale2.get_units(2);
  }
  state.weightTotal = (state.weightLeft + state.weightRight) / 2.0f;
  if (abs(state.weightTotal) < 0.5f) state.weightTotal = 0.0f;

  // 4. Potensiometer Baterai (SoC %) & Jumper Listrik Utama (GPIO 2)
  uint32_t mv = analogReadMilliVolts(POT_SOC_PIN);
  state.socPercent = constrain(map(mv, 0, 3100, 0, 100), 0, 100);
  state.mainsOk    = (digitalRead(MAINS_DETECT_PIN) == HIGH);

  // 5. Evaluasi Logika FSM Lokal
  float refTemp = !isnan(state.shtTemp) ? state.shtTemp : state.dhtTemp;
  if ((!isnan(refTemp) && (refTemp > 37.5f || refTemp < 31.5f)) || (!state.mainsOk && state.socPercent < 20)) {
    state.fsm = "CRITICAL";
  } else if (!state.mainsOk || (!isnan(refTemp) && (refTemp > 36.8f || refTemp < 32.5f)) || state.socPercent < 35) {
    state.fsm = "WARN";
  } else {
    state.fsm = "NORMAL";
  }
}

// -------------------------------------------------------------------------
// 7. UPDATE TAMPILAN 2x LAYAR OLED
// -------------------------------------------------------------------------
void updateOledDisplays() {
  if (oled1Ok) {
    oled1.clearDisplay();
    oled1.setTextSize(1);
    oled1.setTextColor(SSD1306_WHITE);

    oled1.setCursor(0, 0);
    oled1.printf("INKUBATOR [%s]", state.fsm);
    oled1.drawFastHLine(0, 10, 128, SSD1306_WHITE);

    oled1.setCursor(0, 15);
    if (!isnan(state.shtTemp)) {
      oled1.printf("SHT31: %.1fC | %.1f%%", state.shtTemp, state.shtHum);
    } else {
      oled1.print("SHT31: TIDAK AKTIF");
    }

    oled1.setCursor(0, 27);
    if (!isnan(state.dhtTemp)) {
      oled1.printf("DHT11: %.1fC | %.1f%%", state.dhtTemp, state.dhtHum);
    } else {
      oled1.print("DHT11: TIDAK AKTIF");
    }

    oled1.drawFastHLine(0, 39, 128, SSD1306_WHITE);
    oled1.setCursor(0, 44);
    oled1.print("BERAT BAKI:");
    oled1.setCursor(0, 54);
    oled1.printf(">> %.1f gram <<", state.weightTotal);

    oled1.display();
  }

  if (oled2Ok) {
    oled2.clearDisplay();
    oled2.setTextSize(1);
    oled2.setTextColor(SSD1306_WHITE);

    oled2.setCursor(0, 0);
    oled2.print("POWER & TELEMETRI S3");
    oled2.drawFastHLine(0, 10, 128, SSD1306_WHITE);

    oled2.setCursor(0, 15);
    oled2.printf("SUMBER: %s", state.mainsOk ? "PSU:OK (AC)" : "MAINS:FAIL!");

    oled2.setCursor(0, 27);
    oled2.printf("BATERAI SoC: %d%%", state.socPercent);

    oled2.setCursor(0, 39);
    if (WiFi.status() == WL_CONNECTED) {
      oled2.printf("WiFi: OK (%d dBm)", WiFi.RSSI());
    } else {
      oled2.print("WiFi: RECONNECTING..");
    }

    oled2.setCursor(0, 51);
    oled2.printf("MQTT: %s", mqtt.connected() ? "ONLINE (RPi5)" : "OFFLINE");

    oled2.display();
  }
}

// -------------------------------------------------------------------------
// 8. KONEKSI WIFI, MQTT & PUBLISH JSON
// -------------------------------------------------------------------------
void mqttCallback(char* topic, byte* payload, unsigned int length) {
  String msg;
  for (unsigned int i = 0; i < length; i++) msg += (char)payload[i];
  msg.trim();
  Serial.printf("[MQTT CMD] Topik: %s | Pesan: %s\n", topic, msg.c_str());

  if (msg.equalsIgnoreCase("tare")) {
    tareLoadCells();
  }
}

void ensureWifi() {
  static uint32_t lastTry = 0;
  if (WiFi.status() == WL_CONNECTED) return;
  if (millis() - lastTry < 10000 && lastTry != 0) return;
  lastTry = millis();

  Serial.printf("[WiFi] Menghubungkan ke %s ...\n", WIFI_SSID);
  WiFi.disconnect();
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
}

void ensureMqtt() {
  static uint32_t lastTry = 0;
  if (WiFi.status() != WL_CONNECTED || mqtt.connected()) return;
  if (millis() - lastTry < 5000 && lastTry != 0) return;
  lastTry = millis();

  Serial.printf("[MQTT] Menghubungkan ke %s:%d ...\n", MQTT_HOST, MQTT_PORT);
  if (mqtt.connect(MQTT_CLIENT_ID, MQTT_USER, MQTT_PASS,
                   TOPIC_STATUS, 1, true, "offline")) {
    Serial.println("[MQTT] Terhubung ke Broker Raspberry Pi 5!");
    mqtt.publish(TOPIC_STATUS, "online", true);
    mqtt.subscribe(TOPIC_COMMAND);
  } else {
    Serial.printf("[MQTT] Gagal terhubung, rc=%d\n", mqtt.state());
  }
}

void publishSensors() {
  JsonDocument doc;
  JsonObject root = doc.to<JsonObject>();

  JsonObject sht = root["sht31"].to<JsonObject>();
  putNum(sht, "temp", state.shtTemp, 2);
  putNum(sht, "hum", state.shtHum, 2);

  JsonObject dh = root["dht11"].to<JsonObject>();
  putNum(dh, "temp", state.dhtTemp, 1);
  putNum(dh, "hum", state.dhtHum, 1);

  JsonObject weight = root["weight"].to<JsonObject>();
  putNum(weight, "total_g", state.weightTotal, 1);
  putNum(weight, "left_g", state.weightLeft, 1);
  putNum(weight, "right_g", state.weightRight, 1);
  weight["sensor_ok"] = (lc1Ok && lc2Ok);

  JsonObject pwr = root["power"].to<JsonObject>();
  pwr["soc_pct"]  = state.socPercent;
  pwr["mains_ok"] = state.mainsOk;
  pwr["status"]   = state.mainsOk ? "PSU:OK" : "MAINS:FAIL";

  root["fsm_state"] = state.fsm;
  root["uptime_s"]  = millis() / 1000;
  root["rssi"]      = WiFi.RSSI();

  char buf[640];
  size_t n = serializeJson(doc, buf, sizeof(buf));
  bool ok = mqtt.publish(TOPIC_SENSORS, (const uint8_t*)buf, n, false);
  Serial.printf("[PUB %s] %s\n", ok ? "OK" : "GAGAL", buf);
}

// -------------------------------------------------------------------------
// 9. INISIALISASI HARDWARE & SETUP
// -------------------------------------------------------------------------
void initHardware() {
  rgbLed.begin();
  rgbLed.setPixelColor(0, rgbLed.Color(150, 0, 150)); // Ungu awal saat boot
  rgbLed.show();

  analogReadResolution(12);
  analogSetPinAttenuation(POT_SOC_PIN, ADC_11db);
  pinMode(MAINS_DETECT_PIN, INPUT_PULLUP);

  I2C_Bus0.begin(I2C0_SDA, I2C0_SCL, 100000);
  shtOk = sht31.begin(0x44);
  Serial.printf("[SHT31]  %s\n", shtOk ? "OK (0x44)" : "TIDAK TERDETEKSI");

  oled1Ok = oled1.begin(SSD1306_SWITCHCAPVCC, 0x3C);
  Serial.printf("[OLED 1] %s\n", oled1Ok ? "OK (Bus I2C-0, 0x3C)" : "TIDAK TERDETEKSI");

  I2C_Bus1.begin(I2C1_SDA, I2C1_SCL, 100000);
  oled2Ok = oled2.begin(SSD1306_SWITCHCAPVCC, 0x3C);
  Serial.printf("[OLED 2] %s\n", oled2Ok ? "OK (Bus I2C-1, 0x3C)" : "TIDAK TERDETEKSI");

  dht.begin();
  Serial.println("[DHT11]  Diinisialisasi pada GPIO 4");

  scale1.begin(LC1_DT, LC1_SCK);
  scale2.begin(LC2_DT, LC2_SCK);

  lc1Ok = scale1.wait_ready_timeout(1000);
  if (lc1Ok) {
    scale1.set_scale(cal_factor_1);
    scale1.tare();
    Serial.println("[LC 1]   OK & Di-tare (GPIO 16/17)");
  } else {
    Serial.println("[LC 1]   TIDAK TERDETEKSI");
  }

  lc2Ok = scale2.wait_ready_timeout(1000);
  if (lc2Ok) {
    scale2.set_scale(cal_factor_2);
    scale2.tare();
    Serial.println("[LC 2]   OK & Di-tare (GPIO 18/21)");
  } else {
    Serial.println("[LC 2]   TIDAK TERDETEKSI");
  }
}

void setup() {
  Serial.begin(115200);
  delay(500);
  Serial.println("\n=======================================================");
  Serial.println("   SMART INCUBATOR - ESP32-S3 INTEGRATED SENSOR NODE");
  Serial.println("   Ketik 't' + Enter di Serial Monitor untuk TARE");
  Serial.println("   Ketik 'c' + Enter di Serial Monitor untuk KALIBRASI");
  Serial.println("=======================================================\n");

  initHardware();

  WiFi.mode(WIFI_STA);
  WiFi.setAutoReconnect(true);
  mqtt.setServer(MQTT_HOST, MQTT_PORT);
  mqtt.setBufferSize(768);
  mqtt.setKeepAlive(15);
  mqtt.setCallback(mqttCallback);

  ensureWifi();
}

// -------------------------------------------------------------------------
// 10. MAIN LOOP (NON-BLOCKING)
// -------------------------------------------------------------------------
void loop() {
  ensureWifi();
  ensureMqtt();
  mqtt.loop();

  // Jalankan efek kedipan lampu RGB berdasarkan status MQTT
  updateRgbStatus();

  // Cek perintah interaktif dari Serial Monitor ('t' untuk Tare, 'c' untuk Kalibrasi)
  if (Serial.available() > 0) {
    char cmd = Serial.read();
    while (Serial.available() > 0) Serial.read();
    if (cmd == 't' || cmd == 'T') {
      tareLoadCells();
    } else if (cmd == 'c' || cmd == 'C') {
      calibrateLoadCellsInteractive();
    }
  }

  // Baca sensor & perbarui tampilan kedua OLED setiap 1 detik
  static uint32_t lastRead = 0;
  if (millis() - lastRead >= SENSOR_READ_MS) {
    lastRead = millis();
    readAllSensors();
    updateOledDisplays();
  }

  // Publish paket JSON ke MQTT setiap 5 detik
  static uint32_t lastPub = 0;
  if (millis() - lastPub >= PUBLISH_INTERVAL_MS) {
    lastPub = millis();
    if (mqtt.connected()) {
      publishSensors();
    } else {
      Serial.printf("[LOCAL] SHT:%.1fC | DHT:%.1fC | Berat:%.1fg | SoC:%d%% | %s\n",
                    state.shtTemp, state.dhtTemp, state.weightTotal,
                    state.socPercent, state.mainsOk ? "PSU:OK" : "MAINS:FAIL");
    }
  }

  delay(5);
}