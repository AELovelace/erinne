"""Remote apps: LAN-only web apps served through Mnemerynne at /remote/<slug>/, behind its sign-in and
groups, with headers, cookies and links moved under the prefix; websockets relayed; the Remote tab;
the admin page."""

import asyncio
from contextlib import contextmanager

import httpx
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from fakes import FakeDirectory, FakeEmbedder
from mnemerynne import remote, users
from mnemerynne.web import app as app_mod
from mnemerynne.web.app import Services, create_app


@pytest.fixture(autouse=True)
def _hooks():
    yield
    remote.TRANSPORT = None
    remote.WS_CONNECT = None


@pytest.fixture
def people(conn):
    out = {"alice": users.create_user(conn, "alice", "password123"),
           "rob": users.create_user(conn, "rob", "password123"),
           "root": users.create_user(conn, "root", "password123", role="admin")}
    conn.execute("UPDATE users SET ad_account = username")
    return out


@pytest.fixture
def directory(people):
    return FakeDirectory({"alice": {"Ops"}, "rob": {"Staff"}, "root": {"Domain Admins"}},
                         groups=["Ops", "Staff", "Domain Admins"])


def _app(conn, slug="balancer", base="http://10.0.5.20:9180", group="Ops", **cols):
    cols = {"slug": slug, "name": slug.capitalize(), "base_url": base, "group_ref": group, **cols}
    conn.execute(f"INSERT INTO remote_apps({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})", tuple(cols.values()))
    return conn.execute("SELECT * FROM remote_apps WHERE slug = ?", (slug,)).fetchone()


@contextmanager
def _web(settings, name, directory):
    with TestClient(create_app(settings, Services(llm=None, embedder=FakeEmbedder(), directory=directory))) as c:
        if name:
            c.post("/login", data={"username": name, "password": "password123"})
        yield c


def _upstream(seen, handler=None):
    async def default(request):
        seen.append(request)
        return httpx.Response(200, headers={"content-type": "text/html; charset=utf-8", "x-frame-options": "DENY",
                                            "content-security-policy": "default-src 'self'; frame-ancestors 'none'",
                                            "strict-transport-security": "max-age=1"},
                              content=b'<a href="/x">x</a><img src="//cdn.example/i.png"><script>fetch("/api/v1")</script>')
    remote.TRANSPORT = httpx.MockTransport(handler or default)


# ------------------------------------------------------------- the helpers
def test_links_cookies_and_pages_move_under_the_prefix(conn, people):
    app = _app(conn, base="http://10.0.5.20:9180/base")
    assert remote.rewrite_url("/x?y=1#z", app, "balancer") == "/remote/balancer/x?y=1#z"
    assert remote.rewrite_url("http://10.0.5.20:9180/base/login", app, "balancer") == "/remote/balancer/login"
    assert remote.rewrite_url("http://10.0.5.20:9180/base", app, "balancer") == "/remote/balancer/"
    assert remote.rewrite_url("/base/status", app, "balancer") == "/remote/balancer/status"
    assert remote.rewrite_url("/remote/balancer/x", app, "balancer") == "/remote/balancer/x"
    assert remote.rewrite_url("https://other.example/x", app, "balancer") == "https://other.example/x"
    assert remote.rewrite_url("//cdn.example/x", app, "balancer") == "//cdn.example/x"
    assert remote.rewrite_url("status.html", app, "balancer") == "status.html"
    assert remote.upstream_url(app, "/path", "x=1") == "http://10.0.5.20:9180/base/path?x=1"
    assert remote.upstream_url(app, "/ws", "", websocket=True) == "ws://10.0.5.20:9180/base/ws"
    with pytest.raises(remote.RemoteError):
        remote.upstream_url(app, "/../etc")

    assert remote.rewrite_cookie("sid=1; Path=/; HttpOnly; Domain=lan", "balancer") == "sid=1; Path=/remote/balancer/; HttpOnly"
    assert remote.rewrite_cookie("sid=1; Path=/app", "balancer") == "sid=1; Path=/remote/balancer/app"
    assert remote.rewrite_cookie("sid=1", "balancer") == "sid=1; Path=/remote/balancer/"
    assert remote.rewrite_cookie("mnemerynne_session=evil; Path=/", "balancer") is None

    html = ('<a href="/x">x</a> <img src="//cdn/i.png" srcset="/a.png 1x, /b.png 2x"> <form action=\'/post\'>'
            '<style>.a{background:url(/img.png)}</style><script>fetch("/api/v1"); x.open("GET", "/data"); const u = "/api/z";</script>'
            '<a href="/remote/balancer/done">done</a>')
    out = remote.rewrite_html(html, "balancer")
    assert 'href="/remote/balancer/x"' in out and 'src="//cdn/i.png"' in out
    assert 'srcset="/remote/balancer/a.png 1x, /remote/balancer/b.png 2x"' in out
    assert "action='/remote/balancer/post'" in out and "url(/remote/balancer/img.png)" in out
    assert 'fetch("/remote/balancer/api/v1")' in out and 'x.open("GET", "/remote/balancer/data")' in out
    assert 'const u = "/remote/balancer/api/z"' in out and 'href="/remote/balancer/done"' in out
    assert out.count("/remote/balancer/remote/") == 0


def test_xml_pages_absolute_links_and_cookie_paths_under_a_base_path():
    """RD Web Access: an XML page whose XSL sets <base> from an absolute baseurl, and cookies on /RDWeb/."""
    app = {"base_url": "https://192.168.1.4/RDWeb"}
    page = ('<?xml-stylesheet type="text/xsl" href="../Site.xsl"?><RDWAPage baseurl="https://192.168.1.4/RDWeb/Pages/en-US/">'
            '<a href="https://192.168.1.40/x">another box</a> <a href="https://192.168.1.4:8443/y">another port</a>'
            '<a href="https://elsewhere.example/z">a site</a></RDWAPage>')
    out = remote.rewrite_absolute(page, app, "rdweb")
    assert 'baseurl="/remote/rdweb/Pages/en-US/"' in out and 'href="../Site.xsl"' in out
    assert "https://192.168.1.40/x" in out and "https://192.168.1.4:8443/y" in out and "https://elsewhere.example/z" in out
    assert remote.rewrite_absolute(page, {"base_url": "https://192.168.1.4"}, "rdweb").count('"/remote/rdweb/RDWeb/Pages/en-US/"') == 1
    for kind in ("text/xml", "application/xml; charset=utf-8", "text/html", "application/xhtml+xml"):
        assert remote._rewriter(kind) is remote.rewrite_html, kind
    assert remote._rewriter("application/javascript") is remote.rewrite_script and remote._rewriter("image/png") is None

    assert remote.rewrite_cookie("TSWAFeatureCheckCookie=true; path=/RDWeb/", "rdweb", "/RDWeb") == \
        "TSWAFeatureCheckCookie=true; Path=/remote/rdweb/"
    assert remote.rewrite_cookie("a=1; Path=/RDWeb/Pages", "rdweb", "/RDWeb") == "a=1; Path=/remote/rdweb/Pages"
    assert remote.rewrite_cookie("a=1; Path=/other", "rdweb", "/RDWeb") == "a=1; Path=/remote/rdweb/other"
    assert remote.rewrite_cookie("a=1; Path=/RDWebX", "rdweb", "/RDWeb") == "a=1; Path=/remote/rdweb/RDWebX"


def test_request_and_response_headers(conn, people):
    app = _app(conn)
    raw = [(b"host", b"mnemerynne.io"), (b"cookie", b"mnemerynne_session=abc; erynne_theme=neon; app=1"),
           (b"x-forwarded-for", b"6.6.6.6"), (b"connection", b"keep-alive, X-Custom"), (b"x-custom", b"1"),
           (b"accept-encoding", b"gzip"), (b"authorization", b"Basic xyz"), (b"keep-alive", b"300")]
    out = dict(remote.request_headers(raw, client_ip="10.1.1.5", scheme="https", host="mnemerynne.io", slug="balancer",
                                      rewrite=True))
    assert out["cookie"] == "app=1" and out["authorization"] == "Basic xyz"
    assert out["x-forwarded-for"] == "10.1.1.5" and out["x-forwarded-proto"] == "https"
    assert out["x-forwarded-host"] == "mnemerynne.io" and out["x-forwarded-prefix"] == "/remote/balancer"
    assert out["accept-encoding"] == "identity"
    assert not {"host", "connection", "x-custom", "keep-alive"} & set(out)
    only_session = dict(remote.request_headers([(b"cookie", b"mnemerynne_session=abc")], client_ip="", scheme="http",
                                               host="", slug="balancer", rewrite=False))
    assert "cookie" not in only_session and "accept-encoding" not in only_session

    headers = httpx.Headers([("x-frame-options", "DENY"), ("strict-transport-security", "max-age=1"),
                             ("content-security-policy", "default-src 'self'; frame-ancestors 'none'; img-src *"),
                             ("location", "http://10.0.5.20:9180/login"), ("set-cookie", "sid=1; Path=/"),
                             ("set-cookie", "mnemerynne_session=x; Path=/"), ("transfer-encoding", "chunked"),
                             ("content-type", "text/html")])
    out = remote.response_headers(headers, app, "balancer", rewritten=False)
    got = [(k.decode(), v.decode()) for k, v in out]
    assert ("location", "/remote/balancer/login") in got and ("set-cookie", "sid=1; Path=/remote/balancer/") in got
    assert ("content-security-policy", "default-src 'self'; img-src *") in got and ("content-type", "text/html") in got
    assert not {"x-frame-options", "strict-transport-security", "transfer-encoding"} & {k for k, _ in got}
    assert sum(1 for k, _ in got if k == "set-cookie") == 1


# ------------------------------------------------------------- who may open
def test_sign_in_groups_and_the_deep_link(conn, settings, people, directory):
    _app(conn)
    _app(conn, slug="paused", enabled=0)
    seen = []
    _upstream(seen)
    with _web(settings, None, directory) as anon:
        r = anon.get("/remote/balancer/status?x=1", headers={"Accept": "text/html"}, follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"] == "/login"
        back = anon.post("/login", data={"username": "alice", "password": "password123"}, follow_redirects=False)
        assert back.status_code == 303 and back.headers["location"] == "/remote/balancer/status?x=1"
    with _web(settings, None, directory) as anon:
        assert anon.get("/remote/balancer/api", headers={"Accept": "application/json"}).status_code == 401
    with _web(settings, "rob", directory) as rob:
        assert rob.get("/remote/balancer/").status_code == 403
        assert "href=\"/remote\"" not in rob.get("/").text
    with _web(settings, "alice", directory) as alice:
        assert alice.get("/remote/balancer/").status_code == 200
        assert alice.get("/remote/paused/").status_code == 404
        assert alice.get("/remote/nope/").status_code == 404
        moved = alice.get("/remote/balancer?x=1", follow_redirects=False)
        assert moved.status_code == 308 and moved.headers["location"] == "/remote/balancer/?x=1"
        assert "href=\"/remote\"" in alice.get("/").text
        page = alice.get("/remote")
        assert page.status_code == 200 and 'data-open="balancer"' in page.text and 'id="remote-bar"' in page.text
        assert "paused" not in page.text
        assert [a["slug"] for a in alice.get("/api/remote").json()["apps"]] == ["balancer"]
    with _web(settings, "root", directory) as root:
        assert root.get("/remote/balancer/").status_code == 200                 # admins always
    for bad in ("//evil.example/x", "/remote/x\\y", "https://evil.example/", "files", "/remote/a\nb", "/login",
                "/logout?x=1"):
        assert app_mod._safe_return(bad) is False
    assert app_mod._safe_return("/remote/balancer/status?x=1") and app_mod._safe_return("/oauth/authorize?a=b")
    assert app_mod._safe_return("/files") and app_mod._safe_return("/apis/calls/3")


# ------------------------------------------------------------- the proxy
def test_requests_reach_the_app_and_answers_come_back_rewritten(conn, settings, people, directory):
    _app(conn, base="http://10.0.5.20:9180/base", rewrite_html=1)
    seen = []

    async def handler(request):
        seen.append(request)
        if request.url.path.endswith("/login"):
            return httpx.Response(302, headers={"location": "http://10.0.5.20:9180/base/home",
                                                "set-cookie": "sid=1; Path=/; HttpOnly"})
        if request.method == "POST":
            body = await request.aread()
            return httpx.Response(200, json={"got": body.decode()})
        return httpx.Response(200, headers={"content-type": "text/html", "x-frame-options": "DENY",
                                            "content-security-policy": "frame-ancestors 'none'; default-src 'self'"},
                              content=b'<a href="/x">x</a>')
    _upstream(seen, handler)
    with _web(settings, "alice", directory) as alice:
        alice.cookies.set("app", "1")
        page = alice.get("/remote/balancer/status?x=1")
        assert page.status_code == 200 and page.text == '<a href="/remote/balancer/x">x</a>'
        assert "x-frame-options" not in page.headers or page.headers["x-frame-options"] == "SAMEORIGIN"
        assert page.headers["content-security-policy"] == "default-src 'self'; frame-ancestors 'self'"
        upstream = seen[-1]
        assert str(upstream.url) == "http://10.0.5.20:9180/base/status?x=1"
        assert upstream.headers["x-forwarded-prefix"] == "/remote/balancer" and upstream.headers["accept-encoding"] == "identity"
        assert "mnemerynne_session" not in upstream.headers.get("cookie", "") and "app=1" in upstream.headers.get("cookie", "")

        moved = alice.get("/remote/balancer/login", follow_redirects=False)
        assert moved.status_code == 302 and moved.headers["location"] == "/remote/balancer/home"
        assert moved.headers["set-cookie"].startswith("sid=1; Path=/remote/balancer/")

        posted = alice.post("/remote/balancer/api/thing", content=b"payload", headers={"content-type": "text/plain"})
        assert posted.status_code == 200 and posted.json() == {"got": "payload"}
        assert seen[-1].method == "POST"


def test_an_apps_script_files_call_its_own_api(conn, settings, people, directory):
    """An app whose script asks its own /api (Light Console: request("/api/config")) reaches the app, not
    Mnemerynne's /api; other files pass as they are."""
    _app(conn, rewrite_html=1)
    script = (b'async function request(path) { const r = await fetch(path); return r.json(); }\n'
              b'request("/api/config"); request(`/api/boards/${id}/color`); fetch("/static/x.json");\n'
              b'const page = "/about";')

    async def handler(request):
        if request.url.path.endswith(".js"):
            return httpx.Response(200, headers={"content-type": "application/javascript; charset=utf-8",
                                                "content-length": str(len(script))}, content=script)
        return httpx.Response(200, headers={"content-type": "text/css"}, content=b'.a { content: "/api/no"; }')
    remote.TRANSPORT = httpx.MockTransport(handler)
    with _web(settings, "alice", directory) as alice:
        js = alice.get("/remote/balancer/static/app.js")
        assert js.status_code == 200 and int(js.headers["content-length"]) == len(js.content)
        assert 'request("/remote/balancer/api/config")' in js.text and "`/remote/balancer/api/boards/${id}/color`" in js.text
        assert 'fetch("/remote/balancer/static/x.json")' in js.text and 'const page = "/about"' in js.text
        assert alice.get("/remote/balancer/static/app.css").text == '.a { content: "/api/no"; }'


def test_streams_pass_through_and_a_dead_app_is_a_502(conn, settings, people, directory):
    _app(conn)

    async def handler(request):
        if request.url.path.endswith("/down"):
            raise httpx.ConnectError("refused")

        async def chunks():
            for n in range(3):
                yield f"chunk{n}\n".encode()
                await asyncio.sleep(0)
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=chunks())
    remote.TRANSPORT = httpx.MockTransport(handler)
    with _web(settings, "alice", directory) as alice:
        with alice.stream("GET", "/remote/balancer/events") as r:
            assert r.status_code == 200 and r.headers["content-type"] == "text/event-stream"
            assert b"".join(r.iter_bytes()) == b"chunk0\nchunk1\nchunk2\n"
        down = alice.get("/remote/balancer/down")
        assert down.status_code == 502 and "didn't answer" in down.json()["detail"]


class FakeUpstream:
    def __init__(self, subprotocol):
        self.subprotocol = subprotocol
        self.sent, self.closed = [], None
        self.queue = asyncio.Queue()

    async def send(self, data):
        self.sent.append(data)
        await self.queue.put(("echo:" + data) if isinstance(data, str) else data[::-1])

    def __aiter__(self):
        return self

    async def __anext__(self):
        item = await self.queue.get()
        if item is None:
            raise StopAsyncIteration
        return item

    async def close(self, code=1000, reason=""):
        self.closed = code
        await self.queue.put(None)


def test_websockets_are_relayed(conn, settings, people, directory):
    _app(conn)
    opened = []

    async def connect(url, **kwargs):
        opened.append((url, kwargs))
        return FakeUpstream((kwargs.get("subprotocols") or [None])[0])
    remote.WS_CONNECT = connect
    with _web(settings, "alice", directory) as alice:
        with alice.websocket_connect("/remote/balancer/ws?x=1", subprotocols=["cockpit1"]) as ws:
            ws.send_text("hi")
            assert ws.receive_text() == "echo:hi"
            ws.send_bytes(b"abc")
            assert ws.receive_bytes() == b"cba"
        url, kwargs = opened[0]
        assert url == "ws://10.0.5.20:9180/ws?x=1" and kwargs["subprotocols"] == ["cockpit1"]
        headers = dict(kwargs["additional_headers"])
        assert headers["x-forwarded-prefix"] == "/remote/balancer" and "sec-websocket-key" not in headers
        assert "mnemerynne_session" not in headers.get("cookie", "")
    with _web(settings, "rob", directory) as rob:
        with pytest.raises(WebSocketDisconnect) as refused:
            with rob.websocket_connect("/remote/balancer/ws"):
                pass
        assert refused.value.code == 4403
    with _web(settings, None, directory) as anon:
        with pytest.raises(WebSocketDisconnect) as refused:
            with anon.websocket_connect("/remote/balancer/ws"):
                pass
        assert refused.value.code == 4401


# ------------------------------------------------------------- admin
def test_admins_set_up_remote_apps(conn, settings, people, directory):
    with _web(settings, "alice", directory) as alice:
        assert alice.get("/api/admin/remote").status_code == 403
    with _web(settings, "root", directory) as root:
        assert root.post("/api/admin/remote", json={"slug": "Bad Slug", "name": "x", "base_url": "http://a"}).status_code == 422
        assert root.post("/api/admin/remote", json={"slug": "home", "name": "x", "base_url": "http://a"}).status_code == 422
        assert root.post("/api/admin/remote", json={"slug": "nas", "name": "NAS", "base_url": "ftp://nas"}).status_code == 422
        made = root.post("/api/admin/remote", json={"slug": "nas", "name": "NAS", "base_url": "https://nas.lan:5001/"})
        assert made.status_code == 201
        app_id = made.json()["id"]
        assert made.json()["apps"][0]["base_url"] == "https://nas.lan:5001" and made.json()["apps"][0]["url"] == "/remote/nas/"
        assert root.post("/api/admin/remote", json={"slug": "NAS", "name": "x", "base_url": "http://a"}).status_code == 409
        assert root.patch(f"/api/admin/remote/{app_id}", json={"group": "Nobody Here"}).status_code == 422
        assert root.patch(f"/api/admin/remote/{app_id}", json={"ca_pem": "not a cert"}).status_code == 422
        saved = root.patch(f"/api/admin/remote/{app_id}", json={"group": "Ops", "rewrite_html": True, "embed": False,
                                                               "description": "The NAS"}).json()["apps"][0]
        assert saved["group"] == "Ops" and saved["rewrite_html"] and not saved["embed"] and saved["description"] == "The NAS"
        assert root.post(f"/api/admin/remote/{app_id}/fetch-certificate").status_code == 422   # nothing listening
        assert "Remote" in root.get("/admin/libraries").text and "admin_remote.js" in root.get("/admin/libraries").text
        assert root.delete(f"/api/admin/remote/{app_id}").json()["apps"] == []
        assert root.delete(f"/api/admin/remote/{app_id}").status_code == 404
    actions = [tuple(r) for r in conn.execute("SELECT action, target FROM access_log WHERE action LIKE 'remote.%' ORDER BY id")]
    assert actions == [("remote.create", "nas"), ("remote.update", "nas"), ("remote.delete", "nas")]
