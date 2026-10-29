# Builds the Windows installer: dist\Mnemerynne-Backup-Setup-<version>.exe (per user, no admin rights).
# Needs Python 3.12+ with uv, and Inno Setup 6 (iscc on PATH). Run from clients\desktop-sync.
$ErrorActionPreference = 'Stop'
uv venv .build-venv
.\.build-venv\Scripts\activate
uv pip install ".[tray]" pyinstaller==6.16.0
python packaging\make_icons.py
pyinstaller --noconfirm --distpath dist --workpath packaging\build\pyi packaging\mnemerynne-backup.spec
$version = (python -c "import mnemerynne_backup; print(mnemerynne_backup.__version__)")
iscc /DAppVersion=$version packaging\installer.iss
# Sign it if you have a code-signing certificate:
#   signtool sign /fd SHA256 /a /tr http://timestamp.digicert.com /td SHA256 dist\Mnemerynne-Backup-Setup-$version.exe
Write-Host "dist\Mnemerynne-Backup-Setup-$version.exe"
