# After pack: add resources\backend to embed python path (sibling of resources\python).
param(
    [Parameter(Mandatory = $true)]
    [string] $UnpackedDir
)

$ErrorActionPreference = "Stop"
$pth = Join-Path $UnpackedDir "resources\python\python311._pth"
if (-not (Test-Path $pth)) {
    throw "Missing $pth"
}
$line = "..\backend"
$existing = Get-Content $pth -ErrorAction SilentlyContinue
if ($existing -contains $line) {
    Write-Host "OK: $pth already lists $line"
    exit 0
}
Add-Content -Path $pth -Value $line -Encoding ascii
Write-Host "Patched $pth with $line"