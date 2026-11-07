#!/usr/bin/env python3
import json, time, csv, os, argparse
import paho.mqtt.client as mqtt

parser = argparse.ArgumentParser(description="NeoSentinel MQTT CSV Logger")
parser.add_argument("--broker", default="localhost")
parser.add_argument("--port", type=int, default=1883)
# Akun "dashboard" (hanya-baca incubator/#) sudah cukup untuk merekam
parser.add_argument("--user", default="dashboard")
parser.add_argument("--password", default=os.environ.get("MQTT_PASSWORD", ""))
args = parser.parse_args()

CSV_FILE = "eksperimen_log.csv"
file_exists = os.path.isfile(CSV_FILE)

with open(CSV_FILE, mode='a', newline='') as f:
    writer = csv.writer(f)
    if not file_exists:
        # Header tabel CSV
        writer.writerow(["timestamp", "topic", "seq", "t_air", "rh", "t_skin", "hr", "cry", "reason", "infer_ms", "state", "active_alerts", "advisory"])

def on_message(client, userdata, msg):
    topic = msg.topic
    payload_raw = msg.payload.decode("utf-8", errors="ignore")
    try:
        data = json.loads(payload_raw) if payload_raw.startswith("{") else {}
    except:
        data = {}
    
    # Ambil data sesuai topik
    seq = data.get("seq", "")
    t_air = data.get("t_air", "")
    rh = data.get("rh", "")
    t_skin = data.get("t_skin", "")
    hr = data.get("hr", "")
    cry = data.get("cry", data.get("crying", ""))
    reason = data.get("reason", "")
    infer_ms = data.get("infer_ms", "")
    state = data.get("state", "")
    alerts = str(data.get("active_alerts", ""))
    advisory = data.get("advisory", "")

    if topic == "incubator/sensors":
        # Payload ESP32 bersarang: {"ntc":{"temp"},"sht31":{"temp","hum"},"heart":{"bpm"},...}
        sht, dht = data.get("sht31") or {}, data.get("dht11") or {}
        t_air = sht.get("temp") if sht.get("temp") is not None else dht.get("temp", "")
        rh = sht.get("hum") if sht.get("hum") is not None else dht.get("hum", "")
        t_skin = (data.get("ntc") or {}).get("temp", "")
        hr = (data.get("heart") or {}).get("bpm", "")
    elif topic == "incubator/fsm/state":
        tel = data.get("telemetry_summary") or {}
        t_air, rh, t_skin, hr = tel.get("t_air", ""), tel.get("rh", ""), tel.get("t_skin", ""), tel.get("hr", "")
        cry, reason = tel.get("cry", ""), tel.get("cry_reason_advisory", "")
    t_air, rh, t_skin, hr = ["" if v is None else v for v in (t_air, rh, t_skin, hr)]
    
    with open(CSV_FILE, mode='a', newline='') as f:
        writer = csv.writer(f)
        writer.writerow([time.strftime("%Y-%m-%d %H:%M:%S"), topic, seq, t_air, rh, t_skin, hr, cry, reason, infer_ms, state, alerts, advisory])
    print(f"[{time.strftime('%H:%M:%S')}] Logged: {topic}")

client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="logger_pi")
client.username_pw_set(args.user, args.password)
client.on_message = on_message
client.connect(args.broker, args.port, keepalive=10)
client.subscribe("incubator/#", qos=0)

print(f"Merekam log MQTT ke {CSV_FILE}...")
client.loop_forever()