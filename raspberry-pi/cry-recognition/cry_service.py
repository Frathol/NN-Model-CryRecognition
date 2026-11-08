#!/usr/bin/env python3
"""
Layanan cry recognition Smart Incubator (Raspberry Pi 5).

Mic Bluetooth (PipeWire) -> buffer audio -> model TFLite 2 tahap -> MQTT
Topic (prefix default incubator/cry):
  .../result  hasil tiap jendela  {label, cry_prob, level_db, reason, source, ...}
  .../state   status menangis (retained) {crying, since, cry_prob, reason, reason_conf}
  .../status  online | no_mic | offline (retained, LWT)

Pemakaian:
  python cry_service.py                       # ONLINE: mic real-time -> dashboard
  python cry_service.py --replay a.wav        # OFFLINE: putar file (real-time) -> dashboard
  python cry_service.py --replay a.wav --loop # ulangi terus
  python cry_service.py --test-file a.wav     # uji model pada file (tanpa mic/MQTT)
  python cry_service.py --list-devices        # daftar perangkat audio input
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import sys
import threading
import time
from collections import deque
from math import gcd
from pathlib import Path

import numpy as np
import yaml

from cry_model import CryModel, load_labels

BASE = Path(__file__).resolve().parent
log = logging.getLogger("cry_service")


# ----------------------------------------------------------------------
def load_config(path: Path) -> dict:
    cfg = yaml.safe_load(path.read_text(encoding="utf-8"))
    cfg["mqtt"]["password"] = cfg["mqtt"].get("password") or os.environ.get("MQTT_PASSWORD", "")
    for key in ("path", "labels", "dir"):
        if key in cfg["model"]:
            p = Path(cfg["model"][key])
            cfg["model"][key] = str(p if p.is_absolute() else BASE / p)
    return cfg


def build_model(cfg: dict):
    if cfg["model"].get("type") == "two_stage":
        from two_stage_model import TwoStageModel
        model = TwoStageModel(cfg["model"].get("dir", str(BASE / "model")),
                              num_threads=int(cfg["model"].get("num_threads", 4)))
        log.info("Model 2 tahap: input %s, ambang %.2f, alasan %s",
                 model.input_shape, model.threshold, ", ".join(model.reasons))
        log.info("Preprocessing: %s", model.plan.describe())
        return model

    labels = load_labels(cfg["model"]["labels"])
    model = CryModel(cfg["model"]["path"], labels, cfg["features"],
                     positive_label=cfg["model"].get("positive_label", "cry"),
                     num_threads=int(cfg["model"].get("num_threads", 4)))
    log.info("Model input %s %s", model.input_shape, model.inp["dtype"].__name__)
    log.info("Preprocessing: %s", model.plan.describe())
    return model


def resample(y: np.ndarray, src: int, dst: int) -> np.ndarray:
    if src == dst:
        return y
    from scipy.signal import resample_poly
    g = gcd(src, dst)
    return resample_poly(y, dst // g, src // g).astype(np.float32)


def level_db(y: np.ndarray) -> float:
    rms = float(np.sqrt(np.mean(np.square(y)) + 1e-12))
    return 20.0 * np.log10(rms)


# ----------------------------------------------------------------------
class AudioRing:
    """Menyimpan N detik audio terakhir dari callback sounddevice."""

    def __init__(self, seconds: float, rate: int):
        self.seconds = seconds
        self.rate = rate
        self.buf = deque(maxlen=int(seconds * rate))
        self.lock = threading.Lock()
        self.last_data = time.monotonic()

    def reset(self, rate: int):
        """Dipanggil jika mic ternyata berjalan di sample rate lain."""
        with self.lock:
            self.rate = rate
            self.buf = deque(maxlen=int(self.seconds * rate))
            self.last_data = time.monotonic()

    def push(self, block: np.ndarray):
        with self.lock:
            self.buf.extend(block)
            self.last_data = time.monotonic()

    def latest(self, n: int) -> np.ndarray:
        with self.lock:
            data = np.fromiter(self.buf, dtype=np.float32, count=len(self.buf))
        return data[-n:]


def find_device(spec):
    import sounddevice as sd
    if spec is None or spec == "":
        return None
    if isinstance(spec, int):
        return spec
    for i, d in enumerate(sd.query_devices()):
        if d["max_input_channels"] > 0 and str(spec).lower() in d["name"].lower():
            return i
    raise RuntimeError(f"Perangkat input '{spec}' tidak ditemukan")


def open_stream(cfg: dict, ring: AudioRing):
    """Buka stream input; jika rate tidak didukung, pakai rate default perangkat."""
    import sounddevice as sd
    acfg = cfg["audio"]
    dev = find_device(acfg.get("device"))
    channels = int(acfg.get("channels", 1))

    def cb(indata, frames, t, status):
        if status:
            log.debug("audio status: %s", status)
        ring.push(indata.mean(axis=1) if indata.shape[1] > 1 else indata[:, 0].copy())

    rates = [int(acfg.get("capture_rate", 16000))]
    info = sd.query_devices(dev, "input")
    rates.append(int(info["default_samplerate"]))
    last_err = None
    for r in rates:
        try:
            s = sd.InputStream(device=dev, channels=channels, samplerate=r, dtype="float32",
                               blocksize=int(r * 0.1), callback=cb)
            s.start()
            log.info("Mic: '%s' @ %d Hz", info["name"], r)
            return s, r
        except Exception as e:   # noqa: BLE001
            last_err = e
    raise RuntimeError(f"Gagal membuka mic: {last_err}")


# ----------------------------------------------------------------------
class CryService:
    def __init__(self, cfg: dict):
        import paho.mqtt.client as mqtt

        self.cfg = cfg
        self.model = build_model(cfg)
        self.prefix = cfg["mqtt"]["topic_prefix"].rstrip("/")
        self.stop = threading.Event()
        self.crying = False
        self.cry_since = None
        self.pos_streak = 0
        self.neg_streak = 0
        self.reason_buf = deque(maxlen=5)   # probabilitas tahap 2 dari jendela tangisan terakhir
        self.reason, self.reason_conf = "none", 0.0
        self.source = "mic"                 # "mic" (online) atau "replay:<file>" (offline)
        self.connected = threading.Event()

        m = cfg["mqtt"]
        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=m["client_id"])
        if m.get("username"):
            self.client.username_pw_set(m["username"], m.get("password") or None)
        self.client.will_set(f"{self.prefix}/status", "offline", qos=1, retain=True)
        self.client.on_connect = self._on_connect
        self.client.reconnect_delay_set(1, 30)
        self.client.connect_async(m["host"], int(m["port"]), keepalive=30)
        self.client.loop_start()

    def _on_connect(self, client, userdata, flags, rc, props=None):
        if rc == 0:
            log.info("MQTT terhubung")
            self._status("online")
            self._publish_state(force=True)
            self.connected.set()
        else:
            log.error("MQTT ditolak: %s (cek username/password & ACL)", rc)

    def _status(self, s: str):
        self.client.publish(f"{self.prefix}/status", s, qos=1, retain=True)

    def _publish_state(self, cry_prob: float = 0.0, force: bool = False):
        payload = {"crying": self.crying, "since": self.cry_since,
                   "cry_prob": round(cry_prob, 4),
                   "reason": self.reason if self.crying else "none",
                   "reason_conf": self.reason_conf if self.crying else 0.0,
                   "source": self.source, "ts": time.time()}
        self.client.publish(f"{self.prefix}/state", json.dumps(payload), qos=1, retain=True)

    def _update_reason(self, res: dict, is_cry: bool):
        """Rata-rata hasil tahap 2 selama episode tangisan -> alasan yang stabil."""
        if "reason_probs" in res:
            self.reason_buf.append(list(res["reason_probs"].values()))
        elif not is_cry and not self.crying:
            self.reason_buf.clear()          # episode selesai -> mulai baru
        if self.reason_buf:
            labels = list(getattr(self.model, "reasons", []))
            mean = np.mean(self.reason_buf, axis=0)
            i = int(np.argmax(mean))
            if labels:
                return labels[i], round(float(mean[i]), 3)
        return "none", 0.0

    def _update_state(self, cry_prob: float, is_cry: bool, reason=("none", 0.0)):
        inf = self.cfg["inference"]
        if is_cry:
            self.pos_streak += 1; self.neg_streak = 0
        else:
            self.neg_streak += 1; self.pos_streak = 0

        changed = False
        if not self.crying and self.pos_streak >= int(inf.get("consecutive_for_alert", 2)):
            self.crying, self.cry_since, changed = True, time.time(), True
            log.warning("BAYI MENANGIS terdeteksi (p=%.2f, alasan: %s)", cry_prob, reason[0])
        elif self.crying and self.neg_streak >= int(inf.get("clear_after", 3)):
            self.crying, self.cry_since, changed = False, None, True
            self.reason_buf.clear()
            log.info("Tangisan berhenti")
        if not self.crying:
            self.reason, self.reason_conf = "none", 0.0
        elif reason[0] != "none":            # jendela tanpa tahap 2 -> pertahankan alasan terakhir
            if reason[0] != self.reason:
                changed = True               # alasan tangisan berubah -> kirim ulang status
            self.reason, self.reason_conf = reason
        if changed:
            self._publish_state(cry_prob)

    def process_window(self, y: np.ndarray):
        """Satu jendela audio (sudah di sample rate model) -> publish result & state."""
        inf = self.cfg["inference"]
        thr = float(inf.get("cry_threshold", getattr(self.model, "threshold", 0.6)))
        db = level_db(y)
        if db < float(inf.get("silence_db", -55)):
            res = {"label": "silence", "confidence": 1.0, "cry_prob": 0.0, "probabilities": {}}
            is_cry = False
        else:
            t0 = time.perf_counter()
            res = self.model.predict(y)
            res["latency_ms"] = round((time.perf_counter() - t0) * 1000, 1)
            is_cry = res["cry_prob"] >= thr

        reason = self._update_reason(res, is_cry)
        self._update_state(res["cry_prob"], is_cry, reason)
        res.update({"level_db": round(db, 1), "is_cry": is_cry, "source": self.source,
                    "reason": self.reason, "reason_conf": self.reason_conf, "ts": time.time()})
        self.client.publish(f"{self.prefix}/result", json.dumps(res), qos=0)
        log.debug("%s", res)
        return res

    # ------------------------------------------------------------------
    def replay(self, path: str, loop: bool = False):
        """Mode OFFLINE: putar file audio dengan kecepatan real-time, hasil dikirim ke MQTT."""
        import soundfile as sf
        plan = self.model.plan
        hop_s = float(self.cfg["inference"].get("hop_seconds", 1.0))
        y, sr = sf.read(path, dtype="float32", always_2d=True)
        y = resample(y.mean(axis=1), sr, plan.sample_rate)
        win, hop = plan.window_samples, int(hop_s * plan.sample_rate)
        if len(y) < win:
            y = np.pad(y, (0, win - len(y)))
        self.source = f"replay:{Path(path).name}"

        if not self.connected.wait(10):
            log.error("Belum tersambung ke MQTT setelah 10 dtk (cek broker/password)")
            return
        self._status("online")
        log.info("REPLAY %s (%.1f dtk, %s)", path, len(y) / plan.sample_rate,
                 "diulang" if loop else "sekali")
        while not self.stop.is_set():
            for start in range(0, len(y) - win + 1, hop):
                if self.stop.wait(hop_s):
                    return
                res = self.process_window(y[start:start + win])
                log.info("%6.1fs  P(cry)=%.3f  %-8s %s", start / plan.sample_rate, res["cry_prob"],
                         res["label"], f"-> MENANGIS ({self.reason})" if self.crying else "")
            if not loop:
                break
        # selesai: kosongkan status menangis
        for _ in range(int(self.cfg["inference"].get("clear_after", 3))):
            self._update_state(0.0, False)
        log.info("Replay selesai")

    # ------------------------------------------------------------------
    def run(self):
        inf = self.cfg["inference"]
        plan = self.model.plan
        hop_s = float(inf.get("hop_seconds", 1.0))
        win_s = plan.window_samples / plan.sample_rate
        self.source = "mic"

        while not self.stop.is_set():
            ring = AudioRing(win_s + 2.0, int(self.cfg["audio"].get("capture_rate", 16000)))
            try:
                stream, cap_rate = open_stream(self.cfg, ring)
                if cap_rate != ring.rate:
                    ring.reset(cap_rate)
            except Exception as e:   # noqa: BLE001
                log.error("%s — coba lagi 5 detik", e)
                self._status("no_mic")
                self.stop.wait(5)
                continue

            self._status("online")
            need = int(win_s * cap_rate)
            try:
                while not self.stop.is_set():
                    if self.stop.wait(hop_s):
                        break
                    if time.monotonic() - ring.last_data > 3.0:
                        raise RuntimeError("tidak ada data audio (mic Bluetooth terputus?)")
                    raw = ring.latest(need)
                    if len(raw) < need * 0.9:
                        continue   # buffer belum penuh
                    y = resample(raw, cap_rate, plan.sample_rate)[:plan.window_samples]
                    self.process_window(y)
            except Exception as e:   # noqa: BLE001
                log.error("Stream audio error: %s", e)
                self._status("no_mic")
            finally:
                try:
                    stream.stop(); stream.close()
                except Exception:   # noqa: BLE001
                    pass
            self.stop.wait(3)

    def shutdown(self, *_):
        self.stop.set()
        self._status("offline")
        time.sleep(0.3)
        self.client.loop_stop()
        self.client.disconnect()


# ----------------------------------------------------------------------
def cmd_list_devices():
    import sounddevice as sd
    print(sd.query_devices())
    print("\nDefault input:", sd.default.device[0])


def cmd_test_file(cfg: dict, path: str):
    import soundfile as sf
    model = build_model(cfg)
    plan = model.plan
    y, sr = sf.read(path, dtype="float32", always_2d=True)
    y = resample(y.mean(axis=1), sr, plan.sample_rate)
    win, hop = plan.window_samples, int(cfg["inference"].get("hop_seconds", 1.0) * plan.sample_rate)
    silence_db = float(cfg["inference"].get("silence_db", -55))
    if len(y) < win:
        y = np.pad(y, (0, win - len(y)))
    for start in range(0, len(y) - win + 1, hop):
        w = y[start:start + win]
        db = level_db(w)
        if db < silence_db:
            print(f"{start / plan.sample_rate:6.1f}s  {db:6.1f} dB  hening")
            continue
        r = model.predict(w)
        extra = f"  alasan={r['window_reason']}" if "window_reason" in r else ""
        print(f"{start / plan.sample_rate:6.1f}s  {db:6.1f} dB  {r['label']:<8} "
              f"cry_prob={r['cry_prob']:.3f}{extra}")


def main():
    ap = argparse.ArgumentParser(description="Smart Incubator cry recognition service")
    ap.add_argument("-c", "--config", default=str(BASE / "config.yaml"))
    ap.add_argument("--list-devices", action="store_true")
    ap.add_argument("--test-file", help="uji model pada file, hasil hanya di terminal")
    ap.add_argument("--replay", metavar="FILE",
                    help="mode offline: putar file audio real-time & kirim hasil ke dashboard")
    ap.add_argument("--loop", action="store_true", help="dengan --replay: ulangi terus")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if args.list_devices:
        return cmd_list_devices()
    audio_file = args.test_file or args.replay
    if audio_file and not Path(audio_file).is_file():
        sys.exit(f"File audio tidak ditemukan: {audio_file}\n"
                 f"  folder sekarang: {Path.cwd()}\n"
                 f"  cek nama file dengan: ls *.wav   (atau tulis path lengkap)")
    cfg = load_config(Path(args.config))
    if args.test_file:
        return cmd_test_file(cfg, args.test_file)

    svc = CryService(cfg)
    signal.signal(signal.SIGTERM, svc.shutdown)
    signal.signal(signal.SIGINT, svc.shutdown)
    if args.replay:
        try:
            svc.replay(args.replay, loop=args.loop)
        finally:
            if not svc.stop.is_set():
                svc.shutdown()
    else:
        svc.run()


if __name__ == "__main__":
    sys.exit(main())
