#!/usr/bin/env bash
# =====================================================================
#  Pasang kiriman dashboard + fsm.py (Code Blue & detak jantung) di Pi.
#  Dipanggil otomatis oleh kirim_ke_pi.ps1 dari laptop, atau manual:
#    bash ~/kiriman-code-blue/pasang_kiriman.sh                 # cari repo otomatis
#    bash ~/kiriman-code-blue/pasang_kiriman.sh /home/pi/repo   # sebut folder repo
#
#  Yang dilakukan:
#   1. Backup dashboard di /var/www/smart-incubator, fsm.py, dan ACL Mosquitto
#      ke ~/backup-pi_<tanggal>.
#   2. Salin dashboard ke repo dan ke /var/www/smart-incubator.
#   3. Salin fsm.py dan test_alarm_scenarios.py ke repo.
#   4. Bila belum ada: buat akun MQTT 'fsm' dan izinnya di ACL, dan izinkan ESP32
#      membaca status tangisan (incubator/cry/state dan incubator/cry/status).
#   5. Pasang / jalankan ulang layanan fsm (systemd user service).
# =====================================================================
set -euo pipefail

K="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="${1:-}"
WEB_ROOT="${WEB_ROOT:-/var/www/smart-incubator}"
ACL="${ACL:-/etc/mosquitto/smart-incubator.acl}"
PASSWD="${PASSWD:-/etc/mosquitto/passwd}"
CONF_DIR="$HOME/.config/smart-incubator"
UNIT="$HOME/.config/systemd/user/fsm.service"

step() { echo -e "\n\033[1;36m==> $*\033[0m"; }
warn() { echo -e "\033[1;33m[!] $*\033[0m"; }

[[ $EUID -eq 0 ]] && { echo "Jalankan sebagai user biasa (bukan root/sudo)."; exit 1; }

# File dari Windows bisa berakhiran CRLF; systemd tidak menerimanya
sed -i 's/\r$//' "$K/fsm.service"

# ---------------------------------------------------------------------
step "Mencari folder repo di Pi"
if [[ -z "$REPO" ]]; then
  mapfile -t found < <(find "$HOME" -maxdepth 4 -type f -name fsm.py -not -path "$K/*" \
                         -not -path '*/backup*' -not -path '*/.venv/*' 2>/dev/null \
                       | while read -r f; do d=$(dirname "$f"); [[ -d "$d/raspberry-pi/dashboard" ]] && echo "$d"; done)
  if (( ${#found[@]} == 0 )); then
    echo "Folder repo tidak ditemukan di $HOME."
    echo "Ulangi dengan menyebut foldernya, contoh: bash $0 /home/$(id -un)/Documents/i2aspo-incubator"
    exit 1
  elif (( ${#found[@]} == 1 )); then
    REPO="${found[0]}"
  else
    echo "Ada ${#found[@]} folder repo:"; printf '  %s\n' "${found[@]}"
    # 1) Pilih folder yang dipakai layanan yang sudah terpasang (path terpanjang yang cocok)
    units=$(cat "$HOME/.config/systemd/user/fsm.service" "$HOME/.config/systemd/user/cry-recognition.service" \
                /etc/systemd/system/incubator-camera.service 2>/dev/null || true)
    for d in "${found[@]}"; do
      if grep -qF "$d/" <<<"$units" && (( ${#d} > ${#REPO} )); then REPO="$d"; fi
    done
    if [[ -n "$REPO" ]]; then
      echo "Dipilih (dipakai layanan yang sudah terpasang): $REPO"
    else
      # 2) Tidak ada layanan yang menunjuk: pilih folder terluar bila folder lain ada di dalamnya
      outer=$(printf '%s\n' "${found[@]}" | awk '{ print length, $0 }' | sort -n | head -1 | cut -d' ' -f2-)
      for d in "${found[@]}"; do
        [[ "$d" == "$outer" || "$d" == "$outer"/* ]] || { outer=""; break; }
      done
      if [[ -z "$outer" ]]; then
        echo "Tidak bisa memilih otomatis. Ulangi dengan menyebut foldernya: bash $0 <folder-repo>"
        exit 1
      fi
      REPO="$outer"
      echo "Dipilih folder terluar (belum ada layanan yang menunjuk ke salah satunya): $REPO"
    fi
  fi
fi
REPO="$(cd "$REPO" && pwd)"
[[ -d "$REPO/raspberry-pi/dashboard" ]] || { echo "$REPO bukan folder repo (tidak ada raspberry-pi/dashboard)."; exit 1; }
echo "Repo: $REPO"
CRY_DIR="$REPO/raspberry-pi/cry-recognition"

# ---------------------------------------------------------------------
B="$HOME/backup-pi_$(date +%Y%m%d_%H%M%S)"
step "Backup versi yang sekarang ke $B"
mkdir -p "$B/www" "$B/repo"
[[ -d "$WEB_ROOT" ]] && sudo cp -a "$WEB_ROOT/." "$B/www/"
cp -a "$REPO/raspberry-pi/dashboard" "$B/repo/"
[[ -f "$REPO/fsm.py" ]] && cp -a "$REPO/fsm.py" "$B/repo/"
sudo test -f "$ACL" && sudo cp "$ACL" "$B/"
[[ -f "$UNIT" ]] && cp "$UNIT" "$B/"
sudo chown -R "$(id -un):$(id -gn)" "$B"
cat > "$B/KEMBALIKAN.txt" <<TXT
Mengembalikan versi sebelum kiriman $(date '+%Y-%m-%d %H:%M'):
  sudo cp -a $B/www/. $WEB_ROOT/
  cp -a $B/repo/dashboard/. $REPO/raspberry-pi/dashboard/
  cp $B/repo/fsm.py $REPO/fsm.py && systemctl --user restart fsm
TXT

# ---------------------------------------------------------------------
step "Memasang dashboard"
cp "$K"/dashboard/* "$REPO/raspberry-pi/dashboard/"
sudo mkdir -p "$WEB_ROOT"
sudo cp "$K"/dashboard/* "$WEB_ROOT/"
ls -1 "$WEB_ROOT"

step "Memasang fsm.py dan skrip uji"
cp "$K/fsm.py" "$REPO/fsm.py"
mkdir -p "$REPO/raspberry-pi/scripts" "$REPO/raspberry-pi/systemd"
cp "$K/test_alarm_scenarios.py" "$REPO/raspberry-pi/scripts/"
cp "$K/fsm.service" "$REPO/raspberry-pi/systemd/fsm.service"

# ---------------------------------------------------------------------
step "Akun MQTT 'fsm'"
if ! sudo test -f "$ACL"; then
  warn "ACL $ACL tidak ditemukan. Jalankan setup_pi.sh dulu."; exit 1
fi
acl_changed=0
if sudo grep -q '^user fsm' "$ACL" && [[ -f "$CONF_DIR/fsm.env" ]]; then
  echo "Sudah ada, tidak diubah."
else
  echo "Akun 'fsm' belum lengkap. Buat password-nya sekarang (dipakai fsm.py untuk masuk ke broker)."
  while true; do
    read -rsp "Password MQTT untuk user 'fsm': " p1; echo
    read -rsp "Ulangi password: " p2; echo
    [[ -n "$p1" && "$p1" == "$p2" ]] && break
    echo "Tidak cocok / kosong, ulangi."
  done
  sudo mosquitto_passwd -b "$PASSWD" fsm "$p1"
  if ! sudo grep -q '^user fsm' "$ACL"; then
    printf '\n# Mesin alarm FSM (fsm.py): baca semua, tulis keputusan alarm\nuser fsm\ntopic read  incubator/#\ntopic write incubator/fsm/state\n' \
      | sudo tee -a "$ACL" >/dev/null
  fi
  mkdir -p "$CONF_DIR"
  ( umask 077; printf 'MQTT_PASSWORD=%s\n' "$p1" > "$CONF_DIR/fsm.env" )
  acl_changed=1
fi

step "Izin ESP32 membaca status tangisan"
if ! sudo grep -q '^user esp32' "$ACL"; then
  warn "Blok 'user esp32' tidak ada di $ACL; lewati."
elif sudo grep -Eq '^topic read +incubator/cry/state' "$ACL"; then
  echo "Sudah ada, tidak diubah."
else
  sudo sed -i -e '/^user esp32[[:space:]]*$/a topic read  incubator/cry/status' \
              -e '/^user esp32[[:space:]]*$/a topic read  incubator/cry/state' "$ACL"
  echo "Ditambahkan: topic read incubator/cry/state dan incubator/cry/status untuk user esp32."
  acl_changed=1
fi
if (( acl_changed )); then
  sudo systemctl restart mosquitto
  sleep 2
fi

# ---------------------------------------------------------------------
step "Layanan fsm"
PY="$CRY_DIR/.venv/bin/python"
mkdir -p "$(dirname "$UNIT")"
sed -e "s|__CRY_DIR__|$CRY_DIR|g" -e "s|__FSM_PY__|$REPO/fsm.py|g" "$K/fsm.service" > "$UNIT"
if [[ ! -x "$PY" ]]; then
  warn "venv $CRY_DIR/.venv tidak ada; memakai python3 sistem (butuh paho-mqtt)."
  sed -i "s|$CRY_DIR/.venv/bin/python|/usr/bin/python3|" "$UNIT"
fi
systemctl --user daemon-reload
systemctl --user enable fsm >/dev/null 2>&1
systemctl --user restart fsm
main=$(systemctl --user show -p MainPID --value fsm 2>/dev/null || echo 0)
others=$(pgrep -af 'fsm\.py' | awk -v m="$main" '$1 != m' || true)
sudo loginctl enable-linger "$(id -un)"
sleep 5
echo "fsm: $(systemctl --user is-active fsm)"
journalctl --user -u fsm -n 6 --no-pager -o cat || true
if [[ -n "$others" ]]; then
  warn "Sebelumnya ada fsm.py lain yang jalan manual (bukan dari layanan):"
  echo "$others"
  warn "Hentikan yang manual itu supaya tidak ada dua mesin alarm."
fi

# ---------------------------------------------------------------------
IP=$(hostname -I 2>/dev/null | awk '{print $1}')
step "Selesai"
cat <<TXT
Dashboard : http://${IP:-<ip-pi>}/   (tekan Ctrl+Shift+R di browser)
Masuk     : akun MQTT 'dashboard' dan password-nya
Backup    : $B   (cara mengembalikan ada di KEMBALIKAN.txt)
Log fsm   : journalctl --user -u fsm -f
Uji alarm : matikan ESP32, lalu
            MQTT_PASSWORD='<pw admin>' python3 $REPO/raspberry-pi/scripts/test_alarm_scenarios.py
TXT
