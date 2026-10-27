"""Signage: what screens (TVs) show while nobody is using them, set by admins (Access → Signage).

A playlist is a list of slides: pictures and clips from a folder, one picture, one video, a PDF (a
slide a page), text, a web page, a YouTube video, or the announcements for everyone. Each slide can
have its own seconds, transition and fit, and can be limited to weekdays, hours and dates. Assets
come from two share libraries the catalog reads (media/catalog.py): the Signage folder, which takes
anything a screen can show, and the Media library's Pictures and Movies.

The schedule says what plays where and when: an entry targets every screen, a screen group or one
screen, names a playlist, runs on weekdays between two times (start = end: all day; end before start:
past midnight), optionally between two dates, either once the screen has been idle (no casting,
playing or remote presses for idle_minutes) or always (signage hours: casting still interrupts).
A screen's own entries beat its group's, which beat those for all screens; then the higher priority,
then the newer entry. The screen gets its programme, already ranked, and picks by its own clock, so
it keeps going while the server is briefly away.

Screens belong to groups, report what they show (their last state is kept, so "online" survives a
restart), and may wear CRT effects: a screen's own setting, the Settings default for new screens,
or a playlist's override, resolved per programme entry.

A screen gets its files through its own key (web/media.py, /media/screen/signage/...), only those in
playlists its programme names, whatever its owner may read: admins chose them.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import re
import sqlite3
from pathlib import Path
from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, field_validator

from mnemerynne import fs, media
from mnemerynne.config import Settings
from mnemerynne.ingest import markdown, store
from mnemerynne.media import artwork

log = logging.getLogger("mnemerynne.media")

MAX_SLIDES = 200
MAX_ENTRIES = 300               # schedule entries, server-wide
MAX_GROUPS = 100
MAX_ITEMS = 2000                # files from one playlist's folders
MAX_NOTICES = 20
MAX_PDF_PAGES = 200
IMAGE_BOX = (1920, 1080)        # pictures are sent no bigger than the screen
ONLINE_SECONDS = 60             # a screen heard from this recently is online, stream or not
COLOR = r"^#[0-9a-fA-F]{6}$"
TIME = r"^([01][0-9]|2[0-3]):[0-5][0-9]$"
DATE = r"^\d{4}-\d{2}-\d{2}$"
TRANSITIONS = ("fade", "cut", "slide", "zoom")
FITS = ("contain", "cover", "blur")
ORIENTATIONS = ("landscape", "portrait")
Seconds = Annotated[int | None, Field(None, ge=3, le=3600)]   # None: the playlist's own
_YOUTUBE_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")


class SignageError(ValueError):
    """A refusal; the message is safe to show."""
    status = 422


# ------------------------------------------------------------------ effects
class Effects(BaseModel):
    """CRT effects on a screen: an old monitor playing the signage (static/signage_crt.css)."""
    model_config = ConfigDict(extra="ignore")
    crt: bool = False
    scanlines: bool = True
    curvature: bool = True
    fringe: bool = True
    flicker: bool = True
    glow: bool = True
    power_on: bool = True
    intensity: float = Field(0.6, ge=0, le=1)


def clean_effects(raw) -> dict | None:
    """The effects as stored: None (follow the default), or a full, checked dict."""
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise SignageError("Effects are a set of switches.")
    from pydantic import ValidationError
    try:
        return Effects.model_validate(raw).model_dump()
    except ValidationError as exc:
        raise SignageError(f"Effects: {exc.errors()[0]['msg']}.") from None


def resolve_effects(default: dict | None, screen: dict | None, show: dict | None = None) -> dict:
    """What a screen wears for a playlist: the playlist's override, else the screen's own, else the
    Settings default for new screens, else off."""
    for chosen in (show, screen, default):
        if chosen:
            return Effects.model_validate(chosen).model_dump()
    return Effects().model_dump()


# ------------------------------------------------------------------- slides
class _Timing(BaseModel):
    """What every slide may set: its seconds, look, and when it is included (the screen checks its own
    clock, so a folder's pictures can be "weekday mornings only")."""
    seconds: Seconds = None
    transition: Literal["fade", "cut", "slide", "zoom"] | None = None   # None: the playlist's
    fit: Literal["contain", "cover", "blur"] | None = None
    enabled: bool = True
    days: list[int] | None = None                 # 0 Monday … 6 Sunday; None: every day
    start: str | None = Field(None, pattern=TIME)
    end: str | None = Field(None, pattern=TIME)
    date_from: str | None = Field(None, pattern=DATE)
    date_to: str | None = Field(None, pattern=DATE)

    @field_validator("days")
    @classmethod
    def _weekdays(cls, v):
        if v is None:
            return None
        if any(d < 0 or d > 6 for d in v):
            raise ValueError("days are 0 (Monday) to 6 (Sunday)")
        return sorted(set(v)) or None


class FolderSlide(_Timing):
    kind: Literal["folder"]
    folder_id: int | None = None  # None: the Signage folder's root
    library_id: int | None = None  # with folder_id None: which library's root (the Signage library)
    subfolders: bool = False
    videos: bool = True           # its clips too (only those the browser plays as they are)
    sound: bool = False           # for the clips


class ImageSlide(_Timing):
    kind: Literal["image"]
    file_id: int


class VideoSlide(_Timing):
    kind: Literal["video"]
    file_id: int
    sound: bool = True


class PdfSlide(_Timing):
    kind: Literal["pdf"]
    file_id: int                  # one slide a page, each for `seconds`


class TextSlide(_Timing):
    kind: Literal["text"]
    title: str = Field("", max_length=120)
    body: str = Field("", max_length=1000)
    background: str = Field("#12031f", pattern=COLOR)
    color: str = Field("#ffffff", pattern=COLOR)
    image_id: int | None = None   # a picture behind the text


class WebSlide(_Timing):
    kind: Literal["web"]
    url: str = Field(..., max_length=1000)

    @field_validator("url")
    @classmethod
    def _web_address(cls, v: str) -> str:
        v = v.strip()
        if not v.lower().startswith(("https://", "http://")) or any(ch.isspace() for ch in v):
            raise ValueError("a web page's address starts with https:// (or http://)")
        return v


class YoutubeSlide(_Timing):
    kind: Literal["youtube"]
    url: str = Field(..., max_length=1000)
    sound: bool = False

    @field_validator("url")
    @classmethod
    def _youtube(cls, v: str) -> str:
        if youtube_id(v) is None:
            raise ValueError("a YouTube link looks like https://www.youtube.com/watch?v=… or https://youtu.be/…")
        return v.strip()


class AnnouncementsSlide(_Timing):
    kind: Literal["announcements"]


Slide = Annotated[Union[FolderSlide, ImageSlide, VideoSlide, PdfSlide, TextSlide, WebSlide, YoutubeSlide,
                        AnnouncementsSlide], Field(discriminator="kind")]
_slides = TypeAdapter(list[Slide])


def youtube_id(url: str) -> str | None:
    """The video id in a YouTube link (watch?v=, youtu.be/, shorts/, embed/, live/), or None."""
    url = (url or "").strip()
    match = re.search(r"(?:[?&]v=|youtu\.be/|/shorts/|/embed/|/live/)([A-Za-z0-9_-]{11})(?:[&?#/]|$)", url)
    if match and url.lower().startswith(("https://", "http://")) and "youtu" in url.lower():
        return match.group(1)
    return None


# ----------------------------------------------------------------- schedule
class Entry(BaseModel):
    """A schedule entry as the admin page sends it."""
    name: str = Field("", max_length=80)
    target: Literal["all", "group", "screen"] = "all"
    target_id: int | None = None
    show_id: int
    days: list[int] = Field(default_factory=lambda: list(range(7)), min_length=1, max_length=7)  # 0 Monday … 6 Sunday
    start: str = Field("00:00", pattern=TIME)
    end: str = Field("00:00", pattern=TIME)     # the same as start: all day; before it: past midnight
    date_from: str | None = Field(None, pattern=DATE)
    date_to: str | None = Field(None, pattern=DATE)
    mode: Literal["idle", "always"] = "idle"
    priority: int = Field(0, ge=-100, le=100)
    enabled: bool = True

    @field_validator("days")
    @classmethod
    def _weekdays(cls, v: list[int]) -> list[int]:
        if any(d < 0 or d > 6 for d in v):
            raise ValueError("days are 0 (Monday) to 6 (Sunday)")
        return sorted(set(v))


def _date(value: str | None, what: str) -> str | None:
    if value is None:
        return None
    try:
        return dt.date.fromisoformat(value).isoformat()
    except ValueError:
        raise SignageError(f"{what} isn't a date (YYYY-MM-DD).") from None


# ------------------------------------------------------------------ library
def _libraries(conn: sqlite3.Connection) -> dict[int, tuple[sqlite3.Row, dict[int, str], str | None]]:
    """Library id -> (row, folder id -> section, the section of its root files), from the catalog's roots:
    the Media library (its Pictures and Movies only) and the Signage library."""
    out = {}
    for lib, sections, root in media.catalog_roots(conn):
        if root is None:
            sections = {fid: sec for fid, sec in sections.items() if sec in ("pictures", "movies")}
        out[lib["id"]] = (lib, sections, root)
    return out


def _sections(conn: sqlite3.Connection) -> dict[int, str]:
    """Every folder a slide may point at -> its section, across both libraries."""
    out: dict[int, str] = {}
    for _, sections, _ in _libraries(conn).values():
        out.update(sections)
    return out


_ROW_COLUMNS = ("mi.file_id, mi.media_type, mi.playback, mi.title, mi.duration_s, mi.width, mi.height, mi.pages, "
                "mi.artwork_id, f.display_name, f.blob_sha256, f.folder_id, f.collection_id, b.size")


def _item_rows(conn: sqlite3.Connection, where: str, params: tuple, order: str | None = None) -> list[sqlite3.Row]:
    libs = _libraries(conn)
    if not libs:
        return []
    marks = ",".join("?" * len(libs))
    order = order or "COALESCE(mi.taken_at, f.created_at), mi.sort_title, mi.file_id"
    return conn.execute(
        f"SELECT {_ROW_COLUMNS} FROM media_items mi JOIN files f ON f.id = mi.file_id "
        f"JOIN blobs b ON b.sha256 = f.blob_sha256 WHERE f.collection_id IN ({marks}) AND f.deleted_at IS NULL AND mi.error IS NULL AND {where} "
        f"ORDER BY {order}", (*libs.keys(), *params)).fetchall()


def _plays(row: sqlite3.Row) -> bool:
    """Shown as it is: a picture, a PDF, or a video the browser plays without converting."""
    return row["media_type"] in ("image", "document") or (row["media_type"] == "video" and row["playback"] == "direct")


def _file_row(conn: sqlite3.Connection, file_id: int) -> sqlite3.Row | None:
    rows = _item_rows(conn, "mi.file_id = ?", (file_id,))
    return rows[0] if rows else None


def _in_library(conn: sqlite3.Connection, row: sqlite3.Row) -> bool:
    """A catalogued file in a folder (or root) slides may use."""
    libs = _libraries(conn)
    hit = libs.get(row["collection_id"])
    if hit is None:
        return False
    _, sections, root = hit
    return row["folder_id"] in sections if row["folder_id"] is not None else root is not None


def tree(conn: sqlite3.Connection) -> dict:
    """What the left-hand browser shows: the libraries and their folders.
    {"libraries": [{id, name, root}], "folders": [{id, parent_id, name, library_id}]}: `root` says whether files
    at the library's root count (the Signage folder; Media's root holds no pictures)."""
    libraries, folders = [], []
    for lib_id, (lib, sections, root) in _libraries(conn).items():
        libraries.append({"id": lib_id, "name": lib["name"], "root": root is not None,
                          "signage": root == media.SIGNAGE_SECTION})
        for r in conn.execute("SELECT id, parent_id, name FROM folders WHERE collection_id = ? AND deleted_at IS NULL "
                              "ORDER BY name COLLATE NOCASE", (lib_id,)):
            if r["id"] in sections:
                parent = r["parent_id"] if r["parent_id"] in sections else None
                folders.append({"id": r["id"], "parent_id": parent, "name": r["name"], "library_id": lib_id})
    return {"libraries": libraries, "folders": folders}


def folder_label(conn: sqlite3.Connection, library_id: int | None, folder_id: int | None) -> str:
    libs = _libraries(conn)
    if folder_id is not None:
        path = fs.folder_path(conn, folder_id)
        lib = conn.execute("SELECT collection_id FROM folders WHERE id = ?", (folder_id,)).fetchone()
        name = libs[lib["collection_id"]][0]["name"] if lib and lib["collection_id"] in libs else "?"
        return name + "/" + "/".join(p["name"] for p in path)
    return libs[library_id][0]["name"] if library_id in libs else "?"


def _file_json(r: sqlite3.Row) -> dict:
    return {"id": r["file_id"], "name": r["display_name"], "type": r["media_type"], "plays": _plays(r),
            "art": f"/api/admin/signage/art/{r['artwork_id']}" if r["artwork_id"] else None,
            "duration": r["duration_s"], "width": r["width"], "height": r["height"], "pages": r["pages"],
            "size": r["size"], "playback": r["playback"], "folder_id": r["folder_id"], "library_id": r["collection_id"]}


def files_in(conn: sqlite3.Connection, library_id: int, folder_id: int | None) -> list[dict]:
    """The pictures, clips and PDFs in one folder (or a library's root), for the browser and the pickers."""
    libs = _libraries(conn)
    if library_id not in libs:
        raise SignageError("That library isn't one signage draws on.")
    _, sections, root = libs[library_id]
    if folder_id is None and root is None:
        return []
    if folder_id is not None and folder_id not in sections:
        raise SignageError("That folder isn't one signage draws on.")
    where = "f.collection_id = ? AND " + ("f.folder_id IS NULL" if folder_id is None else "f.folder_id = ?")
    params = (library_id,) if folder_id is None else (library_id, folder_id)
    return [_file_json(r) for r in _item_rows(conn, where, params, "mi.sort_title, mi.file_id")]


def _subtree(conn: sqlite3.Connection, folder_id: int, sections: dict[int, str]) -> list[int]:
    children: dict[int, list[int]] = {}
    for r in conn.execute("SELECT id, parent_id FROM folders WHERE deleted_at IS NULL AND parent_id IS NOT NULL"):
        if r["id"] in sections:
            children.setdefault(r["parent_id"], []).append(r["id"])
    out, todo = [], [folder_id]
    while todo and len(out) < 5000:
        fid = todo.pop()
        out.append(fid)
        todo.extend(children.get(fid, []))
    return out


def check_slides(conn: sqlite3.Connection, slides: list) -> list[dict]:
    """The slides as stored, once what they point at is in a library signage draws on."""
    if len(slides) > MAX_SLIDES:
        raise SignageError(f"At most {MAX_SLIDES} slides.")
    libs = _libraries(conn)
    sections = _sections(conn)

    def need(n: int, file_id: int, kinds: tuple[str, ...], what: str) -> sqlite3.Row:
        row = _file_row(conn, file_id)
        if row is None or row["media_type"] not in kinds or not _in_library(conn, row):
            raise SignageError(f"Slide {n}: that {what} isn't in the Signage or Media folders any more.")
        return row

    for n, s in enumerate(slides, 1):
        if s.start is not None and s.end is None or s.end is not None and s.start is None:
            raise SignageError(f"Slide {n}: give both From and To, or neither.")
        if s.date_from and s.date_to and s.date_from > s.date_to:
            raise SignageError(f"Slide {n}: the dates are the wrong way round.")
        if isinstance(s, FolderSlide):
            if s.folder_id is not None and s.folder_id not in sections:
                raise SignageError(f"Slide {n}: that folder isn't in the Signage or Media folders any more.")
            if s.folder_id is None:
                roots = [lid for lid, (_, _, root) in libs.items() if root is not None]
                if not roots or (s.library_id is not None and s.library_id not in roots):
                    raise SignageError(f"Slide {n}: choose a folder.")
                s.library_id = s.library_id or roots[0]
        elif isinstance(s, ImageSlide):
            need(n, s.file_id, ("image",), "picture")
        elif isinstance(s, VideoSlide):
            row = need(n, s.file_id, ("video",), "video")
            if not _plays(row):
                raise SignageError(f"Slide {n}: {row['display_name']} needs converting to play in a browser. "
                                   "Use an MP4 (H.264) copy.")
        elif isinstance(s, PdfSlide):
            row = need(n, s.file_id, ("document",), "PDF")
            if not row["pages"]:
                raise SignageError(f"Slide {n}: {row['display_name']} has no pages the screen can show.")
        elif isinstance(s, TextSlide) and s.image_id is not None:
            need(n, s.image_id, ("image",), "background picture")
    return [s.model_dump() for s in slides]


# ---------------------------------------------------------------- playlists
_SHOW_FIELDS = ("name", "description", "seconds", "shuffle", "transition", "fit", "effects", "slides")


def _show_json(row: sqlite3.Row, used_by: list[str]) -> dict:
    return {"id": row["id"], "name": row["name"], "description": row["description"], "seconds": row["seconds"],
            "shuffle": bool(row["shuffle"]), "transition": row["transition"], "fit": row["fit"],
            "effects": json.loads(row["effects"]) if row["effects"] else None,
            "slides": json.loads(row["slides"]), "used_by": used_by, "updated_at": row["updated_at"]}


def clean_name(name: str, what: str = "playlist", limit: int = 60) -> str:
    name = " ".join((name or "").split())
    if not name or len(name) > limit:
        raise SignageError(f"Name the {what} (up to {limit} characters).")
    return name


def _used_by(conn: sqlite3.Connection) -> dict[int, list[str]]:
    """Playlist id -> where the schedule plays it."""
    screens = {r["id"]: r["name"] for r in conn.execute("SELECT id, name FROM media_screens")}
    groups = {r["id"]: r["name"] for r in conn.execute("SELECT id, name FROM signage_groups")}
    used: dict[int, list[str]] = {}
    for r in conn.execute("SELECT show_id, target, target_id FROM signage_schedule WHERE enabled = 1 ORDER BY id"):
        where = ("all screens" if r["target"] == "all" else screens.get(r["target_id"]) if r["target"] == "screen"
                 else "group " + groups.get(r["target_id"], "?"))
        if where and where not in used.setdefault(r["show_id"], []):
            used[r["show_id"]].append(where)
    return used


def shows(conn: sqlite3.Connection) -> list[dict]:
    used = _used_by(conn)
    out = [_show_json(r, used.get(r["id"], []))
           for r in conn.execute("SELECT * FROM signage_shows ORDER BY name COLLATE NOCASE")]
    for show in out:   # the admin page shows chosen files by name, with a thumbnail
        for slide in show["slides"]:
            chosen = slide.get("file_id") or slide.get("image_id")
            row = _file_row(conn, chosen) if chosen else None
            if row is not None:
                slide["file"] = _file_json(row)
            if slide["kind"] == "folder":
                slide["folder_label"] = folder_label(conn, slide.get("library_id"), slide.get("folder_id"))
    return out


def create_show(conn: sqlite3.Connection, user_id: int, name: str) -> int:
    name = clean_name(name)
    try:
        return conn.execute("INSERT INTO signage_shows(name, created_by, updated_by) VALUES (?, ?, ?)",
                            (name, user_id, user_id)).lastrowid
    except sqlite3.IntegrityError:
        raise SignageError(f"There is already a playlist called {name}.") from None


def update_show(conn: sqlite3.Connection, user_id: int, show_id: int, *, name: str, description: str, seconds: int,
                shuffle: bool, transition: str, fit: str, effects, slides: list) -> None:
    name = clean_name(name)
    if transition not in TRANSITIONS or fit not in FITS:
        raise SignageError("Pick a transition and a fit from the lists.")
    stored = check_slides(conn, slides)
    fx = clean_effects(effects)
    try:
        cur = conn.execute(
            "UPDATE signage_shows SET name = ?, description = ?, seconds = ?, shuffle = ?, transition = ?, fit = ?, "
            "effects = ?, slides = ?, updated_by = ?, updated_at = datetime('now') WHERE id = ?",
            (name, " ".join((description or "").split())[:300], seconds, int(shuffle), transition, fit,
             json.dumps(fx) if fx is not None else None, json.dumps(stored), user_id, show_id))
    except sqlite3.IntegrityError:
        raise SignageError(f"There is already a playlist called {name}.") from None
    if not cur.rowcount:
        raise SignageError("That playlist is gone.")


def duplicate_show(conn: sqlite3.Connection, user_id: int, show_id: int) -> int:
    row = conn.execute("SELECT * FROM signage_shows WHERE id = ?", (show_id,)).fetchone()
    if row is None:
        raise SignageError("That playlist is gone.")
    name, n = row["name"], 2
    while conn.execute("SELECT 1 FROM signage_shows WHERE lower(name) = lower(?)", (f"{name} ({n})",)).fetchone():
        n += 1
    return conn.execute(
        "INSERT INTO signage_shows(name, description, seconds, shuffle, transition, fit, effects, slides, created_by, "
        "updated_by) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (clean_name(f"{name} ({n})"), row["description"], row["seconds"], row["shuffle"], row["transition"], row["fit"],
         row["effects"], row["slides"], user_id, user_id)).lastrowid


def delete_show(conn: sqlite3.Connection, show_id: int) -> None:
    """Removes it, and the schedule entries that played it."""
    conn.execute("DELETE FROM signage_schedule WHERE show_id = ?", (show_id,))
    conn.execute("DELETE FROM signage_shows WHERE id = ?", (show_id,))


# ------------------------------------------------------------------- groups
def groups(conn: sqlite3.Connection) -> list[dict]:
    return [dict(r) for r in conn.execute(
        "SELECT g.id, g.name, (SELECT count(*) FROM media_screens s WHERE s.group_id = g.id) AS screens "
        "FROM signage_groups g ORDER BY g.name COLLATE NOCASE")]


def save_group(conn: sqlite3.Connection, group_id: int | None, name: str) -> int:
    name = clean_name(name, "group", 40)
    try:
        if group_id is None:
            if conn.execute("SELECT count(*) FROM signage_groups").fetchone()[0] >= MAX_GROUPS:
                raise SignageError(f"At most {MAX_GROUPS} groups.")
            return conn.execute("INSERT INTO signage_groups(name) VALUES (?)", (name,)).lastrowid
        if not conn.execute("UPDATE signage_groups SET name = ? WHERE id = ?", (name, group_id)).rowcount:
            raise SignageError("That group is gone.")
        return group_id
    except sqlite3.IntegrityError:
        raise SignageError(f"There is already a group called {name}.") from None


def delete_group(conn: sqlite3.Connection, group_id: int) -> list[int]:
    """Removes it and its schedule entries; its screens keep playing what's for all screens.
    Returns the screens that were in it."""
    members = [r["id"] for r in conn.execute("SELECT id FROM media_screens WHERE group_id = ?", (group_id,))]
    conn.execute("DELETE FROM signage_schedule WHERE target = 'group' AND target_id = ?", (group_id,))
    conn.execute("UPDATE media_screens SET group_id = NULL WHERE group_id = ?", (group_id,))
    if not conn.execute("DELETE FROM signage_groups WHERE id = ?", (group_id,)).rowcount:
        raise SignageError("That group is gone.")
    return members


# ------------------------------------------------------------------ screens
def _idle_minutes(conn: sqlite3.Connection, screen_id: int) -> int:
    row = conn.execute("SELECT idle_minutes FROM signage_screens WHERE screen_id = ?", (screen_id,)).fetchone()
    return row["idle_minutes"] if row else 5


def _recent(iso: str | None, seconds: int) -> bool:
    if not iso:
        return False
    try:
        when = dt.datetime.fromisoformat(iso.replace(" ", "T")).replace(tzinfo=dt.timezone.utc)
    except ValueError:
        return False
    return (dt.datetime.now(dt.timezone.utc) - when).total_seconds() <= seconds


def screens(conn: sqlite3.Connection) -> list[dict]:
    """Every paired screen, whoever owns it, with its group, settings and last report."""
    out = []
    for s in conn.execute("SELECT s.*, u.username, g.name AS group_name FROM media_screens s "
                          "JOIN users u ON u.id = s.user_id LEFT JOIN signage_groups g ON g.id = s.group_id "
                          "ORDER BY g.name COLLATE NOCASE, s.name COLLATE NOCASE, u.username"):
        out.append({"id": s["id"], "name": s["name"], "owner": s["username"], "last_seen": s["last_seen_at"],
                    "idle_minutes": _idle_minutes(conn, s["id"]), "group_id": s["group_id"], "group": s["group_name"],
                    "location": s["location"], "orientation": s["orientation"],
                    "effects": json.loads(s["effects"]) if s["effects"] else None,
                    "last_state": json.loads(s["last_state"]) if s["last_state"] else None,
                    "last_state_at": s["last_state_at"], "player_version": s["player_version"],
                    "recent": _recent(s["last_seen_at"], ONLINE_SECONDS), "safe": {"x": s["safe_x"], "y": s["safe_y"]}})
    return out


def set_screen(conn: sqlite3.Connection, screen_id: int, *, idle_minutes: int, group_id: int | None, location: str,
               orientation: str, effects) -> None:
    if conn.execute("SELECT 1 FROM media_screens WHERE id = ?", (screen_id,)).fetchone() is None:
        raise SignageError("That screen is gone.")
    if group_id is not None and conn.execute("SELECT 1 FROM signage_groups WHERE id = ?", (group_id,)).fetchone() is None:
        raise SignageError("That group is gone.")
    if orientation not in ORIENTATIONS:
        raise SignageError("A screen is landscape or portrait.")
    fx = clean_effects(effects)
    conn.execute("INSERT INTO signage_screens(screen_id, idle_minutes) VALUES (?, ?) "
                 "ON CONFLICT(screen_id) DO UPDATE SET idle_minutes = excluded.idle_minutes, updated_at = datetime('now')",
                 (screen_id, idle_minutes))
    conn.execute("UPDATE media_screens SET group_id = ?, location = ?, orientation = ?, effects = ? WHERE id = ?",
                 (group_id, " ".join((location or "").split())[:80], orientation,
                  json.dumps(fx) if fx is not None else None, screen_id))


def record_state(conn: sqlite3.Connection, screen_id: int, state: dict, version: str | None) -> None:
    """What the screen just reported: kept, so the admin page shows it after a restart and "online"
    doesn't depend on an open stream."""
    conn.execute("UPDATE media_screens SET last_state = ?, last_state_at = datetime('now'), last_seen_at = datetime('now'), "
                 "player_version = COALESCE(?, player_version) WHERE id = ?",
                 (json.dumps(state), (version or "")[:40] or None, screen_id))


# ----------------------------------------------------------------- schedule
def _entry_json(r: sqlite3.Row) -> dict:
    return {"id": r["id"], "name": r["name"], "target": r["target"], "target_id": r["target_id"], "show_id": r["show_id"],
            "days": [int(d) for d in r["days"]], "start": r["start"], "end": r["end"], "date_from": r["date_from"],
            "date_to": r["date_to"], "mode": r["mode"], "priority": r["priority"], "enabled": bool(r["enabled"]),
            "created_at": r["created_at"]}


def schedule(conn: sqlite3.Connection) -> list[dict]:
    return [_entry_json(r) for r in conn.execute(
        "SELECT * FROM signage_schedule ORDER BY CASE target WHEN 'screen' THEN 0 WHEN 'group' THEN 1 ELSE 2 END, "
        "priority DESC, id DESC")]


def _check_entry(conn: sqlite3.Connection, e: Entry) -> None:
    if conn.execute("SELECT 1 FROM signage_shows WHERE id = ?", (e.show_id,)).fetchone() is None:
        raise SignageError("That playlist is gone.")
    if e.target == "all":
        e.target_id = None
    elif e.target_id is None:
        raise SignageError("Say which screen or group the entry is for.")
    elif e.target == "group" and conn.execute("SELECT 1 FROM signage_groups WHERE id = ?", (e.target_id,)).fetchone() is None:
        raise SignageError("That group is gone.")
    elif e.target == "screen" and conn.execute("SELECT 1 FROM media_screens WHERE id = ?", (e.target_id,)).fetchone() is None:
        raise SignageError("That screen is gone.")
    e.date_from, e.date_to = _date(e.date_from, "From"), _date(e.date_to, "To")
    if e.date_from and e.date_to and e.date_from > e.date_to:
        raise SignageError("The dates are the wrong way round.")
    e.name = " ".join(e.name.split())


def save_entry(conn: sqlite3.Connection, entry_id: int | None, e: Entry) -> int:
    _check_entry(conn, e)
    days = "".join(str(d) for d in e.days)
    if entry_id is None:
        if conn.execute("SELECT count(*) FROM signage_schedule").fetchone()[0] >= MAX_ENTRIES:
            raise SignageError(f"At most {MAX_ENTRIES} schedule entries.")
        return conn.execute(
            "INSERT INTO signage_schedule(name, target, target_id, show_id, days, start, \"end\", date_from, date_to, mode, "
            "priority, enabled) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (e.name, e.target, e.target_id, e.show_id, days, e.start, e.end, e.date_from, e.date_to, e.mode, e.priority,
             int(e.enabled))).lastrowid
    cur = conn.execute(
        "UPDATE signage_schedule SET name = ?, target = ?, target_id = ?, show_id = ?, days = ?, start = ?, \"end\" = ?, "
        "date_from = ?, date_to = ?, mode = ?, priority = ?, enabled = ? WHERE id = ?",
        (e.name, e.target, e.target_id, e.show_id, days, e.start, e.end, e.date_from, e.date_to, e.mode, e.priority,
         int(e.enabled), entry_id))
    if not cur.rowcount:
        raise SignageError("That schedule entry is gone.")
    return entry_id


def delete_entry(conn: sqlite3.Connection, entry_id: int) -> None:
    if not conn.execute("DELETE FROM signage_schedule WHERE id = ?", (entry_id,)).rowcount:
        raise SignageError("That schedule entry is gone.")


def entry_target_label(conn: sqlite3.Connection, e: Entry) -> str:
    if e.target == "all":
        return "all screens"
    table = "signage_groups" if e.target == "group" else "media_screens"
    row = conn.execute(f"SELECT name FROM {table} WHERE id = ?", (e.target_id,)).fetchone()
    return ("group " if e.target == "group" else "") + (row["name"] if row else "?")


# ------------------------------------------------------- what a screen plays
def file_src(row: sqlite3.Row, page: int | None = None, admin: bool = False) -> str:
    # The content's hash in the address: a changed picture is fetched again, an unchanged one cached.
    base = "/api/admin/signage/file/" if admin else "/media/screen/signage/file/"
    return f"{base}{row['file_id']}?v={row['blob_sha256'][:12]}" + (f"&page={page}" if page else "")


def _when(s: dict) -> dict | None:
    """A slide's own limits, for the screen to check by its clock; None when it has none."""
    when = {k: s.get(k) for k in ("days", "start", "end", "date_from", "date_to") if s.get(k)}
    return when or None


def _items(conn: sqlite3.Connection, show: sqlite3.Row, admin: bool = False) -> tuple[list[dict], set[int]]:
    """A playlist's slides as the screen plays them, folders expanded; and the files it may fetch."""
    items: list[dict] = []
    files: set[int] = set()
    seconds = show["seconds"]
    sections = _sections(conn)
    libs = _libraries(conn)

    def add(item: dict, s: dict) -> None:
        for k in ("transition", "fit"):
            if s.get(k):
                item[k] = s[k]
        when = _when(s)
        if when:
            item["when"] = when
        items.append(item)

    for s in json.loads(show["slides"]):
        if s.get("enabled") is False:
            continue
        kind, secs = s["kind"], s.get("seconds") or seconds
        if kind == "folder":
            if s.get("folder_id") is None:
                lib_id = s.get("library_id")
                if lib_id not in libs or libs[lib_id][2] is None:
                    continue
                where, params = "f.collection_id = ? AND f.folder_id IS NULL", (lib_id,)
            else:
                if s["folder_id"] not in sections:
                    continue
                ids = _subtree(conn, s["folder_id"], sections) if s.get("subfolders") else [s["folder_id"]]
                where, params = "f.folder_id IN (" + ",".join("?" * len(ids)) + ")", tuple(ids)
            kinds = "('image', 'video')" if s.get("videos", True) else "('image')"
            for r in _item_rows(conn, f"{where} AND mi.media_type IN {kinds}", params):
                if len(files) >= MAX_ITEMS:
                    break
                if not _plays(r):
                    continue
                files.add(r["file_id"])
                add({"type": r["media_type"], "src": file_src(r, admin=admin), "seconds": secs, "sound": s.get("sound", False),
                     "title": r["title"] or r["display_name"], "file_id": r["file_id"]}, s)
        elif kind == "image":
            r = _file_row(conn, s["file_id"])
            if r is not None and r["media_type"] == "image":
                files.add(r["file_id"])
                add({"type": "image", "src": file_src(r, admin=admin), "seconds": secs, "title": r["title"] or r["display_name"],
                     "file_id": r["file_id"]}, s)
        elif kind == "video":
            r = _file_row(conn, s["file_id"])
            if r is not None and r["media_type"] == "video" and _plays(r):
                files.add(r["file_id"])
                add({"type": "video", "src": file_src(r, admin=admin), "sound": s.get("sound", True),
                     "title": r["title"] or r["display_name"], "file_id": r["file_id"]}, s)
        elif kind == "pdf":
            r = _file_row(conn, s["file_id"])
            if r is not None and r["media_type"] == "document" and r["pages"]:
                files.add(r["file_id"])
                for page in range(1, min(r["pages"], MAX_PDF_PAGES) + 1):
                    add({"type": "image", "src": file_src(r, page, admin=admin), "seconds": secs, "file_id": r["file_id"],
                         "title": f"{r['title'] or r['display_name']} · p. {page}"}, s)
        elif kind == "text":
            image = _file_row(conn, s["image_id"]) if s.get("image_id") else None
            if image is not None and image["media_type"] == "image":
                files.add(image["file_id"])
            add({"type": "text", "title": s["title"], "body": s["body"], "background": s["background"],
                 "color": s["color"], "seconds": secs,
                 "image": file_src(image, admin=admin) if image is not None and image["media_type"] == "image" else None}, s)
        elif kind == "web":
            add({"type": "web", "url": s["url"], "seconds": secs, "title": s["url"]}, s)
        elif kind == "youtube":
            vid = youtube_id(s["url"])
            if vid:
                add({"type": "youtube", "id": vid, "seconds": secs, "sound": s.get("sound", False), "title": "YouTube " + vid}, s)
        elif kind == "announcements":
            # Only those for everyone: a TV is seen by whoever walks past.
            for a in conn.execute("SELECT title, body FROM announcements WHERE group_ref = '' AND "
                                  "(expires_at IS NULL OR expires_at > datetime('now')) ORDER BY id DESC LIMIT ?",
                                  (MAX_NOTICES,)):
                add({"type": "notice", "title": a["title"], "seconds": secs,
                     "html": markdown.render_html(a["body"]) if a["body"] else ""}, s)
    return items, files


def _show_for_screen(conn: sqlite3.Connection, show: sqlite3.Row, admin: bool = False) -> tuple[dict, set[int]]:
    items, files = _items(conn, show, admin)
    return {"id": show["id"], "name": show["name"], "shuffle": bool(show["shuffle"]), "transition": show["transition"],
            "fit": show["fit"], "seconds": show["seconds"], "items": items}, files


def programme(conn: sqlite3.Connection, screen: sqlite3.Row, horizon_days: int = 7) -> list[sqlite3.Row]:
    """The schedule entries for this screen, best first: its own, its group's, everyone's; by priority, then
    the newest. Entries dated outside the next `horizon_days` are left out."""
    today = dt.date.today()
    until = (today + dt.timedelta(days=horizon_days)).isoformat()
    rows = conn.execute(
        "SELECT * FROM signage_schedule WHERE enabled = 1 AND (target = 'all' OR (target = 'screen' AND target_id = ?) "
        "OR (target = 'group' AND target_id = ?)) AND (date_from IS NULL OR date_from <= ?) "
        "AND (date_to IS NULL OR date_to >= ?) "
        "ORDER BY CASE target WHEN 'screen' THEN 0 WHEN 'group' THEN 1 ELSE 2 END, priority DESC, id DESC",
        (screen["id"], screen["group_id"], until, today.isoformat())).fetchall()
    return rows


def for_screen(conn: sqlite3.Connection, screen: sqlite3.Row) -> tuple[dict, set[int]]:
    """What the screen is told (its programme, ranked, and the playlists it names, ready to play), and
    the files those may fetch."""
    default = media.load_settings(conn).get("signage_effects") or None
    own = json.loads(screen["effects"]) if screen["effects"] else None
    entries, out, files = [], {}, set()
    for r in programme(conn, screen):
        if str(r["show_id"]) not in out:
            show = conn.execute("SELECT * FROM signage_shows WHERE id = ?", (r["show_id"],)).fetchone()
            if show is None:
                continue
            out[str(r["show_id"])], used = _show_for_screen(conn, show)
            files |= used
        show_fx = json.loads(conn.execute("SELECT effects FROM signage_shows WHERE id = ?", (r["show_id"],)).fetchone()[0] or "null")
        entries.append(_entry_json(r) | {"effects": resolve_effects(default, own, show_fx)})
    return {"idle_minutes": _idle_minutes(conn, screen["id"]),
            "screen": {"id": screen["id"], "name": screen["name"], "orientation": screen["orientation"],
                       "effects": resolve_effects(default, own)},
            "programme": entries, "shows": out}, files


def preview(conn: sqlite3.Connection, show_id: int) -> tuple[dict, set[int]]:
    """A playlist as a screen would play it, with admin file addresses (the admin page's preview)."""
    show = conn.execute("SELECT * FROM signage_shows WHERE id = ?", (show_id,)).fetchone()
    if show is None:
        raise SignageError("That playlist is gone.")
    out, files = _show_for_screen(conn, show, admin=True)
    out["effects"] = json.loads(show["effects"]) if show["effects"] else None
    return out, files


def admin_files(conn: sqlite3.Connection) -> set[int]:
    """Every file any playlist may show (what the admin preview may fetch)."""
    files: set[int] = set()
    for show in conn.execute("SELECT * FROM signage_shows"):
        files |= _items(conn, show, admin=True)[1]
    return files


def picture_path(conn: sqlite3.Connection, settings: Settings, file_id: int, page: int | None = None) -> tuple[Path, str] | None:
    """The file to send for a signage picture, PDF page or video, and its type: pictures and pages made
    screen-sized once (and kept under <media_dir>/signage), GIFs and videos as they are."""
    row = _file_row(conn, file_id)
    if row is None:
        return None
    source = store.content_path(conn, settings, file_id)
    if not source.is_file():
        return None
    name = row["display_name"].lower()
    if row["media_type"] == "video" or name.endswith(".gif"):
        return source, _type_of(name, row["media_type"])
    cache = settings.media_dir / "signage"
    if row["media_type"] == "document":
        page = max(1, page or 1)
        target = cache / f"{file_id}-{row['blob_sha256'][:16]}-p{page}.jpg"
        if not target.is_file() and not _page(source, target, page - 1):
            return None
        return target, "image/jpeg"
    target = cache / f"{file_id}-{row['blob_sha256'][:16]}.jpg"
    if not target.is_file() and not _shrink(source, target):
        return None
    return target, "image/jpeg"


def _type_of(name: str, media_type: str) -> str:
    import mimetypes
    if name.endswith(".gif"):
        return "image/gif"
    return {".mp4": "video/mp4", ".m4v": "video/mp4", ".webm": "video/webm", ".mov": "video/mp4"}.get(
        Path(name).suffix, mimetypes.guess_type(name)[0] or "application/octet-stream")


def _prune_old(target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    stem = target.name.split("-")[0]
    for old in target.parent.glob(stem + "-*.jpg"):   # an earlier version of the file
        if not old.name.startswith(target.name.rsplit("-p", 1)[0]):
            old.unlink(missing_ok=True)


def _shrink(source: Path, target: Path) -> bool:
    from PIL import Image, ImageOps, UnidentifiedImageError

    _prune_old(target)
    tmp = target.with_suffix(".tmp")
    try:
        with Image.open(source) as img:
            img = ImageOps.exif_transpose(img)
            img.thumbnail(IMAGE_BOX)
            if img.mode not in ("RGB", "L"):
                img = img.convert("RGB")
            img.save(tmp, "JPEG", quality=86, optimize=True)
        tmp.replace(target)
        return True
    except (OSError, UnidentifiedImageError, ValueError, Image.DecompressionBombError) as exc:
        log.info("Signage: can't show %s: %s", source.name, exc)
        tmp.unlink(missing_ok=True)
        return False


def _page(source: Path, target: Path, index: int) -> bool:
    _prune_old(target)
    data = artwork.pdf_page(source, index, IMAGE_BOX[0])
    if not data:
        return False
    tmp = target.with_suffix(".tmp")
    tmp.write_bytes(data)
    tmp.replace(target)
    return True


def parse_slides(raw: list) -> list:
    """Slides as the admin page sends them (validated: the pydantic errors become a SignageError)."""
    from pydantic import ValidationError
    try:
        return _slides.validate_python(raw)
    except ValidationError as exc:
        first = exc.errors()[0]
        where = first["loc"][0] + 1 if first["loc"] and isinstance(first["loc"][0], int) else "?"
        raise SignageError(f"Slide {where}: {first['msg']}.") from None
