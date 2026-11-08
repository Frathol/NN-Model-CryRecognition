<#
  Ambil salinan repo dari Raspberry Pi ke laptop UNTUK DIBANDINGKAN.
  Tidak menimpa file apa pun di repo laptop: hasilnya disimpan di
  backup\pi-snapshot_<tanggal>\ (folder backup\ tidak ikut git).

  Pemakaian (PowerShell, dari folder repo):
    powershell -ExecutionPolicy Bypass -File .\raspberry-pi\scripts\ambil_dari_pi.ps1

  Yang TIDAK diambil: .git, .venv, __pycache__, *.pyc, node_modules,
  rekaman video (*.mp4, *.h264, *.mkv, folder recordings) dan salinan lama
  NN-Model-CryRecognition di dalam repo.
  Password Pi akan ditanya 2 kali (ssh, scp).
#>
param(
  [string]$Pi = "irengberi@10.223.183.158",
  [string]$Repo = "/home/irengberi/Documents/i2aspo-incubator"
)
$ErrorActionPreference = "Stop"
Set-Location (Resolve-Path (Join-Path $PSScriptRoot "..\.."))

$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$dest = "backup\pi-snapshot_$stamp"
New-Item -ItemType Directory -Force $dest | Out-Null

Write-Host "`n[1/3] Membungkus repo di Pi ($Repo)..." -ForegroundColor Cyan
$remote = "set -e; cd '$Repo'; " +
  "tar czf /tmp/pi-snapshot.tgz --exclude=.git --exclude=.venv --exclude=__pycache__ --exclude='*.pyc' " +
  "--exclude=node_modules --exclude=./NN-Model-CryRecognition --exclude='*.mp4' --exclude='*.h264' " +
  "--exclude='*.mkv' --exclude=recordings . ; " +
  "{ echo '== git status'; git status --short; echo '== git log'; git log --oneline -8; " +
  "echo '== branch'; git branch -vv; } > /tmp/pi-git-info.txt 2>&1 || true; " +
  "ls -lh /tmp/pi-snapshot.tgz"
ssh $Pi $remote
if ($LASTEXITCODE -ne 0) { throw "Gagal membungkus repo di Pi." }

Write-Host "`n[2/3] Mengunduh ke $dest ..." -ForegroundColor Cyan
scp "${Pi}:/tmp/pi-snapshot.tgz" "${Pi}:/tmp/pi-git-info.txt" "$dest"
if ($LASTEXITCODE -ne 0) { throw "Gagal mengunduh dari Pi." }

Write-Host "`n[3/3] Membuka arsip..." -ForegroundColor Cyan
New-Item -ItemType Directory -Force "$dest\repo" | Out-Null
tar -xzf "$dest\pi-snapshot.tgz" -C "$dest\repo"
if ($LASTEXITCODE -ne 0) { throw "Gagal membuka arsip." }

Write-Host "`nSelesai. Salinan repo Pi ada di: $dest\repo" -ForegroundColor Green
Write-Host "Repo laptop TIDAK diubah."
