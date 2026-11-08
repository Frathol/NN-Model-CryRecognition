# Skrip Utilitas & Deployment (`raspberry-pi/scripts`)

Kumpulan skrip otomasi untuk instalasi, pengujian simulasi, pencatatan dataset CSV, dan sinkronisasi kode antara laptop pengembangan dan **Raspberry Pi 5**.

## Daftar Skrip

| Nama Berkas | Lingkungan | Fungsi Utama |
| :--- | :--- | :--- |
| `setup_pi.sh` | Bash (Pi) | Instalasi awal Mosquitto, Nginx, Python venv, dan akun MQTT. |
| `setup_autostart.sh` | Bash (Pi) | Mengaktifkan systemd service agar berjalan otomatis saat boot. |
| `setup_camera.sh` | Bash (Pi) | Konfigurasi layanan streaming kamera Raspberry Pi. |
| `connect_bt_mic.sh` | Bash (Pi) | Menghubungkan mikrofon Bluetooth dengan profil HFP/HSP secara otomatis. |
| `dummy_esp32.sh` | Bash (Pi/Linux) | Simulator publikasi payload JSON `incubator/sensors` tanpa perangkat ESP32 fisik. |
| `mqtt_to_csv.py` | Python 3 | Merekam telemetri sensor & hasil inferensi tangisan dari MQTT ke berkas CSV eksperimen. |
| `test_alarm_scenarios.py` | Python 3 | Pengujian skenario klinis FSM (*Normal*, *Cry Watch*, *Pre-Alarm*, *Code Blue*, *Thermal Cutoff*). |
| `check_health.sh` | Bash (Pi) | Memeriksa status layanan Mosquitto, Nginx, dan systemd service inkubator. |
| `kirim_ke_pi.ps1` | PowerShell (Windows) | Mengirim perubahan dashboard & `fsm.py` ke Raspberry Pi melalui SSH/SCP. |
| `ambil_dari_pi.ps1` | PowerShell (Windows) | Mengambil snapshot konfigurasi dan berkas terbaru dari Raspberry Pi ke lokal. |
| `pasang_kiriman.sh` | Bash (Pi) | Memasang berkas kiriman di Raspberry Pi dan me-restart layanan terkait. |
