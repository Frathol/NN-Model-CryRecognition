"""
Pemuatan model TFLite + preprocessing audio untuk cry recognition.

Dipakai bersama oleh cry_service.py (realtime) dan inspect_model.py (diagnosa).
Preprocessing dibuat fleksibel karena harus sama persis dengan saat training:
  - waveform   : sampel audio mentah (mis. model berbasis YAMNet)
  - mfcc       : matriks MFCC (n_mfcc x frame)
  - mfcc_mean  : vektor rata-rata MFCC per koefisien (n_mfcc,)
  - melspec    : (log-)mel-spectrogram (n_mels x frame)
  - auto       : ditebak dari bentuk tensor input model
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import numpy as np

log = logging.getLogger("cry_model")


def _load_interpreter_cls():
    """Cari runtime TFLite yang tersedia (LiteRT > tflite_runtime > tensorflow)."""
    try:
        from ai_edge_litert.interpreter import Interpreter
        return Interpreter
    except ImportError:
        pass
    try:
        from tflite_runtime.interpreter import Interpreter
        return Interpreter
    except ImportError:
        pass
    try:
        import tensorflow as tf
        return tf.lite.Interpreter
    except ImportError as e:
        raise ImportError(
            "Runtime TFLite tidak ditemukan. Install: pip install ai-edge-litert"
        ) from e


@dataclass
class FeaturePlan:
    """Hasil penentuan preprocessing yang akan dipakai."""
    type: str                 # waveform | mfcc | mfcc_mean | melspec
    sample_rate: int
    window_samples: int       # panjang audio per inferensi
    n_coef: int = 0           # n_mfcc atau n_mels
    n_frames: int = 0         # jumlah frame waktu yang diharapkan model
    freq_first: bool = True   # True: (koef, waktu); False: (waktu, koef)
    guessed: bool = False

    def describe(self) -> str:
        sec = self.window_samples / self.sample_rate
        s = f"type={self.type}, sr={self.sample_rate}, window={sec:.2f}s ({self.window_samples} sampel)"
        if self.type in ("mfcc", "melspec"):
            order = "(koef, waktu)" if self.freq_first else "(waktu, koef)"
            s += f", koef={self.n_coef}, frame={self.n_frames}, layout={order}"
        elif self.type == "mfcc_mean":
            s += f", n_mfcc={self.n_coef}"
        if self.guessed:
            s += "  [DITEBAK OTOMATIS - verifikasi dengan kode training!]"
        return s


class CryModel:
    def __init__(self, model_path: str | Path, labels: list[str], feat_cfg: dict,
                 positive_label: str = "cry", num_threads: int = 4):
        Interpreter = _load_interpreter_cls()
        self.interp = Interpreter(model_path=str(model_path), num_threads=num_threads)
        self.interp.allocate_tensors()
        self.inp = self.interp.get_input_details()[0]
        self.out = self.interp.get_output_details()[0]
        self.input_shape = tuple(int(x) for x in self.inp["shape"])
        self.labels = labels
        self.positive_label = positive_label
        self.cfg = feat_cfg
        self.plan = self._make_plan()

    # ------------------------------------------------------------------
    #  Penentuan preprocessing
    # ------------------------------------------------------------------
    def _core_dims(self) -> list[int]:
        """Bentuk input tanpa dimensi batch & channel bernilai 1."""
        dims = list(self.input_shape)
        if len(dims) > 1 and dims[0] == 1:
            dims = dims[1:]
        core = [d for d in dims if d != 1]
        return core or [1]

    def _make_plan(self) -> FeaturePlan:
        c = self.cfg
        sr = int(c.get("sample_rate", 22050))
        hop = int(c.get("hop_length", 512))
        ftype = c.get("type", "auto")
        n_mfcc = int(c.get("n_mfcc", 20))
        n_mels = int(c.get("n_mels", 128))
        win_cfg = float(c.get("window_seconds", 0) or 0)
        core = self._core_dims()
        guessed = ftype == "auto"

        if ftype == "auto":
            if len(core) == 1:
                ftype = "waveform" if core[0] >= 2000 else "mfcc_mean"
            elif len(core) == 2:
                a, b = core
                if n_mfcc in (a, b):
                    ftype = "mfcc"
                elif n_mels in (a, b):
                    ftype = "melspec"
                else:
                    ftype = "mfcc" if min(a, b) <= 40 else "melspec"
            else:
                raise ValueError(f"Bentuk input {self.input_shape} tidak dikenali; "
                                 "set features.type secara manual.")

        if ftype == "waveform":
            n = core[0] if len(core) == 1 else int(np.prod(core))
            return FeaturePlan("waveform", sr, n, guessed=guessed)

        if ftype == "mfcc_mean":
            n_coef = core[0] if len(core) == 1 else n_mfcc
            win = int((win_cfg or 2.0) * sr)
            return FeaturePlan("mfcc_mean", sr, win, n_coef=n_coef, guessed=guessed)

        # mfcc / melspec 2D
        want = n_mfcc if ftype == "mfcc" else n_mels
        a, b = core[0], core[1]
        if a == want:
            freq_first, n_coef, n_frames = True, a, b
        elif b == want:
            freq_first, n_coef, n_frames = False, b, a
        else:   # tebak: dimensi lebih kecil = koefisien
            freq_first = a <= b
            n_coef, n_frames = (a, b) if freq_first else (b, a)
            guessed = True
            log.warning("n_%s di config (%d) tidak cocok dengan input %s; memakai %d koefisien",
                        "mfcc" if ftype == "mfcc" else "mels", want, self.input_shape, n_coef)
        # librosa (center=True): frames = 1 + floor(N / hop)
        win = int(win_cfg * sr) if win_cfg else (n_frames - 1) * hop
        return FeaturePlan(ftype, sr, win, n_coef=n_coef, n_frames=n_frames,
                           freq_first=freq_first, guessed=guessed)

    # ------------------------------------------------------------------
    #  Ekstraksi fitur
    # ------------------------------------------------------------------
    def features(self, audio: np.ndarray) -> np.ndarray:
        import librosa

        p, c = self.plan, self.cfg
        y = np.asarray(audio, dtype=np.float32)
        if len(y) < p.window_samples:
            y = np.pad(y, (0, p.window_samples - len(y)))
        y = y[-p.window_samples:]

        if p.type == "waveform":
            x = y
        else:
            n_fft, hop = int(c.get("n_fft", 2048)), int(c.get("hop_length", 512))
            if p.type in ("mfcc", "mfcc_mean"):
                x = librosa.feature.mfcc(y=y, sr=p.sample_rate, n_mfcc=p.n_coef,
                                         n_fft=n_fft, hop_length=hop)
                if p.type == "mfcc_mean":
                    x = x.mean(axis=1)
            else:
                x = librosa.feature.melspectrogram(y=y, sr=p.sample_rate, n_fft=n_fft,
                                                   hop_length=hop, n_mels=p.n_coef)
                if c.get("power_to_db", True):
                    x = librosa.power_to_db(x, ref=np.max)

            if x.ndim == 2:   # sesuaikan jumlah frame (pad/crop)
                t = x.shape[1]
                if t < p.n_frames:
                    x = np.pad(x, ((0, 0), (0, p.n_frames - t)), mode="constant",
                               constant_values=x.min())
                x = x[:, :p.n_frames]
                if not p.freq_first:
                    x = x.T

        norm = c.get("normalize", "none")
        if norm == "standardize":
            x = (x - x.mean()) / (x.std() + 1e-8)
        elif norm == "minmax":
            x = (x - x.min()) / (x.max() - x.min() + 1e-8)
        return x.astype(np.float32)

    # ------------------------------------------------------------------
    #  Inferensi
    # ------------------------------------------------------------------
    def _quantize(self, x: np.ndarray) -> np.ndarray:
        dtype = self.inp["dtype"]
        if dtype == np.float32:
            return x.astype(np.float32)
        scale, zp = self.inp["quantization"]
        if scale == 0:
            return x.astype(dtype)
        info = np.iinfo(dtype)
        return np.clip(np.round(x / scale + zp), info.min, info.max).astype(dtype)

    def _dequantize(self, y: np.ndarray) -> np.ndarray:
        if self.out["dtype"] == np.float32:
            return y.astype(np.float32)
        scale, zp = self.out["quantization"]
        return (y.astype(np.float32) - zp) * (scale or 1.0)

    def predict(self, audio: np.ndarray) -> dict:
        x = self.features(audio).reshape(self.input_shape)
        self.interp.set_tensor(self.inp["index"], self._quantize(x))
        self.interp.invoke()
        raw = self._dequantize(self.interp.get_tensor(self.out["index"])).ravel()

        if raw.size == 1:   # output sigmoid tunggal = P(positive_label)
            p = float(raw[0])
            if not 0.0 <= p <= 1.0:
                p = float(1.0 / (1.0 + np.exp(-p)))
            others = [l for l in self.labels if l != self.positive_label] or ["not_" + self.positive_label]
            probs = {self.positive_label: p, others[0]: 1.0 - p}
        else:
            v = raw
            if v.min() < 0 or not np.isclose(v.sum(), 1.0, atol=1e-2):   # logits -> softmax
                e = np.exp(v - v.max())
                v = e / e.sum()
            names = self.labels if len(self.labels) == v.size else [f"class_{i}" for i in range(v.size)]
            probs = {n: float(pv) for n, pv in zip(names, v)}

        label = max(probs, key=probs.get)
        return {
            "label": label,
            "confidence": round(probs[label], 4),
            "cry_prob": round(probs.get(self.positive_label, 0.0), 4),
            "probabilities": {k: round(v, 4) for k, v in probs.items()},
        }


def load_labels(path: str | Path) -> list[str]:
    p = Path(path)
    if not p.exists():
        return []
    return [l.strip() for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]
