"""Kiosk settings for a screen box (mnemerynne/media/kiosk.py, web/kiosk.py): the TV's code, an admin's
approval on a phone, the box's helper redeeming the grant for a session that changes the screen's top
bar, and sign-ins on a TV with its top bar ending when it goes back to the TV page."""

import pytest
from fastapi.testclient import TestClient

from mnemerynne import accounts, users
from mnemerynne.media import kiosk, screens
from test_media import (_hub, _login, _pair, _turn_on, checker, client, conn,  # noqa: F401
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


# ------------------------------------------------------------- screen boxes
def test_a_box_gets_the_small_bar_and_its_underscan_on_every_page(client, conn, directory):
    _login(client, conn, directory)
    _turn_on(client)
    screen_id, key = _pair(client)
    tv = _tv(client, key)
    assert tv.put("/media/screen/safe-area", json={"x": 3, "y": 2.5}).status_code == 200
    page = tv.get("/media/screen").text
    assert 'class="tv tv-page"' in page and "--safe-x: 3.0%" in page          # a TV, not (yet) a box

    page = tv.get("/media/screen?kiosk=1").text                               # the box's start page opens it so
    assert 'class="tv tv-box tv-page"' in page and "screen_link.js" not in page and "tv-strip" not in page
    assert conn.execute("SELECT is_box FROM media_screens WHERE id = ?", (screen_id,)).fetchone()[0] == 1

    # Any other page: the underscan, the small bar (Back to TV, ‹ ›) and the remote.
    page = tv.get("/login").text
    assert 'class="tv tv-box"' in page and "--safe-yv: 2.5vh" in page
    assert 'data-tv-scale="auto"' in page                                    # its text size too: automatic,
    assert tv.put("/media/screen/ui-scale", json={"scale": 2}).status_code == 200
    page = tv.get("/login").text                                             # or the one saved
    assert "--safe-yv: 2.5vh; --ui-scale: 2.0" in page and 'data-tv-scale' not in page
    assert 'class="tv-strip"' in page and 'data-tv-go="back"' in page and "screen_link.js" in page
    framed = tv.get("/login", headers={"sec-fetch-dest": "iframe"}).text      # in a frame: the page around it has them
    assert 'class="tv' not in framed and "tv-strip" not in framed and "--ui-scale" not in framed
    assert 'class="tv' not in TestClient(client.app).get("/login").text      # not a screen at all

    # With its top bar on: one Back to TV on the sign-in page, and ‹ › in the TV page's bar.
    kiosk.set_top_bar(conn, screen_id, True)
    conn.commit()
    assert tv.get("/login").text.count("Back to TV") == 1
    assert "tv-history" in tv.get("/media/screen").text
    assert tv.post("/login", data={"username": "boss", "password": "password123"},
                   follow_redirects=False).status_code == 303
    page = tv.get("/files").text                                              # signed in: the full bar has them
    assert 'class="topbar main-bar"' in page and page.count('data-tv-go="forward"') == 2   # the bar's and the strip's
    assert "screen_link.js" in page


def test_documents_open_in_a_page_with_the_bar_on_a_box(client, conn, directory):
    _login(client, conn, directory)
    _turn_on(client)
    made = client.post("/api/files/text", json={"name": "Runbook", "content": "# Runbook\n\nSteps.\n"})
    assert made.status_code in (200, 201), made.text
    file_id = made.json()["id"]
    _, key = _pair(client)
    tv = _tv(client, key)

    def sign_in():
        assert tv.post("/login", data={"username": "boss", "password": "password123"},
                       follow_redirects=False).status_code == 303
    opened = {"sec-fetch-dest": "document"}
    sign_in()
    assert tv.get(f"/files/{file_id}/view", headers=opened).status_code == 200      # not a box yet
    tv.get("/media/screen?kiosk=1")
    sign_in()                                                                        # the TV page signed it out
    r = tv.get(f"/files/{file_id}/view", headers=opened, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == f"/files/{file_id}/tv/view"
    page = tv.get(f"/files/{file_id}/tv/view").text
    assert f'<iframe src="/files/{file_id}/view"' in page and "tv-strip" in page and "screen_link.js" in page
    inside = tv.get(f"/files/{file_id}/view", headers={"sec-fetch-dest": "iframe"})
    assert inside.status_code == 200 and "Steps." in inside.text and "tv-strip" not in inside.text
    assert tv.get(f"/files/{file_id}/tv/elsewhere").status_code == 422
    assert client.get(f"/files/{file_id}/view", headers=opened).status_code == 200   # the phone: as before


def test_someone_using_the_tv_can_keep_it_and_an_admin_can_override(client, conn, directory):
    _login(client, conn, directory)                  # boss: an admin
    _turn_on(client)
    screen_id, key = _pair(client)
    tv = _tv(client, key)
    hub = _hub(client)
    queue = hub.listen(screen_id)

    def cast(body, who=client, screen=screen_id):
        r = who.post(f"/api/media/screens/{screen}/command", json=body)
        assert r.status_code == 200, r.text
        return r.json()["id"]

    def answer(command_id, who=client, screen=screen_id):
        return who.get(f"/api/media/screens/{screen}/commands/{command_id}")

    # Someone using the TV keeps it.
    first = cast({"action": "toggle"})
    assert queue.get_nowait()["id"] == first and answer(first).json() == {"state": "sent"}
    assert tv.post(f"/media/screen/commands/{first}", json={"state": "waiting", "seconds": 10}).status_code == 200
    assert answer(first).json() == {"state": "waiting", "seconds": 10}
    assert tv.post(f"/media/screen/commands/{first}", json={"state": "kept"}).status_code == 200
    assert answer(first).json() == {"state": "kept"}
    assert client.post(f"/api/media/screens/{screen_id}/commands/{first}/override").status_code == 409

    # An admin overrides one that's counting down; the TV then says it's done.
    second = cast({"action": "stop"})
    queue.get_nowait()
    tv.post(f"/media/screen/commands/{second}", json={"state": "waiting", "seconds": 10})
    assert client.post(f"/api/media/screens/{screen_id}/commands/{second}/override").status_code == 200
    assert queue.get_nowait() == {"action": "override", "id": second, "sender": "boss"}
    assert tv.post(f"/media/screen/commands/{second}", json={"state": "done"}).status_code == 200
    assert answer(second).json() == {"state": "done"}
    assert tv.post("/media/screen/commands/nonsense", json={"state": "done"}).status_code == 404
    assert tv.post(f"/media/screen/commands/{second}", json={"state": "maybe"}).status_code == 422
    assert answer("nonsense").status_code == 404

    # Or overrides from the start.
    cast({"action": "stop", "override": True})
    assert queue.get_nowait()["override"] is True
    assert tv.put("/api/media/screen/state", json={"kind": "in_use", "title": "Someone is using the TV"}).status_code == 200

    # Someone who isn't an admin: their own screens only, and no overriding.
    jane = _signed_in(client, conn, directory, "jane", "jsmith")
    jane_screen = jane.post("/api/media/screens", json={"name": "Kitchen"}).json()["id"]
    jane_queue = hub.listen(jane_screen)
    assert jane.post(f"/api/media/screens/{jane_screen}/command", json={"action": "stop", "override": True}).status_code == 403
    assert jane.post(f"/api/media/screens/{screen_id}/command", json={"action": "stop"}).status_code == 404
    assert answer(first, jane).status_code == 404
    mine = cast({"action": "stop"}, jane, jane_screen)
    jane_queue.get_nowait()
    assert tv.post(f"/media/screen/commands/{mine}", json={"state": "waiting", "seconds": 10}).status_code == 404   # another TV
    assert answer(mine, jane, jane_screen).json() == {"state": "sent"}
    assert jane.post(f"/api/media/screens/{jane_screen}/commands/{mine}/override").status_code == 403
    assert [s["owner"] for s in jane.get("/api/media/screens").json()["screens"]] == [None]

    # The admin sees everyone's screens and can cast to them; only their owner removes them.
    listed = client.get("/api/media/screens").json()
    assert listed["admin"] is True and [(s["name"], s["owner"]) for s in listed["screens"]] == [
        ("Living room", None), ("Kitchen", "jane")]
    cast({"action": "toggle"}, client, jane_screen)
    assert jane_queue.get_nowait()["sender"] == "boss"
    assert client.delete(f"/api/media/screens/{jane_screen}").status_code == 404
    logged = conn.execute("SELECT action, target, detail FROM access_log WHERE action LIKE 'screen.%' ORDER BY id").fetchall()
    assert [tuple(r) for r in logged] == [("screen.override", "Living room", ""),
                                          ("screen.cast", "Living room", "stop (override)"),
                                          ("screen.cast", "Kitchen", "toggle")]
    hub.unlisten(screen_id, queue)
    hub.unlisten(jane_screen, jane_queue)


def test_commands_answers_run_out():
    clock = Clock()
    hub = screens.Hub(clock=clock)
    assert hub.send(1, {"action": "stop"}) is None                     # nobody listening
    queue = hub.listen(1)
    command_id = hub.send(1, {"action": "stop"})
    assert queue.get_nowait() == {"action": "stop", "id": command_id}
    assert not hub.answer(2, command_id, {"state": "done"})            # only the screen it went to
    assert hub.answer(1, command_id, {"state": "done"}) and hub.answer_of(1, command_id) == {"state": "done"}
    assert hub.answer_of(2, command_id) is None
    clock.now += screens.COMMAND_SECONDS + 1
    assert hub.answer_of(1, command_id) is None
    hub.send(1, {"action": "stop"})                                    # sweeps the old ones
    assert command_id not in hub._answers
