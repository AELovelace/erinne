"""The worker's catalog pass: files in Media/Music, Media/Movies, Media/TV and Media/Pictures that are
new or changed since they were last read get probed (probe.py) and their art found (artwork.py),
then, with online metadata on, looked up on TMDB or MusicBrainz (scrape.py). In TV, each folder
directly inside is a show; its episodes get their season and episode from their names.

It runs after each share scan, for at most `budget_s` seconds, so a newly filled Movies folder
never holds up document ingestion: what's left waits for the next pass. Probing and resizing
happen without a lock; each file's rows are written in one short transaction. A file that can't
be read gets a row with its error, and is tried again when it changes.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path

from mnemerynne import media
from mnemerynne.config import Settings
from mnemerynne.db import transaction
from mnemerynne.ingest import store
from mnemerynne.media import artwork, probe

log = logging.getLogger("mnemerynne.media")

STATUS_KEY = "media_status"


@dataclass
class Stats:
    read: int = 0
    failed: int = 0
    scraped: int = 0
    pending: int = 0
    errors: list[str] = field(default_factory=list)


def album_key(album_artist: str, album: str) -> str:
    return f"{album_artist.strip().lower()}\x1f{album.strip().lower()}"


def _pending(conn: sqlite3.Connection, library_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT f.id, f.folder_id, f.display_name, f.disk_mtime_ns FROM files f "
        "LEFT JOIN media_items mi ON mi.file_id = f.id "
        "WHERE f.collection_id = ? AND f.deleted_at IS NULL AND f.trash_path IS NULL "
        "AND (mi.file_id IS NULL OR mi.disk_mtime_ns IS NOT f.disk_mtime_ns) ORDER BY f.id",
        (library_id,)).fetchall()


@dataclass
class _Read:
    """One file probed, its art prepared: what the transaction writes."""
    info: probe.Info
    section: str
    kind: str
    playback: str
    art: artwork.Prepared | None = None
    fanart: artwork.Prepared | None = None
    album_art: artwork.Prepared | None = None
    tv_show_id: int | None = None                  # TV: the show's folder
    tv_show_art: artwork.Prepared | None = None    # TV: the show's poster, when it has none yet


def _read(conn: sqlite3.Connection, settings: Settings, path: Path, section: str, kind: str,
          tv_show: tuple[int, str] | None = None) -> _Read:
    """Probe one file and prepare its art. tv_show: (folder id, name) of the show a TV episode is in."""
    info = probe.read(settings, path, section, kind)
    out = _Read(info, section, kind, probe.decide(kind, info.container, info.video_codec, info.audio_codec))
    if section == "movies":
        poster, fanart = artwork.movie_sidecars(path)
        out.art = artwork.prepare(artwork.read_file(poster), "poster", "local") if poster else None
        out.fanart = artwork.prepare(artwork.read_file(fanart), "fanart", "local") if fanart else None
        if out.art is None and info.duration_s:
            out.art = artwork.prepare(artwork.frame(settings, path, info.duration_s * 0.1), "thumb", "frame")
    elif section == "music":
        key = album_key(info.album_artist or info.artist, info.album)
        known = conn.execute("SELECT artwork_id FROM media_albums WHERE album_key = ?", (key,)).fetchone()
        if not (known and known["artwork_id"]):
            cover = artwork.album_sidecar(path)
            out.album_art = (artwork.prepare(artwork.read_file(cover), "cover", "local") if cover else None) \
                or artwork.prepare(info.cover, "cover", "embedded")
    elif section == media.TV_SECTION and tv_show is not None:
        if info.duration_s:   # the episode's own still, from a tenth of the way in (past any titles)
            out.art = artwork.prepare(artwork.frame(settings, path, info.duration_s * 0.1), "thumb", "frame")
        out.tv_show_id = tv_show[0]
        known = conn.execute("SELECT artwork_id FROM media_tv_shows WHERE folder_id = ?", (tv_show[0],)).fetchone()
        if not (known and known["artwork_id"]):   # the show's poster is looked for until one is found
            poster = artwork.tv_sidecar(path, tv_show[1])
            out.tv_show_art = artwork.prepare(artwork.read_file(poster), "poster", "local") if poster else None
    elif kind == "image":
        out.art = artwork.prepare(artwork.read_file(path), "thumb", "local")
    elif kind == "document":
        out.art = artwork.prepare(artwork.pdf_page(path, 0, 960), "thumb", "local")
    elif info.duration_s is not None:
        out.art = artwork.prepare(artwork.frame(settings, path, min(1.0, info.duration_s / 2)), "thumb", "frame")
    return out


def _write(conn: sqlite3.Connection, settings: Settings, file_id: int, mtime_ns: int | None, r: _Read) -> None:
    info = r.info
    art_id = artwork.store(conn, settings, r.art)
    fanart_id = artwork.store(conn, settings, r.fanart)
    key = None
    if r.section == "music":
        key = album_key(info.album_artist or info.artist, info.album)
        album_art = artwork.store(conn, settings, r.album_art)
        conn.execute("INSERT INTO media_albums(album_key, artwork_id) VALUES (?, ?) ON CONFLICT(album_key) "
                     "DO UPDATE SET artwork_id = COALESCE(media_albums.artwork_id, excluded.artwork_id)",
                     (key, album_art))
        art_id = conn.execute("SELECT artwork_id FROM media_albums WHERE album_key = ?", (key,)).fetchone()[0]
    if r.tv_show_id is not None:   # the show's row, keeping a poster it already has
        conn.execute("INSERT INTO media_tv_shows(folder_id, artwork_id) VALUES (?, ?) ON CONFLICT(folder_id) "
                     "DO UPDATE SET artwork_id = COALESCE(media_tv_shows.artwork_id, excluded.artwork_id)",
                     (r.tv_show_id, artwork.store(conn, settings, r.tv_show_art)))
    conn.execute(
        "INSERT INTO media_items(file_id, section, media_type, title, sort_title, year, duration_s, width, height, "
        "container, video_codec, audio_codec, bitrate, artist, album_artist, album, album_key, track_no, disc_no, "
        "season_no, episode_no, genre, taken_at, camera, playback, artwork_id, fanart_id, pages, disk_mtime_ns, "
        "probed_at, error) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, "
        "datetime('now'), NULL) "
        "ON CONFLICT(file_id) DO UPDATE SET section = excluded.section, media_type = excluded.media_type, "
        "title = excluded.title, sort_title = excluded.sort_title, year = COALESCE(excluded.year, media_items.year), "
        "duration_s = excluded.duration_s, width = excluded.width, height = excluded.height, "
        "container = excluded.container, video_codec = excluded.video_codec, audio_codec = excluded.audio_codec, "
        "bitrate = excluded.bitrate, artist = excluded.artist, album_artist = excluded.album_artist, "
        "album = excluded.album, album_key = excluded.album_key, track_no = excluded.track_no, disc_no = excluded.disc_no, "
        "season_no = excluded.season_no, episode_no = excluded.episode_no, genre = excluded.genre, "
        "taken_at = excluded.taken_at, camera = excluded.camera, playback = excluded.playback, "
        "artwork_id = COALESCE(excluded.artwork_id, media_items.artwork_id), "
        "fanart_id = COALESCE(excluded.fanart_id, media_items.fanart_id), pages = excluded.pages, "
        "disk_mtime_ns = excluded.disk_mtime_ns, probed_at = excluded.probed_at, error = NULL",
        (file_id, r.section, r.kind, info.title, probe.sort_title(info.title), info.year, info.duration_s,
         info.width, info.height, info.container, info.video_codec, info.audio_codec, info.bitrate, info.artist,
         info.album_artist, info.album, key, info.track_no, info.disc_no, info.season_no, info.episode_no,
         info.genre, info.taken_at, info.camera,
         r.playback, art_id, fanart_id, info.pages, mtime_ns))


def _failed(conn: sqlite3.Connection, file_id: int, mtime_ns: int | None, section: str, kind: str, name: str,
            error: str) -> None:
    conn.execute(
        "INSERT INTO media_items(file_id, section, media_type, title, sort_title, disk_mtime_ns, error) "
        "VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT(file_id) DO UPDATE SET disk_mtime_ns = excluded.disk_mtime_ns, "
        "error = excluded.error, probed_at = datetime('now')",
        (file_id, section, kind, Path(name).stem, probe.sort_title(Path(name).stem), mtime_ns, error[:500]))


def run_pass(conn: sqlite3.Connection, settings: Settings, budget_s: float = 20.0) -> Stats:
    """Catalog what's new or changed, for at most budget_s seconds. Does nothing while Media is off."""
    stats = Stats()
    if not media.enabled(conn):
        return stats
    try:
        with transaction(conn):
            media.ensure_section_folders(conn)   # TV, in a Media folder made before there was one
    except (OSError, media.MediaError):
        log.info("Media: couldn't add the new section folders yet", exc_info=True)
    roots = media.catalog_roots(conn)
    if not roots:
        return stats
    deadline = time.monotonic() + budget_s
    for lib, sections, root_section in roots:
        _catalog_library(conn, settings, lib, sections, root_section, deadline, stats)
    cfg = media.load_settings(conn)
    if cfg.get("online_metadata") and time.monotonic() < deadline:
        from mnemerynne.media import scrape
        try:
            stats.scraped = scrape.run(conn, settings, cfg, deadline)
        except Exception:  # never let the internet stop the pass
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            log.exception("Media: online lookups failed")
    _prune_art(conn, settings)
    _record(conn, stats)
    if stats.read or stats.failed or stats.scraped:
        log.info("Media catalog: %d read, %d failed, %d looked up online, %d waiting.",
                 stats.read, stats.failed, stats.scraped, stats.pending)
    return stats


def _catalog_library(conn: sqlite3.Connection, settings: Settings, lib: sqlite3.Row, sections: dict[int, str],
                     root_section: str | None, deadline: float, stats: Stats) -> None:
    """One library's new or changed files, within the deadline (the Signage library's root files count too)."""
    shows: dict[int, int] | None = None   # TV folder -> its show folder, worked out on the first episode
    for row in _pending(conn, lib["id"]):
        section = sections.get(row["folder_id"]) if row["folder_id"] is not None else root_section
        kind = media.media_type(row["display_name"])
        if section is None or kind is None or kind not in media.SECTION_TYPES[section]:
            continue
        tv_show = None
        if section == media.TV_SECTION:
            if shows is None:
                shows = media.tv_show_folders(conn, lib["id"])
            show_id = shows.get(row["folder_id"])
            if show_id is None:
                continue   # right in the TV folder: an episode belongs in its show's folder
            name = conn.execute("SELECT name FROM folders WHERE id = ?", (show_id,)).fetchone()
            tv_show = (show_id, name["name"] if name else "")
        if time.monotonic() > deadline:
            stats.pending += 1
            continue
        try:
            path = store.content_path(conn, settings, row["id"])
            if not path.is_file():
                continue   # the share went away, or the file did: the next scan sorts it out
            read = _read(conn, settings, path, section, kind, tv_show)
        except OSError as exc:
            log.info("Media: couldn't read %s yet: %s", row["display_name"], exc)
            continue
        except Exception as exc:  # one odd file must not stop the pass
            log.exception("Media: cataloging %s failed", row["display_name"])
            with transaction(conn):
                _failed(conn, row["id"], row["disk_mtime_ns"], section, kind, row["display_name"],
                        f"{type(exc).__name__}: {exc}")
            stats.failed += 1
            continue
        with transaction(conn):
            if conn.execute("SELECT disk_mtime_ns FROM files WHERE id = ? AND deleted_at IS NULL",
                            (row["id"],)).fetchone() is None:
                continue
            _write(conn, settings, row["id"], row["disk_mtime_ns"], read)
        stats.read += 1


def _prune_art(conn: sqlite3.Connection, settings: Settings) -> int:
    """Art nothing uses any more (its files were removed from the share, or replaced): rows and files.
    Albums with no tracks left go too, and TV shows with no episodes left (in the current Media folder)."""
    with transaction(conn):
        conn.execute("DELETE FROM media_albums WHERE album_key NOT IN "
                     "(SELECT album_key FROM media_items WHERE album_key IS NOT NULL)")
        _prune_tv_shows(conn)
        unused = conn.execute(
            "SELECT id, rel_path FROM media_artwork a WHERE NOT EXISTS (SELECT 1 FROM media_items mi "
            "WHERE mi.artwork_id = a.id OR mi.fanart_id = a.id) AND NOT EXISTS "
            "(SELECT 1 FROM media_albums m WHERE m.artwork_id = a.id) AND NOT EXISTS "
            "(SELECT 1 FROM media_tv_shows t WHERE t.artwork_id = a.id)").fetchall()
        for row in unused:
            conn.execute("DELETE FROM media_artwork WHERE id = ?", (row["id"],))
    for row in unused:
        artwork.path_of(settings, row["rel_path"]).unlink(missing_ok=True)
    return len(unused)


def _prune_tv_shows(conn: sqlite3.Connection) -> None:
    """Forget the shows whose folder is gone, or is in the Media folder with no episodes left (in the
    caller's transaction). A show in an earlier Media folder keeps its poster, for switching back."""
    conn.execute("DELETE FROM media_tv_shows WHERE folder_id IN (SELECT id FROM folders WHERE deleted_at IS NOT NULL)")
    lib = media.library(conn)
    if lib is None:
        return
    shows = media.tv_show_folders(conn, lib["id"])
    live = {shows.get(r["folder_id"]) for r in conn.execute(
        "SELECT DISTINCT f.folder_id FROM media_items mi JOIN files f ON f.id = mi.file_id "
        "WHERE mi.section = 'tv' AND f.deleted_at IS NULL AND f.collection_id = ?", (lib["id"],))}
    for row in conn.execute("SELECT t.folder_id FROM media_tv_shows t JOIN folders f ON f.id = t.folder_id "
                            "WHERE f.collection_id = ?", (lib["id"],)).fetchall():
        if row["folder_id"] not in live:
            conn.execute("DELETE FROM media_tv_shows WHERE folder_id = ?", (row["folder_id"],))


def _record(conn: sqlite3.Connection, stats: Stats) -> None:
    with transaction(conn):
        conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)", (STATUS_KEY, json.dumps({
            "at": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime()), "read": stats.read, "failed": stats.failed,
            "scraped": stats.scraped, "pending": stats.pending})))


def status(conn: sqlite3.Connection, lib: sqlite3.Row | None) -> dict:
    """The last pass, and what the current Media library holds (an earlier Media folder's catalog is
    kept, but not counted)."""
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (STATUS_KEY,)).fetchone()
    try:
        last = json.loads(row["value"]) if row else None
    except ValueError:
        last = None
    lib_id = lib["id"] if lib is not None else -1
    counts = {r["section"]: r["n"] for r in conn.execute(
        "SELECT mi.section, count(*) AS n FROM media_items mi JOIN files f ON f.id = mi.file_id "
        "WHERE mi.error IS NULL AND f.collection_id = ? AND f.deleted_at IS NULL GROUP BY mi.section", (lib_id,))}
    errors = [dict(r) for r in conn.execute(
        "SELECT mi.file_id, f.display_name AS name, mi.error FROM media_items mi JOIN files f ON f.id = mi.file_id "
        "WHERE mi.error IS NOT NULL AND f.collection_id = ? ORDER BY mi.probed_at DESC LIMIT 20", (lib_id,))]
    return {"last_pass": last, "counts": {s: counts.get(s, 0) for s in media.SECTIONS}, "errors": errors}


def reset(conn: sqlite3.Connection, online: bool = False) -> int:
    """Read everything again on the next pass (and look it up online again, with `online`)."""
    n = conn.execute("UPDATE media_items SET disk_mtime_ns = NULL").rowcount
    if online:
        conn.execute("UPDATE media_items SET scraped_at = NULL")
        conn.execute("UPDATE media_albums SET scraped_at = NULL")
    return n
