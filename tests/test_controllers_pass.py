"""🎮 Controllers' pass (web/kiosk.py, media/kiosk.py ControllerPasses): the TV page asks with a remote's OK (the
owner's or a TV guest's) or its own keys, gets a one-time pass, and the box's helper redeems it once. Not for a
stale or forged press, a press that isn't OK, a remote that can't drive this TV, or a browser that isn't a TV."""

from mnemerynne.media import kiosk
from test_lumi_tv_player import _couch
from test_media import _login, _no_network, _turn_on, checker, client, conn, directory, root, settings  # noqa: F401

OPEN, REDEEM = "/media/screen/controllers/open", "/api/media/kiosk/controllers/redeem"


def test_a_guests_ok_opens_controllers_once(client, conn, directory):
    owner_id, wife_id, tv, press, screen_id, remote_id = _couch(client, conn, directory)
    opened = tv.post(OPEN, json={"controller": press()})
    assert opened.status_code == 200, opened.text
    assert opened.json()["by"] == "jane" and opened.json()["expires_in"] == kiosk.PASS_SECONDS
    helper = client.__class__(client.app)                                   # the box's helper: no cookies at all
    redeemed = helper.post(REDEEM, json={"pass": opened.json()["pass"]})
    assert redeemed.status_code == 200 and redeemed.json()["by"] == "jane" and redeemed.json()["screen"]
    again = helper.post(REDEEM, json={"pass": opened.json()["pass"]})
    assert again.status_code == 403 and "remote" in again.json()["detail"]   # once only


def test_the_tvs_own_keys_open_it_too(client, conn, directory):
    owner_id, wife_id, tv, press, screen_id, remote_id = _couch(client, conn, directory)
    opened = tv.post(OPEN, json={"local": True})
    assert opened.status_code == 200 and opened.json()["by"] == "this TV"


def test_what_doesnt_open_it(client, conn, directory):
    owner_id, wife_id, tv, press, screen_id, remote_id = _couch(client, conn, directory)
    assert tv.post(OPEN, json={}).status_code == 422                        # neither a press nor the TV's keys
    assert tv.post(OPEN, json={"controller": press("down")}).status_code == 422
    assert tv.post(OPEN, json={"controller": "forged"}).status_code == 403
    assert tv.post(OPEN, json={"local": True, "extra": 1}).status_code == 422
    phone = client.__class__(client.app)                                    # a signed-in phone isn't a TV
    assert phone.post("/login", data={"username": "jane", "password": "password123"}, follow_redirects=False).status_code == 303
    assert phone.post(OPEN, json={"local": True}).status_code in (401, 403)
    assert client.__class__(client.app).post(REDEEM, json={"pass": "made-up"}).status_code == 403
    # Her pass ends the moment she stops being a guest.
    held = press()
    assert client.delete(f"/api/media/screens/guests/{wife_id}").status_code == 200
    assert tv.post(OPEN, json={"controller": held}).status_code == 403


def test_passes_run_out():
    clock = [1000.0]
    passes = kiosk.ControllerPasses(clock=lambda: clock[0])
    token = passes.issue(3, "jane")
    clock[0] += kiosk.PASS_SECONDS + 1
    try:
        passes.redeem(token)
        raise AssertionError("an old pass was taken")
    except kiosk.ScreenError:
        pass
    fresh = passes.issue(3, "jane")
    assert passes.redeem(fresh) == {"screen": 3, "by": "jane"}
