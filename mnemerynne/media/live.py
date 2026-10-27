"""Live TV and radio beside the library.

- Streams (media_sources kind 'rtmp' or 'hls') are pulled by the server and repackaged as HLS
  (transcode.Manager.watch_live), so a LAN-only source like the RTMP server's
  http://10.1.1.21/stream/hls/live.m3u8 plays from anywhere and CORS never matters.
- Radio is SGCRelay's station list (Settings → Media → station list, default
  https://sadgirlsclub.wtf/rr/healthz), read the way the DOLL-OS firmware reads it (Radio.ino):
  `stations`, else `routes`; each station's address is `path` (or url, stream, stream_url,
  inputUrl), relative to the list's own folder. Plus any stations an admin added by hand.
  https stations play straight from the relay; plain http ones go through the server, which a page
  served over https couldn't otherwise play.
"""

from __future__ import annotations

import logging
import re
import sqlite3
import time
from urllib.parse import urlsplit

import httpx

from mnemerynne import media

log = logging.getLogger("mnemerynne.media")

KINDS = ("rtmp", "hls", "radio")
DIRECTORY_SECONDS = 30
MAX_STATIONS = 64
_SCHEMES = {"rtmp": ("rtmp://", "rtmps://"), "hls": ("http://", "https://"), "radio": ("http://", "https://")}

# Tests replace this with an httpx.MockTransport.
TRANSPORT: httpx.BaseTransport | None = None
_cache: dict[str, tuple[float, list[dict], str | None]] = {}


class LiveError(ValueError):
    """A refusal; the message is safe to show."""
    status = 422


# ------------------------------------------------------------------ sources
def check_source(kind: str, name: str, url: str) -> tuple[str, str, str]:
    name, url = (name or "").strip(), (url or "").strip()
    if kind not in KINDS:
        raise LiveError("A source is an RTMP stream, an HLS stream or a radio station.")
    if not name or len(name) > 80:
        raise LiveError("Give the source a name (up to 80 characters).")
    if not url.lower().startswith(_SCHEMES[kind]) or len(url) > 1000 or any(c.isspace() for c in url):
        raise LiveError(f"A {kind} source's address starts with {' or '.join(_SCHEMES[kind])}.")
    return kind, name, url


def sources(conn: sqlite3.Connection, *, enabled_only: bool = True) -> list[dict]:
    rows = conn.execute("SELECT id, kind, name, url, enabled, sort FROM media_sources"
                        + (" WHERE enabled = 1" if enabled_only else "") + " ORDER BY sort, name COLLATE NOCASE")
    return [{**dict(r), "enabled": bool(r["enabled"])} for r in rows]


def source(conn: sqlite3.Connection, source_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM media_sources WHERE id = ?", (source_id,)).fetchone()


# -------------------------------------------------------------------- radio
def _base(url: str) -> str:
    """https://host/rr/healthz -> https://host/rr (the firmware's radioDirectoryBaseUrl)."""
    scheme_end = url.find("://")
    slash = url.rfind("/")
    return url[:slash] if scheme_end >= 0 and slash > scheme_end + 2 else url


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40] or "station"


def parse_directory(url: str, doc: dict) -> list[dict]:
    """SGCRelay's /healthz JSON -> [{key, name, url, running, listeners}]."""
    base = _base(url)
    out: list[dict] = []
    for listing in ("stations", "routes"):
        for station in doc.get(listing) or []:
            if not isinstance(station, dict) or len(out) >= MAX_STATIONS:
                continue
            path = next((str(station[k]) for k in ("path", "url", "stream", "stream_url", "inputUrl")
                         if station.get(k)), "")
            if not path:
                continue
            playable = path if path.startswith(("http://", "https://")) else base + ("" if path.startswith("/")
                                                                                     else "/") + path
            name = str(station.get("name") or path)
            out.append({"key": "relay-" + _slug(path), "name": name, "url": playable,
                        "running": bool(station.get("running")), "listeners": int(station.get("clients") or 0),
                        "from": "relay"})
        if out:
            break   # stations is the list; routes only when there are no stations
    return out


def directory(url: str, *, fresh: bool = False) -> tuple[list[dict], str | None]:
    """(stations, problem) from the relay's list, cached DIRECTORY_SECONDS."""
    if not url:
        return [], None
    hit = _cache.get(url)
    if hit and not fresh and time.monotonic() - hit[0] < DIRECTORY_SECONDS:
        return hit[1], hit[2]
    try:
        with httpx.Client(transport=TRANSPORT, timeout=8.0, follow_redirects=True,
                          headers={"Accept": "application/json", "User-Agent": "Mnemerynne-media/0.1"}) as client:
            resp = client.get(url)
        resp.raise_for_status()
        stations, problem = parse_directory(url, resp.json()), None
        if not stations:
            problem = "The station list has no playable stations."
    except (httpx.HTTPError, ValueError) as exc:
        log.info("Media: station list %s: %s", url, exc)
        stations, problem = (hit[1] if hit else []), f"The station list couldn't be read ({type(exc).__name__})."
    _cache[url] = (time.monotonic(), stations, problem)
    return stations, problem


def stations(conn: sqlite3.Connection) -> tuple[list[dict], str | None]:
    """The relay's stations, then the admin's own; each with how the page should play it."""
    cfg = media.load_settings(conn)
    relay, problem = directory(cfg.get("radio_directory_url") or "")
    own = [{"key": f"src-{s['id']}", "name": s["name"], "url": s["url"], "running": None, "listeners": None,
            "from": "manual"} for s in sources(conn) if s["kind"] == "radio"]
    out = []
    for s in relay + own:
        direct = urlsplit(s["url"]).scheme == "https"
        out.append({**s, "play": s["url"] if direct else f"/media/radio/{s['key']}/stream"})
    return out, problem


def station(conn: sqlite3.Connection, key: str) -> dict | None:
    """A station by key, only from the lists above: the proxy never fetches an address it was handed."""
    return next((s for s in stations(conn)[0] if s["key"] == key), None)
