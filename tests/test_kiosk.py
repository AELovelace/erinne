"""Kiosk settings for a screen box (mnemerynne/media/kiosk.py, web/kiosk.py): the TV's code, an admin's
approval on a phone, the box's helper redeeming the grant for a session that changes the screen's top
bar, and sign-ins on a TV with its top bar ending when it goes back to the TV page."""

import pytest
from fastapi.testclient import TestClient

from mnemerynne import accounts, users
from mnemerynne.media import kiosk, screens
from test_media import (_login, _pair, _turn_on, checker, client, conn,  # noqa: F401
                        directory, root, settings, _no_network)


def _tv(client, key):
    tv = TestClient(client.app)
    tv.cookies.set("mnemerynne_screen", key)
    return tv


def _signed_in(client, conn, directory, name, account):
    other = TestClient(client.app)
    _login(other, conn, directory, name, account)
    return other


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def test_codes_grants_and_sessions_run_out():
    clock = Clock()
    u = kiosk.Unlocks(clock=clock)
    code, secret = u.start(7)
    assert u.poll(7, secret)["state"] == "waiting" and u.poll(8, secret)["state"] == "expired"   # another screen's
    clock.now += kiosk.CODE_SECONDS
    assert u.poll(7, secret) == {"state": "expired"}
    with pytest.raises(screens.ScreenError):
        u.check(1, code)

    code, secret = u.start(7)
    assert u.check(1, screens.show_code(code)) == (code, 7)       # typed with its dash
    u.approve(code, 1, "boss")
    grant = u.poll(7, secret)["grant"]
    assert u.poll(7, secret) == {"state": "expired"}                # handed over once
    clock.now += kiosk.GRANT_SECONDS
    with pytest.raises(screens.ScreenError):
        u.redeem(grant)

    code, secret = u.start(7)
    u.approve(code, 1, "boss")
    token, info = u.redeem(u.poll(7, secret)["grant"])
    assert info == {"screen": 7, "admin": {"id": 1, "name": "boss"}, "expires_in": kiosk.SESSION_SECONDS}
    clock.now += kiosk.SESSION_SECONDS - 1
    assert u.session(token)["screen"] == 7                          # used: renewed
    clock.now += kiosk.SESSION_SECONDS - 1
    assert u.session(token) is not None
    u.end(token)
    assert u.session(token) is None

    for _ in range(kiosk.APPROVE_TRIES):
        with pytest.raises(screens.ScreenError):
            u.check(2, "WRONGCODE")
    with pytest.raises(screens.ScreenBusy):
        u.check(2, "WRONGCODE")


def test_an_admin_opens_a_tvs_kiosk_settings_and_turns_on_its_top_bar(client, conn, directory):
    _login(client, conn, directory)                 # boss: an admin
    _turn_on(client)
    screen_id, key = _pair(client)
    tv = _tv(client, key)
    assert "kiosk-bar" not in tv.get("/media/screen").text
    assert 'id="mc-kiosk"' in tv.get("/media/screen").text and "kiosk_screen.js" in tv.get("/media/screen").text
    assert 'id="mc-wifi"' not in tv.get("/media/screen").text          # Wi-Fi is in Kiosk settings now

    r = tv.post("/media/screen/kiosk/unlock")
    assert r.status_code == 200, r.text
    started = r.json()
    assert "/media/kiosk?code=" in started["url"] and started["qr"].startswith("<svg")
    assert tv.get("/media/screen/kiosk/unlock", params={"secret": started["secret"]}).json()["state"] == "waiting"
    assert TestClient(client.app).post("/media/screen/kiosk/unlock").status_code == 401     # by the screen's key

    # The phone: signed out it signs in first; someone who isn't an admin can't approve.
    assert TestClient(client.app).get("/media/kiosk", params={"code": started["code"]},
                                      follow_redirects=False).headers["location"] == "/login"
    jsmith = _signed_in(client, conn, directory, "jsmith", "jsmith")
    assert "Only admins" in jsmith.get("/media/kiosk", params={"code": started["code"]}).text
    assert jsmith.post("/api/media/kiosk/approve", json={"code": started["code"]}).status_code == 403
    assert started["code"] in client.get("/media/kiosk", params={"code": started["code"]}).text
    assert client.post("/api/media/kiosk/approve", json={"code": "BCDF-GHJK"}).status_code == 422
    r = client.post("/api/media/kiosk/approve", json={"code": started["code"]})
    assert r.status_code == 200 and r.json() == {"screen": "Living room"}

    approved = tv.get("/media/screen/kiosk/unlock", params={"secret": started["secret"]}).json()
    assert approved["state"] == "approved" and approved["admin"] == "boss"

    # The box's helper: no cookies, the grant once, then its session.
    box = TestClient(client.app)
    r = box.post("/api/media/kiosk/redeem", json={"grant": approved["grant"]})
    assert r.status_code == 200, r.text
    session = r.json()
    assert session["admin"] == "boss" and session["screen"] == {"id": screen_id, "name": "Living room", "top_bar": False}
    assert box.post("/api/media/kiosk/redeem", json={"grant": approved["grant"]}).status_code == 403
    headers = {"X-Kiosk-Session": session["session"]}
    assert box.get("/api/media/kiosk/screen").status_code == 401
    assert box.get("/api/media/kiosk/screen", headers=headers).json()["screen"]["top_bar"] is False

    r = box.put("/api/media/kiosk/screen", json={"top_bar": True}, headers=headers)
    assert r.status_code == 200 and r.json()["screen"]["top_bar"] is True
    page = tv.get("/media/screen").text
    assert "kiosk-bar" in page and 'href="/login"' in page
    logged = conn.execute("SELECT actor_name, action, target, detail FROM access_log WHERE action LIKE 'kiosk.%' "
                          "ORDER BY id").fetchall()
    assert [tuple(r) for r in logged] == [("boss", "kiosk.unlock", "Living room", ""),
                                          ("boss", "kiosk.top_bar", "Living room", "on")]

    assert box.post("/api/media/kiosk/lock", headers=headers).status_code == 204
    assert box.get("/api/media/kiosk/screen", headers=headers).status_code == 401


def test_a_sign_in_on_the_tv_ends_when_it_goes_back_to_the_tv(client, conn, directory):
    _login(client, conn, directory)
    _turn_on(client)
    screen_id, key = _pair(client)
    tv = _tv(client, key)
    assert "Back to TV" not in tv.get("/login").text                # top bar off: nothing to go back to
    kiosk.set_top_bar(conn, screen_id, True)
    conn.commit()
    assert "Back to TV" in tv.get("/login").text

    uid = users.create_user(conn, "jsmith", "password123")
    accounts.link(conn, uid, "jsmith", directory)
    assert tv.post("/login", data={"username": "jsmith", "password": "password123"},
                   follow_redirects=False).status_code == 303
    page = tv.get("/files")
    assert page.status_code == 200 and "data-kiosk-back" in page.text and "Back to TV" in page.text

    tv.get("/media/screen")                                         # back on the TV: signed out
    assert tv.get("/files", follow_redirects=False).status_code == 303
    assert tv.get("/media/screen").status_code == 200               # the screen's key still works

    # Someone whose own browser is a screen, signed in before (not on the TV), stays signed in.
    client.get("/media/screen")
    assert client.get("/files").status_code == 200
    assert "data-kiosk-back" not in client.get("/files").text
