$ErrorActionPreference = 'Stop'
$Version = '1.0.0'
$Python = 'py'

& $Python -3.13 -m venv .venv
& .\.venv\Scripts\python.exe -m pip install --upgrade pip
& .\.venv\Scripts\python.exe -m pip install -r requirements.txt
& .\.venv\Scripts\python.exe -m PyInstaller --clean --noconfirm pharmacy_app.spec

$VersionedDir = Join-Path 'dist' $Version
New-Item -ItemType Directory -Force -Path $VersionedDir | Out-Null
Copy-Item 'dist\SOLONA.exe' (Join-Path $VersionedDir 'SOLONA.exe') -Force

$Iscc = Get-Command ISCC.exe -ErrorAction SilentlyContinue
if ($Iscc) {
    & $Iscc.Source "/DMyAppVersion=$Version" 'installer.iss'
}

Get-FileHash (Join-Path $VersionedDir 'SOLONA.exe') -Algorithm SHA256
