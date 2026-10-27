"""Online metadata, only while an admin has it on (Settings → Media → Look up artwork online):
films on TMDB (needs an API key; poster, backdrop, overview), albums on MusicBrainz and their
covers on the Cover Art Archive (no key; MusicBrainz allows one request a second).

Only what the file name or tags say goes out (a film's title and year, an album and its artist),
never file contents or who is asking. Art found here never replaces art that came with the files;
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
from mnemerynne.media import artwork

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


def tmdb_movie(client: httpx.Client, key: str, title: str, year: int | None) -> dict | None:
    params, headers = _tmdb_auth(key)
    query = {**params, "query": title, "include_adult": "false"}
    if year:
        query["year"] = str(year)
    resp = client.get(f"{TMDB_API}/search/movie", params=query, headers=headers)
    if resp.status_code == 401:
        raise PermissionError("TMDB refused the API key.")
    resp.raise_for_status()
    results = resp.json().get("results") or []
    if not results and year:
        return tmdb_movie(client, key, title, None)
    return results[0] if results else None


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
