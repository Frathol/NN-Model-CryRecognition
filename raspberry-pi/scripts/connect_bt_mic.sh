#!/usr/bin/env bash
# Hubungkan mic Bluetooth, aktifkan profil headset (HFP/HSP) agar mic aktif,
# lalu jadikan sumber input default PipeWire.
#
# Pairing pertama kali (sekali saja, manual):
#   bluetoothctl
#     power on
#     agent on
#     scan on            # catat MAC mic, mis. AA:BB:CC:DD:EE:FF
#     pair AA:BB:CC:DD:EE:FF
#     trust AA:BB:CC:DD:EE:FF
#     connect AA:BB:CC:DD:EE:FF
#     quit
#
# Pemakaian: ./connect_bt_mic.sh AA:BB:CC:DD:EE:FF   (atau set env BT_MIC_MAC)
set -u
MAC="${1:-${BT_MIC_MAC:-}}"
if [[ -z "$MAC" ]]; then
  echo "MAC mic belum diisi (argumen atau BT_MIC_MAC di ~/.config/smart-incubator/cry.env)"; exit 1
fi
MAC_US="${MAC//:/_}"

echo "[bt] menghubungkan $MAC ..."
for i in 1 2 3 4 5; do
  bluetoothctl connect "$MAC" >/dev/null 2>&1
  bluetoothctl info "$MAC" | grep -q "Connected: yes" && break
  sleep 3
done
bluetoothctl info "$MAC" | grep -q "Connected: yes" || { echo "[bt] gagal terhubung"; exit 1; }
sleep 2

CARD="$(pactl list short cards | awk '{print $2}' | grep -i "bluez_card.${MAC_US}" | head -n1)"
if [[ -z "$CARD" ]]; then echo "[bt] card PipeWire tidak ditemukan"; exit 1; fi

# A2DP hanya untuk output; mic butuh profil headset (HFP/HSP). mSBC = 16 kHz wideband.
PROFILES="$(pactl list cards | awk -v c="$CARD" '$0 ~ "Name: "c {f=1} f && /Profiles:/ {p=1; next} f && p && /Active Profile/ {exit} f && p {print $1}' | tr -d ':')"
for P in headset-head-unit-msbc headset-head-unit headset-head-unit-cvsd headset_head_unit; do
  if grep -qx "$P" <<<"$PROFILES"; then
    pactl set-card-profile "$CARD" "$P" && echo "[bt] profil: $P" && break
  fi
done
sleep 1

SRC="$(pactl list short sources | awk '{print $2}' | grep -i "bluez_input" | grep -i -e "${MAC_US}" -e "${MAC}" | head -n1)"
if [[ -n "$SRC" ]]; then
  pactl set-default-source "$SRC"
  echo "[bt] default source: $SRC"
else
  echo "[bt] sumber input Bluetooth tidak muncul. Profil yang tersedia:"; echo "$PROFILES"; exit 1
fi
