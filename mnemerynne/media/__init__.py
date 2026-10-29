"""Media: a Kodi-style media centre over the share's Media library.

Off until an admin turns it on (Settings → Entertainment → Media, meta 'media_enabled'). Turning it on makes the
folder Media on the share with Music, Movies, TV and Pictures inside (or adopts a Media folder that is
already there); the worker then catalogs what people put in them (catalog.py), and the Media page
plays it, straight from the share when the browser can, through ffmpeg when it can't
(transcode.py). Live streams pulled by URL and internet radio (live.py) sit beside the library.

Who may use Media at all is a group (Settings → Entertainment → Media; nobody until one is set). Within it, who sees
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
# Section -> its folder directly under Media. In TV, each folder directly inside is one show.
SECTIONS = {"music": "Music", "movies": "Movies", "tv": "TV", "pictures": "Pictures"}
TV_SECTION = "tv"
# The section folders already made in the Media library (ensure_section_folders), so one added in a later
# version (TV) appears on its own, and one an admin removed on purpose isn't made again.
SECTIONS_MADE_KEY = "media_sections_made"

AUDIO_EXT = {".mp3", ".flac", ".m4a", ".aac", ".ogg", ".oga", ".opus", ".wav", ".wma", ".alac", ".aiff", ".aif",
             ".ape", ".wv", ".mka"}
VIDEO_EXT = {".mp4", ".m4v", ".mkv", ".webm", ".avi", ".mov", ".wmv", ".mpg", ".mpeg", ".ts", ".m2ts", ".flv",
             ".ogv", ".3gp"}
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".tif", ".tiff", ".heic", ".heif", ".avif"}
DOCUMENT_EXT = {".pdf"}
# What each section catalogs: artwork beside a film or an album isn't a picture of its own. The Signage
# folder (signage.py) is its own share library: every folder in it is the 'signage' section, which takes
# anything a screen can show, PDFs included (one slide a page).
SECTION_TYPES = {"music": {"audio"}, "movies": {"video"}, "tv": {"video"}, "pictures": {"image", "video"},
                 "signage": {"image", "video", "document"}}
SIGNAGE_SECTION = "signage"
SIGNAGE_LIBRARY_KEY = "signage_library_id"

HWACCELS = ("auto", "vaapi", "nvenc", "software")
DEFAULT_RADIO_DIRECTORY = "https://sadgirlsclub.wtf/rr/healthz"
# folder: the top-level folder on the share that is the Media library (Settings → Entertainment → Media → Media folder).
# media_group: who may use Media at all; screens_group: who may pair screens and cast to them (group_refs
# values, AD or Entra groups). Empty: nobody (may_use, screens.may_use).
# signage_folder: the top-level share folder whose pictures, clips and PDFs signage playlists draw on;
# signage_effects: the CRT effects a newly paired screen gets (signage.Effects; {} is off).
DEFAULTS = {"folder": LIBRARY_NAME, "hwaccel": "auto", "max_transcodes": 2, "online_metadata": False,
            "radio_directory_url": DEFAULT_RADIO_DIRECTORY, "media_group": "", "screens_group": "",
            "signage_folder": "Signage", "signage_effects": {}}
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
    if ext in DOCUMENT_EXT:
        return "document"
    return None


# ------------------------------------------------------------------ switch
def enabled(conn: sqlite3.Connection) -> bool:
    """Whether this organisation uses Media (off unless an admin turned it on)."""
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (ENABLED_KEY,)).fetchone()
    return bool(row and row["value"] == "1")


def set_enabled(conn: sqlite3.Connection, on: bool) -> None:
    conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)", (ENABLED_KEY, "1" if on else "0"))


def may_use(conn: sqlite3.Connection, user, directory) -> bool:
    """In the Media group (Settings → Entertainment → Media → Who may use Media)? No group set: nobody. Checked live, so
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
        if name in ("folder", "signage_folder"):
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


def _meta_library(conn: sqlite3.Connection, key: str, setting: str) -> sqlite3.Row | None:
    """The share library Settings set up under `key` (it keeps being it when renamed in Windows), else the
    share library named like the folder setting."""
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    if row:
        lib = conn.execute("SELECT * FROM collections WHERE id = ? AND kind = 'library' AND share_path IS NOT NULL",
                           (int(row["value"]),)).fetchone()
        if lib is not None:
            return lib
    return _share_library(conn, load_settings(conn)[setting])


def library(conn: sqlite3.Connection) -> sqlite3.Row | None:
    """The Media library (Settings → Entertainment → Media → Media folder)."""
    return _meta_library(conn, LIBRARY_KEY, "folder")


def signage_library(conn: sqlite3.Connection) -> sqlite3.Row | None:
    """The Signage folder's library (Access → Signage → Settings), where signage assets live."""
    return _meta_library(conn, SIGNAGE_LIBRARY_KEY, "signage_folder")


def _set_folder(conn: sqlite3.Connection, settings: Settings, name: str, key: str, setting: str) -> bool:
    name = check_folder(name)
    current = _meta_library(conn, key, setting)
    if current is not None and current["share_path"].lower() == name.lower():
        return False
    save_settings(conn, settings, {setting: name})
    found = _share_library(conn, name)
    if found is not None:
        conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)", (key, str(found["id"])))
    else:
        conn.execute("DELETE FROM meta WHERE key = ?", (key,))   # made by the ensure_* call
    return True


def set_folder(conn: sqlite3.Connection, settings: Settings, name: str) -> bool:
    """Use another top-level folder of the share as the Media library (in the caller's transaction).
    The catalog of the old one is kept, for switching back. True if it changed."""
    return _set_folder(conn, settings, name, LIBRARY_KEY, "folder")


def set_signage_folder(conn: sqlite3.Connection, settings: Settings, name: str) -> bool:
    """Use another top-level folder of the share for signage assets. True if it changed."""
    if str(name).strip().lower() == str(load_settings(conn)["folder"]).lower():
        raise MediaError("The Signage folder can't be the Media folder: pick another folder at the top of the share.")
    return _set_folder(conn, settings, name, SIGNAGE_LIBRARY_KEY, "signage_folder")


def _ensure_share_library(conn: sqlite3.Connection, key: str, setting: str, subfolders: tuple[str, ...],
                          what: str) -> sqlite3.Row:
    rt = share.current()
    if rt is None:
        raise MediaError(f"{what} lives on the file share, and this server has none (MNEMERYNNE_SHARE_ROOT).")
    if not share.mounted(rt):
        raise MediaError(f"The share ({rt.root}) isn't available right now; try again later.")
    lib = _meta_library(conn, key, setting)
    if lib is None:
        name = check_folder(load_settings(conn)[setting])
        if permissions.get_library(conn, name):
            raise MediaError(f"There is already a library called “{name}” that isn't on the share; "
                             f"rename it or choose another {what} folder.")
        base = rt.root / name
        share.make_dir(base)
        library_id = permissions.create_library(conn, name)
        conn.execute("UPDATE collections SET share_path = ?, share_inode = ? WHERE id = ?",
                     (name, base.stat().st_ino, library_id))
        lib = conn.execute("SELECT * FROM collections WHERE id = ?", (library_id,)).fetchone()
    _make_subfolders(rt.root / Path(lib["share_path"]), subfolders)
    conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)", (key, str(lib["id"])))
    return lib


def _make_subfolders(base: Path, subfolders) -> None:
    """Each of subfolders inside base, made on the share unless it's there already (in any case)."""
    for folder in subfolders:
        existing = next((p for p in base.iterdir() if p.is_dir() and p.name.lower() == folder.lower()), None) \
            if base.is_dir() else None
        if existing is None:
            share.make_dir(base / folder)


def ensure_library(conn: sqlite3.Connection) -> sqlite3.Row:
    """The Media folder and its section folders on the share, made where missing; runs in the caller's
    transaction. The folders' rows come with the next share scan, like anything else made through SMB."""
    lib = _ensure_share_library(conn, LIBRARY_KEY, "folder", tuple(SECTIONS.values()), "Media")
    conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)",
                 (SECTIONS_MADE_KEY, json.dumps(sorted(SECTIONS))))   # every section folder is there now
    return lib


def ensure_section_folders(conn: sqlite3.Connection) -> list[str]:
    """Section folders added since Media was turned on (TV), made once in the Media library; the catalog
    pass calls this. Does nothing while Media is off or the share is away. Returns the folders made."""
    rt = share.current()
    if rt is None or not enabled(conn):
        return []
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (SECTIONS_MADE_KEY,)).fetchone()
    try:
        made = set(json.loads(row["value"])) if row else {"music", "movies", "pictures"}   # before TV: those three
    except ValueError:
        made = set()
    missing = [s for s in SECTIONS if s not in made]
    lib = library(conn) if missing else None
    if lib is None or not share.mounted(rt):
        return []
    folders = [SECTIONS[s] for s in missing]
    _make_subfolders(rt.root / Path(lib["share_path"]), folders)   # disk only; the next scan adds the rows
    conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)",
                 (SECTIONS_MADE_KEY, json.dumps(sorted(made | set(missing)))))
    log.info("Media: made %s in the Media folder.", ", ".join(folders))
    return folders


def ensure_signage_library(conn: sqlite3.Connection) -> sqlite3.Row:
    """The Signage folder on the share, made where missing (in the caller's transaction)."""
    return _ensure_share_library(conn, SIGNAGE_LIBRARY_KEY, "signage_folder", (), "Signage")


def catalog_roots(conn: sqlite3.Connection) -> list[tuple[sqlite3.Row, dict[int, str], str | None]]:
    """What the catalog reads: (library, folder id -> section, the section of files at the library's
    root) for the Media library (Music, Movies, TV, Pictures) and the Signage library (all of it)."""
    out = []
    lib = library(conn)
    if lib is not None:
        out.append((lib, folder_sections(conn, lib["id"]), None))
    sg = signage_library(conn)
    if sg is not None and (lib is None or sg["id"] != lib["id"]):
        folders = {r["id"]: SIGNAGE_SECTION for r in conn.execute(
            "SELECT id FROM folders WHERE collection_id = ? AND deleted_at IS NULL", (sg["id"],))}
        out.append((sg, folders, SIGNAGE_SECTION))
    return out


def section_folders(conn: sqlite3.Connection, library_id: int) -> dict[int, str]:
    """Top-level folder id -> section, for the Music, Movies, TV and Pictures folders (any case)."""
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


def tv_root(conn: sqlite3.Connection, library_id: int) -> int | None:
    """The TV folder's id in the Media library, once the share scan has seen it."""
    return next((fid for fid, s in section_folders(conn, library_id).items() if s == TV_SECTION), None)


def tv_show_folders(conn: sqlite3.Connection, library_id: int) -> dict[int, int]:
    """Every live folder under TV -> its show: the folder directly inside TV it sits in (a show folder maps
    to itself). The TV folder itself is no show, so files right in it belong to none."""
    root = tv_root(conn, library_id)
    if root is None:
        return {}
    parents = {r["id"]: r["parent_id"] for r in conn.execute(
        "SELECT id, parent_id FROM folders WHERE collection_id = ? AND deleted_at IS NULL", (library_id,))}
    out: dict[int, int] = {}
    for fid in parents:
        cur, seen = fid, 0
        while cur is not None and parents.get(cur) != root and seen < 10_000:   # climb to the child of TV
            cur, seen = parents.get(cur), seen + 1
        if cur is not None and parents.get(cur) == root:
            out[fid] = cur
    return out
