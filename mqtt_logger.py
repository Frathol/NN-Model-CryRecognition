#!/usr/bin/env python3
import json, time, csv, os, argparse
import paho.mqtt.client as mqtt

parser = argparse.ArgumentParser(description="NeoSentinel MQTT CSV Logger")
parser.add_argument("--broker", default="localhost")
parser.add_argument("--port", type=int, default=1883)
parser.add_argument("--user", default="logger_node")
parser.add_argument("--password", default="PASSWORD")
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
    cry = data.get("cry", "")
    reason = data.get("reason", "")
    infer_ms = data.get("infer_ms", "")
    state = data.get("state", "")
    alerts = str(data.get("active_alerts", ""))
    advisory = data.get("advisory", "")
    
    with open(CSV_FILE, mode='a', newline='') as f:
        writer = csv.writer(f)
        writer.writerow([time.strftime("%Y-%m-%d %H:%M:%S"), topic, seq, t_air, rh, t_skin, hr, cry, reason, infer_ms, state, alerts, advisory])
    print(f"[{time.strftime('%H:%M:%S')}] Logged: {topic}")

client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="logger_pi")
client.username_pw_set(args.user, args.password)
client.on_message = on_message
client.connect(args.broker, args.port, keepalive=10)
client.subscribe("neo/inc01/#", qos=0)

print(f"Merekam log MQTT ke {CSV_FILE}...")
client.loop_forever()