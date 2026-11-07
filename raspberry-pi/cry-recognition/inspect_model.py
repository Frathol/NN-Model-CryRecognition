#!/usr/bin/env python3
"""
Diagnosa model TFLite: tampilkan bentuk input/output dan preprocessing yang
akan dipakai cry_service.py. Jalankan ini SEBELUM menjalankan layanan.

  python inspect_model.py                 # pakai config.yaml
  python inspect_model.py --wav contoh.wav  # sekalian uji 1 file audio
"""
import argparse
import logging
from pathlib import Path

import numpy as np

from cry_service import BASE, build_model, load_config


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-c", "--config", default=str(BASE / "config.yaml"))
    ap.add_argument("--wav")
    args = ap.parse_args()
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")

    cfg = load_config(Path(args.config))
    m = build_model(cfg)

    print("=" * 64)
    print("File model   :", cfg["model"]["path"])
    for d in m.interp.get_input_details():
        print(f"Input        : {d['name']} shape={[int(v) for v in d['shape']]} dtype={d['dtype'].__name__}"
              f" quant={d['quantization']}")
    for d in m.interp.get_output_details():
        print(f"Output       : {d['name']} shape={[int(v) for v in d['shape']]} dtype={d['dtype'].__name__}")
    print("Labels       :", m.labels or "(labels.txt kosong)")
    print("Preprocessing:", m.plan.describe())
    if m.plan.type == "waveform" and m.plan.window_samples == 15600 and m.plan.sample_rate != 16000:
        print("!! Input 15600 sampel = ciri model YAMNet (16 kHz). Set features.sample_rate: 16000")

    out_n = int(np.prod(m.out["shape"]))
    if out_n > 1 and len(m.labels) != out_n:
        print(f"\n!! Jumlah output model ({out_n}) != jumlah label ({len(m.labels)}). Perbaiki labels.txt")

    # Uji jalan dengan noise agar ketahuan jika bentuk fitur tidak cocok
    noise = (np.random.randn(m.plan.window_samples) * 0.05).astype(np.float32)
    feat = m.features(noise)
    print("Bentuk fitur :", feat.shape, "-> di-reshape ke", m.input_shape)
    print("Uji noise    :", m.predict(noise))

    if args.wav:
        import librosa
        y, _ = librosa.load(args.wav, sr=m.plan.sample_rate, mono=True)
        print(f"Uji {args.wav}:", m.predict(y))

    print("=" * 64)
    print("Cocokkan 'Preprocessing' di atas dengan kode training Anda:\n"
          "  - sample rate librosa.load(sr=...)\n"
          "  - n_mfcc / n_mels, n_fft, hop_length\n"
          "  - normalisasi (mis. (x-mean)/std) dan power_to_db\n"
          "Lalu uji dengan beberapa file dari dataset training:\n"
          "  python cry_service.py --test-file path/ke/tangisan.wav")


if __name__ == "__main__":
    main()
