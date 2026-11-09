"""SoundCloud in Media: a profile's tracks, albums, playlists, likes and reposts, search, and stations built from a
track's related tracks, all read with the server's API key.

The key (Settings → Entertainment → Media) and its token stay on the server. A track's audio is signed for by the
server when it is played, then the browser streams it straight from SoundCloud's CDN: the signed links need no
credentials and allow any origin (checked against the real API).

What SoundCloud's API Terms and limits shape here:
- Nothing is stored: lists, profiles and links live in memory for minutes, and audio never touches this server.
- Lists never look up streams. A stream lookup happens only when a track plays, because they are limited to 1,500 a
  day per key; this stops at STREAM_STOP, and any 429 stops all calls until SoundCloud's reset time.
- The token goes only to api.soundcloud.com, and the only addresses fetched are ones built here or its own paging
  links on that host, so a profile name typed by someone can't send the server anywhere else.
- The page must credit the uploader and SoundCloud and link back: each item carries `link` and `artist_link`.
"""

from __future__ import annotations

import base64
import logging
import re
import sqlite3
import threading
import time
from collections import OrderedDict, deque
from urllib.parse import parse_qsl, urljoin, urlsplit

import httpx

from mnemerynne import media
from mnemerynne.config import Settings

log = logging.getLogger("mnemerynne.media")

API_HOST = "api.soundcloud.com"
API = f"https://{API_HOST}"
TOKEN_URL = "https://secure.soundcloud.com/oauth/token"
TIMEOUT = 15

UPSTREAM_PAGE = 200          # the most SoundCloud returns at once, so its limits are spent slowly
PAGE = 60                    # what one Load more shows
MAX_ITEMS = 1000             # the most of any one list kept in memory
QUEUE_MAX = 200              # the most tracks sent to a screen at once
LIST_SECONDS = 600           # how long a list is reused
STALE_SECONDS = 1800         # how long an old list stands in when SoundCloud is limiting or down
MAX_LISTS = 64               # searches and stations each take one, so profile lists aren't pushed out at once
QUERY_MAX = 100              # the longest search kept
STATION_SIZE = 50            # how many related tracks one station top-up asks for
PROFILE_SECONDS = 3600       # a name's account
UNKNOWN_SECONDS = 60         # a name SoundCloud doesn't know: soon asked again, it may be a typo being fixed
MAX_PROFILES = 256
STREAM_STOP = 1400           # stream lookups in a day before this refuses (SoundCloud's limit: 1,500)
DAY_SECONDS = 86400
STREAM_MARGIN = 60           # a signed link is reused until this long before it runs out
STREAM_FALLBACK_SECONDS = 120   # ...or for this long when it doesn't say
MAX_STREAMS = 200
TOKEN_RETRY_SECONDS = 60     # after a refused token, so a bad key can't use up the 50 tokens per 12 hours

TRACK_TABS = ("tracks", "likes", "reposts")
SET_TABS = ("albums", "playlists", "liked_sets")
ALBUM_TYPES = {"album", "ep", "single", "compilation"}     # playlist_type; everything else is a playlist
_ACCESS = "playable,preview,blocked"                        # blocked ones are counted, then left out
_TRACK_PARAMS = {"limit": UPSTREAM_PAGE, "linked_partitioning": "true", "access": _ACCESS}
_SET_PARAMS = {"limit": UPSTREAM_PAGE, "linked_partitioning": "true"}
_RELATED_PARAMS = {"limit": STATION_SIZE, "linked_partitioning": "true", "access": "playable"}   # no 30-second previews on a station
_TAB_PATHS = {"tracks": "tracks", "likes": "likes/tracks", "reposts": "reposts/tracks",
              "albums": "playlists", "playlists": "playlists", "liked_sets": "likes/playlists"}

_URN_PART = r"[A-Za-z0-9_-]+"
USER_URN = re.compile(rf"^soundcloud:users:{_URN_PART}$")
TRACK_URN = re.compile(rf"^soundcloud:tracks:{_URN_PART}$")
SET_URN = re.compile(rf"^soundcloud:(?:playlists|system-playlists):{_URN_PART}$")
_KEY = re.compile(rf"^(?:user:(soundcloud:users:{_URN_PART}):(tracks|likes|reposts)"
                  rf"|set:(soundcloud:(?:playlists|system-playlists):{_URN_PART})"
                  rf"|search:(.{{1,{QUERY_MAX}}})"                      # a search's words: only ever sent as a query value
                  rf"|related:(soundcloud:tracks:{_URN_PART}))$")       # a station, named by the track it started from

_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
_PROFILE_HOSTS = {"soundcloud.com", "www.soundcloud.com", "m.soundcloud.com"}
_PROFILE_TABS = {"tracks", "popular-tracks", "albums", "sets", "reposts", "likes", "following", "followers"}
# SoundCloud's own pages, which look like profile names in an address.
_RESERVED = {"discover", "search", "stream", "you", "upload", "charts", "feed", "settings", "messages", "notifications",
             "pages", "mobile", "tags", "terms-of-use", "people", "popular", "signin", "login", "logout"}
_CDN_SUFFIXES = (".sndcdn.com", ".soundcloud.cloud")

# Tests replace this with an httpx.MockTransport.
TRANSPORT: httpx.BaseTransport | None = None


class SoundCloudError(Exception):
    """A refusal; the message is safe to show. status: what the API answers with."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def _bad_key() -> SoundCloudError:
    return SoundCloudError(503, "SoundCloud refused this server's key. An admin should check it in Settings → "
                                "Entertainment → Media.")


def _no_answer() -> SoundCloudError:
    return SoundCloudError(502, "SoundCloud isn't answering right now.")


def _wait(seconds: float) -> str:
    """How long, in the words a person would use."""
    minutes = max(1, round(seconds / 60))
    if minutes < 90:
        return f"{minutes} minute{'s' if minutes != 1 else ''}"
    return f"{round(minutes / 60)} hours"


def _too_many(seconds: float) -> SoundCloudError:
    return SoundCloudError(429, f"SoundCloud's limit for this server has been reached. Try again in about {_wait(seconds)}.")


# -------------------------------------------------------------- the profile name
def clean_profile(text: str) -> str:
    """A profile's name from what someone typed: a bare name or a soundcloud.com address. '' clears it. Only the name
    is kept, so the one address ever built from it is https://soundcloud.com/<name>."""
    text = (text or "").strip()
    if not text:
        return ""
    name = text
    if "/" in text:                                        # an address, with or without its https://
        split = urlsplit(text if "://" in text else "https://" + text)
        try:
            port = split.port
        except ValueError:
            port = -1
        host = (split.hostname or "").lower()
        if host == "on.soundcloud.com":                    # following a short link would fetch what someone typed
            raise SoundCloudError(422, "That's a short link. Use the address from the profile page, like "
                                       "soundcloud.com/name.")
        if split.scheme not in ("http", "https") or host not in _PROFILE_HOSTS or port is not None or split.username:
            raise SoundCloudError(422, "That isn't a soundcloud.com profile address.")
        parts = [p for p in split.path.split("/") if p]
        if len(parts) > 2 or not parts or (len(parts) == 2 and parts[1].lower() not in _PROFILE_TABS):
            raise SoundCloudError(422, "That address is a page or a track. Use the profile's own, like soundcloud.com/name.")
        name = parts[0]
    if not _NAME.fullmatch(name) or name.lower() in _RESERVED:
        raise SoundCloudError(422, "That isn't a SoundCloud profile name. Use the part after soundcloud.com/.")
    return name


def clean_query(text: str) -> str:
    """Search words as one tidy line, or a refusal when there are none."""
    query = " ".join((text or "").split())[:QUERY_MAX].strip()     # collapses tabs and newlines, and caps the length
    if not query:
        raise SoundCloudError(422, "Type something to search for.")
    return query                                                   # safe to put in a list's key and a query value


# ------------------------------------------------------------------ the key
def credentials(conn: sqlite3.Connection, settings: Settings) -> tuple[str, str] | None:
    """The saved client id and secret, or None."""
    return media.soundcloud_credentials(settings, media.load_settings(conn))


def need_credentials(conn: sqlite3.Connection, settings: Settings) -> tuple[str, str]:
    found = credentials(conn, settings)
    if found is None:
        raise SoundCloudError(503, "SoundCloud isn't set up yet. An admin adds the key in Settings → Entertainment → Media.")
    return found


# -------------------------------------------------------------------- state
# All in memory, so a restart forgets it: the Terms allow no more. _token_lock makes one token at a time, however many
# people open the tab at once; _lists_lock keeps two people paging the same list from adding its items twice.
_token_lock = threading.Lock()
_lists_lock = threading.RLock()
_streams_lock = threading.Lock()
_token: dict = {"creds": None, "value": None, "until": 0.0, "retry": 0.0}
_blocked_until = 0.0                                      # after a 429: no calls at all until then
_profiles: OrderedDict[str, tuple[float, dict | None]] = OrderedDict()
_lists: OrderedDict[tuple, "_List"] = OrderedDict()
_streams: OrderedDict[str, tuple[float, dict]] = OrderedDict()
_lookups: deque[float] = deque()                          # when each stream lookup of the last day happened


def reset() -> None:
    """Forget everything (the tests, between each other)."""
    global _blocked_until
    with _token_lock:
        _token.update(creds=None, value=None, until=0.0, retry=0.0)
    with _lists_lock:
        _lists.clear()
    with _streams_lock:
        _streams.clear()
        _lookups.clear()
    _profiles.clear()
    _blocked_until = 0.0


def _client() -> httpx.Client:
    return httpx.Client(transport=TRANSPORT, timeout=TIMEOUT, follow_redirects=False)   # redirects: _get decides each


def _on_api(url: object) -> bool:
    """Only this host ever sees the token."""
    if not isinstance(url, str):
        return False
    split = urlsplit(url)
    return split.scheme == "https" and split.netloc.lower() == API_HOST


def _on_cdn(url: object) -> bool:
    """Where SoundCloud keeps its audio: the only places a browser is sent to."""
    if not isinstance(url, str):
        return False
    split = urlsplit(url)
    host = (split.hostname or "").lower()
    return split.scheme == "https" and not split.username and split.port is None and host.endswith(_CDN_SUFFIXES)


def _limited(response: httpx.Response) -> SoundCloudError:
    """A 429: stop calling until SoundCloud says, or a minute if it doesn't; at most a day."""
    global _blocked_until
    now = time.time()
    reset_at = response.headers.get("x-ratelimit-reset", "")
    retry = response.headers.get("retry-after", "")
    wait = int(reset_at) - now if reset_at.isdigit() else int(retry) if retry.isdigit() else 60
    wait = min(max(wait, 60), DAY_SECONDS)
    _blocked_until = now + wait
    log.warning("SoundCloud said 429: no calls for %d s", wait)
    return _too_many(wait)


def _check_blocked() -> None:
    wait = _blocked_until - time.time()
    if wait > 0:
        raise _too_many(wait)


def _bearer(creds: tuple[str, str], refused: str | None = None) -> str:
    """The token, minted once and reused until it nearly runs out. refused: one the API just turned down."""
    with _token_lock:
        now = time.time()
        if _token["creds"] == creds:
            if _token["value"] and _token["value"] != refused and now < _token["until"]:
                return _token["value"]
            if now < _token["retry"]:
                raise _bad_key()
        else:
            _token.update(creds=creds, value=None, until=0.0, retry=0.0)      # the key was changed
        try:
            with _client() as client:
                r = client.post(TOKEN_URL, data={"grant_type": "client_credentials"}, auth=creds)
        except httpx.HTTPError:
            raise _no_answer() from None
        if r.status_code == 429:
            raise _limited(r)
        if r.status_code in (400, 401, 403):
            _token.update(value=None, retry=now + TOKEN_RETRY_SECONDS)
            raise _bad_key()
        try:
            body = r.json()
            value, lifetime = body["access_token"], int(body.get("expires_in") or 3600)
        except (ValueError, KeyError, TypeError):
            raise _no_answer() from None
        _token.update(value=value, until=now + lifetime - 60, retry=0.0)
        return value


def _get(creds: tuple[str, str], url: str, params: dict | None = None, follow: bool = True) -> httpx.Response:
    """GET from the API with the token: once more with a new token after a 401; redirects only within the API."""
    if not _on_api(url):
        raise _no_answer()
    _check_blocked()
    token = _bearer(creds)
    retried = False
    try:
        with _client() as client:
            for _ in range(4):
                r = client.get(url, params=params, headers={"Authorization": f"OAuth {token}", "Accept": "application/json"})
                if r.status_code == 401 and not retried:
                    retried = True
                    token = _bearer(creds, refused=token)
                    continue
                if r.status_code == 401:
                    raise _bad_key()
                if r.status_code == 429:
                    raise _limited(r)
                if r.is_redirect and follow:
                    url, params = urljoin(url, r.headers.get("location", "")), None
                    if not _on_api(url):
                        raise _no_answer()
                    continue
                return r
    except httpx.HTTPError:
        raise _no_answer() from None
    raise _no_answer()


def _json(response: httpx.Response) -> dict:
    if response.status_code == 404:
        raise SoundCloudError(404, "SoundCloud doesn't have that any more.")
    if response.status_code != 200:
        raise _no_answer()
    try:
        body = response.json()
    except ValueError:
        raise _no_answer() from None
    if not isinstance(body, dict):
        raise _no_answer()
    return body


# ----------------------------------------------------- what the page is sent
def _link(url: object) -> str | None:
    """A soundcloud.com address without its tracking query, or None: these go straight into the page's links."""
    if not isinstance(url, str):
        return None
    split = urlsplit(url)
    if split.scheme != "https" or (split.hostname or "").lower() not in _PROFILE_HOSTS or split.username or split.port:
        return None
    return f"https://{split.hostname}{split.path}"


def _art(url: object) -> str | None:
    """Artwork at a size that holds up on a TV (SoundCloud's -large is 100 px), or None if it isn't SoundCloud's."""
    if not _on_cdn(url):
        return None
    return str(url).replace("-large.", "-t500x500.")


def _urn(raw: dict, kind: str, pattern: re.Pattern) -> str | None:
    urn = raw.get("urn") or (f"soundcloud:{kind}:{raw['id']}" if raw.get("id") is not None else None)
    return urn if isinstance(urn, str) and pattern.fullmatch(urn) else None


def _track(raw: dict) -> dict | None:
    """A track as the page and the now-playing bar want it. None: blocked, or not one we can name."""
    urn = _urn(raw, "tracks", TRACK_URN)
    if urn is None or raw.get("access") == "blocked":
        return None
    person = raw.get("user") or {}
    ms = raw.get("duration")
    return {"id": urn, "source": "soundcloud", "title": raw.get("title") or "Untitled", "artist": person.get("username") or "",
            "album": None, "duration": ms / 1000 if isinstance(ms, (int, float)) else None,
            "art": _art(raw.get("artwork_url")) or _art(person.get("avatar_url")),
            "preview": raw.get("access") == "preview", "link": _link(raw.get("permalink_url")),
            "artist_link": _link(person.get("permalink_url"))}


def _set(raw: dict) -> dict | None:
    urn = _urn(raw, "playlists", SET_URN)
    if urn is None:
        return None
    person = raw.get("user") or {}
    return {"id": urn, "title": raw.get("title") or "Untitled", "artist": person.get("username") or "",
            "art": _art(raw.get("artwork_url")) or _art(person.get("avatar_url")), "count": raw.get("track_count") or 0,
            "album": str(raw.get("playlist_type") or "").lower() in ALBUM_TYPES, "link": _link(raw.get("permalink_url")),
            "artist_link": _link(person.get("permalink_url"))}


def _person(raw: dict) -> dict | None:
    """A profile in search results. None: not one we can name."""
    urn = _urn(raw, "users", USER_URN)
    if urn is None:
        return None
    return {"id": urn, "name": raw.get("username") or "Unknown", "art": _art(raw.get("avatar_url")),
            "followers": raw.get("followers_count"), "link": _link(raw.get("permalink_url"))}   # what the page's card shows


# ---------------------------------------------------------------- profiles
def _account(body: dict, name: str, link: str | None) -> dict | None:
    """An account as the page wants it, or None if the answer isn't a user. name and link: used when it leaves them out."""
    urn = _urn(body, "users", USER_URN) if body.get("kind") == "user" else None
    if urn is None:
        return None
    return {"urn": urn, "name": body.get("username") or name, "art": _art(body.get("avatar_url")),
            "link": _link(body.get("permalink_url")) or link}


def _remember(key: str, found: dict | None, now: float) -> tuple[float, dict | None]:
    """Keep an account for a while (one that wasn't found, only briefly), oldest pushed out first."""
    cached = (now + (PROFILE_SECONDS if found else UNKNOWN_SECONDS), found)
    _profiles[key] = cached
    _profiles.move_to_end(key)
    while len(_profiles) > MAX_PROFILES:
        _profiles.popitem(last=False)
    return cached                                                  # the same shape _profiles holds


def profile(creds: tuple[str, str], name: str) -> dict:
    """The account behind a clean_profile() name."""
    key = name.lower()
    now = time.time()
    cached = _profiles.get(key)
    if cached is None or cached[0] <= now:
        r = _get(creds, f"{API}/resolve", {"url": f"https://soundcloud.com/{name}"})
        found = None if r.status_code == 404 else _account(_json(r), name, f"https://soundcloud.com/{name}")
        cached = _remember(key, found, now)
    if cached[1] is None:
        raise SoundCloudError(422, f"SoundCloud doesn't know a profile called {name}. Check the address.")
    return cached[1]


def person(creds: tuple[str, str], urn: str) -> dict:
    """The account behind a USER_URN, for a profile opened from search."""
    now = time.time()
    cached = _profiles.get(urn)                                    # a urn has colons, so it can't collide with a name
    if cached is None or cached[0] <= now:
        cached = _remember(urn, _account(_json(_get(creds, f"{API}/users/{urn}")), "Unknown", None), now)
    if cached[1] is None:
        raise SoundCloudError(404, "SoundCloud doesn't have that any more.")
    return cached[1]


# ------------------------------------------------------------------- lists
class _List:
    """One list from SoundCloud, as far as it has been read; more is fetched as people page down."""

    def __init__(self, url: str, params: dict, shape):
        self.at = time.time()
        self.next: str | None = url
        self.params: dict | None = params
        self.shape = shape
        self.items: list[dict] = []
        self.blocked = 0
        self.head: dict | None = None                     # a set's own details


def _pull(creds: tuple[str, str], entry: _List, want: int) -> None:
    """Read until `want` items are in. A next_href off the API ends the list rather than being followed."""
    want = min(want, MAX_ITEMS)
    while entry.next and len(entry.items) < want:
        body = _json(_get(creds, entry.next, entry.params))
        entry.params = None
        rows = body.get("collection")
        rows = rows if isinstance(rows, list) else []
        for raw in rows:
            item = entry.shape(raw) if isinstance(raw, dict) else None
            if item is None:
                entry.blocked += 1
            else:
                entry.items.append(item)
        following = body.get("next_href")
        entry.next = following if rows and _on_api(following) else None


def _list(creds: tuple[str, str], key: tuple, url: str, params: dict, shape, want: int) -> _List:
    """The list, read this far: reused for LIST_SECONDS. If SoundCloud is limiting or down, what we have stands in."""
    with _lists_lock:
        now = time.time()
        old = _lists.get(key)
        entry = old if old is not None and now - old.at < LIST_SECONDS else _List(url, params, shape)
        try:
            _pull(creds, entry, want)
        except SoundCloudError as exc:
            if exc.status not in (429, 502):
                raise
            if not entry.items:                           # with some already read, those are shown and the rest waits
                if old is None or now - old.at >= STALE_SECONDS or not old.items:
                    raise
                entry = old
        _lists[key] = entry
        _lists.move_to_end(key)
        while len(_lists) > MAX_LISTS:
            _lists.popitem(last=False)
        return entry


def listing(creds: tuple[str, str], urn: str, tab: str, offset: int = 0) -> dict:
    """One page of a profile's tab. Albums and playlists come from one SoundCloud list, so all of it is read to split it."""
    path = _TAB_PATHS[tab]
    tracks = tab in TRACK_TABS
    params = dict(_TRACK_PARAMS if tracks else _SET_PARAMS)
    if path == "playlists":
        params["show_tracks"] = "false"
    split = tab in ("albums", "playlists")
    entry = _list(creds, ("user", urn, path), f"{API}/users/{urn}/{path}", params, _track if tracks else _set,
                  MAX_ITEMS if split else offset + PAGE)
    items = entry.items
    if split:
        items = [s for s in items if s["album"] == (tab == "albums")]
    page = items[offset:offset + PAGE]
    more = offset + PAGE < len(items) or (bool(entry.next) and not split and len(items) < MAX_ITEMS)
    return {"tab": tab, "kind": "tracks" if tracks else "sets", "key": f"user:{urn}:{tab}" if tracks else None, "items": page,
            "next_offset": offset + len(page) if more and page else None, "blocked": entry.blocked if tracks else 0}


def _set_tracks(creds: tuple[str, str], urn: str) -> _List:
    return _list(creds, ("set", urn), f"{API}/playlists/{urn}/tracks", dict(_TRACK_PARAMS), _track, MAX_ITEMS)


def set_detail(creds: tuple[str, str], urn: str) -> dict:
    """An album or playlist with its tracks."""
    entry = _set_tracks(creds, urn)
    if entry.head is None:
        head = _set(_json(_get(creds, f"{API}/playlists/{urn}", {"show_tracks": "false"})))
        if head is None:
            raise SoundCloudError(404, "SoundCloud doesn't have that any more.")
        entry.head = head
    return {**entry.head, "key": f"set:{urn}", "tracks": entry.items, "blocked": entry.blocked}


# ------------------------------------------------------------------ search
_SEARCHES = {"tracks": ("tracks", _TRACK_PARAMS, _track), "playlists": ("playlists", {**_SET_PARAMS, "show_tracks": "false"}, _set),
             "users": ("users", _SET_PARAMS, _person)}
_SEARCH_KINDS = {"tracks": "tracks", "playlists": "sets", "users": "users"}   # what the page draws for each


def _search(creds: tuple[str, str], kind: str, query: str, want: int) -> _List:
    path, params, shape = _SEARCHES[kind]
    return _list(creds, ("search", kind, query.lower()), f"{API}/{path}", {**params, "q": query}, shape, want)   # one list per wording


def search(creds: tuple[str, str], kind: str, text: str, offset: int = 0) -> dict:
    """One page of a search, in the shape of listing(). Like every list, it never looks up a stream."""
    query = clean_query(text)
    entry = _search(creds, kind, query, offset + PAGE)
    page = entry.items[offset:offset + PAGE]
    more = offset + PAGE < len(entry.items) or (bool(entry.next) and len(entry.items) < MAX_ITEMS)
    tracks = kind == "tracks"
    return {"tab": kind, "kind": _SEARCH_KINDS[kind], "key": f"search:{query}" if tracks else None, "items": page,
            "next_offset": offset + len(page) if more and page else None, "blocked": entry.blocked if tracks else 0}


# ---------------------------------------------------------------- stations
def _related(creds: tuple[str, str], urn: str) -> _List:
    return _list(creds, ("related", urn), f"{API}/tracks/{urn}/related", dict(_RELATED_PARAMS), _track, STATION_SIZE)


def related(creds: tuple[str, str], urn: str) -> dict:
    """What a station can play after `urn`: lists cost no stream lookups, and each track looks up its own as it starts."""
    return {"key": f"related:{urn}", "tracks": [t for t in _related(creds, urn).items if t["id"] != urn]}   # never the track itself


def _station(creds: tuple[str, str], urn: str) -> list[dict]:
    """A whole station for a screen, which only has the key: the track it started from, then its relatives."""
    entry = _related(creds, urn)
    if entry.head is None:
        entry.head = _track(_json(_get(creds, f"{API}/tracks/{urn}"))) or {}    # {}: can't be played here, so left off
    return ([entry.head] if entry.head else []) + [t for t in entry.items if t["id"] != urn]


# ---------------------------------------------------------------- a screen
def queue(creds: tuple[str, str], key: str, track: str | None) -> list[dict]:
    """What a screen is sent: rebuilt here from a list's key, never taken from the phone. Starts at `track`."""
    found = _KEY.fullmatch(key or "")
    if found is None:
        raise SoundCloudError(422, "That isn't a SoundCloud list.")
    user, tab, playlist, words, seed = found.groups()
    if playlist:
        items = _set_tracks(creds, playlist).items
    elif words:
        items = _search(creds, "tracks", clean_query(words), QUEUE_MAX).items
    elif seed:
        items = _station(creds, seed)
    else:
        path = _TAB_PATHS[tab]
        items = _list(creds, ("user", user, path), f"{API}/users/{user}/{path}", dict(_TRACK_PARAMS), _track, MAX_ITEMS).items
    if not items:
        raise SoundCloudError(404, "There's nothing to play in that list.")
    index = 0
    if track is not None:
        index = next((i for i, t in enumerate(items) if t["id"] == track), -1)
        if index < 0:
            raise SoundCloudError(404, "That track isn't in the list any more.")
    return items[index:index + QUEUE_MAX]


# ----------------------------------------------------------------- playing
def _expires(url: str) -> float | None:
    """When a signed link stops working, from its `expires`, or the CloudFront policy inside it."""
    query = {k.lower(): v for k, v in parse_qsl(urlsplit(url).query)}
    if query.get("expires", "").isdigit():
        return float(query["expires"])
    policy = query.get("policy")
    if policy:
        try:
            decoded = base64.b64decode(policy.replace("-", "+").replace("_", "=").replace("~", "/")).decode()
        except ValueError:
            return None
        found = re.search(r'EpochTime"\s*:\s*(\d+)', decoded)
        if found:
            return float(found.group(1))
    return None


def _signed(creds: tuple[str, str], url: object) -> str | None:
    """The CDN address behind one of a track's /streams links: the API redirects there when asked with the token."""
    if _on_cdn(url):
        return str(url)
    if not _on_api(url):
        return None
    r = _get(creds, str(url), follow=False)
    location = r.headers.get("location")
    return location if r.is_redirect and _on_cdn(location) else None


def _count_lookup() -> None:
    now = time.time()
    with _streams_lock:
        while _lookups and now - _lookups[0] >= DAY_SECONDS:
            _lookups.popleft()
        if len(_lookups) >= STREAM_STOP:
            raise _too_many(_lookups[0] + DAY_SECONDS - now)
        _lookups.append(now)


# Best first. The AAC link lasts about two hours and the MP3 about six minutes, so AAC survives a long pause.
_FORMATS = (("hls_aac_160_url", "hls", False), ("hls_mp3_128_url", "hls", False), ("preview_mp3_128_url", "direct", True))


def stream(creds: tuple[str, str], urn: str, fresh: bool = False) -> dict:
    """Where the browser plays a track from, in the shape of POST /api/media/play. Called only as a track starts.
    fresh: the browser just failed on the link kept here, so that one is skipped and a new lookup is made (and counted)."""
    now = time.time()
    with _streams_lock:
        cached = _streams.get(urn)
    if cached is not None and cached[0] > now and not fresh:
        return dict(cached[1])
    _check_blocked()
    _count_lookup()
    r = _get(creds, f"{API}/tracks/{urn}/streams")
    if r.status_code in (403, 404):
        raise SoundCloudError(403, "SoundCloud doesn't allow this track to be played here.")
    body = _json(r)
    for field, mode, preview in _FORMATS:
        url = _signed(creds, body.get(field))
        if url is None:
            continue
        result = {"mode": mode, "url": url, "session": None, "start": 0, "duration": None, "preview": preview}
        expires = _expires(url)
        until = (expires - STREAM_MARGIN) if expires is not None else now + STREAM_FALLBACK_SECONDS
        if until > now:
            with _streams_lock:
                _streams[urn] = (until, result)
                _streams.move_to_end(urn)
                while len(_streams) > MAX_STREAMS:
                    _streams.popitem(last=False)
        return dict(result)
    raise SoundCloudError(403, "SoundCloud doesn't allow this track to be played here.")
