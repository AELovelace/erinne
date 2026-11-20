"""Who's in Lumi Town (mnemerynne/lumitown.py, POST /api/media/games/lumi-pet/town): each person's Lumi shows for the
others while they're in town, with her look and their name; "Show me in town" off keeps someone hidden at once (they
still see the others); people drop out a while after their page stops asking; only a place, a district and a point
are taken. Lumi City (2026-11-19): out in the city, people see each other (and hear each other's steps) within one block."""

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
    first = boss.post(TOWN, json={"at": "arcade", "district": "down", "x": 320, "y": 300, "face": -1})
    assert first.status_code == 200, first.text
    assert (first.json()["seen"], first.json()["here"], first.json()["place"]) == (True, [], "city:down")   # nobody else yet
    there = jane.post(TOWN, json={"at": "market", "x": 100, "y": 420}).json()           # no district: Downtown
    assert [(p["name"], p["at"], p["district"], p["x"], p["y"], p["face"], p["inside"]) for p in there["here"]] == \
        [("boss", "arcade", "down", 320, 300, -1, False)]
    assert set(there["here"][0]["look"]) == {"glow", "outfit", "colour", "name"}   # lumipet.look: how to draw her
    back = boss.post(TOWN, json={"at": "arcade", "district": "down"}).json()
    assert [p["name"] for p in back["here"]] == ["jane"]                     # never yourself


def test_show_me_in_town_off_hides_her_at_once(client, conn, directory):
    boss, jane = _two(client, conn, directory)
    jane.post(TOWN, json={"at": "tavern", "street": "high"})                # an old page's street is still taken, and ignored
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
    fresh_town.visit(1, "arcade", "down")
    fresh_town.visit(2, "market", "down", x=40.5, y=300, face=-1)
    assert fresh_town.here(but=1) == [{"id": 2, "at": "market", "inside": False, "district": "down", "x": 40.5, "y": 300, "face": -1}]
    fresh_town.clock.now += lumitown.SEEN_SECONDS - 1
    fresh_town.visit(1, "casino", "down")
    fresh_town.clock.now += 2                                                # 2 asked SEEN_SECONDS + 1 ago: gone
    assert [p["id"] for p in fresh_town.here(but=2)] == [1]
    assert fresh_town.here(but=1) == []
    fresh_town.leave(1)
    assert fresh_town.here() == []


@pytest.mark.parametrize("body", [{"at": "<b>", "street": "high"}, {"at": "arcade", "street": "moon"}, {"street": "high"},
                                  {"at": "arcade", "street": "high", "user": 2}, {"at": "a" * 40, "street": "high"},
                                  {"at": "arcade", "district": "m3"}, {"at": "arcade", "district": "l1234"},
                                  {"at": "arcade", "x": 99999}, {"at": "arcade", "y": -500}, {"at": "arcade", "face": 0}],
                         ids=["not a place", "no such street", "no place", "anyone else's", "too long", "no such district",
                              "too far out", "x off the map", "y off the map", "facing nowhere"])
def test_only_a_place_a_district_and_a_point(client, conn, directory, body):
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
    boss.post(TOWN, json={"at": "arcade", "district": "down"})                     # both out in Downtown
    jane.post(TOWN, json={"at": "tavern", "district": "down"})
    said = boss.post(SAY, json={"text": "  hello\n\tthere  "})
    assert said.status_code == 200, said.text
    line = said.json()["line"]
    assert (line["text"], line["name"], line["place"], line["phrase"]) == ("hello there", "boss", "city:down", None)
    assert ("lumi-town", {"kind": "said", "line": line}) in heard                     # the others' streams hear it at once
    there = jane.post(TOWN, json={"at": "tavern", "district": "down"}).json()
    assert [l["text"] for l in there["lines"]] == ["hello there"] and there["place"] == "city:down"   # a TV hears it on its next POST
    inside = jane.post(TOWN, json={"at": "arcade", "inside": True}).json()
    assert inside["place"] == "in:arcade" and inside["lines"] == []                  # inside the Arcade: a different place
    assert ("lumi-town", {"kind": "moved"}) in heard                                   # and her move was heard
    out = jane.post(TOWN, json={"at": "gate-left", "district": "l1"}).json()
    assert out["place"] == "city:l1" and out["lines"] == []                          # a block away: chat stays in Downtown


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
    assert fresh_town.visit(1, "arcade", "down") == "moved"                          # arrived
    assert fresh_town.visit(1, "tavern", "down") == "walk"                           # still in Downtown, by another door
    assert fresh_town.visit(1, "tavern", "down") is None                             # nothing new
    assert fresh_town.visit(1, "tavern", "down", x=10, y=20) == "walk"               # a step
    assert fresh_town.visit(1, "tavern", "down", x=10, y=20, face=-1) == "walk"      # a turn
    assert fresh_town.visit(1, "tavern", "l1", x=-10, y=20) == "moved"               # into LefTown's first block
    assert fresh_town.visit(1, "tavern", "l1", inside=True) == "moved"               # went in
    assert fresh_town.where(1) == "in:tavern" and fresh_town.where(2) is None
    for n in range(lumitown.LINES_KEPT + 5):
        fresh_town.said({"place": "in:tavern", "text": str(n)})
    assert [l["text"] for l in fresh_town.recent("in:tavern", 100)] == [str(n) for n in range(5, lumitown.LINES_KEPT + 5)]
    clock.now += lumitown.LINE_SECONDS
    assert fresh_town.recent("in:tavern") == []                                      # ten minutes on: gone
    assert fresh_town.leave(1) is True and fresh_town.leave(1) is False


# ---- Lumi City (2026-11-19): a free-walk city, seen and heard a block either side.

def test_districts_count_blocks_out_from_downtown():
    assert [lumitown.block(d) for d in (None, "down", "l1", "l12", "r1", "r3")] == [0, 0, -1, -12, 1, 3]
    assert lumitown.place("arcade", None, False) == "city:down" and lumitown.place("arcade", "r2", True) == "in:arcade"
    assert lumitown.near("city:down", "city:l1") and lumitown.near("city:r1", "city:down") and lumitown.near("city:l3", "city:l2")
    assert not lumitown.near("city:l1", "city:r1")                                   # Downtown lies between them
    assert not lumitown.near("city:down", "in:arcade") and not lumitown.near("in:arcade", "in:casino")
    assert lumitown.near("in:arcade", "in:arcade") and lumitown.near("house:1:den", "house:1:den")
    assert not lumitown.near(None, "city:down") and not lumitown.near("house:1:den", "home:1:den")


def test_out_in_the_city_people_see_each_other_a_block_either_side(client, conn, directory, monkeypatch):
    from mnemerynne import events
    heard = []
    monkeypatch.setattr(events, "publish_sync", lambda channel, data: heard.append(data) or 1)
    boss, jane = _two(client, conn, directory)
    boss_id = boss.post(TOWN, json={"at": "office", "district": "down", "x": 0, "y": 300}).json()["you"]
    assert [p["name"] for p in jane.post(TOWN, json={"at": "plot-l-1-1", "district": "l1"}).json()["here"]] == ["boss"]
    assert jane.post(TOWN, json={"at": "plot-l-2-1", "district": "l2"}).json()["here"] == []   # two blocks away: out of sight
    assert jane.post(TOWN, json={"at": "arcade", "inside": True}).json()["here"] == []          # inside: only those inside
    assert [p["name"] for p in jane.post(TOWN, json={"at": "plot-r-1-1", "district": "r1"}).json()["here"]] == ["boss"]
    heard.clear()
    boss.post(TOWN, json={"at": "office", "district": "down", "x": 64.25, "y": 310, "face": -1})   # a step in the same district
    assert heard == [{"kind": "walk", "place": "city:down", "district": "down", "uid": boss_id, "x": 64.2, "y": 310, "face": -1}]
    heard.clear()
    boss.post(TOWN, json={"at": "office", "district": "down", "x": 64.25, "y": 310, "face": -1})   # standing still: nothing to say
    assert heard == []


def test_the_stream_passes_steps_on_within_a_block_and_city_news_to_everyone(fresh_town, monkeypatch):
    from mnemerynne import lumihouse
    from mnemerynne.web import games
    monkeypatch.setattr(lumihouse, "HOUSES", lumihouse.Houses(clock=fresh_town.clock))   # nobody in any flat
    step = {"kind": "walk", "place": "city:l1", "district": "l1", "uid": 2, "x": 1, "y": 2, "face": 1}
    fresh_town.visit(1, "office", "down")                                            # (lines and emotes ask where she is now)
    assert games._relay(step, 1, "city:down") == ("walk", step)                      # a block away: she sees it
    assert games._relay(step, 1, "city:r1") is None                                  # two blocks away: no
    assert games._relay(step, 1, "in:arcade") is None                                # inside a building: no
    assert games._relay(step, 2, "city:l1") is None                                  # never her own steps
    assert games._relay({"kind": "city"}, 1, None) == ("city", {})                   # a plot changed: everyone looks again
    line = {"uid": 2, "place": "city:l1", "text": "hi"}
    assert games._relay({"kind": "said", "line": line}, 1, "city:l1") is None        # chat stays in its own district...
    fresh_town.visit(1, "office", "l1")
    assert games._relay({"kind": "said", "line": line}, 1, "city:l1") == ("said", line)   # ...and is heard there
    assert games._relay({"kind": "emote", "place": "city:l1", "uid": 2, "emote": "wave"}, 1, None) == ("emote", {"uid": 2, "emote": "wave"})
