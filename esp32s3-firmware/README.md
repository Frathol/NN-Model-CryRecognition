# ESP32-S3 Incubator Firmware

Firmware mikrokontroler **ESP32-S3 (DevKitC-1)** untuk akuisisi sensor klinis, kontrol pemanas (PID/SSR), alarm lokal, serta pengiriman telemetri real-time melalui protokol MQTT.

## Fitur Utama
- **Dual-I2C Architecture**:
  - `Wire` (I2C0 - GPIO8/GPIO9): Sensor suhu & kelembapan ruang inkubator **SHT31**.
  - `Wire1` (I2C1 - GPIO17/GPIO18): Pulse oximeter / detak jantung **MAX30102** pada task FreeRTOS tersendiri (`heartRateTask`, 100 Hz) agar pembacaan detak jantung tidak terblokir oleh sensor lain.
- **Suhu Kulit Bayi (Skin Probe)**:
  - Menggunakan termistor **NTC 10kΩ** pada **GPIO1 (ADC1)** dengan persamaan Steinhart-Hart / Beta dan oversampling.
- **Sensor Cadangan & Lingkungan**:
  - **DHT11** pada **GPIO4**.
- **Timbangan Bayi Digital**:
  - Modul **HX711 + Load Cell** pada **GPIO5 (DT)** dan **GPIO6 (SCK)**.
- **Sistem Keamanan & Heater Cut-Off**:
  - Memantau suhu berlebih (*over-temperature*) dan memutus pemanas secara otomatis (`alarm.heater_cutoff`).

## Tabel Pinout ESP32-S3

| Komponen | Pin Modul | Pin ESP32-S3 | Keterangan |
| :--- | :--- | :--- | :--- |
| **SHT31** | SDA / SCL | GPIO8 / GPIO9 | Bus I2C0 |
| **MAX30102** | SDA / SCL | GPIO17 / GPIO18 | Bus I2C1 (Dedicated Task) |
| **NTC 10k** | Divider Out | GPIO1 | Wajib ADC1 karena WiFi aktif |
| **DHT11** | DATA | GPIO4 | Pull-up 10 kΩ ke 3.3V |
| **HX711** | DT / SCK | GPIO5 / GPIO6 | Load cell berat bayi |

## Build & Upload (PlatformIO)

1. Sesuaikan kredensial WiFi dan IP broker MQTT di `include/config.h`.
2. Jalankan perintah kompilasi dan unggah:
   ```bash
   pio run -t upload
   pio device monitor -b 115200
   ```
