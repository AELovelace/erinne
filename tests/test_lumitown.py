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
    assert (first.json()["seen"], first.json()["here"]) == (True, [])        # nobody else yet
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
    assert fresh_town.here(but=1) == [(2, "market", "market", False)]
    fresh_town.clock.now += lumitown.SEEN_SECONDS - 1
    fresh_town.visit(1, "casino", "high")
    fresh_town.clock.now += 2                                                # 2 asked SEEN_SECONDS + 1 ago: gone
    assert fresh_town.here(but=2) == [(1, "casino", "high", False)]
    assert fresh_town.here(but=1) == []
    fresh_town.leave(1)
    assert fresh_town.here() == []


@pytest.mark.parametrize("body", [{"at": "<b>", "street": "high"}, {"at": "arcade", "street": "moon"}, {"at": "arcade"},
                                  {"at": "arcade", "street": "high", "user": 2}, {"at": "a" * 40, "street": "high"}],
                         ids=["not a place", "no such street", "no street", "anyone else's", "too long"])
def test_only_a_place_and_a_street(client, conn, directory, body):
    boss, _ = _two(client, conn, directory)
    assert boss.post(TOWN, json=body).status_code == 422


# ---- Local chat (docs/plans/lumi-pet-social.md phase 2, 2026-11-19): a line is heard by everyone in the same place
# (a street's map, or inside one place), kept in memory a while for whoever walks in, never stored.

SAY = TOWN + "/say"


@pytest.fixture(autouse=True)
def fresh_counts():
    """The rate limits count on the event bus, which outlives a test: start each test with none."""
    from mnemerynne import events
    for uid in range(1, 30):
        events.forget(f"lumi-say:{uid}")
        events.forget(f"lumi-say-burst:{uid}")


def test_a_line_is_heard_in_the_same_place_and_not_elsewhere(client, conn, directory, monkeypatch):
    boss, jane = _two(client, conn, directory)
    heard = []
    from mnemerynne import events
    monkeypatch.setattr(events, "publish_sync", lambda channel, data: heard.append((channel, data)) or 1)
    boss.post(TOWN, json={"at": "arcade", "street": "high"})                       # both out on the High Street's map
    jane.post(TOWN, json={"at": "tavern", "street": "high"})
    said = boss.post(SAY, json={"text": "  hello\n\tthere  "})
    assert said.status_code == 200, said.text
    line = said.json()["line"]
    assert (line["text"], line["name"], line["place"], line["phrase"]) == ("hello there", "boss", "street:high", None)
    assert ("lumi-town", {"kind": "said", "line": line}) in heard                     # the others' streams hear it at once
    there = jane.post(TOWN, json={"at": "tavern", "street": "high"}).json()
    assert [l["text"] for l in there["lines"]] == ["hello there"] and there["place"] == "street:high"   # a TV hears it on its next POST
    inside = jane.post(TOWN, json={"at": "arcade", "street": "high", "inside": True}).json()
    assert inside["place"] == "in:arcade" and inside["lines"] == []                  # inside the Arcade: a different place
    assert ("lumi-town", {"kind": "moved"}) in heard                                   # and her move was heard


def test_quick_phrases_for_remotes_and_pads(client, conn, directory):
    boss, jane = _two(client, conn, directory)
    answer = boss.post(TOWN, json={"at": "arcade", "street": "high"}).json()
    ids = [p["id"] for p in answer["phrases"]]
    assert "hi" in ids and all(set(p) == {"id", "icon", "text"} for p in answer["phrases"])
    said = boss.post(SAY, json={"phrase": "hi"}).json()["line"]
    assert (said["text"], said["phrase"]) == ("Hi!", "hi")
    assert boss.post(SAY, json={"phrase": "nope"}).status_code == 422                # only the town's own phrases
    assert boss.post(SAY, json={"phrase": "hi", "sneaky": 1}).status_code == 422     # nothing else is taken


def test_only_a_lumi_others_can_see_may_speak(client, conn, directory):
    boss, jane = _two(client, conn, directory)
    assert boss.post(SAY, json={"phrase": "hi"}).status_code == 409                  # not in town yet
    jane.post("/api/media/games/lumi-pet/act", json={"action": "town_seen"})         # Show me in town: off
    jane.post(TOWN, json={"at": "tavern", "street": "high"})
    refused = jane.post(SAY, json={"phrase": "hi"})
    assert refused.status_code == 409 and "Show me in town" in refused.json()["detail"]


def test_not_too_fast(client, conn, directory):
    boss, _ = _two(client, conn, directory)
    boss.post(TOWN, json={"at": "arcade", "street": "high"})
    assert boss.post(SAY, json={"phrase": "hi"}).status_code == 200
    slow = boss.post(SAY, json={"phrase": "hi"})
    assert slow.status_code == 429 and "out of breath" in slow.json()["detail"]


def test_typed_words_are_cleaned_and_kept_short():
    assert lumitown.clean("a​b‮ c\x00\n\n d") == "ab c d"                  # zero-widths, bidi tricks, controls gone; one line
    for bad in ["", "   \n\t ", "​"]:
        with pytest.raises(ValueError):
            lumitown.clean(bad)
    assert lumitown.clean("x" * lumitown.TEXT_MAX) == "x" * lumitown.TEXT_MAX
    with pytest.raises(ValueError):
        lumitown.clean("x" * (lumitown.TEXT_MAX + 1))


def test_moves_lines_and_forgetting(fresh_town):
    clock = fresh_town.clock
    assert fresh_town.visit(1, "arcade", "high") is True                             # arrived
    assert fresh_town.visit(1, "tavern", "high") is False                            # still on the High Street's map
    assert fresh_town.visit(1, "tavern", "high", inside=True) is True                # went in
    assert fresh_town.where(1) == "in:tavern" and fresh_town.where(2) is None
    for n in range(lumitown.LINES_KEPT + 5):
        fresh_town.said({"place": "in:tavern", "text": str(n)})
    assert [l["text"] for l in fresh_town.recent("in:tavern", 100)] == [str(n) for n in range(5, lumitown.LINES_KEPT + 5)]
    clock.now += lumitown.LINE_SECONDS
    assert fresh_town.recent("in:tavern") == []                                      # ten minutes on: gone
    assert fresh_town.leave(1) is True and fresh_town.leave(1) is False
