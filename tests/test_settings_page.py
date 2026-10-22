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


def test_everyone_gets_a_settings_page(settings, conn, alice):
    with TestClient(_app(settings)) as c:
        _login(c)
        page = c.get("/settings")
        assert page.status_code == 200 and 'id="password-form"' in page.text and 'id="m365-section"' in page.text
        assert 'href="/settings"' in c.get("/").text                  # in the nav
        assert 'id="m365-section"' not in c.get("/connect").text        # moved here from the Claude page


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
