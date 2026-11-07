# NeoSentinel AI - Raspberry Pi Edge Node

Repositori ini berisi sistem inference *Edge AI* (Deteksi Tangisan Bayi) dan *Finite State Machine* (FSM) pembuat keputusan alarm untuk proyek NeoSentinel.

## Struktur Folder
- `models/` : Berisi file bobot AI terkuantisasi (`.tflite`) dan parameter metadata (`.json`) [TIDAK PERLU DI-TRAIN ULANG].
- `features.py` : Modul ekstraksi spektrum suara (Log-Mel) berbasis NumPy/SciPy.
- `audio_detector.py` : Skrip pembaca mikrofon yang mengeksekusi AI dan mengirim JSON MQTT.
- `fsm.py` : Mesin logika status alarm (menggabungkan sensor ESP32 + deteksi Audio).
- `mqtt_logger.py` : Skrip untuk merekam data MQTT ke dalam `.csv` saat eksperimen pengujian.

## Cara Instalasi (di Raspberry Pi)
Buka terminal di Raspberry Pi dan jalankan perintah berikut:
```bash
sudo apt-get update && sudo apt-get install -y libportaudio2 python3-venv
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt