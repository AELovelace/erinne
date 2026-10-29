"""Live TV and radio beside the library.

- Streams (media_sources kind 'rtmp' or 'hls') are pulled by the server and repackaged as HLS
  (transcode.Manager.watch_live), so a LAN-only source like the RTMP server's
  http://10.1.1.21/stream/hls/live.m3u8 plays from anywhere and CORS never matters.
- YouTube (kind 'youtube'): a video, a playlist or a channel's live stream, played in YouTube's
  own embed player (youtube-nocookie.com). Any usual YouTube link is turned into its embed address
  when it is added (youtube_embed); watch pages themselves refuse to be framed.
- Radio is SGCRelay's station list (Settings → Entertainment → Media → station list, default
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
from urllib.parse import parse_qs, urlsplit

import httpx

from mnemerynne import media

log = logging.getLogger("mnemerynne.media")

KINDS = ("rtmp", "hls", "radio", "youtube")
DIRECTORY_SECONDS = 30
MAX_STATIONS = 64
_SCHEMES = {"rtmp": ("rtmp://", "rtmps://"), "hls": ("http://", "https://"), "radio": ("http://", "https://"),
            "youtube": ("https://", "http://")}
YOUTUBE_EMBED = "https://www.youtube-nocookie.com/embed/"
_YT_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com", "youtu.be",
             "www.youtube-nocookie.com", "youtube-nocookie.com"}
_YT_VIDEO = re.compile(r"^[A-Za-z0-9_-]{11}$")
_YT_LIST = re.compile(r"^[A-Za-z0-9_-]{10,64}$")
_YT_CHANNEL = re.compile(r"^UC[A-Za-z0-9_-]{22}$")

# Tests replace this with an httpx.MockTransport.
TRANSPORT: httpx.BaseTransport | None = None
_cache: dict[str, tuple[float, list[dict], str | None]] = {}


class LiveError(ValueError):
    """A refusal; the message is safe to show."""
    status = 422


# ------------------------------------------------------------------ sources
def check_source(kind: str, name: str, url: str) -> tuple[str, str, str]:
    """(kind, name, url) to store; a YouTube link becomes its embed address."""
    name, url = (name or "").strip(), (url or "").strip()
    if kind not in KINDS:
        raise LiveError("A source is an RTMP stream, an HLS stream, a YouTube link or a radio station.")
    if not name or len(name) > 80:
        raise LiveError("Give the source a name (up to 80 characters).")
    if not url.lower().startswith(_SCHEMES[kind]) or len(url) > 1000 or any(c.isspace() for c in url):
        raise LiveError(f"A {kind} source's address starts with {' or '.join(_SCHEMES[kind])}.")
    if kind == "youtube":
        url = youtube_embed(url)
    return kind, name, url


def youtube_embed(url: str) -> str:
    """The embed address for a YouTube link: a video (watch, youtu.be, shorts, live, embed), a playlist,
    or a channel (/channel/UC...: what it is streaming live). LiveError for anything else."""
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if host not in _YT_HOSTS:
        raise LiveError("That isn't a YouTube link (youtube.com or youtu.be).")
    query = parse_qs(parts.query)
    segs = [s for s in parts.path.split("/") if s]
    video = (query.get("v") or [""])[0]
    playlist = (query.get("list") or [""])[0]
    if host == "youtu.be" and segs:
        video = segs[0]
    elif segs and segs[0] in ("shorts", "live", "embed", "v") and len(segs) > 1:
        if segs[0] == "embed" and segs[1] == "live_stream":
            segs = ["channel", (query.get("channel") or [""])[0]]
        elif segs[0] == "embed" and segs[1] == "videoseries":
            video = ""
        else:
            video = segs[1]
    if segs and segs[0] == "channel" and len(segs) > 1:
        if not _YT_CHANNEL.match(segs[1]):
            raise LiveError("That channel id doesn't look right (it starts with UC and is 24 characters).")
        return f"{YOUTUBE_EMBED}live_stream?channel={segs[1]}"
    if video:
        if not _YT_VIDEO.match(video):
            raise LiveError("That YouTube video id doesn't look right.")
        return f"{YOUTUBE_EMBED}{video}" + (f"?list={playlist}" if _YT_LIST.match(playlist) else "")
    if playlist:
        if not _YT_LIST.match(playlist):
            raise LiveError("That YouTube playlist id doesn't look right.")
        return f"{YOUTUBE_EMBED}videoseries?list={playlist}"
    if segs and (segs[0].startswith("@") or segs[0] in ("c", "user")):
        raise LiveError("YouTube only embeds a channel's live stream by its channel id: open the channel, "
                        "choose About → Share channel → Copy channel ID, and use "
                        "https://www.youtube.com/channel/<that id>.")
    raise LiveError("Use a YouTube video, playlist or /channel/UC... link.")


def youtube_what(embed: str) -> str:
    """'live', 'playlist' or 'video', for an embed address made by youtube_embed."""
    if "live_stream?channel=" in embed:
        return "live"
    return "playlist" if "videoseries?" in embed else "video"


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
