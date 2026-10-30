# Building Mnemerynne Backup

Both builds bundle Python, so people install nothing else. Bump `__version__` in
`mnemerynne_backup/__init__.py` (and `pyproject.toml`) first.

| | Command (from `clients/desktop-sync`) | Result |
|---|---|---|
| Windows | `powershell -File packaging\build-windows.ps1` (needs Inno Setup 6) | `dist\Mnemerynne-Backup-Setup-<version>.exe`: per user, no admin rights, starts at sign-in |
| Linux | `PYTHON=/usr/bin/python3 packaging/build-appimage.sh` (needs `appimagetool`) | `dist/Mnemerynne_Backup-<version>-x86_64.AppImage`; `… cli <command>` runs the command line |

Build with a Python that has Tk: python.org's installer on Windows, the distro's Python with
`python3-tk` (Debian/Ubuntu) or `python3-tkinter` (Fedora) on Linux. If PyInstaller warns
"Library not found: libtcl…", the tray app won't start; fix the Python, not the spec. Build the
AppImage on the oldest distro you need to support (glibc only goes forward).

Sign the Windows installer if there's a code-signing certificate (the command is at the end of the
script); unsigned, SmartScreen warns once.

**Offering it:** copy the installer and the AppImage into `backup-client/` in Mnemerynne's data directory
(`MNEMERYNNE_DATA_DIR`; Access → Settings → Desktop backup shows the exact folder). Preferences → Backups then
offers them, and running apps see the new version (`GET /api/sync/client`) within a day.

Python people can skip the bundle: `uv tool install '.[tray]'` from `clients/desktop-sync`.
