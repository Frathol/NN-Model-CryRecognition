"""
Pemilihan mikrofon otomatis untuk cry_service.py.

Urutan prioritas:
  1. Mic USB      (beberapa mic USB -> diurutkan menurut port USB, port "pertama" menang)
  2. Mic Bluetooth (profil headset/HFP; bisa disambungkan otomatis lewat connect_bt_mic.sh)
  3. Mic lain     (mis. mic bawaan laptop saat mencoba di PC)

Daftar mic diambil dari PipeWire (pw-dump). Mic terpilih dijadikan sumber input default
(wpctl set-default), lalu sounddevice membuka perangkat "default" yang diarahkan PipeWire
ke mic tersebut. Tanpa PipeWire, dipakai daftar sounddevice/ALSA (nama berisi "USB" didahulukan).
"""
from __future__ import annotations

import json
import logging
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger("mic")

KIND_RANK = {"usb": 0, "bluetooth": 1, "other": 2}
KIND_TEXT = {"usb": "USB", "bluetooth": "Bluetooth", "other": "lainnya"}


@dataclass
class Mic:
    kind: str            # usb | bluetooth | other
    name: str            # nama node PipeWire (atau nama perangkat sounddevice)
    desc: str            # nama yang mudah dibaca
    port: str = ""       # jalur fisik USB di /sys (untuk urutan port), mis. .../usb1/1-2/1-2:1.0
    pw_id: int | None = None
    sd_index: int | None = None   # hanya jika tanpa PipeWire

    @property
    def key(self) -> str:
        return f"{self.kind}:{self.name}"

    def label(self) -> str:
        port = f", port USB {short_port(self.port)}" if self.port else ""
        return f"{KIND_TEXT[self.kind]} '{self.desc}'{port}"


def natural_key(s: str):
    """'usb-0:10' sesudah 'usb-0:2' (angka dibandingkan sebagai angka)."""
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", s or "")]


def short_port(path: str) -> str:
    """Jalur sysfs -> nama port USB, mis. '.../xhci-hcd.0/usb1/1-2/1-2:1.0' -> '1-2'."""
    ports = re.findall(r"/(\d+-[\d.]+)(?=/|$)", path or "")
    if ports:
        return ports[-1]
    m = re.search(r"usb-\d+:([\d.]+):", path or "")        # bus-path PipeWire
    return m.group(1) if m else (path or "?")


def usb_sysfs_path(alsa_card) -> str:
    """Jalur fisik kartu suara di /sys (sama dengan cara kamera USB diurutkan)."""
    try:
        return os.path.realpath(f"/sys/class/sound/card{int(alsa_card)}/device")
    except (TypeError, ValueError, OSError):
        return ""


def rank(m: Mic):
    return (KIND_RANK[m.kind], natural_key(m.port or m.name))


# ----------------------------------------------------------------------
def _pipewire_mics() -> list[Mic] | None:
    """Mic dari PipeWire, atau None jika PipeWire tidak bisa dihubungi."""
    try:
        out = subprocess.run(["pw-dump"], capture_output=True, text=True, timeout=8)
        if out.returncode != 0:
            return None
        objs = json.loads(out.stdout or "[]")
    except (OSError, subprocess.SubprocessError, ValueError):
        return None

    devices = {o["id"]: ((o.get("info") or {}).get("props") or {})
               for o in objs if str(o.get("type", "")).endswith(":Device")}
    mics = []
    for o in objs:
        p = (o.get("info") or {}).get("props") or {}
        if p.get("media.class") != "Audio/Source":
            continue                                   # bukan input (output / monitor / virtual)
        d = devices.get(p.get("device.id"), {})
        name = p.get("node.name", "")
        api = p.get("device.api") or d.get("device.api", "")
        bus = p.get("device.bus") or d.get("device.bus", "")
        bus_path = d.get("device.bus-path") or p.get("device.bus-path", "")
        if api == "bluez5" or bus == "bluetooth" or name.startswith("bluez_"):
            kind = "bluetooth"
        elif bus == "usb" or "-usb-" in bus_path or name.startswith("alsa_input.usb-"):
            kind = "usb"
        else:
            kind = "other"
        desc = p.get("node.description") or d.get("device.description") or name
        port = ""
        if kind == "usb":
            card = p.get("api.alsa.card", d.get("api.alsa.card", p.get("alsa.card")))
            port = usb_sysfs_path(card) or bus_path
        mics.append(Mic(kind, name, desc, port, pw_id=o["id"]))
    return mics


def _sounddevice_mics() -> list[Mic]:
    import sounddevice as sd
    mics = []
    for i, d in enumerate(sd.query_devices()):
        if d["max_input_channels"] <= 0:
            continue
        n = d["name"]
        if n.lower() in ("default", "sysdefault", "pipewire", "pulse") or n.lower().startswith(("dmix", "dsnoop")):
            continue
        kind = "usb" if "usb" in n.lower() else ("bluetooth" if "blue" in n.lower() else "other")
        mics.append(Mic(kind, n, n, port=n if kind == "usb" else "", sd_index=i))
    return mics


def list_mics() -> tuple[list[Mic], bool]:
    """(daftar mic urut prioritas, pakai_pipewire)."""
    mics = _pipewire_mics()
    if mics is not None:
        return sorted(mics, key=rank), True
    try:
        return sorted(_sounddevice_mics(), key=rank), False
    except Exception as e:   # noqa: BLE001
        log.debug("sounddevice: %s", e)
        return [], False


def best_mic() -> Mic | None:
    mics, _ = list_mics()
    return mics[0] if mics else None


def activate(mic: Mic):
    """Arahkan perangkat input default ke mic ini. Mengembalikan argumen device untuk sounddevice."""
    if mic.pw_id is None:
        return mic.sd_index
    try:
        subprocess.run(["wpctl", "set-default", str(mic.pw_id)], check=True,
                       capture_output=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        subprocess.run(["pactl", "set-default-source", mic.name], capture_output=True, timeout=5)
    return None           # None = "default" ALSA -> PipeWire -> mic yang baru dipilih


# ----------------------------------------------------------------------
def try_connect_bluetooth(timeout: float = 45) -> bool:
    """Jalankan connect_bt_mic.sh jika BT_MIC_MAC diisi (cry.env). True jika berhasil."""
    mac = os.environ.get("BT_MIC_MAC", "").strip()
    script = Path(__file__).resolve().parent.parent / "scripts" / "connect_bt_mic.sh"
    if not mac or not script.exists():
        return False
    try:
        r = subprocess.run([str(script), mac], capture_output=True, text=True, timeout=timeout)
        log.debug("connect_bt_mic: %s", (r.stdout + r.stderr).strip())
        return r.returncode == 0
    except (OSError, subprocess.SubprocessError) as e:
        log.debug("connect_bt_mic: %s", e)
        return False


def print_mics():
    mics, pw = list_mics()
    print(f"Mikrofon terdeteksi ({'PipeWire' if pw else 'ALSA/sounddevice'}), urut prioritas:")
    if not mics:
        print("  Tidak ada mikrofon tersambung (USB / Bluetooth).")
    for i, m in enumerate(mics, 1):
        print(f"  {i}. {m.label()}{'   <- dipakai' if i == 1 else ''}")
        print(f"     {m.name}")
    if os.environ.get("BT_MIC_MAC"):
        print(f"Mic Bluetooth cadangan: {os.environ['BT_MIC_MAC']} (disambungkan otomatis jika tidak ada mic USB)")
