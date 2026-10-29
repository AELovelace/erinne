"""Online metadata, only while an admin has it on (Settings → Entertainment → Media → Look up artwork online):
films and TV shows on TMDB (needs an API key; poster, backdrop, overview; for a show's episodes, their
names and overviews, one request a season), albums on MusicBrainz and their covers on the Cover Art
Archive (no key; MusicBrainz allows one request a second).

Only what the file name or tags say goes out (a film's title and year, a show's folder name, a season
number, an album and its artist), never file contents or who is asking. Art found here never replaces art that came with the files;
it replaces a frame grabbed from the film. Whatever the answer, the item is marked looked up
(scraped_at), so nothing is asked twice until an admin asks for it again.
"""

from __future__ import annotations

import logging
import sqlite3
import time

import httpx

from mnemerynne import media
from mnemerynne.config import Settings
from mnemerynne.db import transaction
from mnemerynne.media import artwork, probe

log = logging.getLogger("mnemerynne.media")

TMDB_API = "https://api.themoviedb.org/3"
TMDB_IMAGES = "https://image.tmdb.org/t/p"
MUSICBRAINZ_API = "https://musicbrainz.org/ws/2"
COVER_ART = "https://coverartarchive.org"
MB_MIN_SCORE = 90
PER_PASS = 25

# Tests replace this with an httpx.MockTransport.
TRANSPORT: httpx.BaseTransport | None = None
_last_mb = 0.0


def _client(settings: Settings) -> httpx.Client:
    contact = settings.public_url or "https://github.com/AELovelace/mnemerynne"
    return httpx.Client(transport=TRANSPORT, timeout=15.0, follow_redirects=True,
                        headers={"User-Agent": f"Mnemerynne/0.1 ( {contact} )", "Accept": "application/json"})


def _mb_wait() -> None:
    """MusicBrainz: at most one request a second from this server."""
    global _last_mb
    wait = 1.05 - (time.monotonic() - _last_mb)
    if wait > 0 and TRANSPORT is None:
        time.sleep(wait)
    _last_mb = time.monotonic()


def _image(client: httpx.Client, url: str) -> bytes | None:
    try:
        resp = client.get(url, headers={"Accept": "image/*"})
    except httpx.HTTPError as exc:
        log.info("Media: no image from %s: %s", url, exc)
        return None
    return resp.content if resp.status_code == 200 and resp.content else None


# ---------------------------------------------------------------------- TMDB
def _tmdb_auth(key: str) -> tuple[dict, dict]:
    """(params, headers): a v3 key goes in the query, a v4 read token in the header."""
    if len(key) == 32 and all(c in "0123456789abcdef" for c in key.lower()):
        return {"api_key": key}, {}
    return {}, {"Authorization": f"Bearer {key}"}


def _tmdb_get(client: httpx.Client, key: str, path: str, query: dict | None = None) -> httpx.Response:
    params, headers = _tmdb_auth(key)
    resp = client.get(f"{TMDB_API}{path}", params={**params, **(query or {})}, headers=headers)
    if resp.status_code == 401:
        raise PermissionError("TMDB refused the API key.")
    return resp


def _tmdb_search(client: httpx.Client, key: str, kind: str, title: str, year: int | None,
                 year_param: str) -> dict | None:
    """TMDB's best match for a film ('movie') or show ('tv'); without one for the year, any year's."""
    query = {"query": title, "include_adult": "false"}
    if year:
        query[year_param] = str(year)
    resp = _tmdb_get(client, key, f"/search/{kind}", query)
    resp.raise_for_status()
    results = resp.json().get("results") or []
    if not results and year:
        return _tmdb_search(client, key, kind, title, None, year_param)
    return results[0] if results else None


def tmdb_movie(client: httpx.Client, key: str, title: str, year: int | None) -> dict | None:
    return _tmdb_search(client, key, "movie", title, year, "year")


def tmdb_tv(client: httpx.Client, key: str, name: str, year: int | None) -> dict | None:
    return _tmdb_search(client, key, "tv", name, year, "first_air_date_year")


def tmdb_season(client: httpx.Client, key: str, tv_id: int, season: int) -> dict[int, dict]:
    """Episode number -> TMDB's episode (id, name, overview) in one season of a show; {} if TMDB has none."""
    resp = _tmdb_get(client, key, f"/tv/{tv_id}/season/{season}")
    if resp.status_code == 404:
        return {}
    resp.raise_for_status()
    return {e["episode_number"]: e for e in resp.json().get("episodes") or [] if e.get("episode_number") is not None}


def _scrape_movie(conn: sqlite3.Connection, settings: Settings, client: httpx.Client, key: str, row) -> None:
    found = tmdb_movie(client, key, row["title"], row["year"])
    poster = fanart = None
    if found:
        replace = artwork.source_of(conn, row["artwork_id"]) in (None, "frame")
        if replace and found.get("poster_path"):
            poster = artwork.prepare(_image(client, f"{TMDB_IMAGES}/w500{found['poster_path']}"), "poster", "tmdb")
        if row["fanart_id"] is None and found.get("backdrop_path"):
            fanart = artwork.prepare(_image(client, f"{TMDB_IMAGES}/w1280{found['backdrop_path']}"), "fanart", "tmdb")
    with transaction(conn):
        if found:
            year = int(found["release_date"][:4]) if (found.get("release_date") or "")[:4].isdigit() else None
            conn.execute("UPDATE media_items SET tmdb_id = ?, overview = ?, year = COALESCE(year, ?), "
                         "artwork_id = COALESCE(?, artwork_id), fanart_id = COALESCE(?, fanart_id) WHERE file_id = ?",
                         (found.get("id"), found.get("overview") or "", year, artwork.store(conn, settings, poster),
                          artwork.store(conn, settings, fanart), row["file_id"]))
        conn.execute("UPDATE media_items SET scraped_at = datetime('now') WHERE file_id = ?", (row["file_id"],))


def _year(date: str | None) -> int | None:
    return int(date[:4]) if (date or "")[:4].isdigit() else None


def _scrape_tv_show(conn: sqlite3.Connection, settings: Settings, client: httpx.Client, key: str, row) -> None:
    """One show by its folder's name ('Doctor Who (2005)' searches 'Doctor Who' from 2005): its overview and
    year, and a poster and backdrop when its folder has none of its own."""
    name, year = probe.movie_name(row["name"])
    found = tmdb_tv(client, key, name, year)
    poster = fanart = None
    if found:
        if artwork.source_of(conn, row["artwork_id"]) is None and found.get("poster_path"):
            poster = artwork.prepare(_image(client, f"{TMDB_IMAGES}/w500{found['poster_path']}"), "poster", "tmdb")
        if row["fanart_id"] is None and found.get("backdrop_path"):
            fanart = artwork.prepare(_image(client, f"{TMDB_IMAGES}/w1280{found['backdrop_path']}"), "fanart", "tmdb")
    with transaction(conn):
        if found:
            conn.execute("UPDATE media_tv_shows SET tmdb_id = ?, overview = ?, year = ?, "
                         "artwork_id = COALESCE(artwork_id, ?), fanart_id = COALESCE(fanart_id, ?) WHERE folder_id = ?",
                         (found.get("id"), found.get("overview") or "", _year(found.get("first_air_date")),
                          artwork.store(conn, settings, poster), artwork.store(conn, settings, fanart),
                          row["folder_id"]))
        conn.execute("UPDATE media_tv_shows SET scraped_at = datetime('now') WHERE folder_id = ?", (row["folder_id"],))


def _scrape_season(conn: sqlite3.Connection, client: httpx.Client | None, key: str, tv_id: int | None,
                   season: int | None, rows: list) -> bool:
    """One season's episodes: their overviews, and TMDB's name for those whose file gave none ('Episode 3').
    Episodes TMDB can't place (no show found, no season or episode number) are just marked looked up.
    True if TMDB was asked."""
    asked = tv_id is not None and season is not None and any(r["episode_no"] is not None for r in rows)
    found = tmdb_season(client, key, tv_id, season) if asked else {}
    with transaction(conn):
        for r in rows:
            e = found.get(r["episode_no"]) if r["episode_no"] is not None else None
            if e:
                fallback = r["title"] == f"Episode {r['episode_no']}"   # the name the file didn't give
                title = (e.get("name") or r["title"]) if fallback else r["title"]
                conn.execute("UPDATE media_items SET tmdb_id = ?, overview = ?, title = ?, sort_title = ? "
                             "WHERE file_id = ?", (e.get("id"), e.get("overview") or "", title,
                                                   probe.sort_title(title), r["file_id"]))
            conn.execute("UPDATE media_items SET scraped_at = datetime('now') WHERE file_id = ?", (r["file_id"],))
    return asked


def _scrape_tv(conn: sqlite3.Connection, settings: Settings, client: httpx.Client, key: str, deadline: float) -> int:
    """Shows not looked up yet, then the episodes of shows that were, a season at a time. Returns how many
    shows and seasons were asked about."""
    done = 0
    for row in conn.execute("SELECT t.folder_id, f.name, t.artwork_id, t.fanart_id FROM media_tv_shows t "
                            "JOIN folders f ON f.id = t.folder_id WHERE t.scraped_at IS NULL AND f.deleted_at IS NULL "
                            "ORDER BY t.folder_id LIMIT ?", (PER_PASS,)).fetchall():
        if time.monotonic() > deadline:
            return done
        _scrape_tv_show(conn, settings, client, key, row)
        done += 1
    lib = media.library(conn)
    if lib is None:
        return done
    shows = media.tv_show_folders(conn, lib["id"])
    looked_up = {r["folder_id"]: r["tmdb_id"] for r in conn.execute(
        "SELECT folder_id, tmdb_id FROM media_tv_shows WHERE scraped_at IS NOT NULL")}
    seasons: dict[tuple, list] = {}   # (show folder, season) -> its episodes not looked up yet
    for r in conn.execute("SELECT mi.file_id, mi.title, mi.season_no, mi.episode_no, f.folder_id FROM media_items mi "
                          "JOIN files f ON f.id = mi.file_id WHERE mi.section = 'tv' AND mi.scraped_at IS NULL "
                          "AND mi.error IS NULL AND f.deleted_at IS NULL AND f.collection_id = ?", (lib["id"],)):
        show = shows.get(r["folder_id"])
        if show in looked_up:   # the show first; its episodes wait for it
            seasons.setdefault((show, r["season_no"]), []).append(r)
    asked = 0
    for (show, season), rows in seasons.items():
        if time.monotonic() > deadline or asked >= PER_PASS:
            break
        asked += _scrape_season(conn, client, key, looked_up[show], season, rows)
    return done + asked


# ---------------------------------------------------------------- MusicBrainz
def _lucene(text: str) -> str:
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def mb_release(client: httpx.Client, album: str, artist: str) -> str | None:
    """The best-matching release's MBID, or None below MB_MIN_SCORE."""
    query = f"release:{_lucene(album)}" + (f" AND artist:{_lucene(artist)}" if artist else "")
    _mb_wait()
    resp = client.get(f"{MUSICBRAINZ_API}/release/", params={"query": query, "fmt": "json", "limit": "3"})
    resp.raise_for_status()
    for release in resp.json().get("releases") or []:
        if int(release.get("score") or 0) >= MB_MIN_SCORE:
            return release.get("id")
    return None


def _scrape_album(conn: sqlite3.Connection, settings: Settings, client: httpx.Client, row) -> None:
    mbid = mb_release(client, row["album"], row["album_artist"])
    cover = None
    if mbid and artwork.source_of(conn, row["artwork_id"]) is None:
        cover = artwork.prepare(_image(client, f"{COVER_ART}/release/{mbid}/front-500"), "cover", "caa")
    with transaction(conn):
        art = artwork.store(conn, settings, cover)
        conn.execute("UPDATE media_albums SET mbid = COALESCE(?, mbid), artwork_id = COALESCE(artwork_id, ?), "
                     "scraped_at = datetime('now') WHERE album_key = ?", (mbid, art, row["album_key"]))
        if art is not None or mbid:
            current = conn.execute("SELECT artwork_id FROM media_albums WHERE album_key = ?",
                                   (row["album_key"],)).fetchone()["artwork_id"]
            conn.execute("UPDATE media_items SET artwork_id = COALESCE(artwork_id, ?), mbid = COALESCE(mbid, ?) "
                         "WHERE section = 'music' AND album_key = ?", (current, mbid, row["album_key"]))


# ----------------------------------------------------------------------- run
def run(conn: sqlite3.Connection, settings: Settings, cfg: dict, deadline: float) -> int:
    """Look up what hasn't been looked up, until the deadline. Returns how many were."""
    done = 0
    key = media.tmdb_key(settings, cfg)
    with _client(settings) as client:
        if key:
            for row in conn.execute("SELECT file_id, title, year, artwork_id, fanart_id FROM media_items "
                                    "WHERE section = 'movies' AND scraped_at IS NULL AND error IS NULL "
                                    "ORDER BY file_id LIMIT ?", (PER_PASS,)).fetchall():
                if time.monotonic() > deadline:
                    return done
                try:
                    _scrape_movie(conn, settings, client, key, row)
                except PermissionError as exc:
                    log.warning("Media: %s", exc)
                    break
                except (httpx.HTTPError, ValueError) as exc:
                    log.info("Media: TMDB lookup for %s failed: %s", row["title"], exc)
                    break   # try again next pass
                done += 1
            if time.monotonic() <= deadline:
                try:
                    done += _scrape_tv(conn, settings, client, key, deadline)
                except PermissionError as exc:
                    log.warning("Media: %s", exc)
                except (httpx.HTTPError, ValueError) as exc:
                    log.info("Media: TMDB lookup for a TV show failed: %s", exc)   # the rest wait for next pass
        albums = conn.execute(
            "SELECT a.album_key, a.artwork_id, min(mi.album) AS album, "
            "min(CASE WHEN mi.album_artist != '' THEN mi.album_artist ELSE mi.artist END) AS album_artist "
            "FROM media_albums a JOIN media_items mi ON mi.section = 'music' AND mi.album_key = a.album_key "
            "WHERE a.scraped_at IS NULL AND mi.album != '' GROUP BY a.album_key LIMIT ?", (PER_PASS,)).fetchall()
        for row in albums:
            if time.monotonic() > deadline:
                break
            try:
                _scrape_album(conn, settings, client, row)
            except (httpx.HTTPError, ValueError) as exc:
                log.info("Media: MusicBrainz lookup for %s failed: %s", row["album"], exc)
                break
            done += 1
    return done
