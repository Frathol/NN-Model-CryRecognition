#!/usr/bin/env python3
"""
Uji model cry recognition 2 tahap (tanpa MQTT, tanpa dashboard).

  Tahap 1 (model/cry_model.tflite)          : tangisan atau bukan -> P(cry)
  Tahap 2 (model/stage2_reason_int8.tflite) : alasan tangisan, HANYA jika P(cry) >= ambang

Preprocessing memakai features.py (salinan dari NN-Model-CryRecognition), sama persis
dengan saat training: audio 8 kHz, jendela 1 dtk geser 0,5 dtk, log-mel 40 band,
normalisasi mean/std dataset dari model/stage*_norm.json.

Pemakaian:
  .venv/bin/python test_model.py rekaman.wav                 # detail per jendela
  .venv/bin/python test_model.py dataset/*.wav --quiet       # ringkasan per file
  .venv/bin/python test_model.py --mic                       # mic default, real-time
  .venv/bin/python test_model.py --mic --device 1 --sr 48000 # pilih mic & sample rate
  .venv/bin/python test_model.py --list-devices
"""
import argparse
import collections
import json
import sys
import time
from math import gcd
from pathlib import Path

import numpy as np
from scipy.signal import resample_poly

from features import logmel, rms_dbfs, SR, WIN_SAMPLES, HOP_SAMPLES, SILENCE_DBFS

BASE = Path(__file__).resolve().parent
MODEL_DIR = BASE / "model"

try:
    from ai_edge_litert.interpreter import Interpreter
except ImportError:
    try:
        from tflite_runtime.interpreter import Interpreter
    except ImportError:
        import tensorflow as tf
        Interpreter = tf.lite.Interpreter


# ---------------------------------------------------------------------
#  Model
# ---------------------------------------------------------------------
def load_stage(tflite: Path, norm: Path):
    meta = json.loads(norm.read_text(encoding="utf-8"))
    it = Interpreter(model_path=str(tflite))
    it.allocate_tensors()
    inp, out = it.get_input_details()[0], it.get_output_details()[0]
    mean = np.array(meta["mean"], dtype=np.float32)
    std = np.array(meta["std"], dtype=np.float32)

    def run(lm):
        x = (lm - mean) / std
        if inp["dtype"] == np.int8:
            s, z = inp["quantization"]
            x = np.clip(np.round(x / s + z), -128, 127).astype(np.int8)
        it.set_tensor(inp["index"], x.astype(inp["dtype"]))
        it.invoke()
        y = it.get_tensor(out["index"]).astype(np.float32)
        if out["dtype"] == np.int8:
            s, z = out["quantization"]
            y = (y - z) * s
        return y[0]
    return run, meta


stage1, META1 = load_stage(MODEL_DIR / "cry_model.tflite", MODEL_DIR / "stage1_norm.json")
stage2, META2 = load_stage(MODEL_DIR / "stage2_reason_int8.tflite", MODEL_DIR / "stage2_norm.json")
THRESHOLD = float(META1.get("threshold", 0.85))
N_CONSEC = int(META1.get("consecutive_windows", 3))
FLOOR_DB = float(META1["floor_db"])
REASONS = META2["labels"]


def analyze(w8k: np.ndarray):
    """Satu jendela 1 dtk @8 kHz -> (dBFS, P(cry), probabilitas alasan atau None)."""
    db = float(rms_dbfs(w8k[None, :])[0])
    if db < SILENCE_DBFS:
        return db, 0.0, None                         # hening: model tidak dijalankan
    lm = np.maximum(logmel(w8k[None, :]), FLOOR_DB)
    p_cry = float(stage1(lm)[1])                     # output [non_cry, cry]
    reason = stage2(lm) if p_cry >= THRESHOLD else None
    return db, p_cry, reason


def to_8k(y: np.ndarray, sr: int) -> np.ndarray:
    if sr == SR:
        return y.astype(np.float32)
    g = gcd(SR, sr)
    return resample_poly(y, SR // g, sr // g).astype(np.float32)


def reason_text(r):
    return f"{REASONS[int(np.argmax(r))]} ({float(np.max(r)):.2f})"


# ---------------------------------------------------------------------
#  Mode file
# ---------------------------------------------------------------------
def test_files(paths, quiet):
    import soundfile as sf
    for path in paths:
        if not Path(path).is_file():
            print(f"!! File tidak ditemukan: {path}  (cek nama & folder; posisi sekarang: {Path.cwd()})\n")
            continue
        try:
            y, sr = sf.read(path, dtype="float32", always_2d=True)
        except Exception as e:   # noqa: BLE001
            print(f"!! Gagal membaca {path}: {e}  (format didukung: wav, flac, ogg)\n")
            continue
        y = to_8k(y.mean(axis=1), sr)
        if len(y) < WIN_SAMPLES:
            y = np.pad(y, (0, WIN_SAMPLES - len(y)))

        p_list, r_list = [], []
        if not quiet:
            print(f"== {path}  ({len(y) / SR:.1f} dtk)")
            print("   waktu    dBFS  P(cry)  tahap 2")
        for s in range(0, len(y) - WIN_SAMPLES + 1, HOP_SAMPLES):
            db, p, r = analyze(y[s:s + WIN_SAMPLES])
            p_list.append(p)
            if r is not None:
                r_list.append(r)
            if not quiet:
                tag = "hening" if db < SILENCE_DBFS else (reason_text(r) if r is not None else "-")
                print(f"  {s / SR:5.1f}s  {db:6.1f}  {p:6.3f}  {tag}")

        pa = np.array(p_list)
        smooth = np.convolve(pa, np.ones(N_CONSEC) / N_CONSEC, "valid") if len(pa) >= N_CONSEC else pa[:0]
        crying = bool(np.any(smooth >= THRESHOLD))
        line = (f"{path}\n   -> {'MENANGIS' if crying else 'tidak menangis'} | "
                f"P(cry) maks {pa.max():.3f}, rata-rata {pa.mean():.3f}")
        if crying and r_list:
            mr = np.mean(r_list, axis=0)
            line += " | alasan: " + ", ".join(f"{REASONS[i]} {mr[i]:.2f}" for i in np.argsort(mr)[::-1][:3])
        print(line + ("" if quiet else "\n"))


# ---------------------------------------------------------------------
#  Mode mic (real-time)
# ---------------------------------------------------------------------
def test_mic(device, sr_in):
    import sounddevice as sd
    if sr_in is None:
        sr_in = int(sd.query_devices(device, "input")["default_samplerate"])
    hop = sr_in // 2                                 # geser 0,5 dtk
    blocks = collections.deque(maxlen=2)             # 2 x 0,5 dtk = jendela 1 dtk
    scores = collections.deque(maxlen=N_CONSEC)
    reasons = collections.deque(maxlen=5)
    crying = False

    name = sd.query_devices(device, "input")["name"]
    print(f"Mic: {name} @ {sr_in} Hz. Ambang P(cry) {THRESHOLD}, {N_CONSEC} jendela berturut-turut.")
    print("Ctrl+C untuk berhenti.\n   jam      dBFS  P(cry)  tahap 2               status")
    with sd.InputStream(samplerate=sr_in, channels=1, dtype="float32", blocksize=hop, device=device) as st:
        while True:
            blk, overflow = st.read(hop)
            blocks.append(blk[:, 0].copy())
            if len(blocks) < 2:
                continue
            db, p, r = analyze(to_8k(np.concatenate(blocks), sr_in)[:WIN_SAMPLES])
            scores.append(p)
            if r is not None:
                reasons.append(r)
            now_crying = len(scores) == N_CONSEC and float(np.mean(scores)) >= THRESHOLD
            if not now_crying and p < THRESHOLD:
                reasons.clear()
            status = ""
            if now_crying:
                status = "BAYI MENANGIS"
                if reasons:
                    status += f" -> {reason_text(np.mean(reasons, axis=0))}"
            elif crying:
                status = "(tangisan berhenti)"
            crying = now_crying
            tag = "hening" if db < SILENCE_DBFS else (reason_text(r) if r is not None else "-")
            print(f"  {time.strftime('%H:%M:%S')}  {db:6.1f}  {p:6.3f}  {tag:20s}  {status}"
                  + ("  [overflow]" if overflow else ""))


# ---------------------------------------------------------------------
ap = argparse.ArgumentParser(description="Uji model cry recognition 2 tahap")
ap.add_argument("files", nargs="*", help="file audio (wav/flac/ogg, sample rate bebas)")
ap.add_argument("--quiet", action="store_true", help="mode file: ringkasan per file saja")
ap.add_argument("--mic", action="store_true", help="uji real-time dari mikrofon")
ap.add_argument("--device", default=None, help="index atau nama mic (lihat --list-devices)")
ap.add_argument("--sr", type=int, default=None, help="sample rate mic (default: bawaan perangkat)")
ap.add_argument("--list-devices", action="store_true")
args = ap.parse_args()

if args.list_devices:
    import sounddevice as sd
    print(sd.query_devices())
elif args.mic:
    dev = int(args.device) if args.device is not None and str(args.device).isdigit() else args.device
    try:
        test_mic(dev, args.sr)
    except KeyboardInterrupt:
        print("\nSelesai.")
elif args.files:
    print(f"Ambang P(cry) = {THRESHOLD}, butuh {N_CONSEC} jendela berturut-turut, "
          f"hening < {SILENCE_DBFS} dBFS\n")
    test_files(args.files, args.quiet)
else:
    ap.print_help()
    sys.exit(1)
