#!/usr/bin/env bash
# Pemeriksaan cepat status layanan Smart Incubator di Raspberry Pi
set -euo pipefail

echo "=========================================="
echo " Smart Incubator - System Health Check"
echo "=========================================="

echo -e "\n[1] Layanan Sistem (Mosquitto & Nginx):"
for svc in mosquitto nginx; do
  if systemctl is-active --quiet "$svc"; then
    echo "  [OK] $svc aktif"
  else
    echo "  [WARN] $svc TIDAK aktif"
  fi
done

echo -e "\n[2] Layanan User Systemd (Cry, FSM, Camera):"
for usvc in cry-recognition fsm camera; do
  if systemctl --user is-active --quiet "$usvc" 2>/dev/null; then
    echo "  [OK] $usvc.service aktif"
  else
    echo "  [INFO] $usvc.service tidak berjalan di user session ini"
  fi
done

echo -e "\n[3] Port Jaringan (1883 MQTT, 9001 WS, 80 HTTP):"
ss -tln | grep -E ':(1883|9001|80)\b' || echo "  Tidak ada port target yang terdeteksi."

echo -e "\nSelesai."
