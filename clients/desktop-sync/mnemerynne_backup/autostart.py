"""Starting the app when the person signs in to this computer.

Windows: a value under HKCU\\...\\Run (per user, no admin rights). Linux with a desktop: an XDG autostart entry
for the tray app. Linux without one: a systemd user service running `mnemerynne-backup run`.
"""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

from mnemerynne_backup.config import APP, SLUG

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def _tray_command() -> list[str]:
    if getattr(sys, "frozen", False):          # the packaged .exe or AppImage
        return [os.environ.get("APPIMAGE") or sys.executable]
    exe = shutil.which("mnemerynne-backup-tray")
    if exe:
        return [exe]
    python = sys.executable
    if sys.platform == "win32" and python.lower().endswith("python.exe"):
        python = python[:-10] + "pythonw.exe"   # no console window
    return [python, "-m", "mnemerynne_backup.tray"]


def _service_command() -> list[str]:
    exe = shutil.which("mnemerynne-backup")
    return [exe, "run"] if exe else [sys.executable, "-m", "mnemerynne_backup.cli", "run"]


def _desktop_file() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "autostart" / f"{SLUG}.desktop"


def _unit_file() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "systemd" / "user" / f"{SLUG}.service"


def desktop_entry() -> str:
    return (f"[Desktop Entry]\nType=Application\nName={APP}\nComment=Backs folders up to Mnemerynne\n"
            f"Exec={shlex.join(_tray_command())}\nIcon={SLUG}\nTerminal=false\nX-GNOME-Autostart-enabled=true\n")


def unit() -> str:
    return (f"[Unit]\nDescription={APP}\nAfter=network-online.target\n\n[Service]\n"
            f"ExecStart={shlex.join(_service_command())}\nRestart=on-failure\nRestartSec=60\n\n"
            f"[Install]\nWantedBy=default.target\n")


def enabled() -> bool:
    if sys.platform == "win32":
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
                winreg.QueryValueEx(key, APP)
                return True
        except OSError:
            return False
    return _desktop_file().exists() or _unit_file().exists()


def set_enabled(on: bool, *, service: bool = False) -> str:
    """Turn it on or off; says what was done."""
    if sys.platform == "win32":
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            if on:
                winreg.SetValueEx(key, APP, 0, winreg.REG_SZ, subprocess.list2cmdline(_tray_command()))
                return f"{APP} starts when you sign in to Windows."
            try:
                winreg.DeleteValue(key, APP)
            except OSError:
                pass
            return f"{APP} no longer starts by itself."
    if service:
        path = _unit_file()
        if on:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(unit(), encoding="utf-8")
            subprocess.run(["systemctl", "--user", "daemon-reload"], check=False)
            subprocess.run(["systemctl", "--user", "enable", "--now", f"{SLUG}.service"], check=False)
            return (f"Started the {SLUG} user service. To keep it running while you're signed out: "
                    f"sudo loginctl enable-linger {os.environ.get('USER', '$USER')}")
        subprocess.run(["systemctl", "--user", "disable", "--now", f"{SLUG}.service"], check=False)
        path.unlink(missing_ok=True)
        return f"Stopped and removed the {SLUG} user service."
    path = _desktop_file()
    if on:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(desktop_entry(), encoding="utf-8")
        return f"{APP} starts when you sign in ({path})."
    path.unlink(missing_ok=True)
    return f"{APP} no longer starts by itself."
