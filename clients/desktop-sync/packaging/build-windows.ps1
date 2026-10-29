# Builds the Windows installer: dist\Mnemerynne-Backup-Setup-<version>.exe (per user, no admin rights).
# Needs a python.org Python 3.12+ (it has Tk, which the tray app needs), uv (on PATH or as `py -m uv`) and
# Inno Setup 6 (winget install JRSoftware.InnoSetup --scope user). Run from clients\desktop-sync.
#   powershell -ExecutionPolicy Bypass -File packaging\build-windows.ps1 [-Python C:\path\to\python.exe]
param([string]$Python = "")
$ErrorActionPreference = 'Stop'

function Find-Uv {
    if (Get-Command uv -ErrorAction SilentlyContinue) { return ,@('uv') }
    return ,@('py', '-3', '-m', 'uv')
}
function Find-Iscc {
    $found = Get-Command iscc -ErrorAction SilentlyContinue
    if ($found) { return $found.Source }
    foreach ($p in "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe", "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
                   "$env:ProgramFiles\Inno Setup 6\ISCC.exe") {
        if (Test-Path $p) { return $p }
    }
    throw "Inno Setup 6 isn't installed: winget install JRSoftware.InnoSetup --scope user"
}
if (-not $Python) { $Python = (py -3 -c "import sys; print(sys.executable)") }
& $Python -c "import tkinter" 2>$null
if ($LASTEXITCODE -ne 0) { throw "$Python has no Tk; use the python.org installer's Python (-Python ...)" }

$uv = Find-Uv
$iscc = Find-Iscc
& $uv[0] $uv[1..9] venv --python $Python .build-venv
if ($LASTEXITCODE -ne 0) { throw "uv venv failed" }
$venvPython = Resolve-Path .build-venv\Scripts\python.exe
& $uv[0] $uv[1..9] pip install --python $venvPython ".[tray]" pyinstaller==6.16.0
if ($LASTEXITCODE -ne 0) { throw "installing the app failed" }
& $venvPython packaging\make_icons.py
& $venvPython -m PyInstaller --noconfirm --distpath dist --workpath packaging\build\pyi packaging\mnemerynne-backup.spec
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }
$version = (& $venvPython -c "import mnemerynne_backup; print(mnemerynne_backup.__version__)")
& $iscc /Q "/DAppVersion=$version" packaging\installer.iss
if ($LASTEXITCODE -ne 0) { throw "Inno Setup failed" }
# Sign it if you have a code-signing certificate:
#   signtool sign /fd SHA256 /a /tr http://timestamp.digicert.com /td SHA256 dist\Mnemerynne-Backup-Setup-$version.exe
Write-Host "dist\Mnemerynne-Backup-Setup-$version.exe"
