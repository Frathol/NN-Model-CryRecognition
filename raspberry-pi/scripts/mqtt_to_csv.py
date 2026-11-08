#!/usr/bin/env python3
"""
Simpan pesan MQTT dari broker ke file CSV (satu file per topic).

- JSON bersarang diratakan dengan titik:  {"sht31":{"temp":33.1}}  ->  kolom "sht31.temp"
- Pesan teks biasa (mis. "online"/"offline") disimpan di kolom "value"
- Kolom pertama "timestamp" = waktu pesan diterima, format YYYY-MM-DD HH:MM:SS
- Field baru yang muncul belakangan otomatis menjadi kolom baru (header diperbarui)

Pemakaian (di Raspberry Pi):
  MQTT_PASSWORD='<pw dashboard>' python3 mqtt_to_csv.py
  MQTT_PASSWORD='<pw>' python3 mqtt_to_csv.py --out ~/logs/uji1 --topic 'incubator/sensors'

Hasil (default folder ./mqtt_csv):
  incubator_sensors.csv, incubator_esp32_status.csv, incubator_cry_result.csv, ...
Hentikan dengan Ctrl+C.
"""
import argparse
import csv
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

import paho.mqtt.client as mqtt

TS_FORMAT = "%Y-%m-%d %H:%M:%S"
FIXED_COLS = ["timestamp", "topic"]

ap = argparse.ArgumentParser(description="Rekam pesan MQTT (JSON) ke CSV, satu file per topic")
ap.add_argument("--broker", default="localhost")
ap.add_argument("--port", type=int, default=1883)
ap.add_argument("--user", default="dashboard", help="akun MQTT (default: dashboard, hanya-baca)")
ap.add_argument("--password", default=os.environ.get("MQTT_PASSWORD", ""),
                help="default diambil dari env MQTT_PASSWORD")
ap.add_argument("--topic", action="append",
                help="topic yang direkam (boleh berulang, wildcard boleh). Default: incubator/#")
ap.add_argument("--out", default="mqtt_csv", help="folder output CSV (default: ./mqtt_csv)")
ap.add_argument("--include-retained", action="store_true",
                help="ikut simpan pesan retained (status lama) yang dikirim broker saat baru tersambung")
args = ap.parse_args()

topics = args.topic or ["incubator/#"]
out_dir = Path(args.out).expanduser()
out_dir.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------
#  JSON -> baris datar
# ---------------------------------------------------------------------
def flatten(obj, prefix=""):
    """{"a":{"b":1},"c":[1,2]} -> {"a.b": 1, "c": "[1, 2]"}"""
    row = {}
    for key, val in obj.items():
        name = f"{prefix}{key}"
        if isinstance(val, dict):
            row.update(flatten(val, name + "."))
        elif isinstance(val, list):
            row[name] = json.dumps(val, ensure_ascii=False)
        elif isinstance(val, bool):
            row[name] = "true" if val else "false"
        elif val is None:
            row[name] = ""                       # sensor gagal (null) -> sel kosong
        else:
            row[name] = val
    return row


def payload_to_row(payload: str):
    text = payload.strip()
    if text.startswith("{"):
        try:
            data = json.loads(text)
            if isinstance(data, dict):
                return flatten(data)
        except json.JSONDecodeError:
            pass
    return {"value": text}


# ---------------------------------------------------------------------
#  Satu file CSV per topic, header bisa bertambah
# ---------------------------------------------------------------------
class TopicCsv:
    def __init__(self, path: Path):
        self.path = path
        self.cols = []
        if path.exists() and path.stat().st_size > 0:
            with path.open(newline="", encoding="utf-8") as f:
                self.cols = next(csv.reader(f), [])

    def write(self, row: dict):
        new_cols = [c for c in row if c not in self.cols]
        if new_cols or not self.cols:
            self._expand(new_cols)
        with self.path.open("a", newline="", encoding="utf-8") as f:
            csv.DictWriter(f, fieldnames=self.cols, extrasaction="ignore").writerow(row)

    def _expand(self, new_cols):
        old_rows = []
        if self.path.exists() and self.path.stat().st_size > 0:
            with self.path.open(newline="", encoding="utf-8") as f:
                old_rows = list(csv.DictReader(f))
        base = self.cols or FIXED_COLS
        self.cols = base + [c for c in new_cols if c not in base]
        tmp = self.path.with_suffix(".tmp")
        with tmp.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=self.cols, extrasaction="ignore")
            w.writeheader()
            w.writerows(old_rows)
        tmp.replace(self.path)
        if old_rows:
            print(f"   (kolom baru di {self.path.name}: {', '.join(new_cols)})")


files = {}


def csv_for(topic: str) -> TopicCsv:
    if topic not in files:
        name = re.sub(r"[^A-Za-z0-9_-]+", "_", topic).strip("_") or "root"
        files[topic] = TopicCsv(out_dir / f"{name}.csv")
    return files[topic]


# ---------------------------------------------------------------------
#  MQTT
# ---------------------------------------------------------------------
def on_connect(client, userdata, flags, rc, props=None):
    if rc == 0:
        for t in topics:
            client.subscribe(t, qos=0)
        print(f"[MQTT] Tersambung ke {args.broker}:{args.port} sebagai '{args.user}'. "
              f"Merekam {', '.join(topics)} ke {out_dir}/ (Ctrl+C untuk berhenti)")
    else:
        print(f"[MQTT] Ditolak: {rc} (cek username/password & ACL)", file=sys.stderr)


def on_message(client, userdata, msg):
    if msg.retain and not args.include_retained:
        return                                   # status lama, bukan kejadian baru
    ts = datetime.now().strftime(TS_FORMAT)
    row = {"timestamp": ts, "topic": msg.topic}
    row.update(payload_to_row(msg.payload.decode("utf-8", errors="replace")))
    csv_for(msg.topic).write(row)
    print(f"[{ts}] {msg.topic}")


try:                                             # paho-mqtt 2.x
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"csv-logger-{os.getpid()}")
except AttributeError:                           # paho-mqtt 1.x (python3-paho-mqtt di Bookworm)
    client = mqtt.Client(client_id=f"csv-logger-{os.getpid()}")
if args.user:
    client.username_pw_set(args.user, args.password or None)
client.on_connect = on_connect
client.on_message = on_message
client.reconnect_delay_set(1, 30)

try:
    client.connect(args.broker, args.port, keepalive=30)
    client.loop_forever()
except KeyboardInterrupt:
    print("\nSelesai.")
except OSError as e:
    sys.exit(f"[MQTT] Tidak bisa tersambung ke {args.broker}:{args.port}: {e}")
