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
- with rewrite_html on, it also rewrites links in HTML pages and in XML pages and their XSL
  stylesheets (RD Web Access builds its pages that way): root-relative ones, and absolute ones to the
  app's own address; and the plainest calls (fetch("/..."), "/api/..." strings) in the app's script
  files (best effort: apps that build URLs at runtime need their own base-path setting instead);
- with rewrite_html on, an XML page that names an XSL stylesheet (RD Web Access) is transformed
  here and sent as HTML: Firefox draws such pages blank in the Remote window, and browsers are
  dropping XSLT. The stylesheet and the files it reads come from the app through the proxy, as the
  person; if anything fails, the XML goes as it came and the browser transforms it;
- with a look set (xsl_override), such a page is drawn with a stylesheet bundled here instead of the
  app's (remote_overrides/: Erynne's look for RD Web Access), falling back to the app's own.

A proxied app runs on Mnemerynne's origin, so its scripts could call Mnemerynne's API as the
person: list only the organisation's own tools, for the groups that should have them.
"""

from __future__ import annotations

import asyncio
import logging
import re
import sqlite3
from pathlib import Path
from typing import Any, Awaitable, Callable
from urllib.parse import urljoin, urlsplit

import anyio
import httpx
from lxml import etree
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
# Never sent upstream, never accepted from it: the session, the theme, and the arcade's own sign-in (arcade.py).
SESSION_COOKIES = {"mnemerynne_session", "erynne_theme", "mnemerynne_arcade"}
HTML_MAX_BYTES = 5 * 1024 * 1024
XSLT_MAX_LOADS = 16           # files one transform may read: the stylesheet, its imports, document()
XSLT_ORIGIN = "http://remote.invalid"   # the address lxml sees; every load under it goes through the proxy
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
_XML_STYLESHEET = re.compile(r"<\?xml-stylesheet\s([^?]*)\?>")
_PI_ATTR = re.compile(r"""\b(type|href)\s*=\s*(["'])(.*?)\2""")
_XML_TYPES = ("text/xml", "application/xml")

# Looks bundled with Mnemerynne for apps that build their pages with XSLT (remote_apps.xsl_override).
OVERRIDES: dict[str, Path] = {"rdweb": Path(__file__).parent / "remote_overrides" / "rdweb" / "Site.xsl"}
LOOK_THEMES = ("default", "neon")
THEME_COOKIE = "erynne_theme"       # web/settings.THEME_COOKIE: the viewer's theme, for a look that follows it
_override_cache: dict[str, bytes] = {}


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


def rewrite_url(value: str, app: sqlite3.Row, slug: str, prefix: str | None = None) -> str:
    """An upstream URL as the browser should see it: absolute ones on the upstream's host and
    root-relative ones move under the prefix; relative ones and other sites stay. `prefix` "" serves the
    app at the root (the arcade): absolute links to it become root paths, root paths stay."""
    v = (value or "").strip()
    if not v:
        return value
    prefix = prefix_for(slug) if prefix is None else prefix
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


def rewrite_cookie(set_cookie: str, slug: str, base_path: str = "", prefix: str | None = None) -> str | None:
    """A Set-Cookie scoped to the app's prefix; cookies named like Mnemerynne's own are dropped. A path
    under the app's base path (base_url's) loses it, as its pages' addresses do. `prefix` "": paths stay."""
    parts = [p.strip() for p in set_cookie.split(";")]
    name = parts[0].split("=", 1)[0].strip()
    if name.lower() in SESSION_COOKIES:
        return None
    prefix = prefix_for(slug) if prefix is None else prefix
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
            if base_path and (val.lower() == base_path.lower() or val.lower().startswith(base_path.lower() + "/")):
                val = val[len(base_path):] or "/"
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
    return rewrite_script(text, slug)


def rewrite_script(text: str, slug: str) -> str:
    """Best effort, in a script file or a page's inline scripts: fetch("/...") and xhr.open("GET", "/..."),
    and "/api/..." strings (an app's own request helper), move under the prefix."""
    prefix = prefix_for(slug)
    skip = re.escape(prefix)
    text = re.sub(rf"""((?:\bfetch\(|\.open\(\s*["'][A-Za-z]+["']\s*,)\s*)(["'`])/(?!/|{skip[1:]}/)""",
                  lambda m: f"{m.group(1)}{m.group(2)}{prefix}/", text)
    return re.sub(rf"""(["'`])/api/(?!{skip[1:]})""", lambda m: f"{m.group(1)}{prefix}/api/", text)


def rewrite_absolute(text: str, app: sqlite3.Row, slug: str) -> str:
    """Links to the app's own address (http://10.0.0.5/app/x) move under the prefix (rewrite_url):
    the browser can't reach that address, or it isn't Mnemerynne's origin. So do paths under the
    app's base path (/app/x, base_url http://10.0.0.5/app) wherever they stand, not only in href
    or src: RD Web Access lists its icons and .rdp files in its XML, and its XSL places them."""
    base = urlsplit(app["base_url"])
    if not base.netloc:
        return text
    text = re.sub(rf"""\b(?:https?|wss?)://{re.escape(base.netloc)}(?![\w.:-])[^\s"'<>()]*""",
                  lambda m: rewrite_url(m.group(0), app, slug), text, flags=re.IGNORECASE)
    path = base.path.rstrip("/")
    if not path:
        return text
    return re.sub(rf"""(?<![\w/.:%~-]){re.escape(path)}(?=[/"'?#<\s]|$)""", prefix_for(slug), text,
                  flags=re.IGNORECASE)


_SCRIPT_TYPES = ("application/javascript", "text/javascript", "application/x-javascript", "application/ecmascript",
                 "text/ecmascript")
_PAGE_TYPES = ("text/html", "application/xhtml+xml", "text/xml", "application/xml", "text/xsl", "application/xslt+xml")


def _rewriter(content_type: str) -> Callable[[str, str], str] | None:
    """How a response of this type is rewritten (rewrite_html on): pages (HTML, or XML and its XSL) and
    script files; nothing else."""
    kind = content_type.split(";")[0].strip()
    if kind in _PAGE_TYPES:
        return rewrite_html
    if kind in _SCRIPT_TYPES:
        return rewrite_script
    return None


# ------------------------------------------------------------------- XSLT
def xsl_href(text: str) -> str | None:
    """The XSL stylesheet an XML page names (<?xml-stylesheet type="text/xsl" href="..."?>), or None."""
    for m in _XML_STYLESHEET.finditer(text):
        attrs = {k.lower(): v for k, _, v in _PI_ATTR.findall(m.group(1))}
        if "xsl" in attrs.get("type", "").lower() and attrs.get("href"):
            return attrs["href"]
    return None


def override_stylesheet(name: str | None) -> bytes | None:
    """A look bundled with Mnemerynne (remote_apps.xsl_override), or None for the app's own."""
    if name not in _override_cache:
        path = OVERRIDES.get(name or "")
        try:
            _override_cache[name] = path.read_bytes() if path is not None else None
        except OSError:
            log.warning("Remote look %s: %s can't be read; using the app's own stylesheet.", name, path)
            return None
    return _override_cache[name]


def available_looks() -> list[str]:
    """The bundled looks that are actually there (an admin can only pick these)."""
    return [name for name, path in OVERRIDES.items() if path.is_file()]


def look_params(app: sqlite3.Row, headers: list[tuple[bytes, bytes]]) -> dict[str, str]:
    """What a bundled look is told: the theme, fixed for the app or else the viewer's (their theme cookie)."""
    theme = app["look_theme"]
    if not theme:
        for name, value in headers:
            if name.lower() == b"cookie":
                for part in value.decode("latin-1").split(";"):
                    key, _, val = part.strip().partition("=")
                    if key == THEME_COOKIE:
                        theme = val.strip()
    return {"theme": theme if theme in LOOK_THEMES else "default"}


def transform_xml(text: str, page: str, href: str, load: Callable[[str], bytes | None], *,
                  stylesheet: bytes | None = None, params: dict[str, str] | None = None) -> bytes:
    """An XML page (at `page`, a path under the prefix) with its stylesheet (`href`) applied, as the
    browser would. Every file the transform reads, the stylesheet first, comes from load(path), a path
    under the page's prefix, at most XSLT_MAX_LOADS of them; nothing is read from disk or elsewhere.
    A bundled look (`stylesheet`) is used in place of the app's, at the same address, so the files it
    reads relative to itself still come from the app; `params` are passed to it as strings.
    Raises etree.LxmlError when the page or the stylesheet doesn't parse or apply."""
    loads = 0

    def fetch(url: str) -> bytes | None:
        nonlocal loads
        parts = urlsplit(url or "")
        if f"{parts.scheme}://{parts.netloc}" != XSLT_ORIGIN or loads >= XSLT_MAX_LOADS:
            return None
        loads += 1
        return load(parts.path + (f"?{parts.query}" if parts.query else ""))

    class Loader(etree.Resolver):
        def resolve(self, url, pubid, context):
            data = fetch(url)    # anything refused reads as an empty document, never from libxml2 itself
            return self.resolve_string(data if data is not None else b"<nothing/>", context)

    parser = etree.XMLParser(no_network=True, resolve_entities=False, load_dtd=False)
    parser.resolvers.add(Loader())
    doc = etree.fromstring(text.encode("utf-8", "surrogateescape"), parser, base_url=XSLT_ORIGIN + page)
    xsl_url = urljoin(XSLT_ORIGIN + page, href)
    source = stylesheet if stylesheet is not None else fetch(xsl_url)
    if source is None:
        raise etree.XSLTParseError(f"no stylesheet at {href}")
    xsl = etree.fromstring(source, parser, base_url=xsl_url)
    access = etree.XSLTAccessControl(read_file=False, write_file=False, create_dir=False, read_network=True,
                                     write_network=False)
    values = {k: etree.XSLT.strparam(v) for k, v in (params or {}).items()}
    return bytes(etree.XSLT(xsl, access_control=access)(doc, **values))


# ----------------------------------------------------------------- headers
def _cookie_without_sessions(value: str) -> str:
    kept = [part for part in value.split(";") if part.strip().split("=", 1)[0].strip().lower() not in SESSION_COOKIES]
    return "; ".join(p.strip() for p in kept if p.strip())


def request_headers(raw: list[tuple[bytes, bytes]], *, client_ip: str, scheme: str, host: str, slug: str,
                    rewrite: bool, websocket: bool = False, prefix: str | None = None) -> list[tuple[str, str]]:
    """The upstream request's headers: the browser's, minus hop-by-hop, forwarding and Mnemerynne's
    cookies, plus our own X-Forwarded-* (X-Forwarded-Prefix only under a prefix; "" is the root)."""
    prefix = prefix_for(slug) if prefix is None else prefix
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
    out += [("x-forwarded-for", client_ip or ""), ("x-forwarded-proto", scheme), ("x-forwarded-host", host)]
    if prefix:
        out.append(("x-forwarded-prefix", prefix))
    out.append(("x-real-ip", client_ip or ""))
    if rewrite:
        out.append(("accept-encoding", "identity"))
    return out


def response_headers(headers: httpx.Headers, app: sqlite3.Row, slug: str, *, rewritten: bool,
                     prefix: str | None = None) -> list[tuple[bytes, bytes]]:
    """The upstream response's headers for the browser (see the module docstring)."""
    out: list[tuple[bytes, bytes]] = []
    for key, value in headers.multi_items():
        name = key.lower()
        if name in HOP_BY_HOP or name in STRIP_RESPONSE:
            continue
        if rewritten and name in ("content-length", "content-encoding"):
            continue
        if name == "location":
            value = rewrite_url(value, app, slug, prefix)
        elif name == "set-cookie":
            value = rewrite_cookie(value, slug, urlsplit(app["base_url"]).path.rstrip("/"), prefix)
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


async def _transformed(text: str, page: str, href: str, load: Callable[[str], Awaitable[bytes | None]],
                       slug: str, look: tuple[bytes, dict[str, str]] | None = None) -> bytes | None:
    """transform_xml in a worker thread, its loads back on this loop: with the bundled look if the app
    has one, else (or if that fails) the app's own stylesheet; None (send the XML) if that fails too."""
    def run(stylesheet: bytes | None, params: dict[str, str] | None) -> bytes:
        return transform_xml(text, page, href, lambda path: anyio.from_thread.run(load, path),
                             stylesheet=stylesheet, params=params)

    if look is not None:
        try:
            html = await anyio.to_thread.run_sync(run, *look)
            if html:
                return html
        except (etree.LxmlError, ValueError) as exc:
            log.warning("Remote %s: the bundled look failed on %s, using the app's own: %s", slug, page, exc)
    try:
        html = await anyio.to_thread.run_sync(run, None, None)
    except (etree.LxmlError, ValueError) as exc:
        log.info("Remote %s: %s left for the browser to transform: %s", slug, page, exc)
        return None
    return html or None


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

    def _prefix(self, slug: str) -> str:
        """Where the app sits for the browser: /remote/<slug> here; the arcade (arcade.py) serves its site at ""."""
        return prefix_for(slug)

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
        prefix = prefix_for(slug)
        # What the page's stylesheet reads (XSLT): a GET as this person, without the page request's body.
        load_headers = [(k, v) for k, v in headers if k.lower() not in ("content-length", "content-type",
                                                                        "transfer-encoding", "expect")]

        async def load(path: str) -> bytes | None:
            sub, _, sub_query = path.partition("?")
            if not sub.startswith(prefix + "/"):
                return None
            try:
                got = await client.get(upstream_url(app, sub[len(prefix):], sub_query), headers=load_headers)
            except (RemoteError, httpx.HTTPError):
                return None
            if got.status_code != 200 or len(got.content) > HTML_MAX_BYTES:
                return None
            text = got.content.decode("utf-8", "surrogateescape")
            rewriter = _rewriter(got.headers.get("content-type", "").lower())
            if rewriter is not None:   # as the browser would have had it
                text = rewriter(rewrite_absolute(text, app, slug), slug)
            return text.encode("utf-8", "surrogateescape")

        stylesheet = override_stylesheet(app["xsl_override"]) if rewrite else None
        look = (stylesheet, look_params(app, scope["headers"])) if stylesheet is not None else None
        try:
            await self._relay(send, response, app, slug, rewrite, page=prefix + (rest or "/"), load=load, look=look)
        finally:
            await response.aclose()

    async def _relay(self, send, response: httpx.Response, app: sqlite3.Row, slug: str, rewrite: bool, *,
                     page: str = "", load: Callable[[str], Awaitable[bytes | None]] | None = None,
                     look: tuple[bytes, dict[str, str]] | None = None) -> None:
        content_type = response.headers.get("content-type", "").lower()
        chunks = _chunks(response)
        rewriter = _rewriter(content_type) if rewrite else None
        if rewriter is not None:
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
                    text = rewriter(rewrite_absolute(b"".join(parts).decode("utf-8", "surrogateescape"), app, slug), slug)
                    headers = response_headers(response.headers, app, slug, rewritten=True)
                    href = xsl_href(text) if load is not None and content_type.split(";")[0].strip() in _XML_TYPES else None
                    body = await _transformed(text, page, href, load, slug, look) if href else None
                    if body is not None:
                        headers = [(k, v) for k, v in headers if k != b"content-type"]
                        headers.append((b"content-type", b"text/html; charset=utf-8"))
                    else:
                        body = text.encode("utf-8", "surrogateescape")
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
                                  slug=slug, rewrite=False, websocket=True, prefix=self._prefix(slug))
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
