# Build portable embeddable Python 3.11 + backend deps into desktop/python-bundle (packed as resources/python).
$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$DesktopRoot = Split-Path -Parent $ScriptDir
$RepoRoot = Split-Path -Parent $DesktopRoot
$BundleDir = Join-Path $DesktopRoot "python-bundle"
$PyVersion = "3.11.9"
$EmbedZip = "python-$PyVersion-embed-amd64.zip"
$EmbedUrl = "https://www.python.org/ftp/python/$PyVersion/$EmbedZip"
$ReqFile = Join-Path $RepoRoot "backend\requirements.txt"
if (-not (Test-Path $ReqFile)) {
  throw "Missing requirements file (expected repo-root backend/requirements.txt): $ReqFile"
}

Write-Host "=== OIRO embed Python bundle ($PyVersion) ==="
Write-Host "RepoRoot: $RepoRoot"

if (Test-Path $BundleDir) {
  Remove-Item -Recurse -Force $BundleDir
}
New-Item -ItemType Directory -Path $BundleDir | Out-Null

$tempZip = Join-Path $env:TEMP $EmbedZip
if (-not (Test-Path $tempZip)) {
  Write-Host "Downloading $EmbedUrl"
  Invoke-WebRequest -Uri $EmbedUrl -OutFile $tempZip -UseBasicParsing
}
Expand-Archive -Path $tempZip -DestinationPath $BundleDir -Force

$pthName = "python311._pth"
$pthPath = Join-Path $BundleDir $pthName
if (-not (Test-Path $pthPath)) {
  throw "Missing $pthPath after extract"
}

$siteDir = Join-Path $BundleDir "Lib\site-packages"
New-Item -ItemType Directory -Path $siteDir -Force | Out-Null

@"
python311.zip
.
Lib\site-packages
import site
"@ | Set-Content -Path $pthPath -Encoding ascii

$pythonExe = Join-Path $BundleDir "python.exe"
$getPip = Join-Path $env:TEMP "get-pip.py"
if (-not (Test-Path $getPip)) {
  Invoke-WebRequest -Uri "https://bootstrap.pypa.io/get-pip.py" -OutFile $getPip -UseBasicParsing
}

Write-Host "Installing pip into embed python..."
& $pythonExe $getPip --no-warn-script-location
if ($LASTEXITCODE -ne 0) { throw "get-pip failed" }

Write-Host "Installing backend requirements (this may take several minutes)..."
& $pythonExe -m pip install --upgrade pip
& $pythonExe -m pip install -r $ReqFile
if ($LASTEXITCODE -ne 0) { throw "pip install -r requirements failed" }

Write-Host "Verifying uvicorn and backend import (same PYTHONPATH as packaged Electron)..."
$backendDir = (Resolve-Path (Join-Path $RepoRoot "backend")).Path
$backendForPy = $backendDir -replace "\\", "/"

& $pythonExe -m uvicorn --version
if ($LASTEXITCODE -ne 0) { throw "uvicorn missing in bundle" }

$verifyPy = Join-Path $env:TEMP "oiro_bundle_verify.py"
@"

import sys
sys.path.insert(0, r"$backendForPy")
import uvicorn
import app.main
"@ | Set-Content -Path $verifyPy -Encoding UTF8
& $pythonExe $verifyPy
if ($LASTEXITCODE -ne 0) { throw "Cannot import app.main with bundled python" }
Remove-Item $verifyPy -ErrorAction SilentlyContinue
if ($LASTEXITCODE -ne 0) { throw "uvicorn missing in bundle" }

Write-Host "OK: python-bundle ready at $BundleDir"