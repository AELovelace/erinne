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
            "mi.album_key, mi.track_no, mi.disc_no, mi.season_no, mi.episode_no, mi.genre, mi.taken_at, mi.camera, "
            "mi.playback, mi.artwork_id, mi.fanart_id, mi.overview, mi.tmdb_id, f.collection_id, f.folder_id, "
            "f.display_name, f.created_at")
RECENT = 24
# Watched: from 30 seconds in (less is a peek) to 95% (past that, the credits: finished).
STARTED_S = 30
FINISHED = 0.95


class View:
    """The Media library as one person sees it."""

    def __init__(self, conn: sqlite3.Connection, user_id: int) -> None:
        self.conn = conn
        self.user_id = user_id
        self.lib = media.library(conn)
        self.access = fs.Access.of(conn, user_id)
        self._tv: tuple[dict[int, int], dict[int, str]] | None = None   # folder -> show; show -> its name

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

    def watched(self, file_ids) -> dict[int, sqlite3.Row]:
        """file id -> its progress row (position_s, duration_s, updated_at), for those with one."""
        ids, out = list(file_ids), {}
        for at in range(0, len(ids), 500):   # in batches, under SQLite's limit on parameters
            chunk = ids[at:at + 500]
            out.update({r["file_id"]: r for r in self.conn.execute(
                f"SELECT file_id, position_s, duration_s, updated_at FROM media_progress WHERE user_id = ? "
                f"AND file_id IN ({','.join('?' * len(chunk))})", (self.user_id, *chunk))})
        return out

    def tv_show_of(self, folder_id: int | None) -> dict | None:
        """{id, name} of the TV show a folder is in (None outside one); the map is worked out once per view."""
        if self._tv is None:
            shows = media.tv_show_folders(self.conn, self.lib["id"]) if self.lib is not None else {}
            ids = sorted(set(shows.values()))
            names = {r["id"]: r["name"] for r in self.conn.execute(
                f"SELECT id, name FROM folders WHERE id IN ({','.join('?' * len(ids))})", ids)} if ids else {}
            self._tv = (shows, names)
        show_id = self._tv[0].get(folder_id) if folder_id is not None else None
        return {"id": show_id, "name": self._tv[1].get(show_id, "")} if show_id is not None else None

    def json(self, row, progress: dict | None = None) -> dict:
        """item_json, with the show for a TV episode."""
        return item_json(row, progress, self.tv_show_of(row["folder_id"]) if row["section"] == media.TV_SECTION
                         else None)


def art_url(artwork_id: int | None) -> str | None:
    return f"/media/art/{artwork_id}" if artwork_id else None


def item_json(row, progress: dict | None = None, tv_show: dict | None = None) -> dict:
    """One catalog row for the API; tv_show ({id, name}) is the show a TV episode is in (View.json)."""
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
    elif row["section"] == media.TV_SECTION:
        out.update(season=row["season_no"], episode=row["episode_no"], overview=row["overview"], genre=row["genre"],
                   video_codec=row["video_codec"], audio_codec=row["audio_codec"], container=row["container"],
                   tv_show_id=tv_show["id"] if tv_show else None, tv_show=tv_show["name"] if tv_show else "")
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
    return view.json(rows[0], view.progress([file_id]).get(file_id))


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


# ---------------------------------------------------------------------- TV
def _finished(p) -> bool:
    return bool(p["duration_s"]) and p["position_s"] >= p["duration_s"] * FINISHED


def _in_progress(p) -> bool:
    return p["position_s"] >= STARTED_S and not _finished(p)


def _episode_order(r) -> tuple:
    """Numbered seasons in order, then episodes with no season, then Specials; within one, by episode."""
    season = r["season_no"]
    group = 2 if season == 0 else (1 if season is None else 0)
    return (group, season or 0, r["episode_no"] if r["episode_no"] is not None else 1_000_000, r["sort_title"])


def _season_label(season: int | None) -> str:
    return "Specials" if season == 0 else ("Episodes" if season is None else f"Season {season}")


def _tv_episodes(view: View) -> dict[int, list]:
    """Show folder id -> its episodes this person may read, in watching order."""
    out: dict[int, list] = {}
    for r in view.rows("mi.section = 'tv'"):
        show = view.tv_show_of(r["folder_id"])
        if show is not None:
            out.setdefault(show["id"], []).append(r)
    for rows in out.values():
        rows.sort(key=_episode_order)
    return out


def _tv_queue(episodes: list) -> list:
    """What plays one after another: everything but the Specials."""
    return [r for r in episodes if r["season_no"] != 0]


def _next_up(queue: list, marks: dict) -> tuple | None:
    """(episode row, its progress row or None) to watch next: the one last watched if it isn't done, else
    the one after it; the first when none was. None once the last one is finished (caught up)."""
    if not queue:
        return None
    last = max(((marks[r["file_id"]]["updated_at"], i) for i, r in enumerate(queue) if r["file_id"] in marks),
               default=None)
    if last is None:
        return queue[0], None
    i = last[1]
    mark = marks[queue[i]["file_id"]]
    if not _finished(mark):
        return queue[i], mark if _in_progress(mark) else None   # a peek of under 30 s starts it again
    if i + 1 < len(queue):
        nxt = queue[i + 1]
        return nxt, marks.get(nxt["file_id"]) if nxt["file_id"] in marks and _in_progress(marks[nxt["file_id"]]) else None
    return None


def _progress_of(mark) -> dict | None:
    return {"position": mark["position_s"], "duration": mark["duration_s"]} if mark is not None else None


def _tv_root_readable(view: View) -> int | None:
    if view.lib is None:
        return None
    root = media.tv_root(view.conn, view.lib["id"])
    return root if root is not None and view.access.can_read(view.lib["id"], root) else None


def _show_info(view: View, show_id: int, episodes: list) -> dict:
    """What the show itself has: its poster (its folder's poster.jpg, or one found online; else its first
    episode's still), backdrop, year and overview (online)."""
    row = view.conn.execute("SELECT artwork_id, fanart_id, year, overview FROM media_tv_shows WHERE folder_id = ?",
                            (show_id,)).fetchone()
    art = art_url(row["artwork_id"]) if row and row["artwork_id"] else         next((art_url(r["artwork_id"]) for r in episodes if r["artwork_id"]), None)
    return {"art": art, "fanart": art_url(row["fanart_id"]) if row else None,
            "year": row["year"] if row else None, "overview": row["overview"] if row else ""}


TV_SORTS = ("title", "added", "watched", "year")


def tv_shows(view: View, q: str = "", sort: str = "title") -> list[dict]:
    """The shows in TV this person may open (folders with no episodes yet left out); each with its poster,
    how many episodes and seasons, when one was last added and last watched, and the episode to watch next.
    q: only shows whose name or overview has it (any case). sort: by name ('title'), newest episode first
    ('added'), most recently watched first ('watched') or newest show first ('year')."""
    root = _tv_root_readable(view)
    if root is None:
        return []
    episodes = _tv_episodes(view)
    marks = view.watched(r["file_id"] for rows in episodes.values() for r in rows)
    needle = q.strip().lower()
    out = []
    for f in view.conn.execute("SELECT id, name FROM folders WHERE parent_id = ? AND deleted_at IS NULL "
                               "ORDER BY name COLLATE NOCASE", (root,)):
        rows = episodes.get(f["id"])
        if not rows or not view.access.can_read(view.lib["id"], f["id"]):
            continue
        info = _show_info(view, f["id"], rows)
        if needle and needle not in f["name"].lower() and needle not in info["overview"].lower():
            continue
        nxt = _next_up(_tv_queue(rows), marks)
        seen = [marks[r["file_id"]]["updated_at"] for r in rows if r["file_id"] in marks]
        out.append({"id": f["id"], "name": f["name"], "art": info["art"], "year": info["year"],
                    "episodes": len(rows), "seasons": len({r["season_no"] for r in rows if r["season_no"]}),
                    "added": max(r["created_at"] for r in rows), "watched": max(seen) if seen else None,
                    "next_up": view.json(nxt[0], _progress_of(nxt[1])) if nxt else None})
    if sort == "added":
        out.sort(key=lambda s: s["added"], reverse=True)
    elif sort in ("watched", "year"):   # those never watched, or of no known year, last (still by name)
        out.sort(key=lambda s: (s[sort] is not None, s[sort] or 0), reverse=True)
    return out


def tv_show(view: View, folder_id: int) -> dict | None:
    """One show: its seasons with their episodes, the order they play in (`queue`, file ids) and the
    episode to watch next. None when the folder isn't a show in TV, or isn't readable."""
    root = _tv_root_readable(view)
    folder = view.conn.execute("SELECT id, name, parent_id FROM folders WHERE id = ? AND deleted_at IS NULL",
                               (folder_id,)).fetchone()
    if root is None or folder is None or folder["parent_id"] != root             or not view.access.can_read(view.lib["id"], folder_id):
        return None
    rows = _tv_episodes(view).get(folder_id, [])
    marks = view.watched(r["file_id"] for r in rows)
    seasons: list[dict] = []
    for r in rows:
        if not seasons or seasons[-1]["season"] != r["season_no"]:
            seasons.append({"season": r["season_no"], "label": _season_label(r["season_no"]), "episodes": []})
        seasons[-1]["episodes"].append(view.json(r, _progress_of(marks.get(r["file_id"]))))
    queue = _tv_queue(rows)
    nxt = _next_up(queue, marks)
    return {"id": folder_id, "name": folder["name"], **_show_info(view, folder_id, rows),
            "episodes": len(rows), "seasons": seasons, "queue": [r["file_id"] for r in queue],
            "next_up": view.json(nxt[0], _progress_of(nxt[1])) if nxt else None}


def home(view: View) -> dict:
    """Continue watching (films part-way through; for each show, its next episode), then what was added
    lately in each section."""
    conn = view.conn
    cont, shows_seen, episodes = [], set(), None
    for p in conn.execute("SELECT file_id, position_s, duration_s, updated_at FROM media_progress "
                          "WHERE user_id = ? ORDER BY updated_at DESC LIMIT 40", (view.user_id,)):
        rows = view.rows("mi.file_id = ? AND mi.section IN ('movies', 'tv')", (p["file_id"],))
        if not rows:
            continue
        if rows[0]["section"] == media.TV_SECTION:
            show = view.tv_show_of(rows[0]["folder_id"])
            if show is None or show["id"] in shows_seen:
                continue
            shows_seen.add(show["id"])   # a show's latest episode decides its card: resume it, or the next
            episodes = _tv_episodes(view) if episodes is None else episodes
            queue = _tv_queue(episodes.get(show["id"], []))
            nxt = _next_up(queue, view.watched(r["file_id"] for r in queue))
            if nxt:
                cont.append(view.json(nxt[0], _progress_of(nxt[1])))
        elif not p["duration_s"] or _in_progress(p):
            cont.append(item_json(rows[0], _progress_of(p)))
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
    recent_tv = tv_shows(view, sort="added")[:RECENT]   # newest episodes first
    return {"continue": cont, "movies": recent_movies, "tv": recent_tv, "albums": recent_albums,
            "pictures": recent_pictures}


def artwork_readable(view: View, artwork_id: int) -> bool:
    """Art is only served to someone who can read a file it belongs to."""
    if view.lib is None:
        return False
    for row in view.conn.execute(
            "SELECT f.collection_id, f.folder_id FROM media_items mi JOIN files f ON f.id = mi.file_id "
            "WHERE (mi.artwork_id = ? OR mi.fanart_id = ?) AND f.deleted_at IS NULL", (artwork_id, artwork_id)):
        if view.readable(row):
            return True
    for row in view.conn.execute(   # a TV show's poster or backdrop: to whoever may open the show's folder
            "SELECT f.collection_id, f.id AS folder_id FROM media_tv_shows t JOIN folders f ON f.id = t.folder_id "
            "WHERE (t.artwork_id = ? OR t.fanart_id = ?) AND f.deleted_at IS NULL", (artwork_id, artwork_id)):
        if view.readable(row):
            return True
    return False


def save_progress(conn: sqlite3.Connection, user_id: int, file_id: int, position: float,
                  duration: float | None) -> None:
    conn.execute("INSERT INTO media_progress(user_id, file_id, position_s, duration_s, updated_at) "
                 "VALUES (?, ?, ?, ?, datetime('now')) ON CONFLICT(user_id, file_id) DO UPDATE SET "
                 "position_s = excluded.position_s, duration_s = COALESCE(excluded.duration_s, duration_s), "
                 "updated_at = excluded.updated_at", (user_id, file_id, max(position, 0.0), duration))


def mark_watched(view: View, file_ids, watched: bool) -> int:
    """Mark episodes (or films) watched, as if played to the end, or not watched (their place forgotten),
    for the ones this person may read. Returns how many were."""
    ids = list(dict.fromkeys(file_ids))
    rows = []
    for at in range(0, len(ids), 500):   # in batches, under SQLite's limit on parameters
        chunk = ids[at:at + 500]
        rows += view.rows(f"mi.file_id IN ({','.join('?' * len(chunk))}) AND mi.media_type = 'video'", tuple(chunk))
    for r in rows:
        if watched:   # at the very end; with no known length, 1 of 1 second
            end = r["duration_s"] or 1.0
            save_progress(view.conn, view.user_id, r["file_id"], end, end)
        else:
            view.conn.execute("DELETE FROM media_progress WHERE user_id = ? AND file_id = ?", (view.user_id, r["file_id"]))
    return len(rows)
