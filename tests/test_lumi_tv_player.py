"""Who's playing? (lumi_tv.py): on a TV paired to one account, a remote's OK press plays the remote holder's own Lumi."""

from starlette.testclient import TestClient

from mnemerynne import coins
from mnemerynne.media import remotes
from test_media import _hub, _login, _pair, _remote, _turn_on
from test_media import checker, client, conn, directory, root, settings, _no_network  # Reuse the Media app's isolated fixtures.

API = "/api/media/games/lumi-pet/"


def _couch(client, conn, directory):
    """boss owns the TV; jane is boss's TV guest and holds her own phone remote. Returns the TV's client and a way to
    press OK on jane's remote and read the signed proof the TV receives."""
    owner_id = _login(client, conn, directory)
    _turn_on(client)
    screen_id, screen_key = _pair(client)
    wife = TestClient(client.app)
    wife_id = _login(wife, conn, directory, "jane", "jsmith")
    panel, auth, remote_id = _remote(wife, "Jane's phone")
    assert client.post("/api/media/screens/guests", json={"username": "jane"}).status_code == 201
    tv = TestClient(client.app)
    tv.cookies.set("mnemerynne_screen", screen_key)                 # the TV: only its screen key, nobody signed in

    def press(button="enter"):
        queue = _hub(client).listen(screen_id)
        try:
            r = panel.post(f"/api/media/remote/screens/{screen_id}/command", headers=auth,
                           json={"action": "press", "button": button})
            assert r.status_code == 200, r.text
            return queue.get_nowait()["controller"]                 # what the TV page hands to the game
        finally:
            _hub(client).unlisten(screen_id, queue)
    return owner_id, wife_id, tv, press, screen_id, remote_id


def test_a_guests_ok_press_plays_her_own_lumi_on_the_owners_tv(client, conn, directory, games_conn):
    owner_id, wife_id, tv, press, screen_id, remote_id = _couch(client, conn, directory)
    assert 'data-who="1"' in tv.get("/media/games/lumi-pet").text   # the TV asks Who's playing?
    assert 'data-who="1"' not in client.get("/media/games/lumi-pet").text   # a signed-in browser doesn't

    picked = tv.post(API + "tv/player", json={"controller": press()})
    assert picked.status_code == 200, picked.text
    assert picked.json()["name"] == "jane" and picked.json()["owner"] is False
    header = {"X-Lumi-Player": picked.json()["pass"]}

    paid = tv.post(API + "visit", json={}, headers=header).json()           # today's visit pays HER purse
    assert paid["paid"] > 0
    assert coins.peek(games_conn, wife_id) == paid["coins"] and coins.peek(games_conn, owner_id) == coins.START
    assert tv.get(API + "state", headers=header).json()["coins"] == coins.peek(games_conn, wife_id)
    assert tv.get(API + "state").json()["coins"] == coins.START             # without the pass: the TV owner's Lumi

    # Her pass ends the moment she stops being a guest: refused, never swapped for the owner's pet.
    assert client.delete(f"/api/media/screens/guests/{wife_id}").status_code == 200
    refused = tv.get(API + "state", headers=header)
    assert refused.status_code == 403 and "Who's playing" in refused.json()["detail"]


def test_a_pass_only_works_on_its_own_tv_and_pairing(client, conn, directory, media_conn):
    owner_id, wife_id, tv, press, screen_id, remote_id = _couch(client, conn, directory)
    header = {"X-Lumi-Player": tv.post(API + "tv/player", json={"controller": press()}).json()["pass"]}
    assert tv.get(API + "state", headers=header).status_code == 200

    _, other_key = _pair(client, "Bedroom")                                 # another of boss's TVs
    other = TestClient(client.app)
    other.cookies.set("mnemerynne_screen", other_key)
    assert other.get(API + "state", headers=header).status_code == 403     # a pass is for one TV only
    assert client.get(API + "state", headers=header).status_code == 200     # a signed-in browser ignores it

    assert tv.get(API + "state", headers={"X-Lumi-Player": header["X-Lumi-Player"] + "x"}).status_code == 403
    remotes.create(media_conn, wife_id, "Jane's phone")                           # re-pairing her remote ends old passes
    assert tv.get(API + "state", headers=header).status_code == 403


def test_only_an_ok_press_from_a_remote_that_may_drive_the_tv_picks_a_player(client, conn, directory):
    owner_id, wife_id, tv, press, screen_id, remote_id = _couch(client, conn, directory)
    assert tv.post(API + "tv/player", json={"controller": press("up")}).status_code == 422   # an arrow isn't OK
    assert tv.post(API + "tv/player", json={"controller": "forged"}).status_code == 403
    signed_in = client.post(API + "tv/player", json={"controller": press()})   # boss signed in on the TV's browser
    assert signed_in.status_code == 403                                     # only the TV itself, nobody signed in, asks
    token = press()
    client.delete(f"/api/media/screens/guests/{wife_id}")
    assert tv.post(API + "tv/player", json={"controller": token}).status_code == 403   # no longer a guest
