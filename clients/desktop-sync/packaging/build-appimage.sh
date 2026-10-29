#!/usr/bin/env bash
# Builds dist/Mnemerynne_Backup-<version>-x86_64.AppImage. Needs uv, and appimagetool on PATH
# (https://github.com/AppImage/appimagetool/releases). Run from clients/desktop-sync.
set -euo pipefail
uv venv .build-venv
. .build-venv/bin/activate
uv pip install ".[tray]" pyinstaller==6.16.0
python packaging/make_icons.py
pyinstaller --noconfirm --distpath dist --workpath packaging/build/pyi packaging/mnemerynne-backup.spec
version=$(python -c "import mnemerynne_backup; print(mnemerynne_backup.__version__)")

appdir=packaging/build/AppDir
rm -rf "$appdir"
mkdir -p "$appdir/usr/bin"
cp -r dist/mnemerynne-backup/. "$appdir/usr/bin/"
cp packaging/build/icon.png "$appdir/mnemerynne-backup.png"
cp packaging/mnemerynne-backup.desktop "$appdir/"
cat > "$appdir/AppRun" <<'RUN'
#!/bin/sh
# The tray app by default; `Mnemerynne_Backup.AppImage cli …` runs the command line.
here="$(dirname "$(readlink -f "$0")")"
if [ "${1:-}" = "cli" ]; then shift; exec "$here/usr/bin/mnemerynne-backup" "$@"; fi
exec "$here/usr/bin/mnemerynne-backup-tray" "$@"
RUN
chmod +x "$appdir/AppRun"
ARCH=x86_64 appimagetool "$appdir" "dist/Mnemerynne_Backup-$version-x86_64.AppImage"
echo "dist/Mnemerynne_Backup-$version-x86_64.AppImage"
