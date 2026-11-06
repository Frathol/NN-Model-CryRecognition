#!/usr/bin/env python3
import json, time, argparse
import paho.mqtt.client as mqtt

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
parser.add_argument("--user", default="fsm_engine")
parser.add_argument("--password", default="PASSWORD")
args = parser.parse_args()

state_ctx = {
    "setpoint_temp": 33.0,
    "delta_temp": 1.0,
    "t_air": 33.0, "rh": 60.0,
    "t_skin": 36.5, "hr": 130, "weight_g": 2500,
    "cry": False, "cry_since": None, "cry_reason": "none", "cry_reason_conf": 0.0,
    "mains_ok": True, "soc": 100.0,
    "code_blue": False,
    "node_status": {"esp": "online", "audio_pi": "online"},
    "last_seen": {"env": time.time(), "audio": time.time()}
}

def evaluate_fsm():
    now = time.time()
    active = []
    advisories = []

    # 1. Cek Koneksi Sensor / Node Offline (> 10 detik atau Last Will)
    if (
        state_ctx["node_status"].get("esp") == "offline"
        or state_ctx["node_status"].get("audio_pi") == "offline"
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

def on_message(client, userdata, msg):
    topic = msg.topic
    payload_raw = msg.payload.decode("utf-8", errors="ignore")
    try:
        data = json.loads(payload_raw) if payload_raw.startswith("{") else {}
    except Exception:
        data = {}

    if topic.endswith("/env"):
        state_ctx["t_air"] = float(data.get("t_air", state_ctx["t_air"]))
        state_ctx["rh"] = float(data.get("rh", state_ctx["rh"]))
        state_ctx["last_seen"]["env"] = time.time()
    elif topic.endswith("/bio"):
        state_ctx["t_skin"] = float(data.get("t_skin", state_ctx["t_skin"]))
        state_ctx["hr"] = int(data.get("hr", state_ctx["hr"]))
    elif topic.endswith("/audio"):
        state_ctx["cry"] = bool(data.get("cry", False))
        state_ctx["cry_reason"] = str(data.get("reason", "none"))
        state_ctx["cry_reason_conf"] = float(data.get("reason_conf", 0.0))
        state_ctx["last_seen"]["audio"] = time.time()
    elif topic == "neo/power":
        state_ctx["soc"] = float(data.get("soc", 100.0))
        state_ctx["mains_ok"] = bool(data.get("mains_ok", True))
    elif topic.endswith("/status"):
        node_name = topic.split("/")[-2]
        state_ctx["node_status"][node_name] = payload_raw.strip()
    elif topic.endswith("/cmd"):
        if "setpoint" in data:
            state_ctx["setpoint_temp"] = float(data["setpoint"])

    out_state = evaluate_fsm()
    client.publish("neo/inc01/state", json.dumps(out_state), qos=1, retain=True)
    print("[FSM STATE]", out_state["state"], "| Advisory:", out_state["advisory"])

client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="fsm_gateway")
client.username_pw_set(args.user, args.password)
client.on_message = on_message
client.connect(args.broker, args.port, keepalive=10)
client.subscribe("neo/#", qos=1)
print("[FSM] Mendengarkan topik neo/# dan mem-publish ke neo/inc01/state...")
client.loop_forever()
