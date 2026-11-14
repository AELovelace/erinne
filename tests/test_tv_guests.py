"""TV guests (media/screens.py, migration 0072): someone else's own remote drives the owner's TVs, as the owner's TV."""

from starlette.testclient import TestClient

from mnemerynne import casino_remote
from mnemerynne.media import screens
from test_media import _hub, _login, _pair, _remote, _turn_on
from test_media import checker, client, conn, directory, root, settings, _no_network  # Reuse the Media app's isolated fixtures.


def _household(client, conn, directory):
    """Doll's account owns the TV ("boss"); her wife ("jane", a different account) pairs her own phone remote."""
    owner_id = _login(client, conn, directory)                 # boss: signed in on `client`
    _turn_on(client)
    screen_id, screen_key = _pair(client)                       # the living-room TV, paired to boss
    wife = TestClient(client.app)
    wife_id = _login(wife, conn, directory, "jane", "jsmith")   # her own account, signed in on her phone
    panel, auth, remote_id = _remote(wife, "Jane's phone")      # her remote belongs to her account
    return owner_id, wife_id, wife, screen_id, screen_key, panel, auth, remote_id


def test_a_guest_remote_drives_the_owners_tv_as_the_owner(client, conn, directory):
    owner_id, wife_id, wife, screen_id, _key, panel, auth, remote_id = _household(client, conn, directory)
    press = f"/api/media/remote/screens/{screen_id}/command"
    assert panel.post(press, json={"action": "stop"}, headers=auth).status_code == 404       # not a guest yet
    assert panel.get("/api/media/remote", headers=auth).json()["screens"] == []

    added = client.post("/api/media/screens/guests", json={"username": "jane"})
    assert added.status_code == 201 and added.json()["added"] is True
    assert client.post("/api/media/screens/guests", json={"username": "jane"}).json()["added"] is False   # once is enough
    listed = client.get("/api/media/screens").json()
    assert [g["username"] for g in listed["guests"]] == ["jane"] and listed["hosts"] == []
    assert wife.get("/api/media/screens").json()["hosts"] == ["boss"]

    seen = panel.get("/api/media/remote", headers=auth).json()["screens"]
    assert [(s["id"], s["guest"], s["host"]) for s in seen] == [(screen_id, True, "boss")]

    queue = _hub(client).listen(screen_id)                       # the TV's open stream
    try:
        r = panel.post(press, json={"action": "press", "button": "enter"}, headers=auth)
        assert r.status_code == 200, r.text
        sent = queue.get_nowait()
        assert sent["sender"] == "jane" and sent["button"] == "enter"           # the TV is told who pressed
        proof = casino_remote.read(client.app.state.settings.secret_key, sent["controller"])
        assert proof["remote"] == remote_id                                   # her own remote's proof
        assert panel.post(press, json={"action": "play_station", "key": "nope"}, headers=auth).status_code == 404

        # Removing her: her very next press is refused.
        assert client.delete(f"/api/media/screens/guests/{wife_id}").status_code == 200
        assert panel.post(press, json={"action": "stop"}, headers=auth).status_code == 404
        assert client.delete(f"/api/media/screens/guests/{wife_id}").status_code == 404
    finally:
        _hub(client).unlisten(screen_id, queue)


def test_guests_are_added_by_name_and_only_people_who_may_use_screens(client, conn, directory, media_conn):
    owner_id = _login(client, conn, directory)
    _turn_on(client)
    _pair(client)
    assert client.post("/api/media/screens/guests", json={"username": "nobody"}).status_code == 404
    assert client.post("/api/media/screens/guests", json={"username": "boss"}).status_code == 422   # not their own guest
    assert screens.guests(media_conn, owner_id) == []


def test_a_guests_remote_takes_a_casino_couch_seat(client, conn, directory):
    owner_id, wife_id, wife, screen_id, screen_key, panel, auth, remote_id = _household(client, conn, directory)
    client.post("/api/media/screens/guests", json={"username": "jane"})
    tv = TestClient(client.app)
    tv.cookies.set("mnemerynne_screen", screen_key)
    base = "/api/media/games/roulette/rooms"
    state = tv.post(base, json={"mode": "tv"}).json()
    queue = _hub(client).listen(screen_id)
    try:
        assert panel.post(f"/api/media/remote/screens/{screen_id}/command", headers=auth,
                          json={"action": "press", "button": "enter"}).status_code == 200
        token = queue.get_nowait()["controller"]
        body = {"token": token, "round": state["round"], "phase": state["phase"]}
        seated = tv.post(base + "/" + state["code"] + "/tv/input", json=body)
        assert seated.status_code == 200, seated.text
        assert seated.json()["players"][0]["name"] == "Jane's phone"          # couch seats stay on free credits
        assert seated.json()["players"][0]["chips"] == 1000

        client.delete(f"/api/media/screens/guests/{wife_id}")                 # no longer a guest
        assert panel.post(f"/api/media/remote/screens/{screen_id}/command", headers=auth,
                          json={"action": "press", "button": "up"}).status_code == 404
        replay = tv.post(base + "/" + state["code"] + "/tv/input", json=body | {"round": state["round"]})
        assert replay.status_code == 403                                      # her earlier proof is refused too
    finally:
        _hub(client).unlisten(screen_id, queue)


def test_a_guest_never_drives_the_owners_speaker(client, conn, directory, media_conn):
    owner_id, wife_id, wife, screen_id, _key, panel, auth, remote_id = _household(client, conn, directory)
    client.post("/api/media/screens/guests", json={"username": "jane"})
    speaker_id = media_conn.execute("INSERT INTO media_screens(user_id, name, token_hash, speaker) VALUES (?, 'Kitchen', 'x', 1)",
                              (owner_id,)).lastrowid                         # one of boss's speakers
    assert [s["id"] for s in panel.get("/api/media/remote", headers=auth).json()["screens"]] == [screen_id]
    assert panel.post(f"/api/media/remote/screens/{speaker_id}/command", headers=auth,
                      json={"action": "stop"}).status_code == 404
