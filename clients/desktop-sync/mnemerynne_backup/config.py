"""Where the app keeps its settings and state, and the settings themselves (config.json).

Windows: %APPDATA%\\Mnemerynne Backup (settings) and %LOCALAPPDATA%\\Mnemerynne Backup (state).
Linux: ~/.config/mnemerynne-backup and ~/.local/state/mnemerynne-backup.
MNEMERYNNE_BACKUP_HOME puts both in one folder (tests, a headless service with its own home).
"""

from __future__ import annotations

import json
import os
import platform
import socket
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

APP = "Mnemerynne Backup"
SLUG = "mnemerynne-backup"
# Offered as ticks in the settings window; nothing is excluded unless chosen.
COMMON_EXCLUDES = ("node_modules", ".git", "__pycache__", ".venv")


def config_dir() -> Path:
    if home := os.environ.get("MNEMERYNNE_BACKUP_HOME"):
        return Path(home)
    if sys.platform == "win32":
        return Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming") / APP
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / SLUG


def state_dir() -> Path:
    if home := os.environ.get("MNEMERYNNE_BACKUP_HOME"):
        return Path(home)
    if sys.platform == "win32":
        return Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / APP
    return Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state") / SLUG


def computer_name() -> str:
    """COMPUTERNAME on Windows, the host name elsewhere, without a domain."""
    name = os.environ.get("COMPUTERNAME") if sys.platform == "win32" else None
    return (name or platform.node() or socket.gethostname() or "computer").split(".")[0]


@dataclass
class Folder:
    path: str                       # the folder on this computer
    label: str                      # its name under Backups\<computer>\ on the NAS
    root_id: int | None = None      # the server's id for it (sync_roots)
    paused: bool = False
    excludes: list[str] = field(default_factory=list)   # glob patterns, matched on names and relative paths


@dataclass
class Config:
    server: str = ""                # Mnemerynne's address, where people sign in (MNEMERYNNE_PUBLIC_URL)
    api_url: str = ""               # where backups go (MNEMERYNNE_BACKUP_URL); the server says, at sign-in
    computer: str = field(default_factory=computer_name)
    folders: list[Folder] = field(default_factory=list)
    bandwidth_kbps: int = 0         # upload limit in KB/s; 0 = none
    pause_on_metered: bool = True
    settle_seconds: int = 30        # a file changed this recently waits for the next pass
    rescan_minutes: int = 60        # full rescan, besides file events
    check_hours: int = 6            # compare with the server's list (catches changes made on the NAS)

    @property
    def base_url(self) -> str:
        return (self.api_url or self.server).rstrip("/")

    def folder(self, key: str) -> Folder | None:
        """A folder by label or by its path on this computer."""
        for f in self.folders:
            if f.label.lower() == key.lower() or os.path.normcase(os.path.abspath(f.path)) == \
                    os.path.normcase(os.path.abspath(key)):
                return f
        return None

    @classmethod
    def load(cls, path: Path | None = None) -> "Config":
        path = path or config_dir() / "config.json"
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return cls()
        folders = [Folder(**f) for f in raw.pop("folders", [])]
        known = {k: v for k, v in raw.items() if k in cls.__dataclass_fields__}
        return cls(**known, folders=folders)

    def save(self, path: Path | None = None) -> None:
        path = path or config_dir() / "config.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")
        os.replace(tmp, path)
