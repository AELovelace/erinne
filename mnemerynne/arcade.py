"""The arcade: games from another site (Lumi Web, Lumi Games, DOLL-OS), proxied at the root of a host of their
own (MNEMERYNNE_ARCADE_URL, e.g. https://arcade.beepsquad.wtf), so their scripts never run on Mnemerynne's origin.

- One site (Settings → Entertainment → Games: `site_url`, e.g. https://sadgirlsclub.wtf) is served at the arcade
  host's root: /lumibot/x there is /lumibot/x upstream, so the site's own root paths, cookies and Discord callbacks
  work unchanged. Bodies stream through untouched; a Location that names the site moves onto the arcade host, a
  Set-Cookie loses its Domain, and Mnemerynne's own cookies never go upstream.
- Nothing of Mnemerynne answers there: no pages, no /api, no /mcp, no static files (ArcadeHost sends that host
  here before the app sees it). Only /__arcade/enter and /__arcade/leave are Mnemerynne's.
- Getting in: on the main origin a signed-in person who may play asks for a ticket (POST /api/arcade/ticket,
  web/arcade.py): signed, TICKET_SECONDS, one use, for one path. /__arcade/enter spends it and sets the arcade's
  own cookie (COOKIE: host-only, COOKIE_SECONDS). Every request checks again that the person is active, still on
  the same session epoch (signing out of Mnemerynne ends it), and still allowed to play (cached CHECK_SECONDS).
- Who may play: Games on, a site set, and the person in the Games group; admins always. A screen (the TV) plays
  as its owner, never as an admin, and only while it is still paired: its tickets and cookie carry its id.
- The games (table `games`): pages of the site that Media → Games lists, each with a name, an icon, a short
  description and its start page.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import re
import secrets
import sqlite3
import threading
import time
from html import escape
from typing import Any, Callable
from urllib.parse import parse_qs, urlsplit

import anyio
import httpx
from itsdangerous import BadSignature, URLSafeTimedSerializer
from starlette.datastructures import MutableHeaders
from starlette.requests import HTTPConnection
from starlette.responses import HTMLResponse, RedirectResponse
from starlette.websockets import WebSocket

from mnemerynne import db, group_refs, remote, users
from mnemerynne.config import Settings

log = logging.getLogger("mnemerynne.arcade")

COOKIE = "mnemerynne_arcade"
TICKET_SECONDS = 60
COOKIE_SECONDS = 12 * 3600
CHECK_SECONDS = 60          # how long "may play" is trusted before it's checked again
OWN_PREFIX = "/__arcade/"   # the arcade host's only paths that aren't the site's
SETTINGS_KEY = "games_settings"
DEFAULTS = {"enabled": False, "group": "", "site_url": ""}
SITE_ID = -1                # the site's key among RemoteProxy's clients (remote apps have positive ids)

# Tickets already spent (nonce -> when it expires); one web process, as the rate limiters.
_spent: dict[str, float] = {}
_spent_lock = threading.Lock()


class ArcadeError(ValueError):
    """Refused; the message is safe to show."""


# ---------------------------------------------------------------- settings
def origin(settings: Settings) -> str:
    """The arcade's origin (scheme://host[:port]) from MNEMERYNNE_ARCADE_URL; "" when it isn't set."""
    parts = urlsplit((settings.arcade_url or "").strip())
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return ""
    return f"{parts.scheme}://{parts.netloc}".lower()


def host(settings: Settings) -> str:
    """The arcade's host name, lower case and without a port, as requests for it carry it."""
    return (urlsplit(origin(settings)).hostname or "").lower()


def main_origin(settings: Settings) -> str:
    """Mnemerynne's own origin (MNEMERYNNE_PUBLIC_URL): the one page allowed to frame the arcade."""
    parts = urlsplit((settings.public_url or "").strip())
    return f"{parts.scheme}://{parts.netloc}".lower() if parts.scheme and parts.netloc else ""


def check_config(settings: Settings) -> None:
    """At start: a set MNEMERYNNE_ARCADE_URL must be a plain origin on a host of its own. Raises ValueError."""
    raw = (settings.arcade_url or "").strip()
    if not raw:
        return
    if not origin(settings):
        raise ValueError("MNEMERYNNE_ARCADE_URL must look like https://arcade.example.org")
    if urlsplit(raw).path not in ("", "/") or urlsplit(raw).query:
        raise ValueError("MNEMERYNNE_ARCADE_URL is an origin only: no path, e.g. https://arcade.example.org")
    main_host = (urlsplit(main_origin(settings)).hostname or "").lower()
    if main_host and host(settings) == main_host:
        # The whole point is a different origin: the same host would put the games next to Mnemerynne's cookies.
        raise ValueError("MNEMERYNNE_ARCADE_URL must be a different host from MNEMERYNNE_PUBLIC_URL")


def load_settings(conn: sqlite3.Connection) -> dict:
    """Games' settings (meta 'games_settings'), defaults filled in."""
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (SETTINGS_KEY,)).fetchone()
    try:
        saved = json.loads(row["value"]) if row else {}
    except ValueError:
        saved = {}
    return {k: saved.get(k, v) for k, v in DEFAULTS.items()}


def clean_site_url(value: str) -> str:
    """The site to proxy, as an origin (https://sadgirlsclub.wtf); "" clears it. Raises ArcadeError."""
    v = (value or "").strip()
    if not v:
        return ""
    parts = urlsplit(v)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ArcadeError("The site is an address like https://sadgirlsclub.wtf")
    if parts.path not in ("", "/") or parts.query or parts.fragment or parts.username or parts.password:
        raise ArcadeError("The site is the address of the whole site, without a path: https://sadgirlsclub.wtf")
    return f"{parts.scheme}://{parts.netloc}".lower()


def save_settings(conn: sqlite3.Connection, changes: dict) -> dict:
    """Store what changed (enabled, group, site_url; already checked) over what's saved; the new settings."""
    cfg = load_settings(conn) | {k: v for k, v in changes.items() if k in DEFAULTS and v is not None}
    conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)", (SETTINGS_KEY, json.dumps(cfg)))
    return cfg


def may_play(conn: sqlite3.Connection, user: sqlite3.Row | None, directory, admin: bool) -> bool:
    """Games on, a site set, and the person active and in the Games group (admins always)."""
    if user is None or not user["active"]:
        return False
    cfg = load_settings(conn)
    if not cfg["enabled"] or not cfg["site_url"]:
        return False
    return admin or bool(cfg["group"]) and group_refs.in_any(conn, user, directory, cfg["group"])


def clean_path(path: str) -> str:
    """A path on the site to open (a game's start page): root-relative, nothing that could leave the arcade
    host or reach its own /__arcade/ paths. Raises ArcadeError."""
    p = (path or "").strip() or "/"
    parts = urlsplit(p)
    if (not p.startswith("/") or p.startswith("//") or "\\" in p or parts.scheme or parts.netloc
            or any(ord(c) < 32 or c == "\x7f" for c in p) or any(seg == ".." for seg in parts.path.split("/"))):
        raise ArcadeError("A game's page is a path on the site, like /lumibot/")
    if p.startswith(OWN_PREFIX) or p == OWN_PREFIX.rstrip("/"):
        raise ArcadeError("That path is the arcade's own.")
    return p


# ------------------------------------------------------------------- games
GAME_SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")
GAME_FIELDS = ("name", "description", "icon", "start_path", "enabled")


def _slug(name: str) -> str:
    """lumi-web from "Lumi Web!"; "game" when nothing is left."""
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:40].strip("-") or "game"


def clean_game(fields: dict) -> dict:
    """A game's fields as stored: name (1-80), description (up to 300), icon (an emoji or up to 8 characters),
    start_path (a path on the site), enabled. Only the fields given. Raises ArcadeError."""
    out = {}
    if "name" in fields:
        name = " ".join(str(fields["name"] or "").split())
        if not 1 <= len(name) <= 80:
            raise ArcadeError("A game's name is 1 to 80 characters.")
        out["name"] = name
    if "description" in fields:
        description = " ".join(str(fields["description"] or "").split())
        if len(description) > 300:
            raise ArcadeError("A game's description is at most 300 characters.")
        out["description"] = description
    if "icon" in fields:
        icon = str(fields["icon"] or "").strip() or "🎮"
        if len(icon) > 8 or any(ord(c) < 32 for c in icon):
            raise ArcadeError("A game's icon is an emoji or a few letters.")
        out["icon"] = icon
    if "start_path" in fields:
        out["start_path"] = clean_path(fields["start_path"])
    if "enabled" in fields:
        out["enabled"] = 1 if fields["enabled"] else 0
    return out


def game_json(row: sqlite3.Row) -> dict:
    return {"id": row["id"], "slug": row["slug"], "name": row["name"], "description": row["description"],
            "icon": row["icon"], "start_path": row["start_path"], "sort": row["sort"], "enabled": bool(row["enabled"])}


def list_games(conn: sqlite3.Connection, enabled_only: bool = False) -> list[dict]:
    rows = conn.execute("SELECT * FROM games" + (" WHERE enabled = 1" if enabled_only else "")
                        + " ORDER BY sort, name COLLATE NOCASE, id").fetchall()
    return [game_json(r) for r in rows]


def get_game(conn: sqlite3.Connection, slug: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM games WHERE slug = ?", ((slug or "").strip().lower(),)).fetchone()


def add_game(conn: sqlite3.Connection, fields: dict) -> dict:
    """A new game at the end of the list, its slug made from its name (lumi-web, lumi-web-2...)."""
    clean = clean_game({"description": "", "icon": "🎮", "start_path": "/", "enabled": True} | fields)
    if "name" not in clean:
        raise ArcadeError("A game needs a name.")
    base = _slug(clean["name"])
    slug, n = base, 2
    while get_game(conn, slug) is not None:
        slug, n = f"{base[:36]}-{n}", n + 1
    last = conn.execute("SELECT COALESCE(MAX(sort), 0) FROM games").fetchone()[0]
    cur = conn.execute("INSERT INTO games(slug, name, description, icon, start_path, sort, enabled) "
                       "VALUES (?, ?, ?, ?, ?, ?, ?)", (slug, clean["name"], clean["description"], clean["icon"],
                                                         clean["start_path"], last + 1, clean["enabled"]))
    return game_json(conn.execute("SELECT * FROM games WHERE id = ?", (cur.lastrowid,)).fetchone())


def update_game(conn: sqlite3.Connection, game_id: int, fields: dict) -> dict:
    """Change the fields given (its slug stays, so links to it keep working). Raises ArcadeError."""
    clean = clean_game({k: v for k, v in fields.items() if k in GAME_FIELDS})
    if conn.execute("SELECT 1 FROM games WHERE id = ?", (game_id,)).fetchone() is None:
        raise ArcadeError("There's no such game.")
    if clean:
        conn.execute(f"UPDATE games SET {', '.join(f'{k} = ?' for k in clean)} WHERE id = ?",
                     (*clean.values(), game_id))
    return game_json(conn.execute("SELECT * FROM games WHERE id = ?", (game_id,)).fetchone())


def move_game(conn: sqlite3.Connection, game_id: int, step: int) -> None:
    """One place up (step -1) or down (+1) in the list; the list is renumbered 1, 2, 3..."""
    ids = [r[0] for r in conn.execute("SELECT id FROM games ORDER BY sort, name COLLATE NOCASE, id")]
    if game_id not in ids:
        raise ArcadeError("There's no such game.")
    i = ids.index(game_id)
    j = max(0, min(len(ids) - 1, i + (1 if step > 0 else -1)))
    ids[i], ids[j] = ids[j], ids[i]
    conn.executemany("UPDATE games SET sort = ? WHERE id = ?", [(n, gid) for n, gid in enumerate(ids, 1)])


def delete_game(conn: sqlite3.Connection, game_id: int) -> bool:
    return conn.execute("DELETE FROM games WHERE id = ?", (game_id,)).rowcount > 0


def games_for(conn: sqlite3.Connection, user: sqlite3.Row | None, directory, admin: bool) -> list[dict]:
    """The games this person may open (all the turned-on ones, or none)."""
    return list_games(conn, enabled_only=True) if may_play(conn, user, directory, admin) else []


# ------------------------------------------------------- tickets and cookie
def _serializer(secret_key: str, purpose: str) -> URLSafeTimedSerializer:
    """Signs with a key of the arcade's own, derived from the secret key, so nothing the arcade signs can pass
    for a Mnemerynne session (or the other way round)."""
    key = hmac.new(secret_key.encode(), b"mnemerynne-arcade", hashlib.sha256).hexdigest()
    return URLSafeTimedSerializer(key, salt=f"arcade-{purpose}")


def make_ticket(secret_key: str, user: sqlite3.Row, path: str, screen_id: int | None = None) -> str:
    """A one-use pass from the main origin to `path` on the arcade, for this person and session epoch; from a
    screen (the TV), for its owner and that screen."""
    return _serializer(secret_key, "ticket").dumps(
        {"u": user["id"], "e": user["session_epoch"], "s": screen_id, "p": clean_path(path),
         "n": secrets.token_urlsafe(12)})


def spend_ticket(secret_key: str, token: str) -> dict | None:
    """The ticket's {u, e, p} if it's genuine, fresh and not used before (it is used now); else None."""
    try:
        data = _serializer(secret_key, "ticket").loads(token or "", max_age=TICKET_SECONDS)
    except BadSignature:   # also SignatureExpired
        return None
    if not isinstance(data, dict) or not {"u", "e", "p", "n"} <= data.keys():
        return None
    now = time.time()
    with _spent_lock:
        for nonce in [n for n, until in _spent.items() if until < now]:
            del _spent[nonce]
        if data["n"] in _spent:
            return None
        _spent[data["n"]] = now + TICKET_SECONDS + 5
    return data


def make_cookie(secret_key: str, user: sqlite3.Row, screen_id: int | None = None) -> str:
    return _serializer(secret_key, "cookie").dumps({"u": user["id"], "e": user["session_epoch"], "s": screen_id})


def read_cookie(secret_key: str, value: str) -> dict | None:
    """The arcade cookie's {u, e} while it's genuine and younger than COOKIE_SECONDS; else None."""
    try:
        data = _serializer(secret_key, "cookie").loads(value or "", max_age=COOKIE_SECONDS)
    except BadSignature:
        return None
    return data if isinstance(data, dict) and {"u", "e"} <= data.keys() else None


def cookie_header(value: str, *, secure: bool, max_age: int = COOKIE_SECONDS) -> str:
    """Set-Cookie for the arcade's sign-in: host-only, HttpOnly. Over https it's SameSite=None and Partitioned,
    because the arcade usually opens in a frame on Mnemerynne's page (a third-party context), where a Lax cookie
    would never be sent; Partitioned keeps it in that page's own cookie jar."""
    attrs = [f"{COOKIE}={value}", "Path=/", f"Max-Age={max_age}", "HttpOnly"]
    attrs += ["Secure", "SameSite=None", "Partitioned"] if secure else ["SameSite=Lax"]
    return "; ".join(attrs)


# -------------------------------------------------------------- the proxy
def _site_row(site_url: str) -> dict:
    """The site in the shape RemoteProxy's helpers read a remote app (base_url, certificate, a client key)."""
    return {"id": SITE_ID, "updated_at": site_url, "slug": "arcade", "name": "The games site", "base_url": site_url,
            "rewrite_html": 0, "xsl_override": None, "ca_pem": "", "tls_check_hostname": 1}


def _cookie_value(scope, name: str) -> str:
    raw = next((v.decode("latin-1") for k, v in scope["headers"] if k.lower() == b"cookie"), "")
    for part in raw.split(";"):
        key, _, value = part.strip().partition("=")
        if key == name:
            return value
    return ""


def _page(status: int, title: str, text: str, back: str = "") -> HTMLResponse:
    """A plain page of the arcade's own (no scripts): signed out, Games off, a spent ticket."""
    link = f'<p><a href="{escape(back)}">Open Games in Mnemerynne</a></p>' if back else ""
    return HTMLResponse(f"<!doctype html><html lang=en><meta charset=utf-8><meta name=viewport "
                        f"content='width=device-width, initial-scale=1'><title>{escape(title)}</title>"
                        f"<body style='font-family:system-ui,sans-serif;background:#120a1a;color:#f4e8f0;"
                        f"display:grid;place-items:center;min-height:90vh;text-align:center'><main>"
                        f"<h1>{escape(title)}</h1><p>{escape(text)}</p>{link}</main></body></html>",
                        status_code=status, headers={"Cache-Control": "no-store"})


class ArcadeProxy(remote.RemoteProxy):
    """ASGI app for every request to the arcade host: the site at the root, behind the arcade's own sign-in."""

    def __init__(self, settings: Settings, secret_key: str, get_directory: Callable[[], Any] = lambda: None):
        super().__init__(settings, get_directory)
        self.secret_key = secret_key
        self._checked: dict[tuple[int, int | None], tuple[bool, float]] = {}   # (user, screen) -> (may play, until)

    def _prefix(self, slug: str) -> str:
        return ""   # the site sits at the arcade host's root

    def _games_url(self) -> str:
        return (main_origin(self.settings) or "") + "/media"

    def _https(self) -> bool:
        """Whether browsers reach the arcade over https: from MNEMERYNNE_ARCADE_URL, not from the request, because
        the hop in front of us (another nginx on the LAN) may speak plain http to the app."""
        return origin(self.settings).startswith("https://")

    # ------------------------------------------------------------ checks
    def _user_for(self, scope, data: dict | None) -> tuple[str, sqlite3.Row | None, str]:
        """('ok' | 'login' | 'off', user, site_url) for a cookie's or ticket's {u, e, s}. With a screen (s), the
        screen must still exist and belong to that user, who then plays as an ordinary member, never as an admin."""
        conn = db.connect(self.settings.db_path)
        try:
            cfg = load_settings(conn)
            if not cfg["enabled"] or not cfg["site_url"]:
                return "off", None, ""
            user = users.get_user_by_id(conn, data["u"]) if data else None
            if user is None or not user["active"] or data.get("e") != user["session_epoch"]:
                return "login", None, cfg["site_url"]
            screen_id = data.get("s")
            if screen_id is not None:   # the TV: unpaired or given to someone else, and it's out
                screen = conn.execute("SELECT user_id FROM media_screens WHERE id = ?", (screen_id,)).fetchone()
                if screen is None or screen["user_id"] != user["id"]:
                    return "login", None, cfg["site_url"]
            key = (user["id"], screen_id)
            cached = self._checked.get(key)
            if cached and cached[1] > time.monotonic():
                return ("ok" if cached[0] else "login"), user, cfg["site_url"]
            from mnemerynne.web import deps   # here, not at the top: deps imports the web package
            admin = screen_id is None and deps.is_admin(HTTPConnection(scope), conn, user)
            ok = may_play(conn, user, self.get_directory(), admin)
            self._checked[key] = (ok, time.monotonic() + CHECK_SECONDS)
            return ("ok" if ok else "login"), user, cfg["site_url"]
        finally:
            conn.close()

    def _with_headers(self, scope, send):
        """The arcade's own headers on everything it sends: framed only by Mnemerynne, nothing sniffed."""
        frame = main_origin(self.settings) or "'none'"
        https = self._https()

        async def wrapped(message):
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                if "x-frame-options" in headers:
                    del headers["x-frame-options"]
                csp = remote._FRAME_ANCESTORS.sub("", headers.get("content-security-policy", "")).strip("; ").strip()
                headers["Content-Security-Policy"] = (csp + "; " if csp else "") + f"frame-ancestors {frame}"
                headers.setdefault("X-Content-Type-Options", "nosniff")
                headers.setdefault("Referrer-Policy", "same-origin")
                if https:
                    headers.setdefault("Strict-Transport-Security", "max-age=31536000")
            await send(message)
        return wrapped

    # ----------------------------------------------------------- routing
    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] not in ("http", "websocket"):
            return
        path = scope.get("path", "/") or "/"
        if scope["type"] == "http":
            send = self._with_headers(scope, send)
            if path == OWN_PREFIX + "enter":
                await self._enter(scope, receive, send)
                return
            if path == OWN_PREFIX + "leave":
                await self._leave(scope, receive, send)
                return
        if path.startswith(OWN_PREFIX):
            await self._refuse(scope, receive, send, 404, "Not here", "There's nothing at this address.")
            return
        data = read_cookie(self.secret_key, _cookie_value(scope, COOKIE))
        state, user, site_url = await anyio.to_thread.run_sync(self._user_for, scope, data)
        if state == "off":
            await self._refuse(scope, receive, send, 404, "Games are off", "Games aren't turned on here.")
            return
        if state != "ok":
            await self._refuse(scope, receive, send, 401, "Open this from Mnemerynne",
                               "Games open from Mnemerynne: Media → Games.", back=self._games_url())
            return
        site = _site_row(site_url)
        if scope["type"] == "websocket":
            await self._websocket(scope, receive, send, site, "arcade", path)
        else:
            await self._site(scope, receive, send, site, path)

    async def _refuse(self, scope, receive, send, status: int, title: str, text: str, back: str = "") -> None:
        if scope["type"] == "websocket":
            await WebSocket(scope, receive, send).close(
                code=remote.WEBSOCKET_CLOSE_UNAUTHENTICATED if status == 401 else remote.WEBSOCKET_CLOSE_FORBIDDEN)
            return
        await _page(status, title, text, back)(scope, receive, send)

    async def _enter(self, scope, receive, send) -> None:
        """Spend a ticket from the main origin, set the arcade's cookie, and go to the page it was for."""
        token = (parse_qs(scope.get("query_string", b"").decode("latin-1")).get("t") or [""])[0]
        data = spend_ticket(self.secret_key, token)
        if data is None:
            await _page(403, "That link has expired", "Open the game again from Mnemerynne.",
                        self._games_url())(scope, receive, send)
            return
        state, user, _ = await anyio.to_thread.run_sync(self._user_for, scope, data)
        if state != "ok":
            await _page(403, "Games aren't open for you", "Ask an admin about Games in Mnemerynne.",
                        self._games_url())(scope, receive, send)
            return
        response = RedirectResponse(data["p"], status_code=303)
        response.headers.append("set-cookie", cookie_header(make_cookie(self.secret_key, user, data.get("s")),
                                                            secure=self._https()))
        response.headers["Cache-Control"] = "no-store"
        await response(scope, receive, send)

    async def _leave(self, scope, receive, send) -> None:
        response = _page(200, "Signed out of the arcade", "Open Games again from Mnemerynne.", self._games_url())
        response.headers.append("set-cookie", cookie_header("", secure=self._https(), max_age=0))
        await response(scope, receive, send)

    # -------------------------------------------------------------- http
    async def _site(self, scope, receive, send, site: dict, path: str) -> None:
        """The request to the site at the same path; the answer streams back as it comes."""
        query = scope.get("query_string", b"").decode("latin-1")
        try:
            url = remote.upstream_url(site, path, query)
        except remote.RemoteError as exc:
            await _page(404, "Not here", str(exc))(scope, receive, send)
            return
        client_ip = scope["client"][0] if scope.get("client") else ""
        arcade_host = next((v.decode("latin-1") for k, v in scope["headers"] if k.lower() == b"host"), "")
        headers = remote.request_headers(scope["headers"], client_ip=client_ip,
                                         scheme="https" if self._https() else scope.get("scheme", "http"),
                                         host=arcade_host, slug="arcade", rewrite=False, prefix="")
        has_body = any(k.lower() in (b"content-length", b"transfer-encoding") for k, _ in scope["headers"])
        client = self._client(site)
        request = client.build_request(scope["method"], url, headers=headers,
                                       content=remote._body(receive) if has_body else None)
        try:
            response = await client.send(request, stream=True)
        except httpx.HTTPError as exc:
            log.warning("Arcade: %s didn't answer: %s", site["base_url"], exc)
            await _page(502, "The games site didn't answer", "Try again in a moment.")(scope, receive, send)
            return
        try:
            await send({"type": "http.response.start", "status": response.status_code,
                        "headers": remote.response_headers(response.headers, site, "arcade", rewritten=False,
                                                           prefix="")})
            try:
                async for chunk in remote._chunks(response):
                    await send({"type": "http.response.body", "body": chunk, "more_body": True})
            except httpx.HTTPError as exc:   # the site went away mid-stream: end what we can
                log.warning("Arcade: stream from %s ended: %s", site["base_url"], exc)
            await send({"type": "http.response.body", "body": b"", "more_body": False})
        finally:
            await response.aclose()


class ArcadeHost:
    """ASGI middleware: requests for the arcade host go to the arcade, before any of Mnemerynne's own middleware,
    pages or API sees them; everything else goes on to Mnemerynne."""

    def __init__(self, app, proxy: ArcadeProxy, arcade_host: str) -> None:
        self.app = app
        self.proxy = proxy
        self.arcade_host = arcade_host

    async def __call__(self, scope, receive, send) -> None:
        if self.arcade_host and scope["type"] in ("http", "websocket"):
            raw = next((v.decode("latin-1") for k, v in scope.get("headers", []) if k.lower() == b"host"), "")
            if raw.rsplit(":", 1)[0].strip("[]").lower() == self.arcade_host:
                await self.proxy(scope, receive, send)
                return
        await self.app(scope, receive, send)
