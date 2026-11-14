"""One television with independently authenticated controllers: rules, replay handling and API ownership."""

import json
import secrets
from concurrent.futures import ThreadPoolExecutor

import pytest
from starlette.testclient import TestClient

from mnemerynne import casino, casino_remote, db
from mnemerynne.media import remotes
from test_media import _hub, _login, _pair, _remote, _turn_on
from test_media import checker, client, conn, directory, root, settings, _no_network  # Reuse the Media app's isolated fixtures.


def key(conn, state, who, action, event=None):
    """Submit a remote key with the round/phase that was visible when it was pressed."""
    return casino.couch(conn, state["game"], state["code"], "tv", command={
        "actor": who, "name": who, "action": action, "id": event or secrets.token_hex(16),
    }, expected_round=state["round"], expected_phase=state["phase"])


@pytest.mark.parametrize("game", sorted(casino.GAMES))
def test_eight_remote_seats_have_independent_drafts_and_only_the_host_starts(conn, game):
    state = casino.create(conn, game, "tv", "Living room", couch=True)
    assert state["you"] is None and state["players"] == []
    assert casino.rooms(conn, game, "tv")[0]["mine"]
    for i in range(8):
        state = key(conn, state, f"Remote {i}", "enter")
    assert len(state["players"]) == 8 and all(p["chips"] == 1000 and p["bet"] == 0 for p in state["players"])
    with pytest.raises(casino.Refused, match="eight"):
        key(conn, state, "Ninth remote", "enter")
    state = key(conn, state, "Remote 0", "up")
    state = key(conn, state, "Remote 1", "right")
    assert [p["controls"]["amount"] for p in state["players"]][:2] == [60, 50]
    state = key(conn, state, "Remote 0", "enter")
    state = key(conn, state, "Remote 1", "enter")
    assert [p["bet"] for p in state["players"]][:2] == [60, 50]
    multiplier = 3 if game == "slots" else 1
    assert [p["chips"] for p in state["players"]][:2] == [1000 - 60 * multiplier, 1000 - 50 * multiplier]
    if game in ("roulette", "horse-racing"):
        assert state["players"][0]["choice"] != state["players"][1]["choice"]
    state = key(conn, state, "Remote 1", "play")
    assert state["phase"] == "betting" and "host" in state["players"][1]["controls"]["error"]
    state = key(conn, state, "Remote 0", "play")
    assert state["phase"] in ("playing", "results")
    assert "display_actor" not in state and "inputs" not in state and "deck" not in state
    assert all("actor" not in p for p in state["players"])


def test_remote_replay_is_idempotent_and_other_remotes_do_not_make_inputs_stale(conn):
    state = casino.create(conn, "roulette", "tv", "TV", couch=True)
    state = key(conn, state, "One", "enter")
    state = key(conn, state, "Two", "enter")
    old = state
    first = key(conn, old, "One", "up", "same-event")
    second = key(conn, old, "Two", "down")  # Shared revision changed, but this player's round and phase did not.
    replay = key(conn, old, "One", "up", "same-event")
    assert replay["revision"] == second["revision"]
    assert [p["controls"]["amount"] for p in replay["players"]] == [60, 40]
    assert first["players"][0]["id"] == replay["players"][0]["id"]
    saved = casino.couch(conn, "roulette", state["code"], "tv")
    assert saved["players"] == replay["players"]  # Refresh restores both choices without creating new seats.
    saved = key(conn, saved, "One", "enter")
    saved = key(conn, saved, "One", "play")
    with pytest.raises(casino.Refused, match="round changed"):
        key(conn, old, "Two", "enter")


def test_concurrent_remote_inputs_are_serialized_without_losing_either_bet(conn, settings):
    state = casino.create(conn, "slots", "tv", "TV", couch=True)
    for who in ("One", "Two"):
        state = key(conn, state, who, "enter")

    def submit(who):
        """Each worker uses its own database connection, like concurrent web requests."""
        other = db.connect(settings.db_path)
        try:
            return key(other, state, who, "enter")
        finally:
            other.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(submit, ("One", "Two")))
    saved = casino.couch(conn, "slots", state["code"], "tv")
    assert [p["chips"] for p in saved["players"]] == [850, 850]
    assert [p["bet"] for p in saved["players"]] == [50, 50]


def test_shared_blackjack_hands_host_transfer_refills_and_empty_table(conn, monkeypatch):
    monkeypatch.setattr(casino.RNG, "shuffle", lambda deck: None)
    state = casino.create(conn, "blackjack", "tv", "TV", couch=True)
    for who in ("One", "Two"):
        state = key(conn, state, who, "enter")
        state = key(conn, state, who, "enter")
    state = key(conn, state, "One", "play")
    assert state["dealer"][-1] is None
    state = key(conn, state, "Two", "right")
    assert [p["controls"]["hand"] for p in state["players"]] == ["hit", "stand"]
    state = key(conn, state, "Two", "enter")
    assert not state["players"][0]["stood"] and state["players"][1]["stood"]
    state = casino.couch(conn, "blackjack", state["code"], "tv", remove=state["players"][0]["id"], revision=state["revision"])
    assert state["phase"] == "results" and state["players"][0]["name"] == "Two"
    state = casino.couch(conn, "blackjack", state["code"], "tv", remove=state["players"][0]["id"], revision=state["revision"])
    assert not state["players"] and casino.rooms(conn, "blackjack", "tv")
    assert casino.couch(conn, "blackjack", state["code"], "tv", close=True, revision=state["revision"]) == {"left": True}

    state = casino.create(conn, "slots", "tv", "TV", couch=True)
    state = key(conn, state, "One", "enter")
    raw = json.loads(conn.execute("SELECT state FROM casino_rooms WHERE code=?", (state["code"],)).fetchone()[0])
    raw["players"][0]["chips"] = 20
    conn.execute("UPDATE casino_rooms SET state=? WHERE code=?", (json.dumps(raw), state["code"]))
    state = key(conn, state, "One", "enter")
    assert state["players"][0]["chips"] == 1000 and state["players"][0]["bet"] == 0


def test_tv_requires_signed_live_remote_proof_for_its_own_screen(client, conn, directory, monkeypatch, media_conn):
    _login(client, conn, directory)
    _turn_on(client)
    screen_id, screen_key = _pair(client)
    tv = TestClient(client.app)
    tv.cookies.set("mnemerynne_screen", screen_key)
    panel, auth, remote_id = _remote(client, "Pink remote")
    queue = _hub(client).listen(screen_id)
    base = "/api/media/games/roulette/rooms"
    state = tv.post(base, json={"mode": "tv"}).json()
    path = base + "/" + state["code"] + "/tv"
    assert 'id="casino-create-tv" hidden' not in tv.get("/media/games/roulette").text
    anonymous = TestClient(client.app)
    assert anonymous.post(base, json={"mode": "tv"}).status_code == 401
    assert anonymous.get(path).status_code == 401

    def issued(button="enter"):
        """Read the actual signed envelope sent by the authenticated panel route to this TV."""
        result = panel.post(f"/api/media/remote/screens/{screen_id}/command", headers=auth, json={"action": "press", "button": button})
        assert result.status_code == 200
        return queue.get_nowait()["controller"]

    token = issued()
    body = {"token": token, "round": state["round"], "phase": state["phase"]}
    result = tv.post(path + "/input", json=body)
    assert result.status_code == 200, result.text
    assert result.json()["players"][0]["name"] == "Pink remote"
    assert tv.get(base).json()["rooms"][0]["tv"] is True  # A lost sessionStorage pointer still has a TV-resume path.
    assert tv.post(path + "/input", json=body).json()["players"][0]["bet"] == 0  # Replay cannot turn joining into betting.
    assert tv.post(path + "/input", json=body | {"token": token + "tampered"}).status_code == 403
    assert tv.post(path + "/input", json=body | {"remote_id": remote_id}).status_code == 422
    assert client.post(base + "/" + state["code"], json={"action": "bet", "amount": 50, "choice": "red", "revision": 2}).status_code == 403

    other_id = client.post("/api/media/screens", json={"name": "Other TV"}).json()["id"]
    other = TestClient(client.app)
    other.cookies.set("mnemerynne_screen", client.cookies.get("mnemerynne_screen"))
    assert other_id != screen_id and other.get(path).status_code == 403
    other_state = other.post(base, json={"mode": "tv"}).json()
    assert next(r for r in other.get(base).json()["rooms"] if r["code"] == state["code"])["tv"] is False
    other_path = base + "/" + other_state["code"] + "/tv/input"
    assert other.post(other_path, json=body).status_code == 403  # A valid proof cannot be redirected to another TV.

    token = issued("up")
    remotes.create(media_conn, remotes.get(media_conn, remote_id)["user_id"], "Pink remote")  # Rotating the pairing invalidates old proofs.
    assert tv.post(path + "/input", json=body | {"token": token}).status_code == 403
    assert panel.get("/api/media/remote", headers=auth).status_code == 401
    with monkeypatch.context() as patch:
        import itsdangerous.timed
        patch.setattr(itsdangerous.timed.TimestampSigner, "get_timestamp", lambda self: 0)
        expired = casino_remote.issue(client.app.state.settings.secret_key, remotes.get(media_conn, remote_id),
                                     media_conn.execute("SELECT * FROM media_screens WHERE id=?", (screen_id,)).fetchone(), "enter")
    assert tv.post(path + "/input", json=body | {"token": expired}).status_code == 403
    client.delete(f"/api/media/screens/{screen_id}")
    assert tv.get(path).status_code == 401
    _hub(client).unlisten(screen_id, queue)
