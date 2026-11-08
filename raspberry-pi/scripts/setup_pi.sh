#!/usr/bin/env bash
# =====================================================================
#  Setup Raspberry Pi 5 untuk Smart Incubator
#  - Mosquitto (MQTT 1883 + WebSockets 9001 lokal) dengan user & ACL
#  - nginx (dashboard + proxy /mqtt)
#  - PipeWire + Bluetooth untuk mic
#  - Layanan cry recognition (venv + systemd user service)
#
#  Jalankan sebagai user biasa (BUKAN root) dari folder repo:
#     cd smart-incubator/raspberry-pi && ./scripts/setup_pi.sh
# =====================================================================
set -euo pipefail

if [[ $EUID -eq 0 ]]; then
  echo "Jalankan sebagai user biasa (script akan memakai sudo bila perlu)."; exit 1
fi

PI_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CRY_DIR="$PI_DIR/cry-recognition"
SCRIPTS_DIR="$PI_DIR/scripts"
WEB_ROOT=/var/www/smart-incubator
CONF_DIR="$HOME/.config/smart-incubator"

step() { echo -e "\n\033[1;36m==> $*\033[0m"; }

ask_pass() {   # ask_pass <user> -> echo password
  local p1 p2
  while true; do
    read -rsp "Password MQTT untuk user '$1': " p1; echo >&2
    read -rsp "Ulangi password: " p2; echo >&2
    [[ -n "$p1" && "$p1" == "$p2" ]] && { echo "$p1"; return; }
    echo "Tidak cocok / kosong, ulangi." >&2
  done
}

# ---------------------------------------------------------------------
step "Install paket sistem"
sudo apt-get update
sudo apt-get install -y mosquitto mosquitto-clients nginx \
  python3-venv python3-dev libportaudio2 libsndfile1 \
  bluez pipewire pipewire-pulse wireplumber libspa-0.2-bluetooth pulseaudio-utils curl

# ---------------------------------------------------------------------
step "Konfigurasi Mosquitto"
echo "Buat password untuk 5 akun MQTT: esp32, cryservice, fsm, dashboard, admin"
P_ESP=$(ask_pass esp32)
P_CRY=$(ask_pass cryservice)
P_FSM=$(ask_pass fsm)
P_DASH=$(ask_pass dashboard)
P_ADMIN=$(ask_pass admin)

sudo rm -f /etc/mosquitto/passwd
sudo touch /etc/mosquitto/passwd
sudo mosquitto_passwd -b /etc/mosquitto/passwd esp32      "$P_ESP"
sudo mosquitto_passwd -b /etc/mosquitto/passwd cryservice "$P_CRY"
sudo mosquitto_passwd -b /etc/mosquitto/passwd fsm        "$P_FSM"
sudo mosquitto_passwd -b /etc/mosquitto/passwd dashboard  "$P_DASH"
sudo mosquitto_passwd -b /etc/mosquitto/passwd admin      "$P_ADMIN"
sudo chown root:mosquitto /etc/mosquitto/passwd && sudo chmod 640 /etc/mosquitto/passwd

sudo install -m 644 "$PI_DIR/mosquitto/smart-incubator.conf" /etc/mosquitto/conf.d/smart-incubator.conf
sudo install -m 640 -o root -g mosquitto "$PI_DIR/mosquitto/smart-incubator.acl" /etc/mosquitto/smart-incubator.acl
sudo systemctl enable mosquitto
sudo systemctl restart mosquitto

# ---------------------------------------------------------------------
step "Dashboard + nginx"
sudo mkdir -p "$WEB_ROOT/vendor"
sudo cp -r "$PI_DIR/dashboard/." "$WEB_ROOT/"
# Simpan library JS secara lokal agar dashboard tetap jalan tanpa internet
sudo curl -fsSL -o "$WEB_ROOT/vendor/mqtt.min.js"   https://unpkg.com/mqtt@5/dist/mqtt.min.js   || echo "(gagal unduh mqtt.js, dashboard akan memakai CDN)"
sudo curl -fsSL -o "$WEB_ROOT/vendor/chart.umd.js"  https://cdn.jsdelivr.net/npm/chart.js@4/dist/chart.umd.js || echo "(gagal unduh Chart.js, dashboard akan memakai CDN)"
# Password dashboard TIDAK ditulis ke config.js: pengguna login di login.html
# memakai akun MQTT (mis. "dashboard"), dan Mosquitto yang memeriksanya.

sudo install -m 644 "$PI_DIR/nginx/smart-incubator.conf" /etc/nginx/sites-available/smart-incubator
sudo ln -sf /etc/nginx/sites-available/smart-incubator /etc/nginx/sites-enabled/smart-incubator
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t
sudo systemctl enable nginx
sudo systemctl restart nginx

# ---------------------------------------------------------------------
step "Bluetooth"
sudo systemctl enable --now bluetooth
systemctl --user enable --now pipewire pipewire-pulse wireplumber || true

# ---------------------------------------------------------------------
step "Layanan cry recognition (venv)"
python3 -m venv "$CRY_DIR/.venv"
"$CRY_DIR/.venv/bin/pip" install --upgrade pip
"$CRY_DIR/.venv/bin/pip" install -r "$CRY_DIR/requirements.txt"
mkdir -p "$CRY_DIR/model"
chmod +x "$SCRIPTS_DIR/"*.sh

mkdir -p "$CONF_DIR"
read -rp "MAC address mic Bluetooth (kosongkan jika belum pairing): " BT_MAC
umask 077
cat > "$CONF_DIR/cry.env" <<EOF
MQTT_PASSWORD=$P_CRY
BT_MIC_MAC=$BT_MAC
EOF
cat > "$CONF_DIR/fsm.env" <<EOF
MQTT_PASSWORD=$P_FSM
EOF
umask 022

mkdir -p "$HOME/.config/systemd/user"
sed -e "s|__CRY_DIR__|$CRY_DIR|g" -e "s|__SCRIPTS_DIR__|$SCRIPTS_DIR|g" \
  "$PI_DIR/systemd/cry-recognition.service" > "$HOME/.config/systemd/user/cry-recognition.service"
# Mesin alarm FSM (fsm.py di root repo), memakai venv cry-recognition (sudah berisi paho-mqtt)
sed -e "s|__CRY_DIR__|$CRY_DIR|g" -e "s|__FSM_PY__|$(cd "$PI_DIR/.." && pwd)/fsm.py|g" \
  "$PI_DIR/systemd/fsm.service" > "$HOME/.config/systemd/user/fsm.service"
systemctl --user daemon-reload
systemctl --user enable cry-recognition.service
systemctl --user enable --now fsm.service
# Agar user service tetap jalan setelah boot tanpa login
sudo loginctl enable-linger "$USER"

# ---------------------------------------------------------------------
IP=$(hostname -I | awk '{print $1}')
step "Selesai"
cat <<EOF
Dashboard        : http://$IP/
MQTT (ESP32)     : $IP:1883  user=esp32  (isi di esp32s3-firmware/include/config.h)

Langkah berikutnya:
 1. Salin model ke  $CRY_DIR/model/cry_model.tflite
 2. Cek model       cd $CRY_DIR && .venv/bin/python inspect_model.py
 3. Pairing mic BT  (lihat komentar di scripts/connect_bt_mic.sh), lalu isi BT_MIC_MAC di $CONF_DIR/cry.env
 4. Jalankan        systemctl --user start cry-recognition
    Log             journalctl --user -u cry-recognition -f
 5. Pantau MQTT     mosquitto_sub -h localhost -u admin -P '<pw admin>' -t 'incubator/#' -v
EOF
