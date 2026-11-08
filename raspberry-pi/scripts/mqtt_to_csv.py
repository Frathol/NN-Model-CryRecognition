#!/usr/bin/env python3
"""
Simpan pesan MQTT dari broker ke file CSV (satu file per topic).

- JSON bersarang diratakan dengan titik:  {"sht31":{"temp":33.1}}  ->  kolom "sht31.temp"
- Pesan teks biasa (mis. "online"/"offline") disimpan di kolom "value"
- Kolom pertama "timestamp" = waktu pesan diterima, format YYYY-MM-DD HH:MM:SS
- Field "ts" (epoch dari cry_service) -> kolom "sent_at" = waktu kirim, YYYY-MM-DD HH:MM:SS.mmm
  (pakai milidetik karena incubator/cry/result dikirim 2x per detik); "since" -> waktu terbaca
- Topic yang dikenal punya urutan kolom tetap (lihat SCHEMAS); field baru yang muncul
  belakangan tetap disimpan sebagai kolom tambahan di kanan (header diperbarui)

File per topic:
  incubator/sensors       -> incubator_sensors.csv        (data sensor ESP32)
  incubator/esp32/status  -> incubator_esp32_status.csv   (online/offline ESP32)
  incubator/command       -> incubator_command.csv        (perintah ke ESP32, mis. tare)
  incubator/loadcell/left -> incubator_loadcell_left.csv  (load cell kiri dibaca langsung oleh Pi)
  incubator/cry/result    -> incubator_cry_result.csv     (hasil AI tiap 0,5 dtk)
  incubator/cry/state     -> incubator_cry_state.csv      (mulai/berhenti menangis)
  incubator/cry/status    -> incubator_cry_status.csv     (online/no_mic/offline layanan AI)

Pemakaian (di Raspberry Pi):
  MQTT_PASSWORD='<pw dashboard>' python3 mqtt_to_csv.py
  MQTT_PASSWORD='<pw>' python3 mqtt_to_csv.py --out ~/logs/uji1 --topic 'incubator/sensors'

Hasil (default folder ./mqtt_csv):
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
FIXED_COLS = ["timestamp"]

# Urutan kolom per topic (sesuai payload ESP32 & cry_service.py). Field yang tidak ada
# di pesan tertentu dibiarkan kosong, mis. reason_probs.* hanya terisi saat tahap 2 jalan.
SCHEMAS = {
    "incubator/sensors": [
        "timestamp", "fsm_state",
        "ntc.temp", "sht31.temp", "sht31.hum", "dht11.temp", "dht11.hum",
        "heart.bpm", "heart.contact", "heart.ir", "heart.sensor_ok",
        "weight.total_g", "weight.left_g", "weight.right_g", "weight.sensor_ok",
        "power.soc_pct", "power.mains_ok", "power.status",
        "alarm.fsm_state", "alarm.clinical_code", "alarm.buzzer_mode", "alarm.buzzer_muted",
        "alarm.heater_cutoff", "alarm.led_green", "alarm.led_yellow", "alarm.led_red",
        "uptime_s", "rssi",
    ],
    "incubator/cry/result": [
        "timestamp", "sent_at", "source", "label", "is_cry",
        "cry_prob", "raw_cry_prob", "confidence", "probabilities.non_cry", "probabilities.cry",
        "level_db", "reason", "reason_conf", "window_reason",
        "reason_probs.hungry", "reason_probs.belly_pain", "reason_probs.burping",
        "reason_probs.discomfort", "reason_probs.tired",
        "latency_ms",
    ],
    "incubator/cry/state": [
        "timestamp", "sent_at", "crying", "since", "cry_prob", "reason", "reason_conf", "source",
    ],
    "incubator/loadcell/left": [
        "timestamp", "sent_at", "side", "weight.g", "weight.raw", "weight.calibrated", "weight.sensor_ok",
    ],
    "incubator/cry/status": ["timestamp", "value"],
    "incubator/esp32/status": ["timestamp", "value"],
    "incubator/command": ["timestamp", "value"],
}

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


def epoch_text(v, ms=False):
    try:
        t = datetime.fromtimestamp(float(v))
    except (TypeError, ValueError, OverflowError, OSError):
        return v
    return t.strftime(TS_FORMAT + ".%f")[:-3] if ms else t.strftime(TS_FORMAT)


def payload_to_row(payload: str):
    text = payload.strip()
    if text.startswith("{"):
        try:
            data = json.loads(text)
            if isinstance(data, dict):
                row = flatten(data)
                if "ts" in row:                              # epoch -> waktu terbaca
                    row["sent_at"] = epoch_text(row.pop("ts"), ms=True)
                if row.get("since") not in (None, ""):
                    row["since"] = epoch_text(row["since"])
                return row
        except json.JSONDecodeError:
            pass
    return {"value": text}


# ---------------------------------------------------------------------
#  Satu file CSV per topic, header bisa bertambah
# ---------------------------------------------------------------------
class TopicCsv:
    def __init__(self, path: Path, schema=None):
        self.path = path
        self.cols = []
        if path.exists() and path.stat().st_size > 0:
            with path.open(newline="", encoding="utf-8") as f:
                self.cols = next(csv.reader(f), [])
        if schema:
            # file lama (header lain / urutan lain) -> susun ulang sesuai schema, data tetap
            want = schema + [c for c in self.cols if c not in schema and c != "topic"]
            if self.cols != want:
                self._rewrite(want)

    def write(self, row: dict):
        new_cols = [c for c in row if c not in self.cols]
        if new_cols or not self.cols:
            had_header = bool(self.cols)
            base = self.cols or FIXED_COLS
            self._rewrite(base + [c for c in new_cols if c not in base])
            if had_header:
                print(f"   (kolom baru di {self.path.name}: {', '.join(new_cols)})")
        with self.path.open("a", newline="", encoding="utf-8") as f:
            csv.DictWriter(f, fieldnames=self.cols, extrasaction="ignore").writerow(row)

    def _rewrite(self, cols):
        old_rows = []
        if self.path.exists() and self.path.stat().st_size > 0:
            with self.path.open(newline="", encoding="utf-8") as f:
                old_rows = list(csv.DictReader(f))
        self.cols = cols
        tmp = self.path.with_suffix(".tmp")
        with tmp.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=self.cols, extrasaction="ignore")
            w.writeheader()
            w.writerows(old_rows)
        tmp.replace(self.path)


files = {}


def csv_for(topic: str) -> TopicCsv:
    if topic not in files:
        name = re.sub(r"[^A-Za-z0-9_-]+", "_", topic).strip("_") or "root"
        files[topic] = TopicCsv(out_dir / f"{name}.csv", SCHEMAS.get(topic))
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
    row = {"timestamp": ts}
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
