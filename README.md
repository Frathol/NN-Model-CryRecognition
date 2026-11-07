# NeoSentinel - Raspberry Pi Edge Node

Smart Neonatal Incubator Sentinel: sistem monitoring IoT untuk inkubator bayi dengan pengenalan tangisan.
Repositori ini berisi sisi **Raspberry Pi**: pendeteksi tangisan dari mikrofon (model CNN int8), mesin alarm
*Finite State Machine* (FSM), dan pencatat data eksperimen. Sensor suhu, kelembapan, berat, dan detak jantung
dibaca oleh ESP32-S3 yang mengirim data ke broker MQTT di Raspberry Pi yang sama.

> **Status:** purwarupa riset edukasi, bukan alat medis tersertifikasi. Sistem hanya **memonitor dan memberi
> peringatan**, tidak mengontrol pemanas. Keluaran model bersifat *advisory* dan tidak pernah memicu atau
> menghapus alarm sendiri; keputusan alarm ada di `fsm.py`.

## Arsitektur

```
[ESP32-S3: NTC, SHT, MAX30102, load cell] --Wi-Fi, MQTT--> Mosquitto (broker, Raspberry Pi)
[Mikrofon USB] --USB--> audio_detector.py (Raspberry Pi) --MQTT-->        |
                                                                          v
                                          fsm.py (subscribe semua topic, publish state)
                                          Node-RED / dashboard (subscribe)  --> browser perawat
                                          mqtt_logger.py (rekam CSV untuk eksperimen)
```

| Komponen | Peran MQTT |
|---|---|
| ESP32-S3 | Publisher: `env`, `bio`, status (last will) |
| `audio_detector.py` | Publisher: `audio` |
| Mosquitto | Broker (di Raspberry Pi, port 1883) |
| `fsm.py` | Subscriber semua topic, lalu publisher `state` |
| Dashboard (Node-RED) | Subscriber; publisher untuk perintah dari jaringan lokal |
| `mqtt_logger.py` | Subscriber, menulis CSV |

## Struktur Folder

```
.
├── models/
│   ├── stage1_cry_int8.tflite     # model tahap 1 (cry / non-cry / silence), terkuantisasi int8
│   └── stage1_norm.json           # mean, std, skala kuantisasi, ambang silence
├── features.py                    # ekstraksi log-mel (NumPy/SciPy), sama persis dengan saat training
├── audio_detector.py              # baca mikrofon -> model -> publish MQTT
├── fsm.py                         # mesin status alarm (sensor ESP32 + deteksi audio)
├── mqtt_logger.py                 # rekam data MQTT ke eksperimen_log.csv
├── requirements.txt
└── README.md
```

- `models/` berisi bobot yang **sudah dilatih**. Raspberry Pi tidak perlu melatih ulang.
- Model tahap 2 (alasan menangis, `stage2_*`) bersifat eksploratori dan tidak dipakai untuk alarm.
- File `.h` dan `.keras` tidak diperlukan di Raspberry Pi (`.h` hanya untuk firmware mikrokontroler, `.keras` untuk PC).

## Prasyarat

- Raspberry Pi dengan Raspberry Pi OS **64-bit**, adaptor daya yang memadai (Pi 5 membutuhkan 5 V/5 A).
- Mikrofon USB (matikan AGC/auto-gain dan kunci gain lewat `alsamixer`).
- Broker Mosquitto yang berjalan di Raspberry Pi (lihat bagian Broker MQTT).
- Python 3.10 atau lebih baru.

## Instalasi (di Raspberry Pi)

```bash
sudo apt-get update && sudo apt-get install -y libportaudio2 python3-venv
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

Contoh `requirements.txt` (cek ketersediaan paket untuk versi Python di Raspberry Pi Anda; pakai salah satu runtime TFLite):

```
numpy
scipy
soundfile
sounddevice
paho-mqtt>=2.0
ai-edge-litert        # atau tflite-runtime / tensorflow, tergantung yang tersedia di Raspberry Pi OS
```

Jika memakai `ai-edge-litert` atau `tflite-runtime`, ganti impor `tensorflow` di skrip menjadi runtime yang dipakai.

## Broker MQTT (Mosquitto)

Cara paling mudah: Docker.

```yaml
# docker-compose.yml
services:
  mosquitto:
    image: eclipse-mosquitto:2
    ports: ["1883:1883", "9001:9001"]
    volumes: ["./mosquitto:/mosquitto/config"]
    restart: unless-stopped
```

Isi minimal `mosquitto/mosquitto.conf` (Mosquitto 2.x tidak membuka koneksi eksternal tanpa `listener`):

```
listener 1883
allow_anonymous false
password_file /mosquitto/config/passwd
acl_file /mosquitto/config/acl
```

Buat akun per perangkat (`esp`, `audio_node`, `fsm`, `dashboard`) dengan `mosquitto_passwd`, dan batasi topic tiap
akun di file `acl`. Uji broker:

```bash
mosquitto_sub -h localhost -u fsm -P <password> -t "neo/#" -v
mosquitto_pub -h localhost -u esp -P <password> -t neo/inc01/env -m '{"t_air":33.1}'
```

## Topic dan Format Payload

Payload berformat JSON. Setiap pesan memuat `dev`, `ts` (epoch detik), dan `seq` (nomor urut, dipakai untuk menghitung
delivery ratio).

| Topic | Contoh payload | Publisher |
|---|---|---|
| `neo/inc01/env` | `{"dev":"esp","ts":1762400000,"seq":12,"t_air":33.1,"rh":62.0}` | ESP32 |
| `neo/inc01/bio` | `{"dev":"esp","ts":..,"seq":..,"t_skin":36.4,"hr":128,"weight_g":2450}` | ESP32 |
| `neo/inc01/audio` | `{"dev":"pi","ts":..,"seq":..,"cry":true,"p_cry":0.93,"rule":0.98,"db":-32.1}` | `audio_detector.py` |
| `neo/inc01/state` | `{"state":"CRYING_DISTRESS","active":["THERMAL_HAZARD"]}` (retained) | `fsm.py` |
| `neo/inc01/cmd` | `{"setpoint":33.0,"fed":true}` | Dashboard (jaringan lokal saja) |
| `neo/inc01/<node>/status` | `online` / `offline` (last will, retained) | Node / broker |

Perintah mute alarm dari jaringan **ditolak** oleh FSM; alarm in-cabin hanya bisa dimatikan dengan tombol fisik.

## Cara Menjalankan

Pastikan virtual environment aktif (`source .venv/bin/activate`) dan Mosquitto sudah menyala (port 1883).

### 1. Audio Detector (AI mikrofon)

```bash
# Jika mikrofon USB sudah terpasang:
python3 audio_detector.py

# Jika hanya tes dengan data buatan (tanpa mikrofon):
python3 audio_detector.py --simulate
```

Alur di dalamnya: rekam audio -> resample ke 8 kHz -> jendela 1 detik (geser 0,5 detik) -> log-mel 40 band
(`features.py`) -> model tahap 1 -> event tangisan jika rata-rata 3 jendela berturut-turut P(cry) >= 0,80
-> publish ke `neo/inc01/audio`.

### 2. FSM Alert Engine

Buka terminal atau tab baru:

```bash
python3 fsm.py
```

FSM berlangganan semua topic `neo/inc01/#`, menentukan satu state aktif berdasarkan prioritas, dan mem-publish hasilnya
ke `neo/inc01/state`.

### 3. Pencatat data eksperimen (E1, E2, E4, E5)

Buka terminal atau tab baru saat akan mengambil data untuk paper:

```bash
python3 mqtt_logger.py
```

Hasil otomatis tersimpan di `eksperimen_log.csv`. Beri nama file yang jelas untuk setiap uji (misalnya
`e5_delivery_30min.csv`) supaya mudah ditelusuri saat menyusun tabel.

## Kolaborasi di GitHub

- Satu orang mengurus satu area: `fsm.py` (alarm), `audio_detector.py` + `features.py` (audio), `mqtt_logger.py` (data).
- Alur: edit di laptop -> `git commit` -> `git push` -> di Raspberry Pi `git pull` -> jalankan ulang skrip yang berubah.
- Jangan mengubah `features.py` tanpa mengecek hasilnya: fitur harus identik dengan saat model dilatih, kalau tidak
  keluaran model menjadi tidak bermakna.
- Jangan meng-commit kata sandi MQTT. Simpan di file `.env` yang masuk `.gitignore`.

## Pemecahan Masalah

| Gejala | Cek |
|---|---|
| Mikrofon tidak terdeteksi | `arecord -l`, dan pastikan nomor perangkat di `sounddevice` benar (`python3 -m sounddevice`) |
| Nilai P(cry) selalu rendah atau tinggi | Level gain tidak konsisten (AGC aktif), atau ambang silence -50 dBFS perlu disesuaikan dengan mikrofon ini |
| Tidak ada pesan di broker | Alamat dan port broker, akun dan ACL, firewall |
| Error saat memuat model | Versi runtime TFLite; pastikan `models/stage1_cry_int8.tflite` dan `stage1_norm.json` ada |
| ESP tidak tersambung | ESP dan Raspberry Pi harus satu jaringan; hindari Wi-Fi venue yang memakai client isolation |

## Pelatihan Model (PC, bukan Raspberry Pi)

Pelatihan dan evaluasi dilakukan di PC dengan skrip terpisah (`prep.py`, `stage1.py`, `stage2.py`, `export_model.py`).
Hasil ekspor (`stage1_cry_int8.tflite` dan `stage1_norm.json`) disalin ke folder `models/` di repositori ini.
Angka performa untuk paper diambil dari hasil validasi silang (`stage1_results.json`, `stage2_results.json`),
bukan dari model akhir yang dilatih pada seluruh data.

## Keterbatasan

- Data latih berasal dari rekaman ponsel (Donate-a-Cry, label dari orang tua) dan ESC-50, sehingga performa pada
  mikrofon di kabin harus diuji sendiri.
- Tahap 2 (alasan menangis) tidak mengalahkan tebakan kelas mayoritas pada validasi silang; hasilnya hanya eksploratori.
- Tidak ada bayi yang terlibat dalam pengujian. Bukan perangkat medis.