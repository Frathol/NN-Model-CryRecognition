#!/usr/bin/env python3
import os, sys, json, time, argparse, collections
from math import gcd
import numpy as np
from scipy.signal import resample_poly
import paho.mqtt.client as mqtt
from features import logmel, rms_dbfs, SILENCE_DBFS

try:
    from ai_edge_litert.interpreter import Interpreter
except ImportError:
    try:
        from tflite_runtime.interpreter import Interpreter
    except ImportError:
        import tensorflow as tf
        Interpreter = tf.lite.Interpreter

parser = argparse.ArgumentParser(description="NeoSentinel Raspberry Pi Two-Stage Audio Cry Detector")
parser.add_argument("--broker", default="localhost")
parser.add_argument("--port", type=int, default=1883)
parser.add_argument("--user", default="cryservice")
parser.add_argument("--password", default=os.environ.get("MQTT_PASSWORD", ""))
parser.add_argument("--device", type=int, default=None)
parser.add_argument("--sr-in", type=int, default=untuk model tflite apsayaakah sudah 48000)
parser.add_argument("--simulate", action="store_true")
args = parser.parse_args()

# Muat Model Stage 1 (Cry vs Non-Cry)
with open("models/stage1_norm.json", "r", encoding="utf-8") as f:
    meta1 = json.load(f)
MEAN1 = np.array(meta1["mean"], dtype=np.float32)
STD1 = np.array(meta1["std"], dtype=np.float32)
FLOOR_DB = float(meta1["floor_db"])
THRESHOLD = float(meta1.get("threshold", 0.85))

it1 = Interpreter(model_path="models/stage1_cry_int8.tflite")
it1.allocate_tensors()
inp1, out1 = it1.get_input_details()[0], it1.get_output_details()[0]
s1_in, z1_in = inp1["quantization"]
s1_out, z1_out = out1["quantization"]

# Muat Model Stage 2 (5 Cry Reasons - Advisory)
with open("models/stage2_norm.json", "r", encoding="utf-8") as f:
    meta2 = json.load(f)
MEAN2 = np.array(meta2["mean"], dtype=np.float32)
STD2 = np.array(meta2["std"], dtype=np.float32)
REASON_LABELS = meta2["labels"]

it2 = Interpreter(model_path="models/stage2_reason_int8.tflite")
it2.allocate_tensors()
inp2, out2 = it2.get_input_details()[0], it2.get_output_details()[0]
s2_in, z2_in = inp2["quantization"]
s2_out, z2_out = out2["quantization"]

def window_logmel(w8k):
    return np.maximum(logmel(w8k[None, :]), FLOOR_DB)

def run_stage1(lm):
    """Tahap 1: tangisan atau bukan. Output softmax [non_cry, cry] -> P(cry)."""
    x1 = (lm - MEAN1) / STD1
    x1_q = np.clip(np.round(x1 / s1_in + z1_in), -128, 127).astype(np.int8)
    it1.set_tensor(inp1["index"], x1_q)
    it1.invoke()
    p1 = (it1.get_tensor(out1["index"]).astype(np.float32) - z1_out) * s1_out
    return float(p1[0, 1])

def run_stage2(lm):
    """Tahap 2: alasan tangisan (5 kelas, advisory). Hanya dipanggil untuk jendela tangisan."""
    x2 = (lm - MEAN2) / STD2
    x2_q = np.clip(np.round(x2 / s2_in + z2_in), -128, 127).astype(np.int8)
    it2.set_tensor(inp2["index"], x2_q)
    it2.invoke()
    p2 = (it2.get_tensor(out2["index"]).astype(np.float32) - z2_out) * s2_out
    return p2[0]

# Topic MQTT (sama dengan proyek smart-incubator / dashboard)
TOPIC_RESULT = "incubator/cry/result"   # hasil tiap jendela
TOPIC_STATE = "incubator/cry/state"     # status menangis (retained, hanya saat berubah)
TOPIC_STATUS = "incubator/cry/status"   # online / offline (retained, Last Will)

cry_state = {"crying": False, "since": None, "reason": "none"}

def publish_state(p_cry=0.0, reason="none", reason_conf=0.0, reason_probs=None):
    payload = {"crying": cry_state["crying"], "since": cry_state["since"],
               "cry_prob": round(p_cry, 4), "reason": reason, "reason_conf": reason_conf,
               "reason_probs": reason_probs or {}, "ts": time.time()}
    client.publish(TOPIC_STATE, json.dumps(payload), qos=1, retain=True)

def on_connect(client, userdata, flags, rc, props=None):
    if rc == 0:
        client.publish(TOPIC_STATUS, "online", qos=1, retain=True)
        publish_state()
    else:
        print("[MQTT] Ditolak:", rc, "(cek username/password & ACL)")

client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="audio_pi")
client.username_pw_set(args.user, args.password)
client.will_set(TOPIC_STATUS, "offline", qos=1, retain=True)
client.on_connect = on_connect
client.connect(args.broker, args.port, keepalive=10)
client.loop_start()

win_len = int(meta1.get("consecutive_windows", 3))
scores = collections.deque(maxlen=win_len)
# Probabilitas tahap 2 dari jendela-jendela tangisan terakhir (dirata-rata agar stabil)
reason_Buf = collections.deque(maxlen=5)
seq = 0

def step_window(w):
    global seq
    t0 = time.perf_counter()
    is_silence = False
    stage2_ran = False
    db = float(rms_dbfs(w[None, :])[0])

    # ---- Tahap 1: tangisan atau bukan ----
    if db < SILENCE_DBFS:
        is_silence = True
        p_val = 0.0          # hening -> model tidak dijalankan
    else:
        lm = window_logmel(w)
        p_val = run_stage1(lm)
    is_cry_win = bool(p_val >= THRESHOLD)

    # ---- Tahap 2: alasan tangisan, HANYA jika jendela ini tangisan ----
    if is_cry_win:
        reason_Buf.append(run_stage2(lm))
        stage2_ran = True
    inf_ms = (time.perf_counter() - t0) * 1000.0

    # Keputusan "menangis" = rata-rata P(cry) beberapa jendela berturut-turut >= ambang
    scores.append(p_val)
    rule = float(np.mean(scores)) if len(scores) == scores.maxlen else 0.0
    is_crying = bool(rule >= THRESHOLD)

    reason_label, reason_conf, reason_probs = "none", 0.0, {}
    if is_crying and reason_Buf:
        mean_r = np.mean(reason_Buf, axis=0)
        best_idx = int(np.argmax(mean_r))
        reason_label = REASON_LABELS[best_idx]
        reason_conf = round(float(mean_r[best_idx]), 3)
        reason_probs = {lbl: round(float(p), 3) for lbl, p in zip(REASON_LABELS, mean_r)}
    elif not is_crying and not is_cry_win:
        reason_Buf.clear()   # episode tangisan selesai -> mulai baru

    seq += 1
    label = "silence" if is_silence else ("cry" if is_cry_win else "not_cry")
    payload = {
        "dev": "pi", "ts": time.time(), "seq": seq,
        # field yang dibaca dashboard smart-incubator
        "label": label, "confidence": round(p_val if is_cry_win else 1.0 - p_val, 3),
        "cry_prob": round(p_val, 3), "level_db": round(db, 1),
        "is_cry": is_cry_win, "latency_ms": round(inf_ms, 2),
        # field asli NeoSentinel (dipakai fsm.py & mqtt_logger.py)
        "cry": is_crying, "p_cry": round(p_val, 3), "rule": round(rule, 3),
        "reason": reason_label, "reason_conf": reason_conf, "reason_probs": reason_probs,
        "stage2_ran": stage2_ran,
        "db": round(db, 1), "infer_ms": round(inf_ms, 2)
    }
    client.publish(TOPIC_RESULT, json.dumps(payload), qos=0)

    # Status di-publish saat mulai/berhenti menangis, atau saat alasan tangisan berubah (retained)
    if is_crying != cry_state["crying"] or (is_crying and reason_label != cry_state["reason"]):
        if is_crying != cry_state["crying"]:
            cry_state["since"] = time.time() if is_crying else None
        cry_state["crying"] = is_crying
        cry_state["reason"] = reason_label
        publish_state(rule, reason_label, reason_conf, reason_probs)
    return payload

if args.simulate:
    while True:
        phase = (seq // 10) % 3
        t = np.linspace(0, 1.0, 8000, endpoint=False, dtype=np.float32)
        if phase == 0:
            w = np.zeros(8000, dtype=np.float32)
        elif phase == 1:
            w = np.random.normal(0, 0.05, 8000).astype(np.float32)
        else:
            w = (0.3 * np.sin(2 * np.pi * 450 * t) + 0.2 * np.sin(2 * np.pi * 900 * t)).astype(np.float32)
        print("Published:", step_window(w))
        time.sleep(0.5)
else:
    import sounddevice as sd
    SR_IN = args.sr_in
    g, HOP = gcd(8000, SR_IN), SR_IN // 2
    blocks = collections.deque(maxlen=2)
    with sd.InputStream(samplerate=SR_IN, channels=1, dtype="float32", blocksize=HOP, device=args.device) as stream:
        while True:
            blk, _ = stream.read(HOP)
            blocks.append(blk[:, 0])
            if len(blocks) < 2:
                continue
            w = resample_poly(np.concatenate(blocks), 8000 // g, SR_IN // g).astype(np.float32)
            step_window(w)
