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
# Expand-Archive -Force breaks on some GitHub win-2025 + pwsh 7 runners; use BCL zip API.
Add-Type -AssemblyName System.IO.Compression.FileSystem
[System.IO.Compression.ZipFile]::ExtractToDirectory($tempZip, $BundleDir)

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

Write-Host "Verifying uvicorn --app-dir (no PYTHONPATH; matches packaged Electron)..."
$backendDir = (Resolve-Path (Join-Path $RepoRoot "backend")).Path

& $pythonExe -m uvicorn --version
if ($LASTEXITCODE -ne 0) { throw "uvicorn missing in bundle" }

$port = 18976
$proc = Start-Process -FilePath $pythonExe -ArgumentList @(
  "-m", "uvicorn", "app.main:app",
  "--app-dir", $backendDir,
  "--host", "127.0.0.1",
  "--port", "$port"
) -PassThru -NoNewWindow

$healthOk = $false
try {
  for ($i = 0; $i -lt 45; $i++) {
    Start-Sleep -Seconds 1
    try {
      $resp = Invoke-WebRequest -Uri "http://127.0.0.1:$port/api/health" -UseBasicParsing -TimeoutSec 3
      if ($resp.StatusCode -eq 200) {
        $healthOk = $true
        break
      }
    } catch {
      # retry
    }
  }
} finally {
  if ($proc -and -not $proc.HasExited) {
    Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
  }
}
if (-not $healthOk) {
  throw "uvicorn --app-dir failed to serve /api/health from bundled python"
}

Write-Host "OK: python-bundle ready at $BundleDir"