"""The arcade (mnemerynne/arcade.py): the games site proxied at the root of a host of its own, reached only with a
one-use ticket from the main origin, re-checked on every request, and with nothing of Mnemerynne's served there."""

import asyncio
import json
from contextlib import contextmanager

import httpx
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from fakes import FakeDirectory, FakeEmbedder
from mnemerynne import arcade, media, remote, users
from mnemerynne.media import screens
from mnemerynne.config import Settings
from mnemerynne.web import security
from mnemerynne.web.app import Services, create_app

MAIN = "http://erynne.test"
ARCADE = "http://arcade.test"
SITE = "https://sadgirlsclub.wtf"


@pytest.fixture(autouse=True)
def _hooks(monkeypatch):
    monkeypatch.setattr(arcade, "_spent", {})
    yield
    remote.TRANSPORT = None
    remote.WS_CONNECT = None


@pytest.fixture
def settings(tmp_path):
    return Settings(_env_file=None, data_dir=tmp_path / "data", secret_key="test-secret-key-for-the-test-suite-only",
                    embed_dim=4, public_url=MAIN, arcade_url=ARCADE)


@pytest.fixture
def people(conn):
    out = {"alice": users.create_user(conn, "alice", "password123"),
           "rob": users.create_user(conn, "rob", "password123"),
           "root": users.create_user(conn, "root", "password123", role="admin")}
    conn.execute("UPDATE users SET ad_account = username")
    return out


@pytest.fixture
def directory(people):
    return FakeDirectory({"alice": {"Players"}, "rob": {"Staff"}, "root": {"Domain Admins"}},
                         groups=["Players", "Staff", "Domain Admins"])


def _games(conn, **cfg):
    arcade.save_settings(conn, {"enabled": True, "group": "Players", "site_url": SITE} | cfg)


@contextmanager
def _web(settings, name, directory):
    with TestClient(create_app(settings, Services(llm=None, embedder=FakeEmbedder(), directory=directory)),
                    base_url=MAIN) as c:
        if name:
            c.post("/login", data={"username": name, "password": "password123"})
        yield c


def _site(seen, handler=None):
    """The games site: records each request; answers like LumiBot does (a redirect to its login, a cookie)."""
    async def default(request):
        seen.append(request)
        return httpx.Response(200, headers={"content-type": "text/html; charset=utf-8", "x-frame-options": "DENY",
                                            "content-security-policy": "base-uri 'self'; frame-ancestors 'none'",
                                            "set-cookie": "sgc_panel_sess=abc; Path=/; Domain=sadgirlsclub.wtf; HttpOnly"},
                              content=b"<h1>LUMI WEB</h1>")
    remote.TRANSPORT = httpx.MockTransport(handler or default)


def _enter(client, path="/lumibot/"):
    """A ticket from the main origin, spent on the arcade: the arcade cookie is in the client's jar after."""
    got = client.post("/api/arcade/ticket", json={"path": path})
    assert got.status_code == 200, got.text
    url = got.json()["url"]
    assert url.startswith(f"{ARCADE}/__arcade/enter?t=")
    return client.get(url, follow_redirects=False), url


# ------------------------------------------------------------- the helpers
def test_the_arcade_must_be_a_plain_origin_on_a_host_of_its_own(tmp_path):
    def make(**kw):
        return Settings(_env_file=None, data_dir=tmp_path / "d", secret_key="x" * 40, embed_dim=4, **kw)
    assert arcade.origin(make(arcade_url="https://Arcade.Beepsquad.WTF/")) == "https://arcade.beepsquad.wtf"
    assert arcade.host(make(arcade_url="https://arcade.beepsquad.wtf:8443")) == "arcade.beepsquad.wtf"
    arcade.check_config(make())   # unset: no arcade, nothing to check
    for bad in ({"arcade_url": "arcade.beepsquad.wtf"}, {"arcade_url": "https://a.example/games"},
                {"arcade_url": "https://erynne.example", "public_url": "https://erynne.example"}):
        with pytest.raises(ValueError):
            arcade.check_config(make(**bad))
    with pytest.raises(security.InsecureConfig):   # and the app refuses to start on one
        create_app(make(arcade_url="https://erynne.example", public_url="https://erynne.example"),
                   Services(llm=None, embedder=FakeEmbedder()))


def test_paths_and_the_site_address_are_checked():
    assert arcade.clean_path("/lumibot/?x=1") == "/lumibot/?x=1"
    assert arcade.clean_path("") == "/"
    for bad in ("//evil.example/", "https://evil.example/", "lumibot/", "/a\\b", "/__arcade/enter", "/x/../y", "/a\nb"):
        with pytest.raises(arcade.ArcadeError):
            arcade.clean_path(bad)
    assert arcade.clean_site_url("https://SadGirlsClub.wtf/") == "https://sadgirlsclub.wtf"
    assert arcade.clean_site_url("") == ""
    for bad in ("sadgirlsclub.wtf", "https://sadgirlsclub.wtf/lumibot/", "ftp://x.example", "https://u:p@x.example"):
        with pytest.raises(arcade.ArcadeError):
            arcade.clean_site_url(bad)


def test_tickets_are_signed_short_lived_and_spent_once(conn, people, monkeypatch):
    alice = users.get_user_by_id(conn, people["alice"])
    key = "k" * 40
    token = arcade.make_ticket(key, alice, "/games/")
    assert arcade.spend_ticket("another-key" * 4, token) is None        # signed with this server's key only
    assert arcade.spend_ticket(key, token)["p"] == "/games/"
    assert arcade.spend_ticket(key, token) is None                      # one use
    assert arcade.read_cookie(key, token) is None                       # a ticket isn't a cookie
    late = arcade.make_ticket(key, alice, "/games/")
    monkeypatch.setattr(arcade, "TICKET_SECONDS", -1)
    assert arcade.spend_ticket(key, late) is None                       # and it expires


# ---------------------------------------------------------- getting in
def test_a_ticket_is_only_for_someone_who_may_play(conn, settings, people, directory):
    with _web(settings, "alice", directory) as alice:
        assert alice.post("/api/arcade/ticket", json={"path": "/lumibot/"}).status_code == 403   # Games off
        _games(conn)
        assert alice.post("/api/arcade/ticket", json={"path": "/lumibot/"}).status_code == 200
        assert alice.post("/api/arcade/ticket", json={"path": "//evil.example/"}).status_code == 400
    with _web(settings, "rob", directory) as rob:                        # not in the Games group
        assert rob.post("/api/arcade/ticket", json={"path": "/lumibot/"}).status_code == 403
    with _web(settings, "root", directory) as root:                      # admins always
        assert root.post("/api/arcade/ticket", json={"path": "/lumibot/"}).status_code == 200
    with _web(settings, None, directory) as anon:
        assert anon.post("/api/arcade/ticket", json={"path": "/lumibot/"}).status_code == 401


def test_without_an_arcade_address_there_are_no_tickets(conn, settings, people, directory):
    plain = settings.model_copy(update={"arcade_url": ""})   # same database, no MNEMERYNNE_ARCADE_URL
    _games(conn)
    with _web(plain, "alice", directory) as alice:
        assert alice.post("/api/arcade/ticket", json={"path": "/lumibot/"}).status_code == 404


def test_entering_sets_the_arcade_cookie_and_the_site_is_served_at_the_root(conn, settings, people, directory):
    _games(conn)
    seen = []
    _site(seen)
    with _web(settings, "alice", directory) as alice:
        entered, url = _enter(alice)
        assert entered.status_code == 303 and entered.headers["location"] == "/lumibot/"
        cookie = entered.headers["set-cookie"]
        assert cookie.startswith("mnemerynne_arcade=") and "HttpOnly" in cookie and "Domain" not in cookie
        assert alice.get(url, follow_redirects=False).status_code == 403          # the ticket is spent

        page = alice.get(f"{ARCADE}/lumibot/api/me?x=1", headers={"Cookie": "mnemerynne_session=main; sgc_panel_sess=abc; "
                                                                             + cookie.split(";")[0]})
        assert page.status_code == 200 and page.text == "<h1>LUMI WEB</h1>"
        sent = seen[-1]
        assert str(sent.url) == f"{SITE}/lumibot/api/me?x=1"                      # same path, the site's own host
        assert sent.headers["x-forwarded-host"] == "arcade.test" and "x-forwarded-prefix" not in sent.headers
        assert sent.headers["cookie"] == "sgc_panel_sess=abc"                     # Mnemerynne's cookies stay here
        # Framed only by Mnemerynne; the site's own framing rules and cookie Domain go.
        assert page.headers["content-security-policy"] == "base-uri 'self'; frame-ancestors http://erynne.test"
        assert "x-frame-options" not in page.headers
        assert page.headers["set-cookie"] == "sgc_panel_sess=abc; Path=/; HttpOnly"
        assert page.headers["referrer-policy"] == "same-origin"


def test_an_https_arcade_behind_a_plain_http_hop_still_sets_a_cookie_frames_can_use(conn, settings, people, directory):
    # site-chan's nginx talks plain http to the app; browsers still reach the arcade over https.
    https_settings = settings.model_copy(update={"arcade_url": "https://arcade.test"})
    _games(conn)
    _site([])
    with _web(https_settings, "alice", directory) as alice:
        got = alice.post("/api/arcade/ticket", json={"path": "/lumibot/"}).json()["url"]
        assert got.startswith("https://arcade.test/__arcade/enter?t=")
        entered = alice.get(got.replace("https://", "http://"), follow_redirects=False)   # the hop is http
        cookie = entered.headers["set-cookie"]
        assert "Secure" in cookie and "SameSite=None" in cookie and "Partitioned" in cookie
        assert entered.headers["strict-transport-security"] == "max-age=31536000"


def test_redirects_to_the_site_stay_on_the_arcade(conn, settings, people, directory):
    _games(conn)

    async def handler(request):
        return httpx.Response(302, headers={"location": f"{SITE}/lumibot/login?next=%2Flumibot%2F"})
    remote.TRANSPORT = httpx.MockTransport(handler)
    with _web(settings, "alice", directory) as alice:
        _enter(alice)
        got = alice.get(f"{ARCADE}/lumibot/", follow_redirects=False)
        assert got.status_code == 302 and got.headers["location"] == "/lumibot/login?next=%2Flumibot%2F"


def test_nothing_of_mnemerynnes_answers_on_the_arcade_host(conn, settings, people, directory):
    _games(conn)
    seen = []
    _site(seen)
    with _web(settings, "alice", directory) as alice:
        # Without the arcade cookie: the arcade's own page, never Mnemerynne's sign-in, pages or files.
        for path in ("/login", "/static/erynne.css", "/api/me", "/mcp", "/media"):
            got = alice.get(f"{ARCADE}{path}", follow_redirects=False)
            assert got.status_code == 401 and "Open this from Mnemerynne" in got.text, path
        assert not seen
        _enter(alice)
        # With it, the same paths are the site's: Mnemerynne's API and connector simply aren't there.
        alice.get(f"{ARCADE}/api/me")
        alice.post(f"{ARCADE}/mcp", json={})
        assert [str(r.url) for r in seen] == [f"{SITE}/api/me", f"{SITE}/mcp"]
        assert alice.get(f"{ARCADE}/__arcade/anything").status_code == 404
        # And on the main host the arcade's own paths don't exist.
        assert alice.get("/__arcade/enter?t=x").status_code == 404


def test_signing_out_or_leaving_the_group_ends_the_arcade(conn, settings, people, directory, monkeypatch):
    _games(conn)
    _site([])
    monkeypatch.setattr(arcade, "CHECK_SECONDS", 0)
    with _web(settings, "alice", directory) as alice:
        _enter(alice)
        assert alice.get(f"{ARCADE}/lumibot/").status_code == 200
        _games(conn, group="Staff")                                   # alice is no longer in the Games group
        assert alice.get(f"{ARCADE}/lumibot/").status_code == 401
        _games(conn, group="Players")
        assert alice.get(f"{ARCADE}/lumibot/").status_code == 200
        _games(conn, enabled=False)                                   # Games off: nothing answers
        assert alice.get(f"{ARCADE}/lumibot/").status_code == 404
        _games(conn, enabled=True)
        alice.post("/logout")                                         # signing out of Mnemerynne ends it too
        assert alice.get(f"{ARCADE}/lumibot/").status_code == 401
    with _web(settings, "alice", directory) as alice:
        _enter(alice)
        left = alice.get(f"{ARCADE}/__arcade/leave")
        assert left.status_code == 200 and "Max-Age=0" in left.headers["set-cookie"]
        assert alice.get(f"{ARCADE}/lumibot/").status_code == 401


class FakeSocket:
    def __init__(self):
        self.queue = asyncio.Queue()
        self.subprotocol = None

    async def send(self, data):
        await self.queue.put("echo:" + data)

    def __aiter__(self):
        return self

    async def __anext__(self):
        item = await self.queue.get()
        if item is None:
            raise StopAsyncIteration
        return item

    async def close(self, code=1000, reason=""):
        await self.queue.put(None)


def test_websockets_are_relayed_at_the_root(conn, settings, people, directory):
    _games(conn)
    opened = []

    async def connect(url, **kwargs):
        opened.append((url, dict(kwargs["additional_headers"])))
        return FakeSocket()
    remote.WS_CONNECT = connect
    with _web(settings, "alice", directory) as alice:
        with pytest.raises(WebSocketDisconnect) as refused:            # no arcade cookie yet
            with alice.websocket_connect("ws://arcade.test/games/ws"):
                pass
        assert refused.value.code == 4401
        entered, _ = _enter(alice, "/games/")
        cookie = entered.headers["set-cookie"].split(";")[0]
        with alice.websocket_connect("ws://arcade.test/games/ws", headers={"cookie": cookie}) as ws:
            ws.send_text("hi")
            assert ws.receive_text() == "echo:hi"
    url, headers = opened[0]
    assert url == "wss://sadgirlsclub.wtf/games/ws" and "x-forwarded-prefix" not in headers
    assert "mnemerynne_arcade" not in headers.get("cookie", "")


# ------------------------------------------------------------- Media → Games
def _media(conn, settings):
    """Media on for Players and Staff (rob is in Media but not in the Games group), screens for Players."""
    media.set_enabled(conn, True)
    media.save_settings(conn, settings, {"media_group": "Players;Staff", "screens_group": "Players"})


def test_media_lists_the_games_and_opens_one(conn, settings, people, directory):
    _media(conn, settings)
    _games(conn)
    lumi = arcade.add_game(conn, {"name": "Lumi Web", "icon": "💰", "start_path": "/lumibot/", "description": "Your wallet"})
    off = arcade.add_game(conn, {"name": "Old Game", "start_path": "/old/", "enabled": False})
    assert lumi["slug"] == "lumi-web" and off["slug"] == "old-game"
    with _web(settings, "alice", directory) as alice:
        assert 'data-view="games"' in alice.get("/media").text                   # the menu shows Games
        listed = alice.get("/api/media/games").json()["games"]
        assert [g["slug"] for g in listed] == ["lumi-web"]                        # turned-off games aren't listed
        got = alice.post("/api/media/games/lumi-web/ticket")
        assert got.status_code == 200 and got.json()["game"]["name"] == "Lumi Web"
        entered = alice.get(got.json()["url"], follow_redirects=False)
        assert entered.status_code == 303 and entered.headers["location"] == "/lumibot/"
        assert alice.post("/api/media/games/old-game/ticket").status_code == 404
        assert alice.post("/api/media/games/nope/ticket").status_code == 404
    with _web(settings, "rob", directory) as rob:                                 # in Media, not in the Games group
        assert 'data-view="games"' not in rob.get("/media").text
        assert rob.get("/api/media/games").json() == {"games": []}
        assert rob.post("/api/media/games/lumi-web/ticket").status_code == 404


def test_a_screen_plays_as_its_owner_only_while_it_is_paired(conn, settings, people, directory):
    _media(conn, settings)
    _games(conn)
    _site([])
    arcade.add_game(conn, {"name": "Lumi Games", "start_path": "/games/"})
    screen_id, key = screens.create(conn, people["alice"], "Living room")
    with _web(settings, None, directory) as tv:                                   # nobody signed in: the TV's key
        tv.cookies.set(screens.COOKIE, key, domain="erynne.test")
        assert [g["slug"] for g in tv.get("/api/media/games").json()["games"]] == ["lumi-games"]
        url = tv.post("/api/media/games/lumi-games/ticket").json()["url"]
        entered = tv.get(url, follow_redirects=False)
        assert entered.status_code == 303 and entered.headers["location"] == "/games/"
        assert tv.get(f"{ARCADE}/games/").status_code == 200
        conn.execute("DELETE FROM media_screens WHERE id = ?", (screen_id,))       # unpaired: the arcade closes
        assert tv.get(f"{ARCADE}/games/").status_code == 401


def test_the_admin_test_asks_the_site_as_the_arcade_would(conn, settings, people, directory):
    _games(conn)
    game = arcade.add_game(conn, {"name": "Lumi Web", "start_path": "/lumibot/"})
    seen = []

    async def handler(request):
        seen.append(request)
        return httpx.Response(302, headers={"location": f"{SITE}/lumibot/login?next=%2Flumibot%2F"})
    remote.TRANSPORT = httpx.MockTransport(handler)
    with _web(settings, "root", directory) as root:
        r = root.post(f"/api/admin/games/{game['id']}/test").json()
    assert r == {"ok": True, "status": 302, "location": "/lumibot/login?next=%2Flumibot%2F"}
    assert str(seen[0].url) == f"{SITE}/lumibot/" and seen[0].headers["x-forwarded-host"] == "arcade.test"


# ------------------------------------------------------------- the admin
def test_admins_add_edit_order_and_remove_games(conn, settings, people, directory):
    with _web(settings, "alice", directory) as alice:
        assert alice.post("/api/admin/games", json={"name": "X", "start_path": "/x/"}).status_code == 403
    with _web(settings, "root", directory) as root:
        games = root.post("/api/admin/games", json={"name": "Lumi Web", "icon": "💰", "start_path": "/lumibot/"}).json()["games"]
        games = root.post("/api/admin/games", json={"name": "Lumi Games", "start_path": "/games/"}).json()["games"]
        assert [g["name"] for g in games] == ["Lumi Web", "Lumi Games"]
        assert root.post("/api/admin/games", json={"name": "Bad", "start_path": "//evil.example/"}).status_code == 400
        assert root.post("/api/admin/games", json={"start_path": "/x/"}).status_code == 400   # no name
        web, lumi_games = games
        games = root.put(f"/api/admin/games/{lumi_games['id']}", json={"move": -1}).json()["games"]
        assert [g["name"] for g in games] == ["Lumi Games", "Lumi Web"]
        games = root.put(f"/api/admin/games/{web['id']}", json={"description": "Your wallet", "enabled": False}).json()["games"]
        assert games[1]["description"] == "Your wallet" and games[1]["enabled"] is False and games[1]["slug"] == "lumi-web"
        assert root.put("/api/admin/games/999", json={"name": "Nope"}).status_code == 400
        games = root.delete(f"/api/admin/games/{lumi_games['id']}").json()["games"]
        assert [g["name"] for g in games] == ["Lumi Web"]
    actions = [r["action"] for r in conn.execute("SELECT action FROM access_log WHERE action LIKE 'games.%' ORDER BY id")]
    assert actions == ["games.add", "games.add", "games.change", "games.change", "games.remove"]


def test_admins_set_up_games(conn, settings, people, directory):
    with _web(settings, "alice", directory) as alice:
        assert alice.get("/api/admin/settings/games").status_code == 403
    with _web(settings, "root", directory) as root:
        got = root.get("/api/admin/settings/games").json()
        assert got == {"settings": {"enabled": False, "group": "", "site_url": ""}, "arcade_url": ARCADE, "games": []}
        saved = root.put("/api/admin/settings/games",
                         json={"enabled": True, "group": "players", "site_url": "https://SadGirlsClub.wtf/"}).json()
        assert saved["settings"] == {"enabled": True, "group": "Players", "site_url": SITE}
        assert root.put("/api/admin/settings/games", json={"site_url": f"{SITE}/lumibot/"}).status_code == 400
        assert root.put("/api/admin/settings/games", json={"group": "Nobody Here"}).status_code == 400
        assert root.put("/api/admin/settings/games", json={"enabled": False}).json()["settings"]["site_url"] == SITE
    logged = [r["target"] for r in conn.execute("SELECT target FROM access_log WHERE action = 'settings.games'")]
    assert logged and "site_url=https://sadgirlsclub.wtf" in logged[0]
    assert json.loads(conn.execute("SELECT value FROM meta WHERE key = 'games_settings'").fetchone()[0])["enabled"] is False
