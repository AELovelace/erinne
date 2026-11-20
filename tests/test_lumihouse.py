"""Visiting each other's flats (mnemerynne/lumihouse.py, docs/plans/lumi-pet-social.md phase 3): the front door says
who may come in (knock by default, open, closed, and never someone blocked); a knock needs the host at home, who lets
the guest in or not; a guest sees only what a visitor should (the house, never coins or settings); shutting the door
or blocking someone sends guests home; chat inside a flat is heard in that room only."""

import pytest
from starlette.testclient import TestClient

from mnemerynne import events, lumihouse, lumitown
from test_media import _login, _no_network, _turn_on, checker, client, conn, directory, root, settings  # noqa: F401

API = "/api/media/games/lumi-pet/"


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    """A fresh town and fresh flats on one shared pretend clock, no rate limits left over, and every event kept."""
    clock = Clock()
    monkeypatch.setattr(lumitown, "TOWN", lumitown.Town(clock=clock))
    monkeypatch.setattr(lumihouse, "HOUSES", lumihouse.Houses(clock=clock))
    for uid in range(1, 30):
        for key in (f"lumi-say:{uid}", f"lumi-say-burst:{uid}", f"lumi-knock:{uid}"):
            events.forget(key)
    heard = []
    monkeypatch.setattr(events, "publish_sync", lambda channel, data: heard.append(data) or 1)
    return clock, heard


def _two(client, conn, directory):
    """boss (the host) and jane (the visitor), each signed in on their own browser, each with a Lumi."""
    _login(client, conn, directory)
    _turn_on(client)
    jane = TestClient(client.app)
    _login(jane, conn, directory, "jane", "jsmith")
    for who in (client, jane):
        assert who.get(API + "state").status_code == 200        # her Lumi exists from her first look
    boss_id = client.post(API + "town", json={"at": "home", "street": "high"}).json()["you"]
    jane_id = jane.post(API + "town", json={"at": "home", "street": "high"}).json()["you"]
    return client, jane, boss_id, jane_id


def _home(who, host=None, room="bedroom", x=500, y=430):
    """Say where this Lumi stands in a flat (her own when host is None)."""
    return who.post(API + "houses/here", json={"host": host, "room": room, "x": x, "y": y, "face": 1})


def test_a_knock_needs_the_host_home_and_their_answer(client, conn, directory, fresh):
    _, heard = fresh
    boss, jane, boss_id, jane_id = _two(client, conn, directory)
    seen = jane.get(API + f"houses/{boss_id}").json()
    assert (seen["access"], seen["door"], seen["name"]) == ("knock", "knock", "boss")      # knock is the default door
    assert "house" not in seen and set(seen["look"]) == {"glow", "outfit", "colour", "name"}   # no peeking before you're in
    assert _home(jane, boss_id).status_code == 403                                         # can't just walk in
    away = jane.post(API + f"houses/{boss_id}/knock")
    assert away.status_code == 409 and "Nobody's in" in away.json()["detail"]
    _home(boss)                                                                            # boss opens Lumi Pet: home
    knock = jane.post(API + f"houses/{boss_id}/knock").json()
    assert knock["in"] is False and {"kind": "knock", "host": boss_id} in heard
    assert jane.get(API + f"knocks/{knock['knock']}").json() == {"answer": None, "gone": False}
    waiting = _home(boss).json()["knocks"]
    assert [(k["id"], k["name"]) for k in waiting] == [(knock["knock"], "jane")]           # a TV host hears it this way
    assert jane.post(API + f"knocks/{knock['knock']}/answer", json={"yes": True}).status_code == 404   # only the host answers
    assert boss.post(API + f"knocks/{knock['knock']}/answer", json={"yes": True}).json() == {"answer": True}
    assert {"kind": "answer", "guest": jane_id, "host": boss_id, "yes": True} in heard
    assert jane.get(API + f"knocks/{knock['knock']}").json()["answer"] is True
    view = jane.get(API + f"houses/{boss_id}").json()
    assert view["access"] == "in" and isinstance(view["house"], dict) and "boxes" in view
    assert not {"coins", "pet", "inventory", "blocked", "hunger"} & set(view)             # only what a visitor should see
    inside = _home(jane, boss_id, x=300).json()
    assert [(p["name"], p["host"], p["room"]) for p in inside["here"]] == [("boss", True, "bedroom")]
    assert inside["home"] is True and inside["place"] == f"house:{boss_id}:bedroom" and inside["knocks"] == []


def test_not_now_and_an_open_door(client, conn, directory):
    boss, jane, boss_id, _ = _two(client, conn, directory)
    _home(boss)
    knock = jane.post(API + f"houses/{boss_id}/knock").json()["knock"]
    boss.post(API + f"knocks/{knock}/answer", json={"yes": False})
    assert jane.get(API + f"knocks/{knock}").json()["answer"] is False
    assert _home(jane, boss_id).status_code == 403                                         # not let in
    assert boss.post(API + "act", json={"action": "door", "item": "open"}).json()["said"] == {"door": "open"}
    assert jane.post(API + f"houses/{boss_id}/knock").json() == {"in": True}               # an open door: straight in
    assert _home(jane, boss_id).status_code == 200


def test_closing_the_door_and_blocking_send_guests_home(client, conn, directory, fresh):
    _, heard = fresh
    boss, jane, boss_id, jane_id = _two(client, conn, directory)
    boss.post(API + "act", json={"action": "door", "item": "open"})
    _home(boss)
    _home(jane, boss_id, room="den")
    assert [f["name"] for f in jane.get(API + "houses").json()["flats"]] == ["boss"]
    shut = boss.post(API + "act", json={"action": "door"})                                 # open → closed (the next one)
    assert shut.json()["pet"]["door"] == "closed"
    assert {"kind": "home", "host": boss_id, "out": [jane_id]} in heard
    assert jane.get(API + f"houses/{boss_id}").status_code == 403
    assert _home(jane, boss_id).status_code == 403
    assert jane.get(API + "houses").json()["flats"] == []                                  # a closed door isn't listed
    boss.post(API + "act", json={"action": "door", "item": "open"})
    _home(jane, boss_id)
    heard.clear()
    blocked = boss.post(API + "act", json={"action": "block", "item": str(jane_id)})
    assert blocked.status_code == 200 and blocked.json()["blocked"] == [{"id": jane_id, "name": "jane"}]   # named for Settings
    assert {"kind": "home", "host": boss_id, "out": [jane_id]} in heard
    refused = jane.get(API + f"houses/{boss_id}")
    assert refused.status_code == 403 and refused.json()["detail"] == "That door is closed."   # a block looks like a closed door
    assert jane.post(API + f"houses/{boss_id}/knock").status_code == 403
    assert boss.post(API + "act", json={"action": "unblock", "item": str(jane_id)}).json()["blocked"] == []
    assert jane.get(API + f"houses/{boss_id}").json()["access"] == "in"
    assert boss.post(API + "act", json={"action": "block", "item": str(boss_id)}).status_code == 400   # never yourself
    assert boss.post(API + "act", json={"action": "door", "item": "ajar"}).status_code == 400


def test_chat_in_a_flat_is_heard_in_that_room(client, conn, directory, fresh):
    _, heard = fresh
    boss, jane, boss_id, _ = _two(client, conn, directory)
    boss.post(API + "act", json={"action": "door", "item": "open"})
    _home(boss)
    _home(jane, boss_id)
    said = jane.post(API + "town/say", json={"phrase": "hi"})
    assert said.status_code == 200 and said.json()["line"]["place"] == f"house:{boss_id}:bedroom"
    assert [l["text"] for l in _home(boss).json()["lines"]] == ["Hi!"]
    assert _home(boss, room="kitchen").json()["lines"] == []                               # another room: not heard
    walks = [e for e in heard if e.get("kind") == "walk"]
    assert walks and walks[-1]["place"] == f"house:{boss_id}:bedroom"                     # her steps went out to the room


def test_houses_remember_and_forget(fresh):
    clock, _ = fresh
    houses = lumihouse.HOUSES
    assert houses.visit(1, 1, "bedroom", 500, 430, 1) is True and houses.home(1)
    assert houses.visit(1, 1, "bedroom", 520, 430, -1) is False                            # a step, not a new place
    assert houses.visit(2, 1, "kitchen", 300, 420, 1) is True
    assert houses.where(2) == "house:1:kitchen" and [p["id"] for p in houses.inside(1, but=1)] == [2]
    knock = houses.knock(3, 1)
    assert houses.knock(3, 1)["id"] == knock["id"]                                         # knocking again is the same knock
    clock.now += lumihouse.KNOCK_SECONDS
    assert houses.knock_of(knock["id"]) is None                                            # unanswered: forgotten
    clock.now += lumihouse.SEEN_SECONDS
    assert not houses.home(1) and houses.where(2) is None                                  # pages that stopped asking have left
    assert lumihouse.access("open", [5], 1, 5, houses) == "closed"
    assert lumihouse.access("knock", [], 1, 1, houses) == "own"


# ---- Phase 4 (2026-11-19): emotes, the guestbook, house-warming coins.

def test_emotes_are_seen_where_you_are(client, conn, directory, fresh):
    _, heard = fresh
    boss, jane, boss_id, jane_id = _two(client, conn, directory)
    boss.post(API + "act", json={"action": "door", "item": "open"})
    _home(boss)
    _home(jane, boss_id)
    assert {e["id"] for e in _home(jane, boss_id).json()["emotes"]} >= {"wave", "dance", "hug"}
    waved = jane.post(API + "town/emote", json={"emote": "wave"})
    assert waved.status_code == 200 and waved.json()["place"] == f"house:{boss_id}:bedroom"
    assert {"kind": "emote", "place": f"house:{boss_id}:bedroom", "uid": jane_id, "emote": "wave"} in heard
    assert jane.post(API + "town/emote", json={"emote": "wave"}).status_code == 429           # one a second
    assert jane.post(API + "town/emote", json={"emote": "moonwalk"}).status_code == 422       # only the town's own


def test_the_guestbook_is_kept_signed_only_by_guests_and_the_host_can_take_lines_out(client, conn, directory, fresh):
    _, heard = fresh
    boss, jane, boss_id, _ = _two(client, conn, directory)
    book = API + f"houses/{boss_id}/guestbook"
    assert jane.post(book, json={"text": "hi!"}).status_code == 403                           # not let in yet
    assert boss.post(API + f"houses/{boss_id}/guestbook", json={"text": "me"}).status_code == 409   # not your own
    boss.post(API + "act", json={"action": "door", "item": "open"})
    signed = jane.post(book, json={"text": "  lovely\nflat!  "})
    assert signed.status_code == 200, signed.text
    line = signed.json()["guestbook"][-1]
    assert (line["text"], line["name"]) == ("lovely flat!", "jane") and "uid" not in line      # cleaned; never who by account
    assert {"kind": "guestbook", "host": boss_id} in heard                                      # the host's page looks again
    assert jane.post(book, json={"text": "again"}).status_code == 429                         # once in a while
    assert [e["text"] for e in jane.get(API + f"houses/{boss_id}").json()["guestbook"]] == ["lovely flat!"]   # visitors can read it
    mine = boss.get(API + "state").json()["pet"]["guestbook"]
    assert [e["text"] for e in mine] == ["lovely flat!"]                                       # kept in the host's save
    gone = boss.post(API + "act", json={"action": "guestbook_remove", "item": mine[0]["id"]})
    assert gone.status_code == 200 and gone.json()["pet"]["guestbook"] == []


def test_house_warming_coins_once_a_day_per_flat(client, conn, directory):
    boss, jane, boss_id, _ = _two(client, conn, directory)
    boss.post(API + "act", json={"action": "door", "item": "open"})
    before = jane.get("/api/media/games/coins").json()["coins"]
    first = _home(jane, boss_id).json()
    assert first["paid"] == 10 and first["coins"] == before + 10
    assert _home(jane, boss_id, room="kitchen").json()["paid"] == 0                         # another room: not a new visit
    jane.post(API + "town", json={"at": "home", "street": "high"})
    lumihouse.HOUSES.leave(jane.post(API + "town", json={"at": "home", "street": "high"}).json()["you"])
    assert _home(jane, boss_id).json()["paid"] == 0                                           # back again today: nothing more
    assert _home(boss).json()["paid"] == 0                                                    # never for your own flat
