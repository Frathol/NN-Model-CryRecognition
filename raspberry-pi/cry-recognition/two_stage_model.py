"""
Model cry recognition 2 tahap (dari NN-Model-CryRecognition), dipakai cry_service.py.

  Tahap 1 (cry_model.tflite)          : tangisan atau bukan  -> output [non_cry, cry]
  Tahap 2 (stage2_reason_int8.tflite) : alasan tangisan (5 kelas), HANYA untuk jendela
                                         yang lolos tahap 1 (P(cry) >= threshold)

Preprocessing = features.py (salinan dari repo model), sama persis dengan saat training:
audio 8 kHz, jendela 1 dtk, log-mel 40 band, normalisasi mean/std dataset dari *_norm.json.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from features import FLOOR_DB, SR, WIN_SAMPLES, logmel


def _interpreter_cls():
    try:
        from ai_edge_litert.interpreter import Interpreter
    except ImportError:
        try:
            from tflite_runtime.interpreter import Interpreter
        except ImportError:
            import tensorflow as tf
            Interpreter = tf.lite.Interpreter
    return Interpreter


@dataclass
class Plan:
    """Dipakai cry_service.py untuk mengetahui sample rate & panjang jendela."""
    sample_rate: int = SR
    window_samples: int = WIN_SAMPLES

    def describe(self) -> str:
        return (f"two_stage log-mel 40 band @ {self.sample_rate} Hz, "
                f"jendela {self.window_samples / self.sample_rate:.1f} dtk (features.py)")


class _Stage:
    def __init__(self, tflite: Path, norm: Path, num_threads: int):
        self.meta = json.loads(norm.read_text(encoding="utf-8"))
        Interpreter = _interpreter_cls()
        try:
            self.it = Interpreter(model_path=str(tflite), num_threads=num_threads)
        except TypeError:
            self.it = Interpreter(model_path=str(tflite))
        self.it.allocate_tensors()
        self.inp = self.it.get_input_details()[0]
        self.out = self.it.get_output_details()[0]
        self.mean = np.array(self.meta["mean"], dtype=np.float32)
        self.std = np.array(self.meta["std"], dtype=np.float32)

    def __call__(self, lm: np.ndarray) -> np.ndarray:
        x = (lm - self.mean) / self.std
        if self.inp["dtype"] == np.int8:
            s, z = self.inp["quantization"]
            x = np.clip(np.round(x / s + z), -128, 127).astype(np.int8)
        self.it.set_tensor(self.inp["index"], x.astype(self.inp["dtype"]))
        self.it.invoke()
        y = self.it.get_tensor(self.out["index"]).astype(np.float32)
        if self.out["dtype"] == np.int8:
            s, z = self.out["quantization"]
            y = (y - z) * s
        return y[0]


class TwoStageModel:
    def __init__(self, model_dir: str | Path, num_threads: int = 4,
                 stage1="cry_model.tflite", stage1_norm="stage1_norm.json",
                 stage2="stage2_reason_int8.tflite", stage2_norm="stage2_norm.json"):
        d = Path(model_dir)
        self.s1 = _Stage(d / stage1, d / stage1_norm, num_threads)
        self.s2 = _Stage(d / stage2, d / stage2_norm, num_threads)
        self.threshold = float(self.s1.meta.get("threshold", 0.85))
        self.consecutive = int(self.s1.meta.get("consecutive_windows", 3))
        self.reasons = list(self.s2.meta["labels"])
        self.plan = Plan()
        self.input_shape = tuple(int(v) for v in self.s1.inp["shape"])
        self.inp = self.s1.inp

    def predict(self, y8k: np.ndarray) -> dict:
        """y8k: 1 dtk audio float32 @ 8 kHz (sudah lolos cek hening)."""
        w = np.asarray(y8k, dtype=np.float32)
        if len(w) < WIN_SAMPLES:
            w = np.pad(w, (0, WIN_SAMPLES - len(w)))
        lm = np.maximum(logmel(w[None, :WIN_SAMPLES]), FLOOR_DB)

        p = self.s1(lm)
        p_cry = float(p[1])
        res = {
            "label": "cry" if p_cry >= self.threshold else "non_cry",
            "confidence": round(p_cry if p_cry >= self.threshold else 1.0 - p_cry, 4),
            "cry_prob": round(p_cry, 4),
            "probabilities": {"non_cry": round(float(p[0]), 4), "cry": round(p_cry, 4)},
        }
        if p_cry >= self.threshold:                       # tahap 2 hanya untuk tangisan
            r = self.s2(lm)
            i = int(np.argmax(r))
            res["reason_probs"] = {lbl: round(float(v), 4) for lbl, v in zip(self.reasons, r)}
            res["window_reason"] = self.reasons[i]
        return res
