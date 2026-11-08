<#
  Kirim perubahan dashboard + fsm.py (Code Blue & detak jantung) ke Raspberry Pi,
  lalu pasang di sana dengan pasang_kiriman.sh.

  Pemakaian (PowerShell, dari folder repo):
    powershell -ExecutionPolicy Bypass -File .\raspberry-pi\scripts\kirim_ke_pi.ps1
    ... -Pi user@alamat-lain -Repo /home/irengberi/NN-Model-CryRecognition

  -Pi      user@alamat Raspberry Pi (bawaan: Pi tim, irengberi@10.223.183.158)
  -Repo    folder repo di Pi; kosongkan supaya dicari otomatis
  -DryRun  cek kelengkapan file lokal tanpa menjalankan ssh/scp ke Pi

  Password Pi akan ditanya beberapa kali (ssh, scp, sudo).
#>
param(
  [string]$Pi = "irengberi@10.223.183.158",
  [string]$Repo = "",
  [switch]$DryRun
)
$ErrorActionPreference = "Stop"
Set-Location (Resolve-Path (Join-Path $PSScriptRoot "..\.."))

$files = @(
  "raspberry-pi\dashboard",
  "fsm.py",
  "raspberry-pi\scripts\test_alarm_scenarios.py",
  "raspberry-pi\systemd\fsm.service",
  "raspberry-pi\scripts\pasang_kiriman.sh"
)
foreach ($f in $files) { if (-not (Test-Path $f)) { throw "File tidak ditemukan: $f" } }

if ($DryRun) {
  Write-Host "[DryRun] Semua file ditemukan dan siap dikirim ke ${Pi}:" -ForegroundColor Green
  $files | ForEach-Object { Write-Host "  - $_" }
  exit 0
}

Write-Host "`n[1/3] Menyiapkan folder ~/kiriman-code-blue di Pi..." -ForegroundColor Cyan
ssh $Pi "rm -rf ~/kiriman-code-blue && mkdir -p ~/kiriman-code-blue"
if ($LASTEXITCODE -ne 0) { throw "ssh ke $Pi gagal. Cek alamat Pi dan pastikan laptop satu jaringan dengan Pi." }

Write-Host "`n[2/3] Mengirim file..." -ForegroundColor Cyan
scp -r $files "${Pi}:~/kiriman-code-blue/"
if ($LASTEXITCODE -ne 0) { throw "scp gagal." }

Write-Host "`n[3/3] Memasang di Pi..." -ForegroundColor Cyan
ssh -t $Pi "sed -i 's/\r$//' ~/kiriman-code-blue/pasang_kiriman.sh && bash ~/kiriman-code-blue/pasang_kiriman.sh $Repo"
if ($LASTEXITCODE -ne 0) { throw "Pemasangan di Pi gagal; lihat pesan di atas." }
Write-Host "`nSelesai." -ForegroundColor Green
