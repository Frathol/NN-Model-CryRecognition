#pragma once
// =====================================================================
//  Konfigurasi node sensor Smart Incubator (ESP32-S3)
//  Ubah nilai di file ini sesuai jaringan & wiring Anda.
// =====================================================================

// ---------- WiFi ----------
#define WIFI_SSID       "Nama jaringan"
#define WIFI_PASSWORD   "Frathol23091"

// ---------- MQTT (Mosquitto di Raspberry Pi 5) ----------
#define MQTT_HOST       "10.223.183.158"     // IP Raspberry Pi (disarankan IP statis)
#define MQTT_PORT       1883
#define MQTT_USER       "esp32"
#define MQTT_PASS       "12345678"
#define MQTT_CLIENT_ID  "incubator-esp32s3"

#define TOPIC_SENSORS   "incubator/sensors"       // data JSON periodik
#define TOPIC_STATUS    "incubator/esp32/status"  // online / offline (retained + LWT)

#define PUBLISH_INTERVAL_MS 2000   // DHT11 minimal 1 detik antar pembacaan

// ---------- Pin (ESP32-S3-DevKitC-1) ----------
// Bus I2C #0 -> SHT31
#define I2C0_SDA        8
#define I2C0_SCL        9
// Bus I2C #1 -> MAX30102 (bus terpisah agar task detak jantung tidak berebut bus)
#define I2C1_SDA        17
#define I2C1_SCL        18

#define DHT_PIN         4
#define DHT_TYPE        DHT11

// NTC harus di pin ADC1 (GPIO1..GPIO10). ADC2 tidak dapat dipakai bersamaan WiFi.
#define NTC_PIN         1

// ---------- Parameter NTC ----------
// Wiring:  3V3 ── R_SERIES ──┬── NTC ── GND
//                            └── NTC_PIN (ADC)
#define NTC_R_SERIES    10000.0f   // resistor seri (ohm)
#define NTC_R_NOMINAL   10000.0f   // resistansi NTC pada 25°C (ohm)
#define NTC_T_NOMINAL   25.0f      // °C
#define NTC_BETA        3950.0f    // koefisien Beta dari datasheet NTC
#define NTC_VCC_MV      3300.0f    // tegangan supply divider (mV)
#define NTC_SAMPLES     16         // jumlah sampel dirata-rata

// ---------- Parameter MAX30102 ----------
#define HR_IR_FINGER_THRESHOLD 50000   // nilai IR di bawah ini = sensor tidak menempel
#define HR_AVG_SIZE            4       // rata-rata BPM dari N detak terakhir
#define HR_SAMPLE_RATE_HZ      100     // = sampleRate(400) / sampleAverage(4)

// ---------- Load cell + HX711 (berat bayi) ----------
// Wiring HX711: VCC -> 3V3, GND -> GND, DT -> HX711_DOUT_PIN, SCK -> HX711_SCK_PIN
// Kalibrasi lewat Serial Monitor (tidak perlu flash ulang, tersimpan di flash):
//   t        = tare / nol-kan (timbangan harus KOSONG)
//   c 500    = kalibrasi, setelah beban acuan 500 g diletakkan
//   w        = tampilkan nilai mentah, offset, faktor, dan berat
#define HX711_DOUT_PIN   5
#define HX711_SCK_PIN    6
#define WEIGHT_AVG_SIZE  10    // rata-rata N sampel terakhir (HX711 10 SPS = 1 detik)
