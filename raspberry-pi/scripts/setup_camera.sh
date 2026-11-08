#!/usr/bin/env bash
# =====================================================================
#  Pasang fitur kamera Smart Incubator (live stream + rekaman di dashboard)
#  - paket: picamera2 (kamera CSI), OpenCV (kamera USB), ffmpeg (rekaman MP4)
#  - service systemd: incubator-camera (camera_service.py di 127.0.0.1:8081)
#  - nginx: /camera/ dengan password (video bayi = data sensitif)
#  - dashboard: menyalin semua file dashboard (config.js tidak lagi berisi password)
#
#  Jalankan sebagai user biasa dari folder repo:
#     cd smart-incubator/raspberry-pi && ./scripts/setup_camera.sh
#  Aman dijalankan ulang (password MQTT tidak diubah).
# =====================================================================
set -euo pipefail

if [[ $EUID -eq 0 ]]; then
  echo "Jalankan sebagai user biasa (script akan memakai sudo bila perlu)."; exit 1
fi

PI_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CAM_DIR="$PI_DIR/camera"
REC_DIR="$HOME/incubator-recordings"
WEB_ROOT=/var/www/smart-incubator
HTPASSWD=/etc/nginx/smart-incubator-camera.htpasswd

step() { echo -e "\n\033[1;36m==> $*\033[0m"; }

step "Install paket kamera"
sudo apt-get update
sudo apt-get install -y python3-picamera2 python3-opencv python3-numpy ffmpeg openssl
sudo usermod -aG video "$USER"

step "Cek kamera"
python3 "$CAM_DIR/camera_service.py" --list || true

step "Service systemd incubator-camera"
mkdir -p "$REC_DIR"
sed -e "s|__USER__|$USER|g" -e "s|__CAM_DIR__|$CAM_DIR|g" -e "s|__REC_DIR__|$REC_DIR|g" \
  "$PI_DIR/systemd/camera.service" | sudo tee /etc/systemd/system/incubator-camera.service >/dev/null
sudo systemctl daemon-reload
sudo systemctl enable incubator-camera
sudo systemctl restart incubator-camera

step "Password untuk membuka kamera di dashboard"
if sudo test -s "$HTPASSWD"; then
  read -rp "Akun kamera sudah ada. Buat ulang? [y/N] " again
else
  again=y
fi
if [[ "${again,,}" == y* ]]; then
  read -rp "Nama pengguna kamera: " CAM_USER
  while true; do
    read -rsp "Password kamera: " p1; echo
    read -rsp "Ulangi password: " p2; echo
    [[ -n "$p1" && "$p1" == "$p2" ]] && break
    echo "Tidak cocok / kosong, ulangi."
  done
  HASH=$(printf '%s' "$p1" | openssl passwd -apr1 -stdin)
  printf '%s:%s\n' "$CAM_USER" "$HASH" | sudo tee "$HTPASSWD" >/dev/null
  sudo chown root:www-data "$HTPASSWD"
  sudo chmod 640 "$HTPASSWD"
fi

step "nginx (/camera/)"
sudo install -m 644 "$PI_DIR/nginx/smart-incubator.conf" /etc/nginx/sites-available/smart-incubator
sudo ln -sf /etc/nginx/sites-available/smart-incubator /etc/nginx/sites-enabled/smart-incubator
sudo nginx -t
sudo systemctl reload nginx

step "Dashboard (semua file, termasuk halaman login)"
# index.html memerlukan auth.js, login.html, login.js; config.js berisi daftar topic (termasuk FSM)
sudo cp "$PI_DIR/dashboard/"*.html "$PI_DIR/dashboard/"*.js "$PI_DIR/dashboard/style.css" "$WEB_ROOT/"

IP=$(hostname -I | awk '{print $1}')
step "Selesai"
cat <<EOF
Dashboard : http://$IP/   -> tombol "Watch camera" di panel Camera
Rekaman   : $REC_DIR
Log       : journalctl -u incubator-camera -f
Status    : curl -s -u <user>:<pw> http://localhost/camera/api/status

Catatan: jika user baru ditambahkan ke grup 'video', reboot sekali agar berlaku.
EOF
