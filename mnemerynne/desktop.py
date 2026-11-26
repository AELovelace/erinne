"""The desktop app (clients/desktop-app): who may use it, the builds it updates from, and its sign-in tickets.

The app is a Windows program that shows this site in tabs and puts picked libraries in File Explorer as a drive
(drive.py). It's hand-installed and privileged, so an admin names one group on Access → Settings → Desktop app
(meta 'desktop_app_group', a group_refs reference); no group means nobody may download it or sign it in.

It signs in once over OAuth (scope 'desktop', a local redirect only). Its tabs are ordinary web pages with the
ordinary session cookie, which lasts a day; when a tab lands on /login, the app swaps its token for a ticket
(single use, a minute) and opens /desktop/signin?t=… so the person isn't asked for their password again.
"""

from __future__ import annotations

import hashlib
import re
import secrets
import sqlite3
from pathlib import Path

from mnemerynne import group_refs
from mnemerynne.config import Settings

KEY = "desktop_app_group"            # meta key: the group allowed to use the app ('' = off)
TICKET_SECONDS = 60                  # a ticket is used straight away by the app that asked for it
BUILD_DIR = "desktop-app"            # under data_dir: the installers Preferences offers and the app updates from
_BUILD = re.compile(r"^Mnemerynne-Desktop-Setup-(\d+(?:\.\d+){1,3})\.exe$")   # the version is in the name


# ------------------------------------------------------------------- the gate
def group(conn: sqlite3.Connection) -> str:
    """The group whose members may use the desktop app (group_refs text); '' = off."""
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (KEY,)).fetchone()
    return (row["value"] if row else "") or ""


def set_group(conn: sqlite3.Connection, value: str) -> str:
    """Store a resolved group reference (group_refs.resolve), or '' to switch the app off for everyone."""
    value = (value or "").strip()
    if value:
        conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)", (KEY, value))
    else:
        conn.execute("DELETE FROM meta WHERE key = ?", (KEY,))
    return value


def allowed(conn: sqlite3.Connection, user: sqlite3.Row | None, directory) -> bool:
    """A group is set, and this person is active and in it (through their linked domain or Microsoft account)."""
    if user is None or not user["active"]:
        return False
    value = group(conn)
    return bool(value) and group_refs.in_any(conn, user, directory, value)


# ------------------------------------------------------------------ the builds
def _version(name: str) -> tuple[int, ...] | None:
    m = _BUILD.match(name)
    return tuple(int(x) for x in m.group(1).split(".")) if m else None


def latest_build(settings: Settings) -> dict | None:
    """The newest installer in <data_dir>/desktop-app: {"version", "name", "size", "sha256"}, or None.
    The SHA-256 lets the app check what it downloaded before running it."""
    folder = settings.data_dir / BUILD_DIR
    best: tuple[tuple[int, ...], Path] | None = None
    for f in (folder.iterdir() if folder.is_dir() else []):
        v = _version(f.name)
        if v is not None and f.is_file() and (best is None or v > best[0]):
            best = (v, f)
    if best is None:
        return None
    version, path = best
    return {"version": ".".join(map(str, version)), "name": path.name, "size": path.stat().st_size,
            "sha256": _sha256(path)}


_SHA_CACHE: dict[tuple[str, int, int], str] = {}   # (path, size, mtime_ns) -> digest: hashed once per build


def _sha256(path: Path) -> str:
    st = path.stat()
    key = (str(path), st.st_size, st.st_mtime_ns)
    if key not in _SHA_CACHE:
        h = hashlib.sha256()
        with open(path, "rb") as fh:
            for block in iter(lambda: fh.read(1024 * 1024), b""):
                h.update(block)
        _SHA_CACHE[key] = h.hexdigest()
    return _SHA_CACHE[key]


def build_file(settings: Settings, name: str) -> Path | None:
    """One offered installer by its exact name (never a path), or None."""
    if _version(name) is None:
        return None
    path = settings.data_dir / BUILD_DIR / name
    return path if path.is_file() else None


# ---------------------------------------------------------------- the tickets
def _hash(ticket: str) -> str:
    return hashlib.sha256(ticket.encode()).hexdigest()


def issue_ticket(conn: sqlite3.Connection, user_id: int, grant_id: int | None) -> str:
    """A one-minute, single-use ticket that signs this person's tab in (only its hash is kept)."""
    ticket = "mdt_" + secrets.token_urlsafe(32)
    conn.execute("DELETE FROM desktop_tickets WHERE expires_at < datetime('now', '-1 hour')")   # sweep old ones
    conn.execute("INSERT INTO desktop_tickets(ticket_hash, user_id, grant_id, expires_at) "
                 "VALUES (?, ?, ?, datetime('now', ?))", (_hash(ticket), user_id, grant_id, f"+{TICKET_SECONDS} seconds"))
    return ticket


def use_ticket(conn: sqlite3.Connection, ticket: str) -> int | None:
    """The user a ticket signs in, marking it used; None if it's unknown, used, expired or its connection ended."""
    if not ticket or not ticket.startswith("mdt_"):
        return None
    row = conn.execute(
        "SELECT t.ticket_hash, t.user_id FROM desktop_tickets t LEFT JOIN oauth_grants g ON g.id = t.grant_id "
        "JOIN users u ON u.id = t.user_id WHERE t.ticket_hash = ? AND t.used_at IS NULL "
        "AND t.expires_at > datetime('now') AND u.active = 1 AND (t.grant_id IS NULL OR g.revoked_at IS NULL)",
        (_hash(ticket),)).fetchone()
    if row is None:
        return None
    # Marked used in the same statement that checks it's still unused, so two tabs racing get one sign-in.
    done = conn.execute("UPDATE desktop_tickets SET used_at = datetime('now') WHERE ticket_hash = ? AND used_at IS NULL",
                        (row["ticket_hash"],)).rowcount
    return row["user_id"] if done else None
