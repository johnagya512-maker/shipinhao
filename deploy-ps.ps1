# Shipinhao AI - Deploy to Desktop (PowerShell, retry-limited)
$ErrorActionPreference = "Stop"

$src = "D:\chennigongzuoshi\shipinhao\desktop\release\win-unpacked"
$dst = "C:\Users\Administrator\Desktop\shipinhao-desktop"

if (-not (Test-Path $src)) {
    Write-Host "[ERR] Build output not found: $src" -ForegroundColor Red
    exit 1
}

Write-Host "[1/4] Stopping old processes..."
Get-Process | Where-Object { $_.ProcessName -in @("视频号图书带货AI","shipinhao-backend") } | Stop-Process -Force -ErrorAction SilentlyContinue
Start-Sleep -Seconds 2

Write-Host "[2/4] Preparing destination..."
if (-not (Test-Path $dst)) {
    New-Item -ItemType Directory -Path $dst | Out-Null
}

Write-Host "[3/4] Copying files (this may take 1-2 minutes)..."
# Remove old content then mirror with limited retries to avoid getting stuck on locked files
robocopy "$src" "$dst" /MIR /R:3 /W:2 /MT:8 /NP /NFL /NDL
$rc = $LASTEXITCODE
if ($rc -ge 8) {
    Write-Host "[ERR] robocopy failed with code $rc" -ForegroundColor Red
    exit 1
}

Write-Host "[4/4] Done."
Write-Host "You can now start: $dst\视频号图书带货AI.exe" -ForegroundColor Green
