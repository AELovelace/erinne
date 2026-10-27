"""Signage: what a screen (the TV) shows while nobody is using it, set by admins (Access → Signage).

A slideshow is a list of slides: a folder of the Media library (its pictures, and its clips when they
play in the browser as they are), one video, a text slide, a web page, or the announcements for
everyone. Each screen has rules: which slideshow, on which weekdays, between which times, and whether
it waits until the screen has been idle (no casting, playing or remote presses for idle_minutes) or
runs always (signage hours: casting still interrupts it). The first rule that matches the screen's
own clock wins; the screen decides, so it keeps going while the server is briefly away.

The screen gets its pictures and videos through its own key (web/media.py, /media/screen/signage/...),
only those in slideshows its rules name, whatever its owner may read: admins chose them.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path
from typing import Annotated, Literal, Union

from pydantic import BaseModel, Field, TypeAdapter, field_validator

from mnemerynne import fs, media
from mnemerynne.config import Settings
from mnemerynne.ingest import markdown, store

log = logging.getLogger("mnemerynne.media")

MAX_SLIDES = 100
MAX_RULES = 50
MAX_ITEMS = 2000                # files from one slideshow's folders
MAX_NOTICES = 20
IMAGE_BOX = (1920, 1080)        # pictures are sent no bigger than the screen
COLOR = r"^#[0-9a-fA-F]{6}$"
TIME = r"^([01][0-9]|2[0-3]):[0-5][0-9]$"
Seconds = Annotated[int | None, Field(None, ge=3, le=3600)]   # None: the slideshow's own


class SignageError(ValueError):
    """A refusal; the message is safe to show."""
    status = 422


# ------------------------------------------------------------------- slides
class FolderSlide(BaseModel):
    kind: Literal["folder"]
    folder_id: int
    subfolders: bool = False
    videos: bool = True           # its clips too (only those the browser plays as they are)
    sound: bool = False           # for the clips
    seconds: Seconds = None


class VideoSlide(BaseModel):
    kind: Literal["video"]
    file_id: int
    sound: bool = True


class TextSlide(BaseModel):
    kind: Literal["text"]
    title: str = Field("", max_length=120)
    body: str = Field("", max_length=1000)
    background: str = Field("#12031f", pattern=COLOR)
    color: str = Field("#ffffff", pattern=COLOR)
    image_id: int | None = None   # a picture behind the text
    seconds: Seconds = None


class WebSlide(BaseModel):
    kind: Literal["web"]
    url: str = Field(..., max_length=1000)
    seconds: Seconds = None

    @field_validator("url")
    @classmethod
    def _web_address(cls, v: str) -> str:
        v = v.strip()
        if not v.lower().startswith(("https://", "http://")) or any(ch.isspace() for ch in v):
            raise ValueError("a web page's address starts with https:// (or http://)")
        return v


class AnnouncementsSlide(BaseModel):
    kind: Literal["announcements"]
    seconds: Seconds = None


Slide = Annotated[Union[FolderSlide, VideoSlide, TextSlide, WebSlide, AnnouncementsSlide],
                  Field(discriminator="kind")]
_slides = TypeAdapter(list[Slide])


class Rule(BaseModel):
    show_id: int
    days: list[int] = Field(default_factory=lambda: list(range(7)), min_length=1, max_length=7)  # 0 Monday … 6 Sunday
    start: str = Field("00:00", pattern=TIME)
    end: str = Field("00:00", pattern=TIME)     # the same as start: all day; before it: past midnight
    mode: Literal["idle", "always"] = "idle"

    @field_validator("days")
    @classmethod
    def _weekdays(cls, v: list[int]) -> list[int]:
        if any(d < 0 or d > 6 for d in v):
            raise ValueError("days are 0 (Monday) to 6 (Sunday)")
        return sorted(set(v))


_rules = TypeAdapter(list[Rule])


# ----------------------------------------------------------------- library
def _library_folders(conn: sqlite3.Connection) -> tuple[int | None, dict[int, str]]:
    lib = media.library(conn)
    if lib is None:
        return None, {}
    return lib["id"], media.folder_sections(conn, lib["id"])


def folders(conn: sqlite3.Connection) -> list[dict]:
    """The Media library's folders, by path, to choose from."""
    _, sections = _library_folders(conn)
    out = [{"id": fid, "path": "/".join(p["name"] for p in fs.folder_path(conn, fid))} for fid in sections]
    return sorted(out, key=lambda f: f["path"].lower())


def _item_rows(conn: sqlite3.Connection, where: str, params: tuple) -> list[sqlite3.Row]:
    lib = media.library(conn)
    if lib is None:
        return []
    return conn.execute(
        "SELECT mi.file_id, mi.media_type, mi.playback, mi.title, f.display_name, f.blob_sha256, f.folder_id "
        "FROM media_items mi JOIN files f ON f.id = mi.file_id WHERE f.collection_id = ? AND f.deleted_at IS NULL "
        f"AND mi.error IS NULL AND {where} ORDER BY COALESCE(mi.taken_at, f.created_at), mi.sort_title, mi.file_id",
        (lib["id"], *params)).fetchall()


def _plays(row: sqlite3.Row) -> bool:
    """Shown as it is: a picture, or a video the browser plays without converting."""
    return row["media_type"] == "image" or (row["media_type"] == "video" and row["playback"] == "direct")


def files_in(conn: sqlite3.Connection, folder_id: int) -> list[dict]:
    """The pictures and videos in one folder, to choose a video or a background from."""
    return [{"id": r["file_id"], "name": r["display_name"], "type": r["media_type"], "plays": _plays(r)}
            for r in _item_rows(conn, "f.folder_id = ? AND mi.media_type IN ('image', 'video')", (folder_id,))]


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


def _file_row(conn: sqlite3.Connection, file_id: int) -> sqlite3.Row | None:
    rows = _item_rows(conn, "mi.file_id = ?", (file_id,))
    return rows[0] if rows else None


def check_slides(conn: sqlite3.Connection, slides: list) -> list[dict]:
    """The slides as stored, once what they point at is in the Media library."""
    if len(slides) > MAX_SLIDES:
        raise SignageError(f"At most {MAX_SLIDES} slides.")
    _, sections = _library_folders(conn)
    for n, s in enumerate(slides, 1):
        if isinstance(s, FolderSlide) and s.folder_id not in sections:
            raise SignageError(f"Slide {n}: that folder isn't in the Media library any more.")
        if isinstance(s, VideoSlide):
            row = _file_row(conn, s.file_id)
            if row is None or row["media_type"] != "video":
                raise SignageError(f"Slide {n}: that video isn't in the Media library any more.")
            if not _plays(row):
                raise SignageError(f"Slide {n}: {row['display_name']} needs converting to play in a browser. "
                                   "Use an MP4 (H.264) copy.")
        if isinstance(s, TextSlide) and s.image_id is not None:
            row = _file_row(conn, s.image_id)
            if row is None or row["media_type"] != "image":
                raise SignageError(f"Slide {n}: that background picture isn't in the Media library any more.")
    return [s.model_dump() for s in slides]


# ---------------------------------------------------------------- slideshows
def _show_json(row: sqlite3.Row, used_by: list[str]) -> dict:
    return {"id": row["id"], "name": row["name"], "seconds": row["seconds"], "shuffle": bool(row["shuffle"]),
            "slides": json.loads(row["slides"]), "used_by": used_by, "updated_at": row["updated_at"]}


def clean_name(name: str) -> str:
    name = " ".join((name or "").split())
    if not name or len(name) > 60:
        raise SignageError("Name the slideshow (up to 60 characters), e.g. Lobby.")
    return name


def shows(conn: sqlite3.Connection) -> list[dict]:
    names = {r["id"]: r["name"] for r in conn.execute("SELECT id, name FROM media_screens")}
    used: dict[int, list[str]] = {}
    for r in conn.execute("SELECT screen_id, rules FROM signage_screens"):
        for rule in json.loads(r["rules"]):
            if r["screen_id"] in names and names[r["screen_id"]] not in used.setdefault(rule["show_id"], []):
                used[rule["show_id"]].append(names[r["screen_id"]])
    out = [_show_json(r, used.get(r["id"], []))
           for r in conn.execute("SELECT * FROM signage_shows ORDER BY name COLLATE NOCASE")]
    for show in out:   # the admin page shows chosen files by name, in their folder
        for slide in show["slides"]:
            chosen = slide.get("file_id") or slide.get("image_id")
            row = _file_row(conn, chosen) if chosen else None
            if row is not None:
                slide["file_name"], slide["file_folder"] = row["display_name"], row["folder_id"]
    return out


def create_show(conn: sqlite3.Connection, user_id: int, name: str, seconds: int = 10, shuffle: bool = False) -> int:
    name = clean_name(name)
    try:
        return conn.execute("INSERT INTO signage_shows(name, seconds, shuffle, created_by) VALUES (?, ?, ?, ?)",
                            (name, seconds, int(shuffle), user_id)).lastrowid
    except sqlite3.IntegrityError:
        raise SignageError(f"There is already a slideshow called {name}.") from None


def update_show(conn: sqlite3.Connection, show_id: int, name: str, seconds: int, shuffle: bool, slides: list) -> None:
    name = clean_name(name)
    stored = check_slides(conn, slides)
    try:
        cur = conn.execute("UPDATE signage_shows SET name = ?, seconds = ?, shuffle = ?, slides = ?, "
                           "updated_at = datetime('now') WHERE id = ?",
                           (name, seconds, int(shuffle), json.dumps(stored), show_id))
    except sqlite3.IntegrityError:
        raise SignageError(f"There is already a slideshow called {name}.") from None
    if not cur.rowcount:
        raise SignageError("That slideshow is gone.")


def delete_show(conn: sqlite3.Connection, show_id: int) -> list[int]:
    """Removes it, and the rules that played it; the screens whose rules changed."""
    conn.execute("DELETE FROM signage_shows WHERE id = ?", (show_id,))
    changed = []
    for r in conn.execute("SELECT screen_id, rules FROM signage_screens").fetchall():
        rules = json.loads(r["rules"])
        kept = [rule for rule in rules if rule["show_id"] != show_id]
        if len(kept) != len(rules):
            conn.execute("UPDATE signage_screens SET rules = ?, updated_at = datetime('now') WHERE screen_id = ?",
                         (json.dumps(kept), r["screen_id"]))
            changed.append(r["screen_id"])
    return changed


# ------------------------------------------------------------------ screens
def _screen_setting(conn: sqlite3.Connection, screen_id: int) -> tuple[int, list[dict]]:
    row = conn.execute("SELECT idle_minutes, rules FROM signage_screens WHERE screen_id = ?", (screen_id,)).fetchone()
    return (row["idle_minutes"], json.loads(row["rules"])) if row else (5, [])


def screens(conn: sqlite3.Connection) -> list[dict]:
    """Every paired screen, whoever owns it, with its rules."""
    out = []
    for s in conn.execute("SELECT s.id, s.name, s.last_seen_at, u.username FROM media_screens s "
                          "JOIN users u ON u.id = s.user_id ORDER BY s.name COLLATE NOCASE, u.username"):
        idle, rules = _screen_setting(conn, s["id"])
        out.append({"id": s["id"], "name": s["name"], "owner": s["username"], "last_seen": s["last_seen_at"],
                    "idle_minutes": idle, "rules": rules})
    return out


def set_screen(conn: sqlite3.Connection, screen_id: int, idle_minutes: int, rules: list[Rule]) -> None:
    if conn.execute("SELECT 1 FROM media_screens WHERE id = ?", (screen_id,)).fetchone() is None:
        raise SignageError("That screen is gone.")
    if len(rules) > MAX_RULES:
        raise SignageError(f"At most {MAX_RULES} rules a screen.")
    known = {r[0] for r in conn.execute("SELECT id FROM signage_shows")}
    for n, rule in enumerate(rules, 1):
        if rule.show_id not in known:
            raise SignageError(f"Rule {n}: that slideshow is gone.")
    conn.execute("INSERT INTO signage_screens(screen_id, idle_minutes, rules) VALUES (?, ?, ?) "
                 "ON CONFLICT(screen_id) DO UPDATE SET idle_minutes = excluded.idle_minutes, rules = excluded.rules, "
                 "updated_at = datetime('now')",
                 (screen_id, idle_minutes, json.dumps([r.model_dump() for r in rules])))


# ------------------------------------------------------- what a screen plays
def file_src(row: sqlite3.Row) -> str:
    # The content's hash in the address: a changed picture is fetched again, an unchanged one cached.
    return f"/media/screen/signage/file/{row['file_id']}?v={row['blob_sha256'][:12]}"


def _items(conn: sqlite3.Connection, show: sqlite3.Row, sections: dict[int, str]) -> tuple[list[dict], set[int]]:
    """A slideshow's slides as the screen plays them, folders expanded; and the files it may fetch."""
    items: list[dict] = []
    files: set[int] = set()
    seconds = show["seconds"]
    for s in json.loads(show["slides"]):
        kind, secs = s["kind"], s.get("seconds") or seconds
        if kind == "folder":
            if s["folder_id"] not in sections:
                continue
            ids = _subtree(conn, s["folder_id"], sections) if s["subfolders"] else [s["folder_id"]]
            marks = ",".join("?" * len(ids))
            kinds = "('image', 'video')" if s["videos"] else "('image')"
            for r in _item_rows(conn, f"f.folder_id IN ({marks}) AND mi.media_type IN {kinds}", tuple(ids)):
                if len(files) >= MAX_ITEMS:
                    break
                if not _plays(r):
                    continue
                files.add(r["file_id"])
                items.append({"type": r["media_type"], "src": file_src(r), "seconds": secs, "sound": s["sound"],
                              "title": r["title"] or r["display_name"]})
        elif kind == "video":
            r = _file_row(conn, s["file_id"])
            if r is not None and r["media_type"] == "video" and _plays(r):
                files.add(r["file_id"])
                items.append({"type": "video", "src": file_src(r), "sound": s["sound"],
                              "title": r["title"] or r["display_name"]})
        elif kind == "text":
            image = _file_row(conn, s["image_id"]) if s.get("image_id") else None
            if image is not None and image["media_type"] == "image":
                files.add(image["file_id"])
            items.append({"type": "text", "title": s["title"], "body": s["body"], "background": s["background"],
                          "color": s["color"], "seconds": secs,
                          "image": file_src(image) if image is not None and image["media_type"] == "image" else None})
        elif kind == "web":
            items.append({"type": "web", "url": s["url"], "seconds": secs, "title": s["url"]})
        elif kind == "announcements":
            # Only those for everyone: a TV is seen by whoever walks past.
            for a in conn.execute("SELECT title, body FROM announcements WHERE group_ref = '' AND "
                                  "(expires_at IS NULL OR expires_at > datetime('now')) ORDER BY id DESC LIMIT ?",
                                  (MAX_NOTICES,)):
                items.append({"type": "notice", "title": a["title"], "seconds": secs,
                              "html": markdown.render_html(a["body"]) if a["body"] else ""})
    return items, files


def for_screen(conn: sqlite3.Connection, screen_id: int) -> tuple[dict, set[int]]:
    """What the screen is told (its rules and the slideshows they name, ready to play), and the files
    those may fetch."""
    idle, rules = _screen_setting(conn, screen_id)
    _, sections = _library_folders(conn)
    out: dict[str, dict] = {}
    files: set[int] = set()
    for show_id in dict.fromkeys(r["show_id"] for r in rules):
        show = conn.execute("SELECT * FROM signage_shows WHERE id = ?", (show_id,)).fetchone()
        if show is None:
            continue
        items, used = _items(conn, show, sections)
        files |= used
        out[str(show_id)] = {"name": show["name"], "shuffle": bool(show["shuffle"]), "items": items}
    return {"idle_minutes": idle, "rules": rules, "shows": out}, files


def picture_path(conn: sqlite3.Connection, settings: Settings, file_id: int) -> tuple[Path, str] | None:
    """The file to send for a signage picture or video, and its type: pictures made screen-sized once
    (and kept under <media_dir>/signage), GIFs and videos as they are."""
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


def _shrink(source: Path, target: Path) -> bool:
    from PIL import Image, ImageOps, UnidentifiedImageError

    target.parent.mkdir(parents=True, exist_ok=True)
    for old in target.parent.glob(target.name.split("-")[0] + "-*.jpg"):   # an earlier version of the file
        old.unlink(missing_ok=True)
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


def parse_slides(raw: list) -> list:
    """Slides as the admin page sends them (validated: the pydantic errors become a SignageError)."""
    from pydantic import ValidationError
    try:
        return _slides.validate_python(raw)
    except ValidationError as exc:
        first = exc.errors()[0]
        where = first["loc"][0] + 1 if first["loc"] and isinstance(first["loc"][0], int) else "?"
        raise SignageError(f"Slide {where}: {first['msg']}.") from None
