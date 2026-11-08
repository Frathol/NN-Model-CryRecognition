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
    "CODE_BLUE",          # suhu kulit / detak jantung di luar batas aman (atau manual)
    "THERMAL_HAZARD",
    "HUMIDITY_HAZARD",
    "MAINS_FAIL",
    "SENSOR_FAULT",
    "CRYING_DISTRESS",
    "VITAL_SIGN_ALERT",   # peringatan: detak jantung 161-180 BPM tanpa tangisan selama tunda
    "INACTIVITY_INFO",
    "POWER_WARNING",
    "IDLE_NORMAL"
]

parser = argparse.ArgumentParser(description="NeoSentinel Priority FSM Alert Engine")
parser.add_argument("--broker", default="localhost")
parser.add_argument("--port", type=int, default=1883)
parser.add_argument("--user", default="fsm")
parser.add_argument("--password", default=os.environ.get("MQTT_PASSWORD", ""))
parser.add_argument("--hr-high-delay", type=float, default=180.0,
                    help="detik detak jantung tinggi harus bertahan sebelum alarm, dipakai HANYA bila ESP32 "
                         "tidak mengirim alarm.hr_high_delay_s (uji resmi 180; demo boleh 30)")
args = parser.parse_args()

# ---------------------------------------------------------------------------
#  VITAL LIMITS - HARUS SAMA dengan raspberry-pi/dashboard/config.js
#  Sumber: dokumen "Sumber Rujukan Batas Alarm Dashboard Smart Incubator"
# ---------------------------------------------------------------------------
SKIN_SAFE = (36.2, 37.5)        # degC; di luar ini -> CODE_BLUE langsung (NHS WoS 36.2; CMNRP 37.5)
HR_NORMAL_MAX = 160             # BPM; di atas ini = di atas normal (ASU / CAHS 120-160)
HR_CRYING_MAX = 180             # BPM; saat bayi menangis 161-180 dianggap wajar (ASU)
HR_CODE_BLUE = (100, 180)       # BPM; < 100 atau > 180 -> CODE_BLUE setelah tunda
HR_DELAY_LOW = 10.0             # detik; bradikardia (NICU tanpa tunda; 10 dtk = 2 kiriman ESP32)
HR_DELAY_HIGH = args.hr_high_delay  # detik; cadangan bila ESP32 tidak mengirim tunda (Erasmus MC: 180 dtk)
HR_DELAY_RANGE = (5.0, 600.0)   # detik; tunda dari ESP32 di luar rentang ini diabaikan
DELAY_TOLERANCE = 1.0           # detik; kiriman ESP32 tiap ~5 dtk tidak pernah tepat

state_ctx = {
    "setpoint_temp": 33.0,
    "delta_temp": 1.0,
    "t_air": 33.0, "rh": 60.0,
    "t_skin": None, "hr": None, "weight_g": None,   # None = sensor tidak mengirim data
    "cry": False, "cry_since": None, "cry_reason": "none", "cry_reason_conf": 0.0,
    "mains_ok": True, "soc": 100.0,
    "code_blue": False,      # Code Blue manual (perintah dashboard lokal), belum dipakai
    "hr_since": {"low": None, "high": None, "elevated": None},   # awal tiap kondisi detak jantung
    "hr_delay_high": HR_DELAY_HIGH,   # diganti oleh alarm.hr_high_delay_s dari ESP32 (DEMO_FAST_DELAY)
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

    # 2+3. Code Blue (suhu kulit / detak jantung) dan peringatan detak jantung tinggi
    #      Aturan sama dengan dashboard (config.js). Hanya dinilai jika data sensor masih segar,
    #      supaya nilai lama dari ESP32 yang terputus tidak memicu alarm.
    fresh = now - state_ctx["last_seen"]["env"] <= 10.0
    t_skin = state_ctx["t_skin"] if fresh else None
    hr = state_ctx["hr"] if fresh else None
    code_blue_reasons = []
    if t_skin is not None and not (SKIN_SAFE[0] <= t_skin <= SKIN_SAFE[1]):
        code_blue_reasons.append(
            f"skin temperature {t_skin:.1f} C outside {SKIN_SAFE[0]}-{SKIN_SAFE[1]} C")

    since = state_ctx["hr_since"]
    low = hr is not None and hr < HR_CODE_BLUE[0]
    high = hr is not None and hr > HR_CODE_BLUE[1]
    elevated = hr is not None and not high and hr > HR_NORMAL_MAX
    # Hasil deteksi tangis hanya menjelaskan 161-180 BPM; tidak pernah memicu/membatalkan Code Blue
    elevated_alone = elevated and not (state_ctx["cry"] and hr <= HR_CRYING_MAX)
    for key, cond in (("low", low), ("high", high), ("elevated", elevated_alone)):
        since[key] = (since[key] or now) if cond else None

    def held(key, sec):
        return since[key] is not None and now - since[key] >= sec - DELAY_TOLERANCE

    if held("low", HR_DELAY_LOW):
        code_blue_reasons.append(f"heart rate {hr} BPM too slow (< {HR_CODE_BLUE[0]})")
    delay_high = state_ctx["hr_delay_high"]
    if held("high", delay_high):
        code_blue_reasons.append(f"heart rate {hr} BPM too fast (> {HR_CODE_BLUE[1]})")

    if state_ctx["code_blue"] or code_blue_reasons:
        active.append("CODE_BLUE")
        advisories.append("CODE BLUE: " + "; ".join(code_blue_reasons or ["manual"]) + ". Check the infant now.")
    if held("elevated", delay_high):
        active.append("VITAL_SIGN_ALERT")   # dipakai sebagai: detak jantung di atas normal (peringatan)
        advisories.append(f"Heart rate {hr} BPM above {HR_NORMAL_MAX} for more than {int(delay_high)} s.")

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
        "RED" if top_state in ("CODE_BLUE", "THERMAL_HAZARD", "SENSOR_FAULT") else "YELLOW"
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
            "soc": state_ctx["soc"],
            "hr_high_delay_s": state_ctx["hr_delay_high"]
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
            # Suhu kulit & HR: tidak ada data / sensor lepas -> None (bukan nilai lama)
            state_ctx["t_skin"] = t_skin
            state_ctx["hr"] = int(hr) if hr is not None else None
            # Daya: firmware mengirim {"power": {"soc_pct": .., "mains_ok": ..}}
            pw = data.get("power") or {}
            if isinstance(pw.get("mains_ok"), bool):
                state_ctx["mains_ok"] = pw["mains_ok"]
            soc = _num(pw.get("soc_pct"))
            if soc is not None:
                state_ctx["soc"] = soc
            wt = data.get("weight") or {}
            state_ctx["weight_g"] = _num(wt.get("g", wt.get("total_g")))
            # Lama tunda detak jantung tinggi mengikuti ESP32 (DEMO_FAST_DELAY: 30 / 180 dtk)
            d = _num((data.get("alarm") or {}).get("hr_high_delay_s"))
            if d is not None and HR_DELAY_RANGE[0] <= d <= HR_DELAY_RANGE[1] and d != state_ctx["hr_delay_high"]:
                print(f"[FSM] Tunda detak jantung tinggi dari ESP32: {d:g} dtk (sebelumnya {state_ctx['hr_delay_high']:g})")
                state_ctx["hr_delay_high"] = d
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
