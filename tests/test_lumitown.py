"""Who's in Lumi Town (mnemerynne/lumitown.py, POST /api/media/games/lumi-pet/town): each person's Lumi shows for the
others while they're in town, with her look and their name; "Show me in town" off keeps someone hidden at once (they
still see the others); people drop out a while after their page stops asking; only a place and a street are taken."""

import pytest
from starlette.testclient import TestClient

from mnemerynne import lumitown
from test_media import _login, _no_network, _turn_on, checker, client, conn, directory, root, settings  # noqa: F401

TOWN = "/api/media/games/lumi-pet/town"


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


@pytest.fixture(autouse=True)
def fresh_town(monkeypatch):
    town = lumitown.Town(clock=Clock())
    monkeypatch.setattr(lumitown, "TOWN", town)
    return town


def _two(client, conn, directory):
    """boss and jane, each signed in on their own browser, with Media on."""
    _login(client, conn, directory)
    _turn_on(client)
    jane = TestClient(client.app)
    _login(jane, conn, directory, "jane", "jsmith")
    return client, jane


def test_each_sees_the_other_in_town(client, conn, directory):
    boss, jane = _two(client, conn, directory)
    first = boss.post(TOWN, json={"at": "arcade", "street": "high"})
    assert first.status_code == 200, first.text
    assert first.json() == {"seen": True, "here": []}                       # nobody else yet
    there = jane.post(TOWN, json={"at": "market", "street": "market"}).json()
    assert [(p["name"], p["at"], p["street"]) for p in there["here"]] == [("boss", "arcade", "high")]
    assert set(there["here"][0]["look"]) == {"glow", "outfit", "colour", "name"}   # lumipet.look: how to draw her
    back = boss.post(TOWN, json={"at": "arcade", "street": "high"}).json()
    assert [p["name"] for p in back["here"]] == ["jane"]                     # never yourself


def test_show_me_in_town_off_hides_her_at_once(client, conn, directory):
    boss, jane = _two(client, conn, directory)
    jane.post(TOWN, json={"at": "tavern", "street": "high"})
    assert [p["name"] for p in boss.post(TOWN, json={"at": "home", "street": "high"}).json()["here"]] == ["jane"]
    state = jane.get("/api/media/games/lumi-pet/state").json()
    assert state["pet"]["town_seen"] is True                                 # on unless turned off
    toggled = jane.post("/api/media/games/lumi-pet/act", json={"action": "town_seen"})
    assert toggled.status_code == 200 and toggled.json()["pet"]["town_seen"] is False
    hidden = jane.post(TOWN, json={"at": "tavern", "street": "high"}).json()
    assert hidden["seen"] is False and [p["name"] for p in hidden["here"]] == ["boss"]   # she still sees him
    assert boss.post(TOWN, json={"at": "home", "street": "high"}).json()["here"] == []   # he no longer sees her
    jane.post("/api/media/games/lumi-pet/act", json={"action": "town_seen"})            # and back on
    jane.post(TOWN, json={"at": "tavern", "street": "high"})
    assert [p["name"] for p in boss.post(TOWN, json={"at": "home", "street": "high"}).json()["here"]] == ["jane"]


def test_people_drop_out_when_their_page_stops_asking(fresh_town):
    fresh_town.visit(1, "arcade", "high")
    fresh_town.visit(2, "market", "market")
    assert fresh_town.here(but=1) == [(2, "market", "market")]
    fresh_town.clock.now += lumitown.SEEN_SECONDS - 1
    fresh_town.visit(1, "casino", "high")
    fresh_town.clock.now += 2                                                # 2 asked SEEN_SECONDS + 1 ago: gone
    assert fresh_town.here(but=2) == [(1, "casino", "high")]
    assert fresh_town.here(but=1) == []
    fresh_town.leave(1)
    assert fresh_town.here() == []


@pytest.mark.parametrize("body", [{"at": "<b>", "street": "high"}, {"at": "arcade", "street": "moon"}, {"at": "arcade"},
                                  {"at": "arcade", "street": "high", "user": 2}, {"at": "a" * 40, "street": "high"}],
                         ids=["not a place", "no such street", "no street", "anyone else's", "too long"])
def test_only_a_place_and_a_street(client, conn, directory, body):
    boss, _ = _two(client, conn, directory)
    assert boss.post(TOWN, json=body).status_code == 422
