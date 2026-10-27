"""Media: a Kodi-style media centre over the share's Media library.

Off until an admin turns it on (Settings → Media, meta 'media_enabled'). Turning it on makes the
folder Media on the share with Music, Movies and Pictures inside (or adopts a Media folder that is
already there); the worker then catalogs what people put in them (catalog.py), and the Media page
plays it, straight from the share when the browser can, through ffmpeg when it can't
(transcode.py). Live streams pulled by URL and internet radio (live.py) sit beside the library.

Who may use Media at all is a group (Settings → Media; nobody until one is set). Within it, who sees
what follows the share's own permissions on the Media folders, like any share library. Off, the page and its API answer as if they didn't exist;
the folders, the files and the catalog are kept for when it's back.
"""

from __future__ import annotations

import base64
import json
import logging
import sqlite3
from pathlib import Path

from mnemerynne import apis, group_refs, permissions, share
from mnemerynne.config import Settings

log = logging.getLogger("mnemerynne.media")

ENABLED_KEY = "media_enabled"
SETTINGS_KEY = "media_settings"
LIBRARY_KEY = "media_library_id"
LIBRARY_NAME = "Media"
# Section -> its folder directly under Media.
SECTIONS = {"music": "Music", "movies": "Movies", "pictures": "Pictures"}

AUDIO_EXT = {".mp3", ".flac", ".m4a", ".aac", ".ogg", ".oga", ".opus", ".wav", ".wma", ".alac", ".aiff", ".aif",
             ".ape", ".wv", ".mka"}
VIDEO_EXT = {".mp4", ".m4v", ".mkv", ".webm", ".avi", ".mov", ".wmv", ".mpg", ".mpeg", ".ts", ".m2ts", ".flv",
             ".ogv", ".3gp"}
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".tif", ".tiff", ".heic", ".heif", ".avif"}
# What each section catalogs: artwork beside a film or an album isn't a picture of its own.
SECTION_TYPES = {"music": {"audio"}, "movies": {"video"}, "pictures": {"image", "video"}}

HWACCELS = ("auto", "vaapi", "nvenc", "software")
DEFAULT_RADIO_DIRECTORY = "https://sadgirlsclub.wtf/rr/healthz"
# folder: the top-level folder on the share that is the Media library (Settings → Media → Media folder).
# media_group: who may use Media at all; screens_group: who may pair screens and cast to them (group_refs
# values, AD or Entra groups). Empty: nobody (may_use, screens.may_use).
DEFAULTS = {"folder": LIBRARY_NAME, "hwaccel": "auto", "max_transcodes": 2, "online_metadata": False,
            "radio_directory_url": DEFAULT_RADIO_DIRECTORY, "media_group": "", "screens_group": ""}
GROUP_SETTINGS = ("media_group", "screens_group")


class MediaError(ValueError):
    """A refusal; the message is safe to show."""
    status = 422


def media_type(name: str) -> str | None:
    ext = Path(name).suffix.lower()
    if ext in AUDIO_EXT:
        return "audio"
    if ext in VIDEO_EXT:
        return "video"
    if ext in IMAGE_EXT:
        return "image"
    return None


# ------------------------------------------------------------------ switch
def enabled(conn: sqlite3.Connection) -> bool:
    """Whether this organisation uses Media (off unless an admin turned it on)."""
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (ENABLED_KEY,)).fetchone()
    return bool(row and row["value"] == "1")


def set_enabled(conn: sqlite3.Connection, on: bool) -> None:
    conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)", (ENABLED_KEY, "1" if on else "0"))


def may_use(conn: sqlite3.Connection, user, directory) -> bool:
    """In the Media group (Settings → Media → Who may use Media)? No group set: nobody. Checked live, so
    leaving the group closes Media (and that person's screens) at once."""
    return user is not None and group_refs.in_any(conn, user, directory, load_settings(conn)["media_group"])


# ---------------------------------------------------------------- settings
def load_settings(conn: sqlite3.Connection) -> dict:
    """The admin's choices (meta 'media_settings'), defaults filled in. 'tmdb_key_enc' stays encrypted."""
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (SETTINGS_KEY,)).fetchone()
    cfg = dict(DEFAULTS)
    if row:
        try:
            stored = json.loads(row["value"])
            cfg.update({k: v for k, v in stored.items() if k in DEFAULTS or k == "tmdb_key_enc"})
        except (ValueError, AttributeError):
            log.warning("The media settings are unreadable; using the defaults.")
    return cfg


def tmdb_key(settings: Settings, cfg: dict) -> str:
    enc = cfg.get("tmdb_key_enc")
    return (apis.decrypt_secret(settings, base64.b64decode(enc)) or "") if enc else ""


def save_settings(conn: sqlite3.Connection, settings: Settings, changes: dict, key: str | None = None) -> dict:
    """changes: any of DEFAULTS' keys (others ignored). key: None keeps the TMDB key, '' removes it."""
    cfg = load_settings(conn)
    for name, value in changes.items():
        if value is None or name not in DEFAULTS:
            continue
        if name == "hwaccel" and value not in HWACCELS:
            raise MediaError(f"Transcoding uses one of {', '.join(HWACCELS)}.")
        if name == "max_transcodes" and not 0 <= int(value) <= 8:
            raise MediaError("At most 8 transcodes at once (0: never transcode).")
        if name == "radio_directory_url" and value and not str(value).startswith(("http://", "https://")):
            raise MediaError("The station list is an http:// or https:// address.")
        if name == "folder":
            value = check_folder(value)
        cfg[name] = value
    if key is not None:
        if key.strip():
            cfg["tmdb_key_enc"] = base64.b64encode(apis.encrypt_secret(settings, key.strip())).decode()
        else:
            cfg.pop("tmdb_key_enc", None)
    conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)", (SETTINGS_KEY, json.dumps(cfg)))
    return cfg


# ----------------------------------------------------------------- library
def check_folder(name) -> str:
    """A name usable as the Media folder: one folder at the top of the share, not the inboxes' folder."""
    name = str(name or "").strip()
    if not name or len(name) > 120:
        raise MediaError("Name the Media folder (up to 120 characters).")
    if "/" in name or "\\" in name:
        raise MediaError("The Media folder is a folder at the top of the share: a name, not a path.")
    try:
        share.check_name(name)
    except ValueError as exc:
        raise MediaError(str(exc)) from None
    rt = share.current()
    if rt is not None and name.lower() == rt.settings.users_folder.lower():
        raise MediaError(f"“{name}” is kept for inboxes.")
    return name


def _share_library(conn: sqlite3.Connection, name: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM collections WHERE kind = 'library' AND share_path IS NOT NULL "
                        "AND lower(share_path) = lower(?)", (name,)).fetchone()


def library(conn: sqlite3.Connection) -> sqlite3.Row | None:
    """The Media library: the one Settings set up (it keeps being it when renamed in Windows), else the
    share library named like the Media folder setting."""
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (LIBRARY_KEY,)).fetchone()
    if row:
        lib = conn.execute("SELECT * FROM collections WHERE id = ? AND kind = 'library' AND share_path IS NOT NULL",
                           (int(row["value"]),)).fetchone()
        if lib is not None:
            return lib
    return _share_library(conn, load_settings(conn)["folder"])


def set_folder(conn: sqlite3.Connection, settings: Settings, name: str) -> bool:
    """Use another top-level folder of the share as the Media library (in the caller's transaction).
    The catalog of the old one is kept, for switching back. True if it changed."""
    name = check_folder(name)
    current = library(conn)
    if current is not None and current["share_path"].lower() == name.lower():
        return False
    save_settings(conn, settings, {"folder": name})
    found = _share_library(conn, name)
    if found is not None:
        conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)", (LIBRARY_KEY, str(found["id"])))
    else:
        conn.execute("DELETE FROM meta WHERE key = ?", (LIBRARY_KEY,))   # made by ensure_library
    return True


def ensure_library(conn: sqlite3.Connection) -> sqlite3.Row:
    """The Media folder and its three folders on the share, made where missing; runs in the caller's
    transaction. The folders' rows come with the next share scan, like anything else made through SMB."""
    rt = share.current()
    if rt is None:
        raise MediaError("Media lives on the file share, and this server has none (MNEMERYNNE_SHARE_ROOT).")
    if not share.mounted(rt):
        raise MediaError(f"The share ({rt.root}) isn't available right now; try again later.")
    lib = library(conn)
    if lib is None:
        name = check_folder(load_settings(conn)["folder"])
        if permissions.get_library(conn, name):
            raise MediaError(f"There is already a library called “{name}” that isn't on the share; "
                             "rename it or choose another Media folder.")
        base = rt.root / name
        share.make_dir(base)
        library_id = permissions.create_library(conn, name)
        conn.execute("UPDATE collections SET share_path = ?, share_inode = ? WHERE id = ?",
                     (name, base.stat().st_ino, library_id))
        lib = conn.execute("SELECT * FROM collections WHERE id = ?", (library_id,)).fetchone()
    base = rt.root / Path(lib["share_path"])
    for folder in SECTIONS.values():
        existing = next((p for p in base.iterdir() if p.is_dir() and p.name.lower() == folder.lower()), None) \
            if base.is_dir() else None
        if existing is None:
            share.make_dir(base / folder)
    conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)", (LIBRARY_KEY, str(lib["id"])))
    return lib


def section_folders(conn: sqlite3.Connection, library_id: int) -> dict[int, str]:
    """Top-level folder id -> section, for the Music, Movies and Pictures folders (any case)."""
    by_name = {v.lower(): k for k, v in SECTIONS.items()}
    return {r["id"]: by_name[r["name"].lower()] for r in conn.execute(
        "SELECT id, name FROM folders WHERE collection_id = ? AND parent_id IS NULL AND deleted_at IS NULL",
        (library_id,)) if r["name"].lower() in by_name}


def folder_sections(conn: sqlite3.Connection, library_id: int) -> dict[int, str]:
    """Every live folder in the library under a section folder -> that section."""
    rows = conn.execute("SELECT id, parent_id FROM folders WHERE collection_id = ? AND deleted_at IS NULL",
                        (library_id,)).fetchall()
    parents = {r["id"]: r["parent_id"] for r in rows}
    tops = section_folders(conn, library_id)
    out: dict[int, str] = {}
    for fid in parents:
        cur, seen = fid, 0
        while cur is not None and cur not in tops and seen < 10_000:
            cur, seen = parents.get(cur), seen + 1
        if cur in tops:
            out[fid] = tops[cur]
    return out
