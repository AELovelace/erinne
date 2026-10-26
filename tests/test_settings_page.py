import pytest
from fastapi.testclient import TestClient

from fakes import FakeEmbedder
from mnemerynne import users
from mnemerynne.web.app import Services, create_app


def _app(settings):
    return create_app(settings, Services(llm=None, embedder=FakeEmbedder()))


@pytest.fixture
def alice(conn):
    return users.create_user(conn, "alice", "password123")


def _login(client, name="alice", password="password123"):
    return client.post("/login", data={"username": name, "password": password}, follow_redirects=False)


def test_everyone_gets_a_preferences_page(settings, conn, alice):
    with TestClient(_app(settings)) as c:
        _login(c)
        page = c.get("/preferences")
        assert page.status_code == 200 and 'id="password-form"' in page.text and 'id="m365-section"' in page.text
        assert 'id="claude-title"' in page.text                         # the Claude page is its right half
        nav = c.get("/").text
        assert 'href="/preferences"' in nav and 'href="/connect"' not in nav   # one tab for both
        assert 'href="/memories"' not in nav and 'id="memories"' in page.text   # memories live here now


def test_old_settings_and_memories_urls_redirect(settings, conn, alice):
    with TestClient(_app(settings)) as c:
        _login(c)
        moved = c.get("/settings?m365=linked", follow_redirects=False)
        assert moved.status_code == 308 and moved.headers["location"] == "/preferences?m365=linked"
        assert c.get("/settings", follow_redirects=False).headers["location"] == "/preferences"
        moved = c.get("/memories", follow_redirects=False)
        assert moved.status_code == 308 and moved.headers["location"] == "/preferences#memories"


def test_change_your_own_password(settings, conn, alice):
    with TestClient(_app(settings)) as me, TestClient(_app(settings)) as laptop:
        _login(me)
        _login(laptop)
        assert me.post("/api/me/password", json={"current": "wrong-one", "new": "new-password-1"}).status_code == 422
        assert me.post("/api/me/password", json={"current": "password123", "new": "short"}).status_code == 422
        assert me.post("/api/me/password", json={"current": "password123", "new": "new-password-1"}).status_code == 200
        assert me.get("/api/me").status_code == 200                    # this browser stays signed in
        assert laptop.get("/api/me").status_code == 401                # the other one is signed out
        assert _login(laptop, password="new-password-1").status_code == 303
        log = conn.execute("SELECT action, detail FROM access_log ORDER BY id DESC LIMIT 1").fetchone()
        assert tuple(log) == ("account.password", "changed it themselves")


def test_wrong_current_passwords_lock_out(settings, conn, alice):
    with TestClient(_app(settings)) as c:
        _login(c)
        codes = [c.post("/api/me/password", json={"current": "nope-nope", "new": "new-password-1"}).status_code
                 for _ in range(settings.login_max_failures + 1)]
        assert codes[-1] == 429


def test_sign_out_other_browsers(settings, conn, alice):
    with TestClient(_app(settings)) as me, TestClient(_app(settings)) as other:
        _login(me)
        _login(other)
        assert me.post("/api/me/sign-out-others").status_code == 200
        assert me.get("/api/me").status_code == 200 and other.get("/api/me").status_code == 401


def test_pick_a_theme(settings, conn, alice):
    with TestClient(_app(settings)) as c:
        _login(c)
        page = c.get("/preferences").text
        assert 'data-theme="neon"' in page and 'content="dark"' in page          # the default
        assert "theme-neon.css" in page and "parallax.js" in page
        assert c.put("/api/me/theme", json={"theme": "vaporwave"}).status_code == 422
        r = c.put("/api/me/theme", json={"theme": "sakura"})
        assert r.status_code == 200 and r.cookies.get("erynne_theme") == "sakura"
        assert users.get_user(conn, "alice")["theme"] == "sakura"
        assert 'data-theme="sakura"' in c.get("/files").text
        c.post("/logout")
        assert 'data-theme="sakura"' in c.get("/login").text                     # signed out: the cookie
        c.cookies.clear()      # set() alone adds a second cookie beside the server's, which is sent first
        c.cookies.set("erynne_theme", "<script>")
        assert 'data-theme="neon"' in c.get("/login").text                       # anything else: neon
        assert c.get("/static/parallax/city-8/5.png").status_code == 200
