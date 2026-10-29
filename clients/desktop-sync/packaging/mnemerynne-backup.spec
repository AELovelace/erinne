# PyInstaller: `pyinstaller packaging/mnemerynne-backup.spec` from clients/desktop-sync (see packaging/README.md).
# Two programs in one folder: the tray app (no console window on Windows) and the command line.
import sys
from pathlib import Path

here = Path(SPECPATH)
icon = str(here / "build" / ("icon.ico" if sys.platform == "win32" else "icon.png"))
hidden = ["pystray._win32", "pystray._xorg", "pystray._appindicator", "pystray._gtk",
          "keyring.backends.Windows", "keyring.backends.SecretService", "keyring.backends.kwallet",
          "watchdog.observers.read_directory_changes", "watchdog.observers.inotify"]

tray = Analysis([str(here / "entry_tray.py")], pathex=[str(here.parent)], hiddenimports=hidden)
cli = Analysis([str(here / "entry_cli.py")], pathex=[str(here.parent)], hiddenimports=hidden)
MERGE((tray, "mnemerynne-backup-tray", "mnemerynne-backup-tray"), (cli, "mnemerynne-backup", "mnemerynne-backup"))

tray_exe = EXE(PYZ(tray.pure), tray.scripts, [], exclude_binaries=True, name="mnemerynne-backup-tray",
               console=False, icon=icon)
cli_exe = EXE(PYZ(cli.pure), cli.scripts, [], exclude_binaries=True, name="mnemerynne-backup", console=True, icon=icon)
COLLECT(tray_exe, tray.binaries, tray.datas, cli_exe, cli.binaries, cli.datas, name="mnemerynne-backup")
