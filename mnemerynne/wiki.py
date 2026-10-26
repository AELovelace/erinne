"""The organisation wiki: a library of Markdown pages, read on the Wiki tab and
edited in the browser (web/wiki.py, web/editor.py).

Which library: the one an admin picked on Access → Settings (meta key
wiki_library), otherwise the library called "Wiki"; on the share that is a
folder named Wiki at its root, which the share scan turns into a library. Who
can read and change pages follows the library's own access (fs.Access), so on
the share the Windows permissions decide. Links between pages are ordinary
relative links (`Guides/intro.md`, `../index.md#section`); they are resolved to
the pages they point at when a page is shown.
"""

from __future__ import annotations

import posixpath
import sqlite3
from typing import Callable
from urllib.parse import unquote

from mnemerynne import fs, permissions

WIKI_LIBRARY_KEY = "wiki_library"
DEFAULT_NAME = "Wiki"
PAGE_SUFFIXES = (".md", ".markdown")
_INLINE_IMAGES = {"image/png", "image/jpeg", "image/gif", "image/webp"}


# ------------------------------------------------------------------ library
def setting(conn: sqlite3.Connection) -> int | None:
    """The library id an admin picked, or None for the name rule."""
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (WIKI_LIBRARY_KEY,)).fetchone()
    return int(row["value"]) if row and str(row["value"]).isdigit() else None


def library_row(conn: sqlite3.Connection) -> sqlite3.Row | None:
    chosen = setting(conn)
    if chosen is not None:
        row = conn.execute("SELECT * FROM collections WHERE id = ? AND kind = 'library'", (chosen,)).fetchone()
        if row is not None:
            return row
    return permissions.get_library(conn, DEFAULT_NAME)


def set_library(conn: sqlite3.Connection, library_id: int | None) -> None:
    """Pick a library (None: back to the one called Wiki)."""
    if library_id is None:
        conn.execute("DELETE FROM meta WHERE key = ?", (WIKI_LIBRARY_KEY,))
        return
    if conn.execute("SELECT 1 FROM collections WHERE id = ? AND kind = 'library'", (library_id,)).fetchone() is None:
        raise ValueError("That isn't a library.")
    conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)", (WIKI_LIBRARY_KEY, str(library_id)))


# -------------------------------------------------------------------- pages
def is_page(name: str) -> bool:
    return name.lower().endswith(PAGE_SUFFIXES)


def title_of(name: str) -> str:
    stem = name.rsplit(".", 1)[0] if is_page(name) else name
    return stem


def _live_files(conn: sqlite3.Connection, access: fs.Access, collection_id: int) -> list[sqlite3.Row]:
    """Every live file in the library the person may read, by name."""
    live = fs.live_folder_ids(conn, collection_id)
    rows = conn.execute(f"SELECT {fs._FILE_COLUMNS} {fs._FILE_FROM} WHERE f.collection_id = ? AND f.deleted_at IS NULL "
                        f"ORDER BY f.display_name COLLATE NOCASE", (collection_id,)).fetchall()
    return [r for r in rows if (r["folder_id"] is None or r["folder_id"] in live) and access.can_read_file(r)]


def tree(conn: sqlite3.Connection, access: fs.Access, collection_id: int) -> dict:
    """Folders and pages nested as on disk, with what the person may change."""
    by_parent: dict[int | None, list[dict]] = {}
    for f in fs.folder_tree(conn, access, collection_id):
        by_parent.setdefault(f["parent_id"], []).append(f)
    pages: dict[int | None, list[sqlite3.Row]] = {}
    for r in _live_files(conn, access, collection_id):
        if is_page(r["display_name"]):
            pages.setdefault(r["folder_id"], []).append(r)
    seen: set[int] = set()

    def branch(parent_id: int | None) -> dict:
        folders = []
        for f in by_parent.get(parent_id, []):
            if f["id"] in seen:
                continue
            seen.add(f["id"])
            folders.append({"id": f["id"], "name": f["name"], "can_write": f["can_write"], **branch(f["id"])})
        return {"folders": folders,
                "files": [{"id": r["id"], "name": r["display_name"], "title": title_of(r["display_name"])}
                          for r in pages.get(parent_id, [])]}

    return {"can_write": access.can_write(collection_id, None), **branch(None)}


def page_path(conn: sqlite3.Connection, row: sqlite3.Row) -> str:
    """'Guides/intro.md': the page's place in the library, as a link would name it."""
    return "/".join([p["name"] for p in fs.folder_path(conn, row["folder_id"])] + [row["display_name"]])


def rewriter(conn: sqlite3.Connection, access: fs.Access, collection_id: int,
             current: sqlite3.Row) -> Callable[[str], str]:
    """Relative links from `current` to other pages become /wiki/{id} (keeping #anchors); to other
    files in the library, the file's inline view (images) or download. Anything else stays as written."""
    targets: dict[str, tuple[str, int]] = {}
    for r in _live_files(conn, access, collection_id):
        kind = "page" if is_page(r["display_name"]) else ("image" if r["mime"] in _INLINE_IMAGES else "file")
        targets[page_path(conn, r).lower()] = (kind, r["id"])
    current_dir = posixpath.dirname(page_path(conn, current))

    def rewrite(url: str) -> str:
        url = url.strip()
        if not url or url.startswith(("#", "/", "mailto:")) or "://" in url:
            return url
        target, _, fragment = url.partition("#")
        full = posixpath.normpath(posixpath.join(current_dir, unquote(target))) if target else ""
        found = targets.get(full.lower())
        if found is None or full.startswith("../"):
            return url
        kind, file_id = found
        if kind == "page":
            return f"/wiki/{file_id}" + (f"#{fragment}" if fragment else "")
        return f"/files/{file_id}/inline" if kind == "image" else f"/files/{file_id}/raw"

    return rewrite
