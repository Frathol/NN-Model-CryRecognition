#!/usr/bin/env bash
# =====================================================================
#  Nyalakan deteksi tangisan (mic) & kamera OTOMATIS setiap Pi menyala.
#
#  - cry-recognition  : systemd USER service (butuh PipeWire untuk mic USB/Bluetooth)
#                       mic otomatis: USB (urut port) > Bluetooth (BT_MIC_MAC di cry.env)
#  - fsm              : systemd USER service, mesin alarm (fsm.py di root repo)
#  - incubator-camera : systemd service sistem
#                       kamera otomatis: CSI (CAM0, CAM1) > USB (urut port)
#  Belum ada mic/kamera -> service tetap jalan, menulis "Tidak ada ... tersambung" di log,
#  lalu langsung aktif begitu perangkat dipasang (tanpa restart).
#
#  Prasyarat: setup_pi.sh (venv + akun MQTT) dan setup_camera.sh (paket kamera + nginx)
#  sudah pernah dijalankan.
#
#  Pemakaian:
#    ./scripts/setup_autostart.sh          # pasang/perbarui & nyalakan keduanya
#    ./scripts/setup_autostart.sh --off    # matikan & jangan jalan otomatis lagi
# =====================================================================
set -euo pipefail

if [[ $EUID -eq 0 ]]; then
  echo "Jalankan sebagai user biasa (script akan memakai sudo bila perlu)."; exit 1
fi

PI_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CRY_DIR="$PI_DIR/cry-recognition"
CAM_DIR="$PI_DIR/camera"
SCRIPTS_DIR="$PI_DIR/scripts"
REC_DIR="$HOME/incubator-recordings"
CONF_DIR="$HOME/.config/smart-incubator"
USER_UNIT="$HOME/.config/systemd/user/cry-recognition.service"
FSM_UNIT="$HOME/.config/systemd/user/fsm.service"
FSM_PY="$(cd "$PI_DIR/.." && pwd)/fsm.py"
CAM_UNIT=/etc/systemd/system/incubator-camera.service

step() { echo -e "\n\033[1;36m==> $*\033[0m"; }
warn() { echo -e "\033[1;33m[!] $*\033[0m"; }

if [[ "${1:-}" == "--off" ]]; then
  step "Mematikan autostart"
  systemctl --user disable --now cry-recognition 2>/dev/null || true
  systemctl --user disable --now fsm 2>/dev/null || true
  sudo systemctl disable --now incubator-camera 2>/dev/null || true
  echo "Selesai. Jalankan lagi tanpa --off untuk menyalakan."
  exit 0
fi

# ---------------------------------------------------------------------
step "Cek prasyarat"
ok=1
[[ -x "$CRY_DIR/.venv/bin/python" ]] || { warn "venv cry-recognition belum ada -> jalankan ./scripts/setup_pi.sh"; ok=0; }
[[ -f "$CONF_DIR/cry.env" ]]         || { warn "$CONF_DIR/cry.env belum ada -> jalankan ./scripts/setup_pi.sh"; ok=0; }
python3 -c "import cv2" 2>/dev/null   || { warn "OpenCV belum terpasang -> jalankan ./scripts/setup_camera.sh"; ok=0; }
[[ -f "$CRY_DIR/model/cry_model.tflite" ]] || warn "Model belum ada di $CRY_DIR/model/ (service tidak bisa jalan)"
[[ $ok -eq 1 ]] || exit 1
chmod +x "$SCRIPTS_DIR/"*.sh

# ---------------------------------------------------------------------
step "Perangkat yang terdeteksi saat ini"
(
  set -a; source "$CONF_DIR/cry.env"; set +a
  cd "$CRY_DIR" && .venv/bin/python cry_service.py --list-devices 2>/dev/null | sed '/^Perangkat sounddevice/,$d'
) || warn "Gagal membaca daftar mic"
echo
python3 "$CAM_DIR/camera_service.py" --list || warn "Gagal membaca daftar kamera"
echo
echo "Perangkat yang belum tersambung boleh dipasang belakangan; fitur langsung aktif saat terpasang."

# ---------------------------------------------------------------------
step "Bluetooth tetap aktif tanpa login (headless)"
# WirePlumber secara default hanya mengaktifkan Bluetooth untuk user yang login di layar.
# Pada Pi tanpa monitor/login, mic Bluetooth tidak akan muncul tanpa pengaturan ini.
mkdir -p "$HOME/.config/wireplumber/bluetooth.lua.d" "$HOME/.config/wireplumber/wireplumber.conf.d"
cat > "$HOME/.config/wireplumber/bluetooth.lua.d/80-smart-incubator-headless.lua" <<'EOF'
-- WirePlumber 0.4 (Raspberry Pi OS Bookworm): Bluetooth aktif walau tidak ada sesi login
bluez_monitor.properties["with-logind"] = false
EOF
cat > "$HOME/.config/wireplumber/wireplumber.conf.d/80-smart-incubator-headless.conf" <<'EOF'
# WirePlumber 0.5+ (Raspberry Pi OS Trixie): Bluetooth aktif walau tidak ada sesi login
wireplumber.profiles = {
  main = {
    monitor.bluez.seat-monitoring = disabled
  }
}
EOF
systemctl --user enable pipewire pipewire-pulse wireplumber 2>/dev/null || true
systemctl --user restart wireplumber 2>/dev/null || true
sudo systemctl enable --now bluetooth

# ---------------------------------------------------------------------
step "Deteksi tangisan (cry-recognition, user service)"
mkdir -p "$(dirname "$USER_UNIT")"
sed -e "s|__CRY_DIR__|$CRY_DIR|g" -e "s|__SCRIPTS_DIR__|$SCRIPTS_DIR|g" \
  "$PI_DIR/systemd/cry-recognition.service" > "$USER_UNIT"
systemctl --user daemon-reload
systemctl --user enable cry-recognition
systemctl --user restart cry-recognition
# user service tetap jalan saat boot walau belum ada yang login
sudo loginctl enable-linger "$USER"

# ---------------------------------------------------------------------
step "Mesin alarm FSM (fsm, user service)"
if [[ ! -f "$CONF_DIR/fsm.env" ]]; then
  # Pi yang dipasang sebelum akun fsm ada: tambahkan akun tanpa mengubah akun lain
  echo "Akun MQTT 'fsm' belum ada. Buat password-nya sekarang."
  while true; do
    read -rsp "Password MQTT untuk user 'fsm': " p1; echo
    read -rsp "Ulangi password: " p2; echo
    [[ -n "$p1" && "$p1" == "$p2" ]] && break
    echo "Tidak cocok / kosong, ulangi."
  done
  sudo mosquitto_passwd -b /etc/mosquitto/passwd fsm "$p1"
  sudo install -m 640 -o root -g mosquitto "$PI_DIR/mosquitto/smart-incubator.acl" /etc/mosquitto/smart-incubator.acl
  sudo systemctl restart mosquitto
  ( umask 077; printf 'MQTT_PASSWORD=%s\n' "$p1" > "$CONF_DIR/fsm.env" )
fi
[[ -f "$FSM_PY" ]] || warn "fsm.py tidak ditemukan di $FSM_PY"
sed -e "s|__CRY_DIR__|$CRY_DIR|g" -e "s|__FSM_PY__|$FSM_PY|g" \
  "$PI_DIR/systemd/fsm.service" > "$FSM_UNIT"
systemctl --user daemon-reload
systemctl --user enable fsm
systemctl --user restart fsm

# ---------------------------------------------------------------------
step "Kamera (incubator-camera, system service)"
mkdir -p "$REC_DIR"
sed -e "s|__USER__|$USER|g" -e "s|__CAM_DIR__|$CAM_DIR|g" -e "s|__REC_DIR__|$REC_DIR|g" \
  "$PI_DIR/systemd/camera.service" | sudo tee "$CAM_UNIT" >/dev/null
sudo systemctl daemon-reload
sudo systemctl enable incubator-camera
sudo systemctl restart incubator-camera
sudo test -s /etc/nginx/smart-incubator-camera.htpasswd \
  || warn "Akun kamera nginx belum ada -> jalankan ./scripts/setup_camera.sh agar kamera tampil di dashboard"

# ---------------------------------------------------------------------
step "Status (tunggu 8 detik)"
sleep 8
echo "--- cry-recognition: $(systemctl --user is-active cry-recognition)"
journalctl --user -u cry-recognition -n 6 --no-pager -o cat || true
echo "--- fsm: $(systemctl --user is-active fsm)"
journalctl --user -u fsm -n 4 --no-pager -o cat || true
echo "--- incubator-camera: $(systemctl is-active incubator-camera)"
sudo journalctl -u incubator-camera -n 4 --no-pager -o cat || true

cat <<EOF

Selesai. Deteksi tangisan, FSM, dan kamera sekarang menyala otomatis setiap Pi dihidupkan.
  Log mic/AI : journalctl --user -u cry-recognition -f
  Log FSM    : journalctl --user -u fsm -f
  Log kamera : journalctl -u incubator-camera -f
  Restart    : systemctl --user restart cry-recognition   |  sudo systemctl restart incubator-camera
  Matikan    : ./scripts/setup_autostart.sh --off
EOF
