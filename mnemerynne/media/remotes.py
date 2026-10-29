"""Remotes: a small touch panel (the ESP32 tablet, Mnemerynne-tab) that controls its owner's screens.

Pairing: like a TV with no keyboard (screens.Pairings, kind "remote"). The panel asks for a code and shows
it with a QR code; the owner types or scans it on their phone (Media → Screens → Pair a TV or remote) and
names it; the panel, asking every few seconds with its pairing secret, then gets its remote key, which it
sends from then on as `Authorization: Bearer …`. Only the key's hash is stored.

A remote key opens its owner's screens and nothing else: what they are playing, the commands that control
them (play/pause, skip, volume, mute, night filter, next, previous, stop, and the arrow and Enter buttons
that move around the TV page) and playing a live stream or a radio station. It can't browse the library, reach anyone else's screens or override someone using a TV.
It doesn't expire with sign-in sessions; its owner removes it under Media → Screens, or the panel forgets
itself. Like a screen key, it stops working at once when its owner leaves the screens group.
"""

from __future__ import annotations

import hashlib
import secrets
import sqlite3

from mnemerynne.media import screens

MAX_PER_USER = 10
ACTIONS = ("toggle", "skip", "volume", "mute", "night", "next", "prev", "stop", "press", "play_source", "play_station")
BUTTONS = ("up", "down", "left", "right", "enter")   # press: the TV page replays them as its own arrow keys


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create(conn: sqlite3.Connection, user_id: int, name: str) -> tuple[int, str]:
    """(remote id, key) in the caller's transaction. A remote of the same name is re-paired: it gets a new
    key and the old one stops working."""
    name = screens.clean_name(name)
    token = secrets.token_urlsafe(32)
    same = conn.execute("SELECT id FROM media_remotes WHERE user_id = ? AND lower(name) = lower(?)",
                        (user_id, name)).fetchone()
    if same is not None:
        conn.execute("UPDATE media_remotes SET name = ?, token_hash = ?, created_at = datetime('now'), "
                     "last_seen_at = NULL WHERE id = ?", (name, _hash(token), same["id"]))
        return same["id"], token
    if conn.execute("SELECT count(*) FROM media_remotes WHERE user_id = ?", (user_id,)).fetchone()[0] >= MAX_PER_USER:
        raise screens.ScreenError(f"At most {MAX_PER_USER} remotes; remove one first.")
    remote_id = conn.execute("INSERT INTO media_remotes(user_id, name, token_hash) VALUES (?, ?, ?)",
                             (user_id, name, _hash(token))).lastrowid
    return remote_id, token


def by_key(conn: sqlite3.Connection, token: str | None) -> sqlite3.Row | None:
    """The remote a key belongs to, with its owner (active accounts only)."""
    if not token or len(token) > 200:
        return None
    return conn.execute("SELECT r.*, u.username FROM media_remotes r JOIN users u ON u.id = r.user_id "
                        "WHERE r.token_hash = ? AND u.active = 1", (_hash(token),)).fetchone()


def get(conn: sqlite3.Connection, remote_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM media_remotes WHERE id = ?", (remote_id,)).fetchone()


def of_user(conn: sqlite3.Connection, user_id: int) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM media_remotes WHERE user_id = ? ORDER BY name COLLATE NOCASE",
                        (user_id,)).fetchall()


def seen(conn: sqlite3.Connection, remote_id: int) -> None:
    """At most once a minute: the panel asks every few seconds."""
    conn.execute("UPDATE media_remotes SET last_seen_at = datetime('now') WHERE id = ? "
                 "AND (last_seen_at IS NULL OR last_seen_at < datetime('now', '-60 seconds'))", (remote_id,))


def remove(conn: sqlite3.Connection, remote_id: int, user_id: int) -> bool:
    return conn.execute("DELETE FROM media_remotes WHERE id = ? AND user_id = ?", (remote_id, user_id)).rowcount > 0
