"""Remote apps: LAN-only web apps reached through Mnemerynne.

An admin lists an app on Access → Remote (a slug, its address on the LAN, the group that may open
it, and for a self-signed server its certificate). It is then served at /remote/<slug>/... to
signed-in people in that group (admins always), on the Remote tab or in a tab of its own, by
RemoteProxy: a plain reverse proxy that streams requests and responses both ways, websockets
included, and never follows redirects itself.

What the proxy changes on the way:
- it never forwards Mnemerynne's own cookies (the session stays here), strips hop-by-hop and
  spoofable forwarding headers and sets its own X-Forwarded-* (with X-Forwarded-Prefix, which
  apps that support a base path honour);
- it drops the upstream's X-Frame-Options and frame-ancestors so the Remote tab can show the app
  in its window (the web app's own headers then allow framing by Mnemerynne only), rewrites
  Location and Set-Cookie to the /remote/<slug> prefix, and drops cookies named like Mnemerynne's;
- with rewrite_html on, it also rewrites absolute links in HTML pages (best effort: apps that
  build URLs at runtime need their own base-path setting instead).

A proxied app runs on Mnemerynne's origin, so its scripts could call Mnemerynne's API as the
person: list only the organisation's own tools, for the groups that should have them.
"""

from __future__ import annotations

import asyncio
import logging
import re
import sqlite3
from typing import Any, Callable
from urllib.parse import urlsplit

import anyio
import httpx
from starlette.requests import HTTPConnection
from starlette.responses import JSONResponse, RedirectResponse
from starlette.websockets import WebSocket

from mnemerynne import apis, db, group_refs
from mnemerynne.config import Settings
from mnemerynne.web import deps

log = logging.getLogger("mnemerynne.remote")

PREFIX = "/remote"
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")
RESERVED_SLUGS = {"home", "api", "new"}
HOP_BY_HOP = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailer",
              "transfer-encoding", "upgrade"}
STRIP_REQUEST = {"host", "forwarded", "x-real-ip"}           # plus every x-forwarded-*: ours replace them
STRIP_RESPONSE = {"x-frame-options", "strict-transport-security", "alt-svc", "public-key-pins",
                  "content-security-policy-report-only"}
SESSION_COOKIES = {"mnemerynne_session", "erynne_theme"}    # never sent upstream, never accepted from it
HTML_MAX_BYTES = 5 * 1024 * 1024
CONNECT_TIMEOUT = 10.0
WEBSOCKET_CLOSE_UNAUTHENTICATED, WEBSOCKET_CLOSE_FORBIDDEN, WEBSOCKET_CLOSE_UPSTREAM = 4401, 4403, 1011

# Test hooks: an httpx transport in place of the network, and a websocket connect() in place of
# websockets.asyncio.client.connect (an async callable returning a connection-like object).
TRANSPORT: httpx.AsyncBaseTransport | None = None
WS_CONNECT: Callable[..., Any] | None = None

_FRAME_ANCESTORS = re.compile(r"\s*frame-ancestors[^;]*;?", re.IGNORECASE)
_ATTR = re.compile(r"""(\b(?:href|src|action|formaction|poster|data-src)\s*=\s*)(["'])/(?!/)""", re.IGNORECASE)
_SRCSET = re.compile(r"""(\bsrcset\s*=\s*)(["'])([^"']*)(["'])""", re.IGNORECASE)
_CSS_URL = re.compile(r"""(\burl\(\s*)(["']?)/(?!/)""", re.IGNORECASE)
_JS = re.compile(r"""((?:\bfetch\(|\.open\(\s*["'][A-Za-z]+["']\s*,)\s*)(["'`])/(?!/)""")
_API_LITERAL = re.compile(r"""(["'`])(/api/)""")


class RemoteError(ValueError):
    """Refused; the message is safe to show."""


# ------------------------------------------------------------------ apps
def get_app(conn: sqlite3.Connection, slug: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM remote_apps WHERE slug = ? COLLATE NOCASE", ((slug or "").strip().lower(),)).fetchone()


def apps_for(conn: sqlite3.Connection, user: sqlite3.Row, directory, admin: bool) -> list[sqlite3.Row]:
    """The enabled apps this person may open: all of them for an admin, otherwise the ones whose
    group they are in (an app with no group is for admins only)."""
    rows = conn.execute("SELECT * FROM remote_apps WHERE enabled = 1 ORDER BY name COLLATE NOCASE").fetchall()
    if admin:
        return rows
    groups = None
    out = []
    for r in rows:
        if not r["group_ref"]:
            continue
        if groups is None:
            groups = group_refs.member_of(conn, user, directory)
        if group_refs.in_any(conn, user, directory, r["group_ref"], groups):
            out.append(r)
    return out


def app_json(row: sqlite3.Row) -> dict:
    return {"slug": row["slug"], "name": row["name"], "description": row["description"], "embed": bool(row["embed"]),
            "url": f"{PREFIX}/{row['slug']}/"}


def check_slug(slug: str) -> str:
    slug = (slug or "").strip().lower()
    if not SLUG_RE.match(slug) or slug in RESERVED_SLUGS:
        raise RemoteError("A slug is 1-40 lower-case letters, digits and dashes, e.g. balancer or nas-ui.")
    return slug


# ----------------------------------------------------------------- rewriting
def prefix_for(slug: str) -> str:
    return f"{PREFIX}/{slug}"


def upstream_url(app: sqlite3.Row, subpath: str, query: str = "", *, websocket: bool = False) -> str:
    """The upstream address for a request under the prefix; '..' and backslashes are refused."""
    subpath = subpath or "/"
    if "\\" in subpath or any(seg == ".." for seg in subpath.split("/")):
        raise RemoteError("That path isn't allowed.")
    base = app["base_url"].rstrip("/")
    if websocket:
        base = re.sub(r"^http", "ws", base, count=1)
    return base + (subpath if subpath.startswith("/") else "/" + subpath) + (f"?{query}" if query else "")


def rewrite_url(value: str, app: sqlite3.Row, slug: str) -> str:
    """An upstream URL as the browser should see it: absolute ones on the upstream's host and
    root-relative ones move under the prefix; relative ones and other sites stay."""
    v = (value or "").strip()
    if not v:
        return value
    prefix = prefix_for(slug)
    base = urlsplit(app["base_url"].rstrip("/"))
    parts = urlsplit(v)
    if parts.scheme and parts.netloc:
        if parts.scheme.lower() in ("http", "https", "ws", "wss") and parts.netloc.lower() == base.netloc.lower():
            path = parts.path or "/"
            if base.path and path.startswith(base.path):
                path = path[len(base.path):] or "/"
            return prefix + path + (f"?{parts.query}" if parts.query else "") + (f"#{parts.fragment}" if parts.fragment else "")
        return value
    if v.startswith("//") or not v.startswith("/"):
        return value
    if v == prefix or v.startswith(prefix + "/"):
        return value
    if base.path and v.startswith(base.path + "/"):
        v = v[len(base.path):]
    return prefix + v


def rewrite_cookie(set_cookie: str, slug: str) -> str | None:
    """A Set-Cookie scoped to the app's prefix; cookies named like Mnemerynne's own are dropped."""
    parts = [p.strip() for p in set_cookie.split(";")]
    name = parts[0].split("=", 1)[0].strip()
    if name.lower() in SESSION_COOKIES:
        return None
    prefix = prefix_for(slug)
    out, has_path = [parts[0]], False
    for attr in parts[1:]:
        key, _, val = attr.partition("=")
        k = key.strip().lower()
        if k == "domain":
            continue
        if k == "path":
            val = val.strip() or "/"
            if not val.startswith("/"):
                val = "/" + val
            attr = f"Path={prefix}{val if val != '/' else '/'}"
            has_path = True
        out.append(attr)
    if not has_path:
        out.append(f"Path={prefix}/")
    return "; ".join(out)


def rewrite_html(text: str, slug: str) -> str:
    """Best effort: absolute links in markup, stylesheets and the plainest script calls move under
    the prefix. Links already under it, protocol-relative ones and other sites are left alone."""
    prefix = prefix_for(slug)
    skip = re.escape(prefix)
    text = re.sub(rf"""(\b(?:href|src|action|formaction|poster|data-src)\s*=\s*)(["'])/(?!/|{skip[1:]}/|{skip[1:]}["'])""",
                  lambda m: f"{m.group(1)}{m.group(2)}{prefix}/", text, flags=re.IGNORECASE)

    def srcset(m):
        value = re.sub(rf"(^|,\s*)/(?!/|{skip[1:]}/)", lambda n: f"{n.group(1)}{prefix}/", m.group(3))
        return f"{m.group(1)}{m.group(2)}{value}{m.group(4)}"
    text = _SRCSET.sub(srcset, text)
    text = re.sub(rf"""(\burl\(\s*)(["']?)/(?!/|{skip[1:]}/)""", lambda m: f"{m.group(1)}{m.group(2)}{prefix}/", text,
                  flags=re.IGNORECASE)
    text = re.sub(rf"""((?:\bfetch\(|\.open\(\s*["'][A-Za-z]+["']\s*,)\s*)(["'`])/(?!/|{skip[1:]}/)""",
                  lambda m: f"{m.group(1)}{m.group(2)}{prefix}/", text)
    text = re.sub(rf"""(["'`])/api/(?!{skip[1:]})""", lambda m: f"{m.group(1)}{prefix}/api/", text)
    return text


# ----------------------------------------------------------------- headers
def _cookie_without_sessions(value: str) -> str:
    kept = [part for part in value.split(";") if part.strip().split("=", 1)[0].strip().lower() not in SESSION_COOKIES]
    return "; ".join(p.strip() for p in kept if p.strip())


def request_headers(raw: list[tuple[bytes, bytes]], *, client_ip: str, scheme: str, host: str, slug: str,
                    rewrite: bool, websocket: bool = False) -> list[tuple[str, str]]:
    """The upstream request's headers: the browser's, minus hop-by-hop, forwarding and Mnemerynne's
    cookies, plus our own X-Forwarded-*."""
    headers = [(k.decode("latin-1").lower(), v.decode("latin-1")) for k, v in raw]
    drop = set(HOP_BY_HOP) | STRIP_REQUEST
    for name, value in headers:
        if name == "connection":
            drop |= {t.strip().lower() for t in value.split(",") if t.strip()}
    out: list[tuple[str, str]] = []
    for name, value in headers:
        if name in drop or name.startswith("x-forwarded-"):
            continue
        if websocket and name.startswith("sec-websocket-"):
            continue
        if name == "cookie":
            value = _cookie_without_sessions(value)
            if not value:
                continue
        if name == "accept-encoding" and rewrite:
            continue
        out.append((name, value))
    out += [("x-forwarded-for", client_ip or ""), ("x-forwarded-proto", scheme), ("x-forwarded-host", host),
            ("x-forwarded-prefix", prefix_for(slug)), ("x-real-ip", client_ip or "")]
    if rewrite:
        out.append(("accept-encoding", "identity"))
    return out


def response_headers(headers: httpx.Headers, app: sqlite3.Row, slug: str, *, rewritten: bool) -> list[tuple[bytes, bytes]]:
    """The upstream response's headers for the browser (see the module docstring)."""
    out: list[tuple[bytes, bytes]] = []
    for key, value in headers.multi_items():
        name = key.lower()
        if name in HOP_BY_HOP or name in STRIP_RESPONSE:
            continue
        if rewritten and name in ("content-length", "content-encoding"):
            continue
        if name == "location":
            value = rewrite_url(value, app, slug)
        elif name == "set-cookie":
            value = rewrite_cookie(value, slug)
            if value is None:
                continue
        elif name == "content-security-policy":
            value = _FRAME_ANCESTORS.sub("", value).strip("; ").strip()
            if not value:
                continue
        out.append((name.encode("latin-1"), value.encode("latin-1", "replace")))
    return out


# ------------------------------------------------------------------- the proxy
async def _body(receive) -> Any:
    while True:
        message = await receive()
        if message["type"] == "http.disconnect":
            return
        chunk = message.get("body", b"")
        if chunk:
            yield chunk
        if not message.get("more_body", False):
            return


async def _chunks(response: httpx.Response):
    """The response body as it arrives; a body httpx already holds (a test transport's) comes whole."""
    if response.is_stream_consumed:
        data = await response.aread()
        if data:
            yield data
        return
    async for chunk in response.aiter_raw():
        yield chunk


def _wants_html(headers: list[tuple[bytes, bytes]]) -> bool:
    accept = next((v.decode("latin-1") for k, v in headers if k.lower() == b"accept"), "")
    return "text/html" in accept or "*/*" in accept and "application/json" not in accept


class RemoteProxy:
    """ASGI app mounted at /remote: /remote/<slug>/<path> for http and websocket requests."""

    def __init__(self, settings: Settings, get_directory: Callable[[], Any] = lambda: None):
        self.settings = settings
        self.get_directory = get_directory
        self._clients: dict[tuple[int, str], httpx.AsyncClient] = {}

    async def aclose(self) -> None:
        clients, self._clients = list(self._clients.values()), {}
        for client in clients:
            await client.aclose()

    def _client(self, app: sqlite3.Row) -> httpx.AsyncClient:
        key = (app["id"], app["updated_at"])
        client = self._clients.get(key)
        if client is None:
            for stale in [k for k in self._clients if k[0] == app["id"]]:   # the admin changed it
                asyncio.ensure_future(self._clients.pop(stale).aclose())
            client = httpx.AsyncClient(verify=apis._ssl(app), follow_redirects=False,
                                       timeout=httpx.Timeout(None, connect=CONNECT_TIMEOUT, pool=CONNECT_TIMEOUT),
                                       transport=TRANSPORT)
            self._clients[key] = client
        return client

    # ------------------------------------------------------------ routing
    @staticmethod
    def _split(scope) -> tuple[str, str]:
        """(slug, path under it) from the request path; '' slug for /remote itself."""
        root = scope.get("root_path", "") or ""
        path = scope["path"]
        sub = path[len(root):] if root and path.startswith(root) else path
        m = re.match(r"^/?([^/]*)(/.*)?$", sub)
        return (m.group(1).lower(), m.group(2) or "") if m else ("", "")

    def _authorize(self, scope, slug: str) -> tuple[str, Any, Any]:
        """('ok' | 'login' | 'forbidden' | 'notfound', user, app), checked against the database."""
        connection = HTTPConnection(scope)
        conn = db.connect(self.settings.db_path)
        try:
            user = deps._session_user(connection, conn)
            if user is None:
                return "login", None, None
            app = get_app(conn, slug)
            if app is None or not app["enabled"]:
                return "notfound", user, None
            if deps.is_admin(connection, conn, user):
                return "ok", user, app
            if app["group_ref"] and group_refs.in_any(conn, user, self.get_directory(), app["group_ref"]):
                return "ok", user, app
            return "forbidden", user, None
        finally:
            conn.close()

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] not in ("http", "websocket"):
            return
        slug, rest = self._split(scope)
        if scope["type"] == "http" and (not slug or not SLUG_RE.match(slug)):
            await RedirectResponse(PREFIX, status_code=303)(scope, receive, send)
            return
        if scope["type"] == "http" and not rest:
            query = scope.get("query_string", b"").decode("latin-1")
            await RedirectResponse(f"{PREFIX}/{slug}/" + (f"?{query}" if query else ""), status_code=308)(scope, receive, send)
            return
        state, user, app = await anyio.to_thread.run_sync(self._authorize, scope, slug)
        if scope["type"] == "websocket":
            if state != "ok":
                ws = WebSocket(scope, receive, send)
                await ws.close(code=WEBSOCKET_CLOSE_UNAUTHENTICATED if state == "login" else WEBSOCKET_CLOSE_FORBIDDEN)
                return
            await self._websocket(scope, receive, send, app, slug, rest)
            return
        if state == "login":
            if scope["method"] in ("GET", "HEAD") and _wants_html(scope["headers"]):
                query = scope.get("query_string", b"").decode("latin-1")
                scope.setdefault("session", {})["after_login"] = f"{PREFIX}/{slug}{rest}" + (f"?{query}" if query else "")
                await RedirectResponse("/login", status_code=303)(scope, receive, send)
            else:
                await JSONResponse({"detail": "Not authenticated"}, status_code=401)(scope, receive, send)
            return
        if state == "notfound":
            await JSONResponse({"detail": "No such remote app"}, status_code=404)(scope, receive, send)
            return
        if state == "forbidden":
            await JSONResponse({"detail": "You can't open this app"}, status_code=403)(scope, receive, send)
            return
        await self._http(scope, receive, send, app, slug, rest)

    # --------------------------------------------------------------- http
    async def _http(self, scope, receive, send, app: sqlite3.Row, slug: str, rest: str) -> None:
        query = scope.get("query_string", b"").decode("latin-1")
        try:
            url = upstream_url(app, rest, query)
        except RemoteError as exc:
            await JSONResponse({"detail": str(exc)}, status_code=404)(scope, receive, send)
            return
        client_ip = scope["client"][0] if scope.get("client") else ""
        host = next((v.decode("latin-1") for k, v in scope["headers"] if k.lower() == b"host"), "")
        rewrite = bool(app["rewrite_html"])
        headers = request_headers(scope["headers"], client_ip=client_ip, scheme=scope.get("scheme", "http"), host=host,
                                  slug=slug, rewrite=rewrite)
        has_body = any(k.lower() in (b"content-length", b"transfer-encoding") for k, _ in scope["headers"])
        client = self._client(app)
        request = client.build_request(scope["method"], url, headers=headers, content=_body(receive) if has_body else None)
        try:
            response = await client.send(request, stream=True)
        except httpx.HTTPError as exc:
            log.warning("Remote %s: %s didn't answer: %s", slug, app["base_url"], exc)
            await JSONResponse({"detail": f"{app['name']} didn't answer: {exc.__class__.__name__}"},
                               status_code=502)(scope, receive, send)
            return
        try:
            await self._relay(send, response, app, slug, rewrite)
        finally:
            await response.aclose()

    async def _relay(self, send, response: httpx.Response, app: sqlite3.Row, slug: str, rewrite: bool) -> None:
        content_type = response.headers.get("content-type", "").lower()
        chunks = _chunks(response)
        if rewrite and content_type.startswith("text/html"):
            length = response.headers.get("content-length")
            if length is None or int(length) <= HTML_MAX_BYTES:
                parts, total, over = [], 0, False
                async for chunk in chunks:
                    parts.append(chunk)
                    total += len(chunk)
                    if total > HTML_MAX_BYTES:
                        over = True
                        break
                if not over:
                    text = b"".join(parts).decode("utf-8", "surrogateescape")
                    body = rewrite_html(text, slug).encode("utf-8", "surrogateescape")
                    headers = response_headers(response.headers, app, slug, rewritten=True)
                    headers.append((b"content-length", str(len(body)).encode()))
                    await send({"type": "http.response.start", "status": response.status_code, "headers": headers})
                    await send({"type": "http.response.body", "body": body, "more_body": False})
                    return
                # Too big to rewrite after all: pass what we have, then the rest.
                await send({"type": "http.response.start", "status": response.status_code,
                            "headers": response_headers(response.headers, app, slug, rewritten=False)})
                for chunk in parts:
                    await send({"type": "http.response.body", "body": chunk, "more_body": True})
                async for chunk in chunks:
                    await send({"type": "http.response.body", "body": chunk, "more_body": True})
                await send({"type": "http.response.body", "body": b"", "more_body": False})
                return
        await send({"type": "http.response.start", "status": response.status_code,
                    "headers": response_headers(response.headers, app, slug, rewritten=False)})
        try:
            async for chunk in chunks:
                await send({"type": "http.response.body", "body": chunk, "more_body": True})
        except httpx.HTTPError as exc:   # the upstream went away mid-stream: end what we can
            log.warning("Remote %s: stream from %s ended: %s", slug, app["base_url"], exc)
        await send({"type": "http.response.body", "body": b"", "more_body": False})

    # ---------------------------------------------------------- websocket
    async def _websocket(self, scope, receive, send, app: sqlite3.Row, slug: str, rest: str) -> None:
        from websockets.exceptions import ConnectionClosed

        ws = WebSocket(scope, receive, send)
        query = scope.get("query_string", b"").decode("latin-1")
        try:
            url = upstream_url(app, rest, query, websocket=True)
        except RemoteError:
            await ws.close(code=1008)
            return
        client_ip = scope["client"][0] if scope.get("client") else ""
        host = next((v.decode("latin-1") for k, v in scope["headers"] if k.lower() == b"host"), "")
        headers = request_headers(scope["headers"], client_ip=client_ip, scheme=scope.get("scheme", "ws"), host=host,
                                  slug=slug, rewrite=False, websocket=True)
        offered = [p.strip() for p in ws.headers.get("sec-websocket-protocol", "").split(",") if p.strip()] or None
        ssl_ctx = apis._ssl(app) if url.startswith("wss") else None
        connect = WS_CONNECT
        if connect is None:
            from websockets.asyncio.client import connect as ws_connect
            connect = ws_connect
        try:
            upstream = await connect(url, additional_headers=headers, subprotocols=offered,
                                     ssl=None if ssl_ctx is True else ssl_ctx, open_timeout=CONNECT_TIMEOUT,
                                     ping_interval=None, max_size=None)
        except Exception as exc:   # refused, timed out, bad handshake: nothing to relay
            log.warning("Remote %s: websocket to %s failed: %s", slug, url, exc)
            await ws.close(code=WEBSOCKET_CLOSE_UPSTREAM, reason=f"{app['name']} didn't answer")
            return
        await ws.accept(subprotocol=getattr(upstream, "subprotocol", None))

        async def to_upstream():
            while True:
                message = await ws.receive()
                if message["type"] == "websocket.disconnect":
                    await upstream.close(code=message.get("code", 1000) or 1000)
                    return
                if message.get("text") is not None:
                    await upstream.send(message["text"])
                elif message.get("bytes") is not None:
                    await upstream.send(message["bytes"])

        async def to_client():
            code, reason = 1000, ""
            try:
                async for data in upstream:
                    if isinstance(data, str):
                        await ws.send_text(data)
                    else:
                        await ws.send_bytes(data)
            except ConnectionClosed as exc:
                if exc.rcvd is not None:
                    code, reason = exc.rcvd.code, exc.rcvd.reason
            try:
                await ws.close(code=code, reason=reason)
            except RuntimeError:
                pass

        tasks = [asyncio.ensure_future(to_upstream()), asyncio.ensure_future(to_client())]
        try:
            await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            try:
                await upstream.close()
            except Exception:
                pass
