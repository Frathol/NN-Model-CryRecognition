#!/usr/bin/env python3
import os, json, time, argparse, threading
import paho.mqtt.client as mqtt

# Topic MQTT (sama dengan proyek smart-incubator)
TOPIC_SENSORS = "incubator/sensors"           # ESP32: ntc, sht31, dht11, heart
TOPIC_ESP_STATUS = "incubator/esp32/status"   # ESP32: online / offline
TOPIC_CRY_RESULT = "incubator/cry/result"     # audio: hasil tiap jendela (+ alasan tahap 2)
TOPIC_CRY_STATE = "incubator/cry/state"       # audio: {"crying": true/false, ...}
TOPIC_CRY_STATUS = "incubator/cry/status"     # audio: online / no_mic / offline
TOPIC_CMD = "incubator/cmd/setpoint"          # perintah: {"setpoint": 33.0}
TOPIC_FSM_STATE = "incubator/fsm/state"       # keluaran FSM (retained)

PRIORITY = [
    "CODE_BLUE",
    "VITAL_SIGN_ALERT",
    "THERMAL_HAZARD",
    "HUMIDITY_HAZARD",
    "MAINS_FAIL",
    "SENSOR_FAULT",
    "CRYING_DISTRESS",
    "INACTIVITY_INFO",
    "POWER_WARNING",
    "IDLE_NORMAL"
]

parser = argparse.ArgumentParser(description="NeoSentinel Priority FSM Alert Engine")
parser.add_argument("--broker", default="localhost")
parser.add_argument("--port", type=int, default=1883)
parser.add_argument("--user", default="fsm")
parser.add_argument("--password", default=os.environ.get("MQTT_PASSWORD", ""))
args = parser.parse_args()

state_ctx = {
    "setpoint_temp": 33.0,
    "delta_temp": 1.0,
    "t_air": 33.0, "rh": 60.0,
    "t_skin": 36.5, "hr": 130, "weight_g": 2500,
    "cry": False, "cry_since": None, "cry_reason": "none", "cry_reason_conf": 0.0,
    "mains_ok": True, "soc": 100.0,
    "code_blue": False,
    "node_status": {"esp": "online", "audio": "online"},
    "last_seen": {"env": time.time(), "audio": time.time()}
}

def evaluate_fsm():
    now = time.time()
    active = []
    advisories = []

    # 1. Cek Koneksi Sensor / Node Offline (> 10 detik atau Last Will)
    if (
        state_ctx["node_status"].get("esp") == "offline"
        or state_ctx["node_status"].get("audio") in ("offline", "no_mic")
        or (now - state_ctx["last_seen"]["env"] > 10.0)
    ):
        active.append("SENSOR_FAULT")

    # 2. Cek Code Blue Lokal
    if state_ctx["code_blue"]:
        active.append("CODE_BLUE")

    # 3. Cek Tanda Vital
    if not (36.0 <= state_ctx["t_skin"] <= 37.5) or not (100 <= state_ctx["hr"] <= 180):
        active.append("VITAL_SIGN_ALERT")

    # 4. Cek Suhu Kabin (Setpoint +/- Delta)
    sp, dt = state_ctx["setpoint_temp"], state_ctx["delta_temp"]
    if state_ctx["t_air"] >= (sp + dt) or state_ctx["t_air"] <= (sp - dt):
        active.append("THERMAL_HAZARD")

    # 5. Cek Kelembapan Kabin
    if state_ctx["rh"] >= 80.0 or state_ctx["rh"] <= 50.0:
        active.append("HUMIDITY_HAZARD")

    # 6. Cek Listrik Utama & Baterai Surya
    if not state_ctx["mains_ok"]:
        active.append("MAINS_FAIL")
    if state_ctx["soc"] < 30.0:
        active.append("POWER_WARNING")

    # 7. Cek Tangisan Bayi (Stage 1) + Alasan Tangisan (Stage 2 Advisory)
    if state_ctx["cry"]:
        if state_ctx["cry_since"] is None:
            state_ctx["cry_since"] = now
        cry_duration = now - state_ctx["cry_since"]
        active.append("CRYING_DISTRESS")

        # Gabungkan konteks inkubator dengan hasil Stage 2 sebagai Advisory untuk Dashboard
        r_lbl = state_ctx["cry_reason"]
        r_cnf = int(state_ctx["cry_reason_conf"] * 100)
        if "THERMAL_HAZARD" in active:
            advisories.append(
                f"Thermal deviation ({state_ctx['t_air']:.1f} C) coincides with crying "
                f"[Stage-2 advisory: {r_lbl} ({r_cnf}%)]. Inspect cabin temperature."
            )
        elif "HUMIDITY_HAZARD" in active:
            advisories.append(
                f"Humidity deviation ({state_ctx['rh']:.1f}% RH) coincides with crying "
                f"[Stage-2 advisory: {r_lbl} ({r_cnf}%)]."
            )
        else:
            esc_txt = "SUSTAINED (>30s)" if cry_duration >= 30.0 else f"({int(cry_duration)}s)"
            advisories.append(
                f"Infant crying {esc_txt} with normal cabin microclimate "
                f"[Stage-2 advisory suggestion: {r_lbl} ({r_cnf}%) - non-diagnostic]."
            )
    else:
        state_ctx["cry_since"] = None

    top_state = next((s for s in PRIORITY if s in active), "IDLE_NORMAL")
    in_cabin_buzzer = top_state in ("CODE_BLUE",)
    in_cabin_led = "GREEN" if top_state == "IDLE_NORMAL" else (
        "RED" if top_state in ("CODE_BLUE", "VITAL_SIGN_ALERT", "THERMAL_HAZARD", "SENSOR_FAULT") else "YELLOW"
    )

    return {
        "ts": int(now),
        "state": top_state,
        "active_alerts": active,
        "in_cabin_buzzer": in_cabin_buzzer,
        "in_cabin_led": in_cabin_led,
        "advisory": " | ".join(advisories) if advisories else "Normal monitoring.",
        "telemetry_summary": {
            "t_air": state_ctx["t_air"],
            "rh": state_ctx["rh"],
            "t_skin": state_ctx["t_skin"],
            "hr": state_ctx["hr"],
            "cry": state_ctx["cry"],
            "cry_reason_advisory": state_ctx["cry_reason"],
            "cry_reason_conf": state_ctx["cry_reason_conf"],
            "soc": state_ctx["soc"]
        }
    }

def _num(v):
    """Nilai sensor yang gagal dibaca dikirim ESP32 sebagai null -> None."""
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None

ctx_lock = threading.Lock()
last_published = {"key": None, "at": 0.0}

def on_message(client, userdata, msg):
    topic = msg.topic
    payload_raw = msg.payload.decode("utf-8", errors="ignore")
    try:
        data = json.loads(payload_raw) if payload_raw.startswith("{") else {}
    except Exception:
        data = {}

    with ctx_lock:
        if topic == TOPIC_SENSORS:
            sht, dht = data.get("sht31") or {}, data.get("dht11") or {}
            ntc, heart = data.get("ntc") or {}, data.get("heart") or {}
            # Suhu & kelembapan udara: SHT31 sebagai sensor utama, DHT11 cadangan
            t_air = _num(sht.get("temp"))
            t_air = t_air if t_air is not None else _num(dht.get("temp"))
            rh = _num(sht.get("hum"))
            rh = rh if rh is not None else _num(dht.get("hum"))
            t_skin = _num(ntc.get("temp"))                 # NTC = probe suhu kulit
            hr = _num(heart.get("bpm")) if heart.get("contact") else None
            if t_air is not None: state_ctx["t_air"] = t_air
            if rh is not None: state_ctx["rh"] = rh
            if t_skin is not None: state_ctx["t_skin"] = t_skin
            if hr is not None: state_ctx["hr"] = int(hr)
            state_ctx["last_seen"]["env"] = time.time()
        elif topic == TOPIC_ESP_STATUS:
            state_ctx["node_status"]["esp"] = payload_raw.strip()
        elif topic == TOPIC_CRY_STATUS:
            state_ctx["node_status"]["audio"] = payload_raw.strip()
        elif topic == TOPIC_CRY_STATE:
            state_ctx["cry"] = bool(data.get("crying", False))
            state_ctx["last_seen"]["audio"] = time.time()
        elif topic == TOPIC_CRY_RESULT:
            # Alasan tangisan (tahap 2) hanya dikirim audio_detector.py
            if "reason" in data:
                state_ctx["cry_reason"] = str(data.get("reason", "none"))
                state_ctx["cry_reason_conf"] = float(data.get("reason_conf", 0.0))
            state_ctx["last_seen"]["audio"] = time.time()
        elif topic == TOPIC_CMD:
            if "setpoint" in data:
                state_ctx["setpoint_temp"] = float(data["setpoint"])
        else:
            return   # topic lain (termasuk keluaran FSM sendiri) diabaikan -> tidak loop
    publish_state(client)

def publish_state(client, heartbeat_s=5.0):
    """Evaluasi FSM; publish bila hasilnya berubah atau tiap heartbeat_s detik."""
    with ctx_lock:
        out_state = evaluate_fsm()
    key = (out_state["state"], tuple(out_state["active_alerts"]), out_state["advisory"])
    now = time.time()
    if key == last_published["key"] and now - last_published["at"] < heartbeat_s:
        return
    client.publish(TOPIC_FSM_STATE, json.dumps(out_state), qos=1, retain=True)
    if key != last_published["key"]:
        print("[FSM STATE]", out_state["state"], "| Advisory:", out_state["advisory"])
    last_published.update(key=key, at=now)

def on_connect(client, userdata, flags, rc, props=None):
    if rc == 0:
        client.subscribe("incubator/#", qos=1)
    else:
        print("[FSM] MQTT ditolak:", rc, "(cek username/password & ACL)")

client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="fsm_gateway")
client.username_pw_set(args.user, args.password)
client.on_connect = on_connect
client.on_message = on_message
client.connect(args.broker, args.port, keepalive=10)
client.loop_start()
print(f"[FSM] Mendengarkan topik incubator/# dan mem-publish ke {TOPIC_FSM_STATE}...")

# Evaluasi berkala agar timeout sensor (SENSOR_FAULT) tetap terdeteksi walau tidak ada pesan masuk
try:
    while True:
        time.sleep(1.0)
        publish_state(client)
except KeyboardInterrupt:
    client.loop_stop()
