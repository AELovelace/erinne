"""The app's sign-in: a sync token pasted from Mnemerynne, or an OAuth connection made in the browser.

Kept in the system's keyring (Windows Credential Manager, Secret Service on Linux). Where there is no keyring
(a headless Linux box) it goes in a file only this user can read.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from mnemerynne_backup.config import SLUG, config_dir

log = logging.getLogger(__name__)


def _file() -> Path:
    return config_dir() / "credentials.json"


def _keyring():
    if os.environ.get("MNEMERYNNE_BACKUP_NO_KEYRING"):
        return None
    try:
        import keyring
        from keyring.backends import fail
    except ImportError:
        return None
    return None if isinstance(keyring.get_keyring(), fail.Keyring) else keyring


def load(server: str) -> dict | None:
    """{"kind": "token", "token": …} or {"kind": "oauth", "client_id", "access", "refresh", "expires"}."""
    kr = _keyring()
    raw = None
    if kr is not None:
        try:
            raw = kr.get_password(SLUG, server)
        except Exception as exc:     # a locked or broken keyring: fall back to the file
            log.warning("Couldn't read the keyring: %s", exc)
    if raw is None and _file().exists():
        raw = json.loads(_file().read_text(encoding="utf-8")).get(server)
        raw = json.dumps(raw) if raw else None
    return json.loads(raw) if raw else None


def save(server: str, creds: dict) -> None:
    kr = _keyring()
    if kr is not None:
        try:
            kr.set_password(SLUG, server, json.dumps(creds))
            return
        except Exception as exc:
            log.warning("Couldn't write to the keyring, using a private file instead: %s", exc)
    path = _file()
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    data[server] = creds
    fd = os.open(path.with_suffix(".tmp"), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(data, fh)
    os.replace(path.with_suffix(".tmp"), path)


def forget(server: str) -> None:
    kr = _keyring()
    if kr is not None:
        try:
            kr.delete_password(SLUG, server)
        except Exception:
            pass
    path = _file()
    if path.exists():
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.pop(server, None) is not None:
            path.write_text(json.dumps(data), encoding="utf-8")
