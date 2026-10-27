"""What the Media page shows, for one person: the catalog filtered to the files they may read.

Reading follows the share's permissions on each folder (fs.Access), exactly as in Files, so a
Movies subfolder closed to someone in Windows is closed here too. Everything returns plain dicts
for the JSON API; art is referred to by URL (/media/art/<id>), files by /media/files/<id>/stream.
"""

from __future__ import annotations

import sqlite3
from collections import OrderedDict

from mnemerynne import fs, media

_COLUMNS = ("mi.file_id, mi.section, mi.media_type, mi.title, mi.sort_title, mi.year, mi.duration_s, mi.width, "
            "mi.height, mi.container, mi.video_codec, mi.audio_codec, mi.artist, mi.album_artist, mi.album, "
            "mi.album_key, mi.track_no, mi.disc_no, mi.genre, mi.taken_at, mi.camera, mi.playback, mi.artwork_id, "
            "mi.fanart_id, mi.overview, mi.tmdb_id, f.collection_id, f.folder_id, f.display_name, f.created_at")
RECENT = 24


class View:
    """The Media library as one person sees it."""

    def __init__(self, conn: sqlite3.Connection, user_id: int) -> None:
        self.conn = conn
        self.user_id = user_id
        self.lib = media.library(conn)
        self.access = fs.Access.of(conn, user_id)

    def readable(self, row) -> bool:
        return (self.lib is not None and row["collection_id"] == self.lib["id"]
                and self.access.can_read(row["collection_id"], row["folder_id"]))

    def rows(self, where: str = "", params: tuple = (), order: str = "mi.sort_title", limit: int | None = None):
        if self.lib is None:
            return []
        sql = (f"SELECT {_COLUMNS} FROM media_items mi JOIN files f ON f.id = mi.file_id "
               f"WHERE f.collection_id = ? AND f.deleted_at IS NULL AND mi.error IS NULL"
               + (f" AND {where}" if where else "") + f" ORDER BY {order}")
        out = []
        for row in self.conn.execute(sql, (self.lib["id"], *params)):
            if self.readable(row):
                out.append(row)
                if limit is not None and len(out) >= limit:
                    break
        return out

    def progress(self, file_ids) -> dict[int, dict]:
        ids = list(file_ids)
        if not ids:
            return {}
        marks = ",".join("?" * len(ids))
        return {r["file_id"]: {"position": r["position_s"], "duration": r["duration_s"]} for r in self.conn.execute(
            f"SELECT file_id, position_s, duration_s FROM media_progress WHERE user_id = ? AND file_id IN ({marks})",
            (self.user_id, *ids))}


def art_url(artwork_id: int | None) -> str | None:
    return f"/media/art/{artwork_id}" if artwork_id else None


def item_json(row, progress: dict | None = None) -> dict:
    out = {"id": row["file_id"], "section": row["section"], "type": row["media_type"], "title": row["title"],
           "year": row["year"], "duration": row["duration_s"], "width": row["width"], "height": row["height"],
           "playback": row["playback"], "art": art_url(row["artwork_id"]), "fanart": art_url(row["fanart_id"]),
           "stream": f"/media/files/{row['file_id']}/stream", "name": row["display_name"]}
    if row["section"] == "music":
        out.update(artist=row["artist"], album_artist=row["album_artist"] or row["artist"], album=row["album"],
                   album_key=row["album_key"], track=row["track_no"], disc=row["disc_no"], genre=row["genre"])
    elif row["section"] == "movies":
        out.update(overview=row["overview"], genre=row["genre"], tmdb_id=row["tmdb_id"],
                   video_codec=row["video_codec"], audio_codec=row["audio_codec"], container=row["container"])
    else:
        out.update(taken_at=row["taken_at"], camera=row["camera"], folder_id=row["folder_id"])
    if progress:
        out["progress"] = progress
    return out


# ----------------------------------------------------------------- sections
def movies(view: View, q: str = "", sort: str = "title") -> list[dict]:
    order = {"title": "mi.sort_title", "year": "mi.year DESC, mi.sort_title",
             "added": "f.created_at DESC, mi.file_id DESC"}.get(sort, "mi.sort_title")
    where, params = "mi.section = 'movies'", ()
    if q.strip():
        where += " AND (mi.title LIKE ? ESCAPE '\\' OR mi.overview LIKE ? ESCAPE '\\')"
        like = "%" + q.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        params = (like, like)
    rows = view.rows(where, params, order)
    prog = view.progress(r["file_id"] for r in rows)
    return [item_json(r, prog.get(r["file_id"])) for r in rows]


def item(view: View, file_id: int) -> dict | None:
    rows = view.rows("mi.file_id = ?", (file_id,))
    if not rows:
        return None
    return item_json(rows[0], view.progress([file_id]).get(file_id))


def albums(view: View, artist: str = "") -> list[dict]:
    """Albums, newest tracks' art first; an artist's only, given `artist` (album artist, any case)."""
    groups: OrderedDict[str, dict] = OrderedDict()
    for r in view.rows("mi.section = 'music'", (), "lower(mi.album), mi.disc_no, mi.track_no, mi.sort_title"):
        who = r["album_artist"] or r["artist"]
        if artist and who.lower() != artist.lower():
            continue
        g = groups.get(r["album_key"])
        if g is None:
            g = groups[r["album_key"]] = {"key": r["album_key"], "album": r["album"] or "Unknown album",
                                          "artist": who or "Unknown artist", "year": r["year"],
                                          "art": art_url(r["artwork_id"]), "tracks": 0, "duration": 0.0}
        g["tracks"] += 1
        g["duration"] += r["duration_s"] or 0
        g["year"] = g["year"] or r["year"]
        g["art"] = g["art"] or art_url(r["artwork_id"])
    return list(groups.values())


def album(view: View, key: str) -> dict | None:
    rows = view.rows("mi.section = 'music' AND mi.album_key = ?", (key,),
                     "mi.disc_no, mi.track_no, mi.sort_title")
    if not rows:
        return None
    first = rows[0]
    return {"key": key, "album": first["album"] or "Unknown album",
            "artist": first["album_artist"] or first["artist"] or "Unknown artist",
            "year": next((r["year"] for r in rows if r["year"]), None),
            "art": next((art_url(r["artwork_id"]) for r in rows if r["artwork_id"]), None),
            "tracks": [item_json(r) for r in rows]}


def artists(view: View) -> list[dict]:
    seen: dict[str, dict] = {}
    for a in albums(view):
        k = a["artist"].lower()
        entry = seen.setdefault(k, {"name": a["artist"], "albums": 0, "tracks": 0, "art": a["art"]})
        entry["albums"] += 1
        entry["tracks"] += a["tracks"]
        entry["art"] = entry["art"] or a["art"]
    return sorted(seen.values(), key=lambda a: a["name"].lower())


def pictures(view: View, folder_id: int | None) -> dict | None:
    """One folder of Pictures: its subfolders (with a cover) and its pictures and clips.
    folder_id None: the Pictures folder itself. None when it isn't in Pictures or isn't readable."""
    if view.lib is None:
        return None
    sections = media.folder_sections(view.conn, view.lib["id"])
    root = next((fid for fid, s in media.section_folders(view.conn, view.lib["id"]).items() if s == "pictures"), None)
    if root is None:
        return {"folder": None, "path": [], "folders": [], "items": []}
    folder_id = folder_id or root
    if sections.get(folder_id) != "pictures" or not view.access.can_read(view.lib["id"], folder_id):
        return None
    subs = []
    for f in view.conn.execute("SELECT id, name FROM folders WHERE parent_id = ? AND deleted_at IS NULL "
                               "ORDER BY name COLLATE NOCASE", (folder_id,)):
        if not view.access.can_read(view.lib["id"], f["id"]):
            continue
        cover = view.conn.execute(
            "SELECT mi.artwork_id FROM media_items mi JOIN files fl ON fl.id = mi.file_id "
            "WHERE fl.folder_id = ? AND fl.deleted_at IS NULL AND mi.artwork_id IS NOT NULL "
            "ORDER BY mi.taken_at, mi.sort_title LIMIT 1", (f["id"],)).fetchone()
        subs.append({"id": f["id"], "name": f["name"], "art": art_url(cover["artwork_id"] if cover else None)})
    items = [item_json(r) for r in view.rows("mi.section = 'pictures' AND f.folder_id = ?", (folder_id,),
                                             "COALESCE(mi.taken_at, f.created_at), mi.sort_title")]
    path = [p for p in fs.folder_path(view.conn, folder_id) if sections.get(p["id"]) == "pictures"]
    return {"folder": folder_id, "root": root, "path": path, "folders": subs, "items": items}


def home(view: View) -> dict:
    """Continue watching, then what was added lately in each section."""
    conn = view.conn
    cont = []
    for p in conn.execute("SELECT file_id, position_s, duration_s FROM media_progress WHERE user_id = ? "
                          "ORDER BY updated_at DESC LIMIT 40", (view.user_id,)):
        if p["duration_s"] and not (30 <= p["position_s"] <= p["duration_s"] * 0.95):
            continue
        rows = view.rows("mi.file_id = ? AND mi.section = 'movies'", (p["file_id"],))
        if rows:
            cont.append(item_json(rows[0], {"position": p["position_s"], "duration": p["duration_s"]}))
        if len(cont) >= 12:
            break
    recent_order = "f.created_at DESC, mi.file_id DESC"
    recent_movies = [item_json(r) for r in view.rows("mi.section = 'movies'", (), recent_order, RECENT)]
    recent_albums, seen = [], set()
    for r in view.rows("mi.section = 'music'", (), recent_order, RECENT * 12):
        if r["album_key"] in seen:
            continue
        seen.add(r["album_key"])
        recent_albums.append({"key": r["album_key"], "album": r["album"] or "Unknown album",
                              "artist": r["album_artist"] or r["artist"] or "Unknown artist",
                              "art": art_url(r["artwork_id"]), "year": r["year"]})
        if len(recent_albums) >= RECENT:
            break
    recent_pictures = [item_json(r) for r in view.rows("mi.section = 'pictures'", (), recent_order, RECENT)]
    return {"continue": cont, "movies": recent_movies, "albums": recent_albums, "pictures": recent_pictures}


def artwork_readable(view: View, artwork_id: int) -> bool:
    """Art is only served to someone who can read a file it belongs to."""
    if view.lib is None:
        return False
    for row in view.conn.execute(
            "SELECT f.collection_id, f.folder_id FROM media_items mi JOIN files f ON f.id = mi.file_id "
            "WHERE (mi.artwork_id = ? OR mi.fanart_id = ?) AND f.deleted_at IS NULL", (artwork_id, artwork_id)):
        if view.readable(row):
            return True
    return False


def save_progress(conn: sqlite3.Connection, user_id: int, file_id: int, position: float,
                  duration: float | None) -> None:
    conn.execute("INSERT INTO media_progress(user_id, file_id, position_s, duration_s, updated_at) "
                 "VALUES (?, ?, ?, ?, datetime('now')) ON CONFLICT(user_id, file_id) DO UPDATE SET "
                 "position_s = excluded.position_s, duration_s = COALESCE(excluded.duration_s, duration_s), "
                 "updated_at = excluded.updated_at", (user_id, file_id, max(position, 0.0), duration))
