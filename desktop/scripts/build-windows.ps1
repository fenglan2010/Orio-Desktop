# Reproducible Windows desktop build (Node 20 LTS recommended). Ships embedded Python — hotel users need no system Python.
param(
    [switch] $WithInstaller
)

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$DesktopRoot = Split-Path -Parent $ScriptDir
$RepoRoot = Split-Path -Parent $DesktopRoot
Set-Location $DesktopRoot

Write-Host "=== OIRO Windows pack (unsigned, embedded Python) ==="
Write-Host "Repo root: $RepoRoot"

$nodeVersion = (node -v 2>$null)
if ($nodeVersion -match '^v(\d+)') {
    $major = [int]$Matches[1]
    if ($major -ge 26) {
        Write-Warning "Node $nodeVersion detected. Electron tooling is tested on Node 20 LTS; Node 26+ may fail. Use nvm-windows or install Node 20."
    }
}

Write-Host "Ensuring frontend dist..."
node scripts/ensure-frontend-dist.js --build

Write-Host "Bundling embedded Python + backend deps..."
& (Join-Path $ScriptDir "bundle-embed-python.ps1")
$bundlePy = Join-Path $DesktopRoot "python-bundle\python.exe"
if (-not (Test-Path $bundlePy)) { throw "Missing $bundlePy after bundle step" }

$env:CSC_IDENTITY_AUTO_DISCOVERY = "false"
$env:OIRO_DISTRIBUTION = "packaged"

npm ci
npm run pack:dir

if ($WithInstaller) {
    Write-Host "Building unsigned NSIS installer..."
    npm run pack:win:nsis
    $setup = Get-ChildItem -Path (Join-Path $DesktopRoot "dist") -Filter "OIRO-Setup-*.exe" -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($setup) {
        Write-Host "Installer: $($setup.FullName)"
        Write-Warning "Unsigned build: Windows SmartScreen may warn. Distribute only builds you trust."
    } else {
        Write-Warning "NSIS step finished but OIRO-Setup-*.exe not found under desktop\dist"
    }
}

$unpacked = Join-Path $DesktopRoot "dist\win-unpacked"
$checks = @(
    (Join-Path $unpacked "resources\renderer\index.html"),
    (Join-Path $unpacked "resources\backend\app\main.py"),
    (Join-Path $unpacked "resources\python\python.exe")
)
foreach ($path in $checks) {
    if (-not (Test-Path $path)) { throw "Missing required artifact: $path" }
    Write-Host "OK: $path"
}

Write-Host ""
Write-Host "Hotel flow: install -> open OIRO -> enter install key -> login (no Python install required)."
Write-Host "Run: desktop\dist\win-unpacked\OIRO.exe"