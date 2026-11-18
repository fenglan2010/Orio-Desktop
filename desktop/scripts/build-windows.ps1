# Reproducible Windows desktop build (run on Windows with Node 20+ and Python venv prepared).
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location (Join-Path $Root "desktop")

Write-Host "Building frontend..."
Push-Location (Join-Path $Root "frontend")
npm ci
npm run build
Pop-Location

$VenvPython = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $VenvPython)) {
  Write-Host "Create backend venv from repo root:"
  Write-Host "  python -m venv .venv"
  Write-Host "  .\.venv\Scripts\pip install -r backend\requirements.txt"
  exit 1
}

# Skip code-sign tooling extraction (winCodeSign) unless you opt in to signing.
$env:CSC_IDENTITY_AUTO_DISCOVERY = "false"
$env:OIRO_DISTRIBUTION = "packaged"

npm ci
npm run pack:dir

Write-Host "Artifacts in desktop\dist (run desktop\dist\win-unpacked\OIRO.exe)"
Write-Host "Optional signing: run PowerShell as Administrator or enable Windows Developer Mode if a future build step needs symlink privileges."