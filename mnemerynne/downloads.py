"""Downloading several things at once as one zip: folders (with everything in them), several files, or
everything in a place (the Files page's Select all).

plan() works out what goes in, with the person's own access, the same rules as browsing:
- nothing in the bin;
- only folders they may see;
- files only from folders they may read (and SharePoint files only when SharePoint agrees).

stream() writes the zip while it's being sent. Nothing is built on disk or held in memory, so a big
folder starts downloading at once, and the memory used stays the same at any size. The zip has no
Content-Length, so the browser shows progress without a total. Already-compressed files (pictures,
films, PDFs, Office files) are stored as they are; text is deflated. Zip64 is used where a file needs
it (over 4 GB).

The Files page asks first (POST /api/downloads: what would go in, how big), then opens the signed link
it gets back (GET /files/zip/<token>, web/files.py), which works out the plan again with the access the
person has then.
"""

from __future__ import annotations

import os
import sqlite3
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

from mnemerynne import fs, permissions, share
from mnemerynne.ingest import store

MAX_FILES = 20_000            # one zip; past this, working out the plan and the download itself get unwieldy
CHUNK = 1024 * 1024           # read the source files a megabyte at a time
SEND_AT = 256 * 1024          # hand the zip on to the browser in pieces of at least this much
NOT_INCLUDED = "Not included.txt"   # added at the end when a file couldn't be read while zipping

# Text-like files are worth deflating; everything else (pictures, films, PDFs, docx/xlsx, which are zips
# themselves) barely shrinks and would only cost the NAS's CPU.
_DEFLATE_KINDS = {"md", "txt", "code"}
_DEFLATE_MIMES = ("text/", "application/json", "application/xml", "application/javascript", "image/svg+xml",
                  "application/x-ndjson", "application/sql")


class TooMany(fs.FsError):
    status = 413


@dataclass
class Entry:
    """One thing in the zip: a folder (file_id None, arcname ending in '/') or a file."""
    arcname: str
    file_id: int | None = None
    size: int = 0
    deflate: bool = False


@dataclass
class Plan:
    name: str                                  # the zip's file name, without .zip
    entries: list[Entry] = field(default_factory=list)
    missing: int = 0                           # chosen items that are gone, or not the person's to read

    @property
    def files(self) -> int:
        return sum(1 for e in self.entries if e.file_id is not None)

    @property
    def bytes(self) -> int:
        return sum(e.size for e in self.entries)

    def json(self) -> dict:
        return {"name": self.name + ".zip", "files": self.files,
                "folders": sum(1 for e in self.entries if e.file_id is None), "bytes": self.bytes,
                "missing": self.missing}


def safe_name(name: str) -> str:
    """A file or folder name that is one zip path part: no slashes, no control characters, never '.' or '..'
    (which would let an unzip step out of its folder)."""
    cleaned = "".join("_" if c in "/\\" or ord(c) < 32 or c == "\x7f" else c for c in (name or "")).strip()
    return "_" if cleaned in ("", ".", "..") else cleaned


def _unique(name: str, taken: set[str]) -> str:
    """name, or "name (2).ext", "name (3).ext"… when that is already used at this level (case-insensitively,
    as Windows and macOS see it)."""
    if name.lower() not in taken:
        taken.add(name.lower())
        return name
    stem, dot, ext = name.rpartition(".")
    stem, ext = (stem, dot + ext) if stem else (name, "")
    n = 2
    while f"{stem} ({n}){ext}".lower() in taken:
        n += 1
    taken.add(f"{stem} ({n}){ext}".lower())
    return f"{stem} ({n}){ext}"


def _deflate(row) -> bool:
    """Whether a file is worth compressing (see _DEFLATE_KINDS)."""
    mime = (row["mime"] or "").lower()
    return row["kind"] in _DEFLATE_KINDS or mime.startswith(_DEFLATE_MIMES)


class _Collection:
    """One collection's folders, worked out once per plan: which are live, which the person may see,
    and each one's subfolders."""

    def __init__(self, conn: sqlite3.Connection, access: fs.Access, collection_id: int):
        live = fs.live_folder_ids(conn, collection_id)
        self.visible = live & access.visible(collection_id, live)    # not in the bin, and theirs to see
        self.children: dict[int | None, list[sqlite3.Row]] = {}
        for r in conn.execute("SELECT id, parent_id, name FROM folders WHERE collection_id = ? "
                              "ORDER BY name COLLATE NOCASE", (collection_id,)):
            if r["id"] in self.visible:
                self.children.setdefault(r["parent_id"], []).append(r)


class _Planner:
    """Builds a Plan: walks the chosen folders and adds what the person may read."""

    def __init__(self, conn: sqlite3.Connection, access: fs.Access, name: str):
        self.conn, self.access = conn, access
        self.plan = Plan(name=safe_name(name))
        self.collections: dict[int, _Collection] = {}
        self.taken: dict[str, set[str]] = {}       # zip folder ("" is the top) -> names used in it

    def _collection(self, cid: int) -> _Collection:
        if cid not in self.collections:
            self.collections[cid] = _Collection(self.conn, self.access, cid)
        return self.collections[cid]

    def _name_in(self, parent: str, name: str) -> str:
        """The full zip path of `name` inside zip folder `parent`, made unique there."""
        return parent + _unique(safe_name(name), self.taken.setdefault(parent, set()))

    def _check_size(self) -> None:
        if self.plan.files > MAX_FILES:
            raise TooMany(f"That's more than {MAX_FILES:,} files for one zip. Download a few folders at a time.")

    def _files(self, rows: list[sqlite3.Row], parent: str) -> None:
        """Add the readable ones of these file rows into zip folder `parent`."""
        self.access.prefetch(rows)                 # SharePoint files: asked about in one go
        for r in rows:
            if self.access.can_read_file(r):
                self.plan.entries.append(Entry(self._name_in(parent, r["display_name"]), r["id"], r["size"] or 0,
                                               _deflate(r)))
        self._check_size()

    def _folder_files(self, cid: int, folder_id: int | None) -> list[sqlite3.Row]:
        if not self.access.can_read(cid, folder_id):
            return []                               # a folder only on the way to one deeper down: no files
        return self.conn.execute(
            f"SELECT {fs._FILE_COLUMNS} {fs._FILE_FROM} WHERE f.collection_id = ? AND COALESCE(f.folder_id, 0) = ? "
            "AND f.deleted_at IS NULL ORDER BY f.display_name COLLATE NOCASE", (cid, folder_id or 0)).fetchall()

    def add_folder(self, cid: int, folder_id: int, name: str, parent: str) -> None:
        """A folder as zip folder parent/name/, with everything in it the person may see and read."""
        coll = self._collection(cid)
        here = self._name_in(parent, name) + "/"
        self.plan.entries.append(Entry(here))      # kept even when empty, so the structure comes out the same
        self._files(self._folder_files(cid, folder_id), here)
        for child in coll.children.get(folder_id, []):
            self.add_folder(cid, child["id"], child["name"], here)

    def add_item(self, item: fs.Item) -> None:
        """One chosen file or folder, at the top of the zip; quietly counted as missing when it's gone, in the
        bin, or not the person's to open (the same answer either way, so nothing leaks)."""
        if item.type == "file":
            row = fs.file_row(self.conn, item.id)
            if (row is None or row["deleted_at"] is not None or not fs.is_live_folder(self.conn, row["folder_id"])
                    or not self.access.can_read_file(row)):
                self.plan.missing += 1
                return
            self._files([row], "")
            return
        row = self.conn.execute("SELECT id, collection_id, name FROM folders WHERE id = ?", (item.id,)).fetchone()
        if row is None or row["collection_id"] not in self.access.reaches \
                or row["id"] not in self._collection(row["collection_id"]).visible:
            self.plan.missing += 1
            return
        self.add_folder(row["collection_id"], row["id"], row["name"], "")

    def add_place(self, cid: int, folder_id: int | None) -> None:
        """Everything in a place (a library's top, or a folder) at the top of the zip, as browse() lists it."""
        listing = fs.browse(self.conn, self.access, cid, folder_id)    # FsError if it isn't theirs to see
        for f in listing["folders"]:
            self.add_folder(cid, f["id"], f["name"], "")
        self._files(listing["files"], "")


def place_name(conn: sqlite3.Connection, cid: int, folder_id: int | None) -> str:
    """A place's own name: the folder's, or the library's ("My files" for an inbox)."""
    if folder_id is not None:
        row = conn.execute("SELECT name FROM folders WHERE id = ?", (folder_id,)).fetchone()
        if row is not None:
            return row["name"]
    coll = conn.execute("SELECT kind, name FROM collections WHERE id = ?", (cid,)).fetchone()
    if coll is None:
        return "Download"
    return permissions.PRIVATE_LABEL if coll["kind"] == "private" else coll["name"]


def plan(conn: sqlite3.Connection, access: fs.Access, items: list[fs.Item] | None = None,
         place: tuple[int, int | None] | None = None) -> Plan:
    """What a zip of these items (or of everything in `place`) holds for this person. The zip is named after
    the one item chosen, or the place, or the place the chosen items are in, or else "Download"."""
    if place is not None:
        planner = _Planner(conn, access, place_name(conn, *place))
        planner.add_place(*place)
        return planner.plan
    items = items or []
    planner = _Planner(conn, access, _items_name(conn, items))
    for item in items:
        planner.add_item(item)
    return planner.plan


def _items_name(conn: sqlite3.Connection, items: list[fs.Item]) -> str:
    """The zip's name for chosen items: the only one's name (without its extension), or the folder they're all
    in, or "Download"."""
    where = set()
    for item in items:
        if item.type == "file":
            row = conn.execute("SELECT display_name AS name, collection_id, folder_id AS parent FROM files "
                               "WHERE id = ?", (item.id,)).fetchone()
        else:
            row = conn.execute("SELECT name, collection_id, parent_id AS parent FROM folders WHERE id = ?",
                               (item.id,)).fetchone()
        if row is None:
            continue
        if len(items) == 1:
            stem = row["name"].rpartition(".")[0] if item.type == "file" else ""
            return stem or row["name"]
        where.add((row["collection_id"], row["parent"]))
    if len(where) == 1:
        return place_name(conn, *where.pop())
    return "Download"


# ------------------------------------------------------------------- zipping
def source_paths(conn: sqlite3.Connection, settings, plan_: Plan) -> dict[int, Path | None]:
    """Where each file's bytes are (the share, or blob storage), worked out while the database is at hand;
    None when the share can't say."""
    out: dict[int, Path | None] = {}
    for e in plan_.entries:
        if e.file_id is not None:
            try:
                out[e.file_id] = store.content_path(conn, settings, e.file_id)
            except (share.ShareError, OSError, TypeError):   # TypeError: its blob row is gone
                out[e.file_id] = None
    return out


class _Pipe:
    """A write-only file for ZipFile that keeps what was written until stream() hands it on. It has no tell()
    or seek(), so ZipFile writes each file's sizes after its data (a data descriptor) and never goes back."""

    def __init__(self):
        self.parts: list[bytes] = []
        self.size = 0

    def write(self, data) -> int:
        self.parts.append(bytes(data))
        self.size += len(data)
        return len(data)

    def flush(self) -> None:
        pass

    def take(self) -> bytes:
        out = b"".join(self.parts)
        self.parts, self.size = [], 0
        return out


def stream(plan_: Plan, paths: dict[int, Path | None]) -> Iterator[bytes]:
    """The zip, piece by piece. A file that can't be read now (moved, or the share went away) is left out
    and named in NOT_INCLUDED at the end, rather than breaking a download that's already halfway."""
    pipe = _Pipe()
    skipped: list[str] = []
    with zipfile.ZipFile(pipe, "w", allowZip64=True, strict_timestamps=False) as zf:
        for e in plan_.entries:
            if e.file_id is None:                                   # a folder: an empty entry ending in '/'
                info = zipfile.ZipInfo(e.arcname)
                info.external_attr = (0o40755 << 16) | 0x10            # a directory, for unzip and for Windows
                zf.writestr(info, b"")
                continue
            path = paths.get(e.file_id)
            try:
                src = open(path, "rb") if path is not None else None
            except OSError:
                src = None
            if src is None:
                skipped.append(e.arcname)
                continue
            with src:
                try:
                    info = zipfile.ZipInfo.from_file(path, e.arcname, strict_timestamps=False)   # its date and size
                    info.compress_type = zipfile.ZIP_DEFLATED if e.deflate else zipfile.ZIP_STORED
                    with zf.open(info, "w") as dst:                    # Zip64 when the size needs it
                        while chunk := src.read(CHUNK):
                            dst.write(chunk)
                            if pipe.size >= SEND_AT:
                                yield pipe.take()
                except OSError:                                        # the share went away mid-file
                    skipped.append(e.arcname)                          # what's in the zip of it stays cut short
            if pipe.size:
                yield pipe.take()
        if skipped:
            zf.writestr(NOT_INCLUDED, "These couldn't be read when the zip was made, so they aren't in it (or are "
                        "cut short). Try downloading them again:\r\n\r\n" + "\r\n".join(skipped) + "\r\n")
    yield pipe.take()                                                  # the zip's table of contents
