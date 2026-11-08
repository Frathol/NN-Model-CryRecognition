#!/usr/bin/env python3
"""
Uji logika alarm dashboard Smart Incubator dengan data buatan (eksperimen E2).

Skrip ini mengirim pesan MQTT dengan format yang sama persis seperti ESP32
(incubator/sensors, tiap 5 detik) dan, untuk skenario menangis, status tangisan
(incubator/cry/state). Dashboard yang menilai alarm; operator mengamati dashboard
dan mencatat status + waktunya dari tabel "Event log".

Matikan ESP32 (dan layanan deteksi tangis bila --cry mqtt) selama uji supaya data
tidak bercampur.

Pemakaian (di Raspberry Pi):
  MQTT_PASSWORD='<pw admin>' python3 test_alarm_scenarios.py              # 9 skenario
  MQTT_PASSWORD='<pw admin>' python3 test_alarm_scenarios.py --only 4 6   # skenario tertentu
  MQTT_PASSWORD='<pw admin>' python3 test_alarm_scenarios.py --esp-delay 30 --hold 40   # demo (tunda 30 dtk)
  ... --cry speaker   # skenario 7 & 8: putar rekaman tangis sendiri lewat speaker

Hasil: alarm_test_<YYYYmmdd_HHMMSS>.csv (waktu mulai/selesai tiap skenario,
status yang diharapkan, kolom kosong untuk status teramati).
"""
import argparse
import csv
import json
import os
import sys
import time
from datetime import datetime

import paho.mqtt.client as mqtt

ap = argparse.ArgumentParser(description="Jalankan skenario uji alarm dashboard lewat MQTT")
ap.add_argument("--broker", default="localhost")
ap.add_argument("--port", type=int, default=1883)
ap.add_argument("--user", default="admin", help="akun yang boleh menulis incubator/sensors dan incubator/cry/*")
ap.add_argument("--password", default=os.environ.get("MQTT_PASSWORD", ""))
ap.add_argument("--only", type=int, nargs="+", help="nomor skenario yang dijalankan (default: semua)")
ap.add_argument("--hold", type=int, default=240,
                help="lama skenario 'bertahan' dalam detik (default 240 = 4 menit, untuk tunda 180 dtk)")
ap.add_argument("--esp-delay", type=int, default=180,
                help="tunda detak jantung tinggi yang dikirim seperti firmware (alarm.hr_high_delay_s): "
                     "180 = resmi, 30 = demo. Dashboard dan fsm.py mengikuti angka ini")
ap.add_argument("--rest", type=int, default=20, help="jeda kondisi normal antar skenario (detik)")
ap.add_argument("--cry", choices=["mqtt", "speaker"], default="mqtt",
                help="mqtt = skrip mengirim status menangis; speaker = operator memutar rekaman tangis")
ap.add_argument("--out", default=None, help="nama file CSV hasil")
args = ap.parse_args()

PERIOD = 5  # ESP32 mengirim tiap 5 detik

# (no, judul, suhu kulit, BPM, menempel, menangis, durasi, diharapkan)
# durasi: angka detik, atau "hold" = --hold
SCENARIOS = [
    (1, "Suhu kulit 36,8 C, detak jantung 140", 36.8, 140, True, False, 30, "Normal"),
    (2, "Suhu kulit 36,0 C", 36.0, 140, True, False, 20, "Code Blue (dingin), langsung"),
    (3, "Suhu kulit 37,8 C", 37.8, 140, True, False, 20, "Code Blue (panas), langsung"),
    (4, "Detak jantung 85 selama 15 detik", 36.8, 85, True, False, 15, "Code Blue (terlalu lambat), ~10 detik"),
    (5, "Detak jantung 85 hanya 5 detik", 36.8, 85, True, False, 5, "Tetap Normal (lonjakan disaring)"),
    (6, "Detak jantung 170, tanpa tangisan", 36.8, 170, True, False, "hold", f"Peringatan, ~{args.esp_delay} detik"),
    (7, "Detak jantung 170 sambil menangis", 36.8, 170, True, True, 60, "Normal + 'naik karena menangis'"),
    (8, "Detak jantung 190 sambil menangis", 36.8, 190, True, True, "hold", f"Code Blue (terlalu cepat), ~{args.esp_delay} detik"),
    (9, "Detak jantung 200, sensor lepas", 36.8, 200, False, False, 30, "Hanya peringatan 'sensor not attached'"),
]
NORMAL = (36.8, 140, True)


def sensors_payload(skin, bpm, contact, uptime):
    """Format JSON sama seperti firmware ESP32 (NTC, SHT31, DHT11, pulse sensor, berat, daya)."""
    return json.dumps({
        "ntc": {"temp": skin},
        "sht31": {"temp": 33.0, "hum": 60.0},
        "dht11": {"temp": 33.2, "hum": 62.0},
        "heart": {"bpm": bpm, "contact": contact, "ir": 2000 if contact else 100, "sensor_ok": True},
        "weight": {"total_g": 2850.0, "left_g": 2842.0, "right_g": 2858.0, "sensor_ok": True},
        "power": {"soc_pct": 90, "mains_ok": True, "status": "PSU:OK"},
        "alarm": {"fsm_state": "NORMAL", "buzzer_muted": False, "hr_high_delay_s": args.esp_delay},
        "fsm_state": "NORMAL",
        "uptime_s": uptime,
        "rssi": -50,
    })


def cry_state(crying):
    return json.dumps({"crying": crying, "since": time.time() if crying else None,
                       "cry_prob": 0.92 if crying else 0.05,
                       "reason": "none", "reason_conf": 0.0, "ts": time.time()})


client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="alarm-test-runner")
client.username_pw_set(args.user, args.password)
try:
    client.connect(args.broker, args.port, keepalive=30)
except Exception as e:  # noqa: BLE001
    sys.exit(f"Gagal terhubung ke broker {args.broker}:{args.port}: {e}")
client.loop_start()
client.publish("incubator/esp32/status", "online", qos=1, retain=True)
t0 = time.time()


def send_for(seconds, skin, bpm, contact):
    """Kirim data sensor tiap PERIOD detik selama `seconds` detik (minimal satu kali)."""
    end = time.time() + seconds
    while True:
        client.publish("incubator/sensors", sensors_payload(skin, bpm, contact, int(time.time() - t0)))
        if time.time() + PERIOD > end + 0.01:
            break
        time.sleep(PERIOD)
    time.sleep(max(0.0, end - time.time()))


selected = [s for s in SCENARIOS if not args.only or s[0] in args.only]
out = args.out or f"alarm_test_{datetime.now():%Y%m%d_%H%M%S}.csv"
rows = []
try:
    print(f"Kondisi normal {args.rest} dtk sebelum mulai (pastikan dashboard terbuka)...")
    client.publish("incubator/cry/state", cry_state(False), qos=1, retain=True)
    send_for(args.rest, *NORMAL)
    for no, title, skin, bpm, contact, crying, dur, expected in selected:
        dur = args.hold if dur == "hold" else dur
        if crying and args.cry == "speaker":
            input(f"\n[{no}] PUTAR rekaman tangis di dekat mikrofon, lalu tekan Enter...")
        elif crying:
            client.publish("incubator/cry/state", cry_state(True), qos=1, retain=True)
        start = datetime.now()
        print(f"\n[{no}] {start:%H:%M:%S} mulai : {title} ({dur} dtk)")
        print(f"     diharapkan: {expected}")
        send_for(dur, skin, bpm, contact)
        end = datetime.now()
        if crying and args.cry == "mqtt":
            client.publish("incubator/cry/state", cry_state(False), qos=1, retain=True)
        elif crying:
            print("     hentikan rekaman tangis")
        print(f"     {end:%H:%M:%S} selesai; kembali normal {args.rest} dtk")
        rows.append({"no": no, "skenario": title, "mulai": f"{start:%H:%M:%S}", "selesai": f"{end:%H:%M:%S}",
                     "diharapkan": expected, "teramati": "", "waktu_sampai_alarm_dtk": "", "lulus": ""})
        send_for(args.rest, *NORMAL)
except KeyboardInterrupt:
    print("\nDihentikan.")
finally:
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["no", "skenario", "mulai", "selesai", "diharapkan",
                                          "teramati", "waktu_sampai_alarm_dtk", "lulus"])
        w.writeheader()
        w.writerows(rows)
    client.publish("incubator/esp32/status", "offline", qos=1, retain=True)
    time.sleep(0.5)
    client.loop_stop()
    print(f"\nHasil disimpan di {out}. Isi kolom 'teramati' dari Event log dashboard.")
