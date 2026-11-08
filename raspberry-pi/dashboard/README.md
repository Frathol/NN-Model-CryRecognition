# Clinical Web Dashboard (`raspberry-pi/dashboard`)

Antarmuka web pemantauan real-time untuk **Smart Neonatal Incubator** berbasis HTML5, CSS3, Chart.js, dan MQTT over WebSockets (`MQTT.js`).

## Struktur Berkas
- `index.html`: Halaman utama pemantauan klinis inkubator, grafik tren multi-sensor, panel kamera, serta indikator alarm & *Code Blue*.
- `login.html` & `login.js`: Halaman autentikasi petugas medis sebelum mengakses dashboard.
- `auth.js`: Guard sesi autentikasi di sisi klien.
- `config.js`: Konfigurasi koneksi WebSocket MQTT dan ambang batas klinis (*alarm thresholds*).
- `app.js`: Logika langganan (*subscriber*) MQTT, pembaruan DOM, peringatan suara (*Web Audio API*), grafik real-time, dan sinkronisasi status FSM.
- `style.css`: Desain visual bertema klinis terang (*Clinical Light Theme*) dengan indikator warna status normal, peringatan (*warning*), dan darurat (*alarm / Code Blue*).

## Fitur Pemantauan
1. **Suhu & Kelembapan Ruang (SHT31 & DHT11)**
2. **Suhu Kulit Bayi (NTC Skin Probe)**
3. **Detak Jantung (MAX30102 BPM & Status Kontak Jari/Kulit)**
4. **Berat Badan Bayi (HX711 Load Cell dalam gram)**
5. **Klasifikasi Tangisan Bayi 2-Tahap (Cry Detection & Alasan Tangisan)**
6. **Status Pemanas, Daya, & FSM Clinical State (`NORMAL`, `CRY_WATCH`, `PRE_ALARM`, `ALARM`, `THERMAL_CUTOFF`)**
7. **Live Camera Stream Monitoring**
