#!/usr/bin/env python3
"""
Layanan kamera Smart Incubator (Raspberry Pi 5): live stream + rekaman.

  Kamera (CSI via picamera2, atau USB via OpenCV)
     -> JPEG -> /stream.mjpg        (live, ditampilkan di dashboard)
     -> ffmpeg (H.264) -> recordings/rec_YYYYmmdd_HHMMSS.mp4

HTTP API (hanya 127.0.0.1; dari luar lewat nginx di /camera/ dengan password):
  GET    /stream.mjpg              live MJPEG
  GET    /snapshot.jpg             satu gambar
  GET    /api/status               status kamera & rekaman (JSON)
  POST   /api/record/start         mulai rekam
  POST   /api/record/stop          berhenti rekam
  GET    /api/recordings           daftar rekaman (JSON)
  GET    /recordings/<file>.mp4    putar/unduh rekaman (mendukung seek / Range)
  DELETE /api/recordings/<file>    hapus rekaman

Pemakaian:
  python3 camera_service.py                      # kamera otomatis: CSI dulu, lalu USB urut port;
                                                 # belum ada kamera -> menunggu, aktif saat dipasang
  python3 camera_service.py --backend usb --device 0
  python3 camera_service.py --backend test       # pola uji tanpa kamera
  python3 camera_service.py --list               # daftar kamera terdeteksi
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

os.environ.setdefault("OPENCV_LOG_LEVEL", "ERROR")   # sembunyikan WARN V4L2 yang tidak penting
import cv2  # noqa: E402
import numpy as np  # noqa: E402

log = logging.getLogger("camera")
REC_NAME = re.compile(r"^rec_\d{8}_\d{6}\.mp4$")


# ======================================================================
#  Sumber gambar
# ======================================================================
class Source:
    name = "none"

    def read(self):  # -> BGR np.ndarray atau None
        raise NotImplementedError

    def close(self):
        pass


class PiCamSource(Source):
    """Kamera CSI (Camera Module) lewat libcamera / picamera2."""
    name = "picamera2"

    def __init__(self, width, height, fps, num=0, label="CSI"):
        from picamera2 import Picamera2
        self.label = label
        self.cam = Picamera2(num)
        frame_us = int(1_000_000 / fps)
        cfg = self.cam.create_video_configuration(
            main={"size": (width, height), "format": "RGB888"},   # RGB888 = urutan B,G,R (cocok untuk OpenCV)
            controls={"FrameDurationLimits": (frame_us, frame_us)})
        self.cam.configure(cfg)
        self.cam.start()
        self.size = cfg["main"]["size"]

    def read(self):
        return self.cam.capture_array("main")

    def close(self):
        try:
            self.cam.stop(); self.cam.close()
        except Exception:   # noqa: BLE001
            pass


def natural_key(s: str):
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", s)]


def csi_cameras() -> list:
    """[(nomor_libcamera, model, id)] kamera CSI saja, urut konektor (CAM0 lalu CAM1).

    libcamera juga mendaftar webcam USB (UVC); itu dilewati di sini karena USB
    ditangani terpisah (prioritas lebih rendah, urut port).
    """
    try:
        from picamera2 import Picamera2
        info = Picamera2.global_camera_info()
    except Exception as e:   # noqa: BLE001
        log.debug("picamera2: %s", e)
        return []
    out = []
    for i, c in enumerate(info):
        cid = str(c.get("Id", ""))
        if "usb" in cid.lower():
            continue
        out.append((c.get("Num", i), c.get("Model", "?"), cid))
    return out


def usb_cameras() -> list:
    """[(/dev/videoN, nama, port)] kamera USB, urut port USB (port terendah dulu).

    Di Pi 5, /dev/video0.. dan /dev/video19..35 adalah perangkat internal (penerima CSI,
    ISP, decoder) yang BUKAN webcam; membukanya dengan OpenCV hanya menunggu timeout.
    Kamera USB dikenali dari jalurnya di /sys yang melewati bus USB.
    """
    found = []
    for d in Path("/sys/class/video4linux").glob("video*"):
        try:
            real = os.path.realpath(d / "device")
            if "/usb" not in real:
                continue
            idx = d / "index"
            if idx.exists() and idx.read_text().strip() != "0":
                continue                       # node metadata, bukan gambar
            name = (d / "name").read_text().strip() if (d / "name").exists() else "?"
            ports = re.findall(r"/(\d+-[\d.]+)(?=/|$)", real)
            found.append((f"/dev/{d.name}", name, ports[-1] if ports else "?", real))
        except OSError:
            continue
    found.sort(key=lambda c: natural_key(c[3]))   # urut jalur fisik = urut port
    return [c[:3] for c in found]


class UsbSource(Source):
    """Webcam USB (UVC) lewat OpenCV / V4L2."""
    name = "usb"

    def __init__(self, device, width, height, fps, label=None):
        dev = f"/dev/video{device}" if str(device).isdigit() else device
        self.dev = dev
        self.label = label or f"USB {dev}"
        self.cap = cv2.VideoCapture(dev, cv2.CAP_V4L2)
        if not self.cap.isOpened():
            raise RuntimeError(f"kamera USB '{device}' tidak bisa dibuka")
        self.cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
        self.cap.set(cv2.CAP_PROP_FPS, fps)
        self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        ok, frame = self.cap.read()
        if not ok:
            raise RuntimeError(f"kamera USB '{device}' tidak mengirim gambar")
        self.size = (frame.shape[1], frame.shape[0])

    def read(self):
        ok, frame = self.cap.read()
        return frame if ok else None

    def close(self):
        self.cap.release()


class TestSource(Source):
    """Pola uji bergerak, untuk mencoba dashboard tanpa kamera."""
    name = "test"

    def __init__(self, width, height, fps):
        self.size, self.dt, self.t0 = (width, height), 1.0 / fps, time.monotonic()
        self.next = self.t0

    def read(self):
        self.next += self.dt
        time.sleep(max(0.0, self.next - time.monotonic()))
        w, h = self.size
        t = time.monotonic() - self.t0
        img = np.zeros((h, w, 3), np.uint8)
        img[:] = (60, 50, 40)
        x = int((np.sin(t) * 0.4 + 0.5) * w)
        cv2.circle(img, (x, h // 2), max(20, h // 8), (80, 160, 240), -1)
        cv2.putText(img, "TEST PATTERN (no camera)", (20, h - 30),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2)
        return img


class NoCamera(RuntimeError):
    pass


FAILED_USB: set = set()   # kamera USB yang gagal dibuka; tidak dicoba "beralih" lagi sampai dicabut


def open_source(args) -> Source:
    """Buka kamera menurut prioritas: CSI (CAM0, CAM1) -> USB (urut port) .

    --backend picamera2/usb membatasi jenisnya; --device N memaksa kamera USB tertentu.
    """
    if args.backend == "test":
        src = TestSource(args.width, args.height, args.fps)
        src.label = "test pattern"
        return src
    errors, tried = [], 0
    if args.backend in ("auto", "picamera2"):
        for num, model, _ in csi_cameras():
            tried += 1
            try:
                return PiCamSource(args.width, args.height, args.fps, num, f"CSI #{num} {model}")
            except Exception as e:   # noqa: BLE001
                errors.append(f"CSI #{num}: {e}")
    if args.backend in ("auto", "usb"):
        if str(args.device) != "auto":
            cams = [(args.device, "", "")]
        else:
            cams = usb_cameras()
        for dev, name, port in cams:
            tried += 1
            label = f"USB {dev} {name}".strip() + (f", port USB {port}" if port else "")
            try:
                src = UsbSource(dev, args.width, args.height, args.fps, label)
                FAILED_USB.discard(dev)
                return src
            except Exception as e:   # noqa: BLE001
                FAILED_USB.add(dev)
                errors.append(f"{label}: {e}")
                log.warning("Kamera %s gagal dibuka (%s), coba berikutnya", label, e)
    if not tried:
        raise NoCamera("tidak ada kamera tersambung")
    raise RuntimeError("; ".join(errors))


def better_usb_waiting(src: Source) -> str | None:
    """Saat memakai kamera USB: ada kamera USB lain di port yang lebih diutamakan?"""
    cams = usb_cameras()
    present = {c[0] for c in cams}
    FAILED_USB.intersection_update(present)          # dicabut -> boleh dicoba lagi nanti
    for dev, name, port in cams:
        if dev == getattr(src, "dev", None):
            return None                              # kamera sekarang sudah yang terbaik
        if dev not in FAILED_USB:
            return f"USB {dev} {name}, port USB {port}"
    return None


# ======================================================================
#  Rekaman (ffmpeg, H.264 MP4 yang bisa diputar di browser)
# ======================================================================
class Recorder:
    """Menulis frame JPEG ke ffmpeg dengan laju TETAP (fps), supaya durasi video = waktu nyata:
    frame terakhir diulang jika kamera telat, tick dilewati jika ffmpeg tertinggal."""

    def __init__(self, out_dir: Path, fps: int, max_seconds: int, min_free_mb: int):
        self.out_dir = out_dir
        self.fps = fps
        self.max_seconds = max_seconds
        self.min_free_mb = min_free_mb
        self.lock = threading.Lock()
        self.proc = None
        self.latest = None
        self.stop_evt = threading.Event()
        self.writer = None
        self.file = None
        self.started = None
        self.dropped = 0
        self.last_error = None

    @property
    def active(self) -> bool:
        return self.proc is not None

    def free_mb(self) -> int:
        return shutil.disk_usage(self.out_dir).free // (1024 * 1024)

    def start(self) -> dict:
        with self.lock:
            if self.proc:
                return {"ok": True, "file": self.file.name, "already": True}
            if self.free_mb() < self.min_free_mb:
                self.last_error = f"ruang disk tinggal {self.free_mb()} MB"
                return {"ok": False, "error": self.last_error}
            name = f"rec_{datetime.now():%Y%m%d_%H%M%S}.mp4"
            self.file = self.out_dir / name
            part = self.file.with_suffix(".part")
            cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                   "-f", "mjpeg", "-framerate", str(self.fps), "-i", "-",
                   "-c:v", "libx264", "-preset", "ultrafast", "-crf", "26",
                   "-pix_fmt", "yuv420p", "-r", str(self.fps),
                   "-movflags", "+faststart", "-f", "mp4", str(part)]
            try:
                self.proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
            except FileNotFoundError:
                self.proc, self.last_error = None, "ffmpeg belum terpasang (sudo apt install ffmpeg)"
                return {"ok": False, "error": self.last_error}
            self.started, self.dropped, self.last_error = time.time(), 0, None
            self.stop_evt.clear()
            self.writer = threading.Thread(target=self._writer, args=(self.proc,), daemon=True)
            self.writer.start()
            log.info("Mulai rekam: %s", name)
            return {"ok": True, "file": name}

    def _writer(self, proc):
        dt = 1.0 / self.fps
        nxt = time.monotonic()
        while not self.stop_evt.is_set():
            data = self.latest
            if data is not None:
                try:
                    proc.stdin.write(data)
                except (BrokenPipeError, OSError):
                    self.last_error = "ffmpeg berhenti tiba-tiba"
                    break
            nxt += dt
            lag = time.monotonic() - nxt
            if lag > 0.5:                       # ffmpeg tertinggal -> lewati tick
                skip = int(lag / dt)
                self.dropped += skip
                nxt += skip * dt
            self.stop_evt.wait(max(0.0, nxt - time.monotonic()))
            if self.max_seconds and time.time() - self.started > self.max_seconds:
                log.info("Batas durasi rekaman tercapai")
                threading.Thread(target=self.stop, daemon=True).start()
                break

    def write(self, jpeg: bytes):
        """Dipanggil kamera tiap frame baru; writer mengambil frame terbaru sesuai fps."""
        if self.proc:
            self.latest = jpeg

    def stop(self) -> dict:
        with self.lock:
            if not self.proc:
                return {"ok": True, "file": None}
            proc, final, writer = self.proc, self.file, self.writer
            self.proc = None
        self.stop_evt.set()
        if writer and writer is not threading.current_thread():
            writer.join(timeout=5)
        self.latest = None
        try:
            proc.stdin.close()
        except OSError:
            pass
        try:
            _, err = proc.communicate(timeout=30)
        except subprocess.TimeoutExpired:
            proc.kill(); _, err = proc.communicate()
        part = final.with_suffix(".part")
        if proc.returncode == 0 and part.exists() and part.stat().st_size > 0:
            part.replace(final)
            log.info("Rekaman tersimpan: %s (%.1f MB, %d frame dibuang)",
                     final.name, final.stat().st_size / 1e6, self.dropped)
            return {"ok": True, "file": final.name}
        self.last_error = (err or b"").decode(errors="replace").strip()[-300:] or "rekaman gagal"
        log.error("Rekaman gagal: %s", self.last_error)
        part.unlink(missing_ok=True)
        return {"ok": False, "error": self.last_error}

    def status(self) -> dict:
        return {"active": self.active,
                "file": self.file.name if self.active and self.file else None,
                "elapsed_s": round(time.time() - self.started, 1) if self.active else 0,
                "max_s": self.max_seconds, "dropped": self.dropped,
                "free_mb": self.free_mb(), "error": self.last_error}


# ======================================================================
#  Kamera: loop ambil gambar
# ======================================================================
class _SwitchCamera(Exception):
    pass


class Camera(threading.Thread):
    def __init__(self, args, recorder: Recorder):
        super().__init__(daemon=True)
        self.args, self.rec = args, recorder
        self.src: Source | None = None
        self.error = None
        self.cond = threading.Condition()
        self.jpeg, self.seq = None, 0
        self.viewers = 0
        self.want_until = 0.0          # snapshot meminta frame walau tidak ada penonton
        self.fps_measured = 0.0
        self.running = True

    def run(self):
        warned_at = -1e9
        while self.running:
            try:
                self.src = open_source(self.args)
                self.error = None
                warned_at = -1e9
                log.info("Kamera terdeteksi: %s %sx%s @ %s fps -> live view & rekaman AKTIF",
                         getattr(self.src, "label", self.src.name), *self.src.size, self.args.fps)
                self._loop()
            except NoCamera as e:
                self.error = "No camera connected (CSI / USB)"
                if time.monotonic() - warned_at > 60:
                    log.warning("Tidak ada kamera tersambung (CSI / USB). Menunggu kamera dipasang... "
                                "(fitur aktif otomatis begitu kamera tersambung)")
                    warned_at = time.monotonic()
                log.debug("%s", e)
            except _SwitchCamera as e:
                log.info("Kamera prioritas lebih tinggi terpasang: %s -> beralih", e)
            except Exception as e:   # noqa: BLE001
                self.error = str(e)
                log.error("Kamera error: %s (coba lagi)", e)
            finally:
                if self.src:
                    self.src.close()
                self.src = None
            if self.rec.active:
                self.rec.stop()
            time.sleep(2)

    def _loop(self):
        fails, n, t_fps = 0, 0, time.monotonic()
        auto_usb = self.src.name == "usb" and self.args.backend == "auto" and str(self.args.device) == "auto"
        next_check = time.monotonic() + 5
        quality = [int(cv2.IMWRITE_JPEG_QUALITY), self.args.quality]
        rot = {90: cv2.ROTATE_90_CLOCKWISE, 180: cv2.ROTATE_180, 270: cv2.ROTATE_90_COUNTERCLOCKWISE}
        while self.running:
            frame = self.src.read()
            if frame is None:
                fails += 1
                if fails > 30:
                    raise RuntimeError("kamera berhenti mengirim gambar")
                time.sleep(0.05)
                continue
            fails = 0
            n += 1
            if auto_usb and time.monotonic() >= next_check:
                next_check = time.monotonic() + 5
                better = better_usb_waiting(self.src)
                if better:
                    raise _SwitchCamera(better)
            if time.monotonic() - t_fps >= 2:
                self.fps_measured = n / (time.monotonic() - t_fps)
                n, t_fps = 0, time.monotonic()

            if not (self.viewers or self.rec.active or time.monotonic() < self.want_until):
                continue                       # tidak ada yang butuh gambar -> hemat CPU
            if self.args.rotate in rot:
                frame = cv2.rotate(frame, rot[self.args.rotate])
            self._overlay(frame)
            ok, buf = cv2.imencode(".jpg", frame, quality)
            if not ok:
                continue
            data = buf.tobytes()
            with self.cond:
                self.jpeg, self.seq = data, self.seq + 1
                self.cond.notify_all()
            self.rec.write(data)

    def _overlay(self, frame):
        text = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        if self.rec.active:
            text += "  REC"
        h = frame.shape[0]
        scale = max(0.5, h / 720 * 0.8)
        thick = max(1, round(scale * 1.5))
        font = cv2.FONT_HERSHEY_SIMPLEX
        (tw, th), base = cv2.getTextSize(text, font, scale, thick)
        pad = max(4, int(6 * scale))
        x, y = 10, 10 + th + pad
        # kotak gelap di belakang teks agar terbaca di atas gambar terang maupun gelap
        cv2.rectangle(frame, (x - pad, y - th - pad), (x + tw + pad, y + base + pad // 2), (0, 0, 0), -1)
        cv2.putText(frame, text, (x, y), font, scale,
                    (80, 80, 255) if self.rec.active else (255, 255, 255), thick, cv2.LINE_AA)

    def wait_frame(self, last_seq: int, timeout=5.0):
        with self.cond:
            self.cond.wait_for(lambda: self.seq != last_seq or not self.running, timeout)
            return self.seq, self.jpeg

    def status(self) -> dict:
        return {"ok": self.src is not None and self.error is None,
                "backend": self.src.name if self.src else None,
                "camera": getattr(self.src, "label", None) if self.src else None,
                "size": list(self.src.size) if self.src else None,
                "fps": round(self.fps_measured, 1), "viewers": self.viewers,
                "error": self.error}


# ======================================================================
#  HTTP
# ======================================================================
def make_handler(cam: Camera, rec: Recorder, max_viewers: int):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "IncubatorCamera/1.0"

        def log_message(self, fmt, *a):
            log.debug("%s %s", self.address_string(), fmt % a)

        def _json(self, obj, code=200):
            body = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            path = urlparse(self.path).path
            if path == "/stream.mjpg":
                return self._stream()
            if path == "/snapshot.jpg":
                return self._snapshot()
            if path == "/api/status":
                return self._json({"camera": cam.status(), "recording": rec.status()})
            if path == "/api/recordings":
                return self._json({"recordings": list_recordings(rec.out_dir)})
            if path.startswith("/recordings/"):
                return self._file(unquote(path[len("/recordings/"):]))
            self._json({"error": "not found"}, 404)

        def do_HEAD(self):
            self.do_GET()

        def do_POST(self):
            path = urlparse(self.path).path
            if path == "/api/record/start":
                if not cam.status()["ok"]:
                    return self._json({"ok": False, "error": "kamera tidak aktif"}, 409)
                r = rec.start()
                return self._json(r, 200 if r["ok"] else 500)
            if path == "/api/record/stop":
                r = rec.stop()
                return self._json(r, 200 if r["ok"] else 500)
            self._json({"error": "not found"}, 404)

        def do_DELETE(self):
            path = urlparse(self.path).path
            if path.startswith("/api/recordings/"):
                name = unquote(path[len("/api/recordings/"):])
                f = rec.out_dir / name
                if not REC_NAME.match(name) or not f.is_file():
                    return self._json({"ok": False, "error": "file tidak ditemukan"}, 404)
                if rec.active and rec.file and rec.file.name == name:
                    return self._json({"ok": False, "error": "sedang direkam"}, 409)
                f.unlink()
                log.info("Rekaman dihapus: %s", name)
                return self._json({"ok": True})
            self._json({"error": "not found"}, 404)

        # ---- live stream ----
        def _stream(self):
            with cam.cond:
                if cam.viewers >= max_viewers:
                    full = True
                else:
                    full = False
                    cam.viewers += 1
            if full:
                return self._json({"error": f"maksimal {max_viewers} penonton"}, 503)
            self.send_response(200)
            self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Accel-Buffering", "no")
            self.send_header("Connection", "close")
            self.end_headers()
            seq = -1
            try:
                while cam.running:
                    seq, data = cam.wait_frame(seq)
                    if data is None:
                        continue
                    self.wfile.write(b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: "
                                     + str(len(data)).encode() + b"\r\n\r\n" + data + b"\r\n")
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, TimeoutError):
                pass
            finally:
                with cam.cond:
                    cam.viewers -= 1
                self.close_connection = True

        def _snapshot(self):
            cam.want_until = time.monotonic() + 3
            _, data = cam.wait_frame(cam.seq, timeout=3)
            if data is None:
                return self._json({"error": "belum ada gambar"}, 503)
            self.send_response(200)
            self.send_header("Content-Type", "image/jpeg")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        # ---- file rekaman (dengan Range, agar video bisa di-seek) ----
        def _file(self, name):
            f = rec.out_dir / name
            if not REC_NAME.match(name) or not f.is_file():
                return self._json({"error": "file tidak ditemukan"}, 404)
            size = f.stat().st_size
            start, end = 0, size - 1
            m = re.match(r"bytes=(\d*)-(\d*)$", self.headers.get("Range", ""))
            if m and (m.group(1) or m.group(2)):
                if m.group(1):
                    start = int(m.group(1)); end = int(m.group(2)) if m.group(2) else size - 1
                else:
                    start = max(0, size - int(m.group(2)))
                end = min(end, size - 1)
                if start > end:
                    self.send_response(416)
                    self.send_header("Content-Range", f"bytes */{size}")
                    self.end_headers()
                    return
                self.send_response(206)
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            else:
                self.send_response(200)
            self.send_header("Content-Type", "video/mp4")
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Length", str(end - start + 1))
            if "download" in (urlparse(self.path).query or ""):
                self.send_header("Content-Disposition", f'attachment; filename="{name}"')
            self.end_headers()
            if self.command == "HEAD":
                return
            with f.open("rb") as fh:
                fh.seek(start)
                left = end - start + 1
                try:
                    while left > 0:
                        chunk = fh.read(min(256 * 1024, left))
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                        left -= len(chunk)
                except (BrokenPipeError, ConnectionResetError):
                    pass

    return Handler


def list_recordings(out_dir: Path) -> list:
    items = []
    for f in sorted(out_dir.glob("rec_*.mp4"), reverse=True):
        if REC_NAME.match(f.name):
            st = f.stat()
            items.append({"name": f.name, "size": st.st_size,
                          "mtime": datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M:%S")})
    return items


def list_cameras():
    print("Kamera terdeteksi, urut prioritas (CSI dulu, lalu USB menurut port):")
    n = 0
    try:
        import picamera2  # noqa: F401
    except Exception as e:   # noqa: BLE001
        print(f"  (picamera2 tidak tersedia, kamera CSI tidak bisa dicek: {e})")
    for num, model, cid in csi_cameras():
        n += 1
        print(f"  {n}. CSI #{num}: {model}{'   <- dipakai' if n == 1 else ''}")
        print(f"     {cid}")
    for dev, name, port in usb_cameras():
        n += 1
        print(f"  {n}. USB {dev}: {name}, port USB {port}{'   <- dipakai' if n == 1 else ''}")
    if not n:
        print("  Tidak ada kamera tersambung (CSI / USB).")
        print("  CSI: cek kabel pita (sisi kontak benar) & konektor CAM0/CAM1; USB: cek `lsusb`.")


# ======================================================================
def main():
    ap = argparse.ArgumentParser(description="Layanan kamera Smart Incubator")
    ap.add_argument("--backend", choices=["auto", "picamera2", "usb", "test"], default="auto")
    ap.add_argument("--device", default="auto",
                    help="kamera USB: auto (urut port, setelah CSI), nomor N (/dev/videoN), atau path")
    ap.add_argument("--width", type=int, default=1280)
    ap.add_argument("--height", type=int, default=720)
    ap.add_argument("--fps", type=int, default=15)
    ap.add_argument("--rotate", type=int, choices=[0, 90, 180, 270], default=0)
    ap.add_argument("--quality", type=int, default=80, help="kualitas JPEG stream (1-100)")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8081)
    ap.add_argument("--record-dir", default="~/incubator-recordings")
    ap.add_argument("--max-minutes", type=float, default=30, help="batas durasi satu rekaman (0 = tanpa batas)")
    ap.add_argument("--min-free-mb", type=int, default=500, help="rekaman ditolak/berhenti jika disk < ini")
    ap.add_argument("--max-viewers", type=int, default=4)
    ap.add_argument("--list", action="store_true", help="tampilkan kamera yang terdeteksi")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if args.list:
        return list_cameras()

    out_dir = Path(args.record_dir).expanduser()
    out_dir.mkdir(parents=True, exist_ok=True)
    for stale in out_dir.glob("rec_*.part"):           # sisa rekaman yang terputus
        stale.unlink(missing_ok=True)

    rec = Recorder(out_dir, args.fps, args.max_minutes * 60, args.min_free_mb)
    cam = Camera(args, rec)
    cam.start()

    server = ThreadingHTTPServer((args.host, args.port), make_handler(cam, rec, args.max_viewers))
    server.daemon_threads = True

    def shutdown(*_):
        log.info("Berhenti...")
        cam.running = False
        with cam.cond:
            cam.cond.notify_all()
        rec.stop()
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    log.info("HTTP di http://%s:%d  (rekaman: %s)", args.host, args.port, out_dir)
    server.serve_forever()


if __name__ == "__main__":
    sys.exit(main())
