"""Lumi-Soft on the server (mnemerynne/lumisoft.py, lumisoft_rules.py): the replay agrees with the page's rules.js on
recorded runs, entry and continue fees, paying once, the wall-clock check, the daily cap, the Daily Range's one scored
try, stage select's order, best scores and the prizes for Lumi's house and closet. The recorded runs are made by
tests/js/lumi_soft_bot.mjs (node tests/js/lumi_soft_bot.mjs rewrites them after a rules change)."""

import json
from pathlib import Path

import pytest

from mnemerynne import coins, lumipet, lumisoft, lumisoft_rules as rules, media, users
from mnemerynne.db import transaction
from test_media import _login, _no_network, _turn_on, checker, client, conn, directory, root, settings  # noqa: F401

RUNS = json.loads((Path(__file__).parent / "fixtures" / "lumi_soft_runs.json").read_text(encoding="utf-8"))


def _run(mode, stage=None):
    """A recorded run by its mode (and stage)."""
    return next(r for r in RUNS if r["mode"] == mode and r["stage"] == stage)


@pytest.fixture
def alice(conn):
    return users.create_user(conn, "alice", "password123")


def _play(conn, uid, recorded, *, pay_continues=True, start=1000.0):
    """Start a run like the recorded one, pay its continues, and finish it with its log."""
    ticket = lumisoft.start(conn, uid, recorded["mode"], recorded["stage"], now=start)
    if pay_continues:
        for _ in range(recorded["log"]["continues"]):
            lumisoft.carry_on(conn, uid, ticket["run"])
    return ticket, lumisoft.finish(conn, uid, ticket["run"], recorded["log"], now=start + recorded["log"]["end"] / 60 + 5)


# ------------------------------------------------------------------ the rules
@pytest.mark.parametrize("recorded", RUNS, ids=[f"{r['mode']}-{r['stage'] or r['day']}" for r in RUNS])
def test_the_server_replays_the_page_exactly(recorded):
    """The same shots give the same run in Python as they did in rules.js: every score, hit, rank and step."""
    segs = rules.plan(lumisoft.DATA, recorded["mode"], recorded["stage"], recorded["day"])
    state = rules.replay(lumisoft.DATA, segs, recorded["log"])
    assert rules.summary(state) == recorded["summary"]
    assert state["refused"] == 0


def test_the_recorded_runs_cover_what_matters():
    """Clears, a whole story, a game over with continues, one without, and a run quit partway."""
    assert _run("story")["summary"]["done"] and len(_run("story")["summary"]["cleared"]) == 5
    assert _run("stage", "lounge")["summary"]["continues"] == 2
    assert _run("stage", "fair")["summary"]["over"] and not _run("stage", "fair")["summary"]["continues"]
    assert not _run("stage", "dungeon")["summary"]["done"] and not _run("stage", "dungeon")["summary"]["over"]


def test_plans_story_daily_and_stages():
    data, stages = lumisoft.DATA, lumisoft.STAGES
    story = rules.plan(data, "story")
    assert [data["stages"][s["stage"]]["id"] for s in story if s["scene"] < 0] == stages      # every stage ends with its boss
    assert len(story) == len(stages) * 4                                                      # three scenes and a boss each
    daily = [rules.plan(data, "daily", None, day) for day in range(10)]
    assert all(len(d) == 2 and d[1]["scene"] == -1 for d in daily)                            # one scene, then its boss
    assert len({json.dumps(d) for d in daily}) == 10                                          # a different range each day
    assert rules.plan(data, "stage", "training") is None and rules.plan(data, "stage", "nope") is None
    assert rules.plan(data, "training") == [{"stage": 0, "scene": 0}]


def test_every_target_and_boss_is_well_formed():
    """Each target has a kind, a look and a move the rules know; every boss has spots and hearts."""
    for st in lumisoft.DATA["stages"]:
        for sc in st["scenes"]:
            assert sc["waves"], sc["id"]
            for wave in sc["waves"]:
                for t in wave["targets"]:
                    assert t["kind"] in ("creature", "friend", "pickup") and t["look"] and 0.3 <= t["s"] <= 1.2
                    assert t["move"]["type"] in ("stand", "pop", "peek", "path", "bob", "rise")
                    assert -200 <= t["x"] <= rules.W + 200 and 0 <= t["y"] <= rules.H   # walkers start just off an edge
        if not st.get("training"):
            assert st["boss"]["hp"] > 0 and st["boss"]["spots"] and st["boss"]["wind"] > 0


# ------------------------------------------------------------------ coins
def test_a_run_costs_its_entry_and_pays_its_score_once(conn, alice):
    recorded = _run("stage", "garden")
    ticket, out = _play(conn, alice, recorded)
    assert ticket["entry"] == lumisoft.ENTRY and not ticket["practice"]
    assert out["summary"]["score"] == recorded["summary"]["score"]                         # the server's own replay
    assert out["earned"] == recorded["summary"]["score"] // lumisoft.POINTS_PER_COIN
    assert out["paid"] == min(out["earned"], 300) and out["coins"] == coins.START - lumisoft.ENTRY + out["paid"]
    with pytest.raises(lumisoft.Refused):                                                   # the ticket is used up
        lumisoft.finish(conn, alice, ticket["run"], recorded["log"], now=99999.0)


def test_the_daily_cap_and_its_setting(conn, settings, alice):
    assert media.load_settings(conn)["lumisoft_daily_cap"] == 300 == lumisoft.daily_cap(conn)
    with transaction(conn):
        media.save_settings(conn, settings, {"lumisoft_daily_cap": 0})                     # 0: no limit
    _, out = _play(conn, alice, _run("story"))
    assert out["paid"] == out["earned"] > 300 and not out["capped"]
    with transaction(conn):
        media.save_settings(conn, settings, {"lumisoft_daily_cap": 10})
    _, out = _play(conn, alice, _run("stage", "garden"))
    assert out["paid"] == 0 and out["capped"]                                               # today's 10 went long ago
    with pytest.raises(media.MediaError):
        media.save_settings(conn, settings, {"lumisoft_daily_cap": -1})


def test_no_coins_no_run_and_training_is_free(conn, alice):
    coins.held(conn, alice)                                                                 # open her purse
    while coins.spend(conn, alice, 100, "test"):                                            # spend her purse down
        pass
    while coins.spend(conn, alice, 1, "test"):
        pass
    with pytest.raises(lumisoft.Refused) as no:
        lumisoft.start(conn, alice, "story")
    assert no.value.status == 402
    ticket = lumisoft.start(conn, alice, "training")
    assert ticket["practice"] and ticket["entry"] == 0
    with pytest.raises(lumisoft.Refused):                                                   # no game over to continue from
        lumisoft.carry_on(conn, alice, ticket["run"])


def test_continues_cost_more_each_time_and_keep_the_score(conn, alice):
    recorded = _run("stage", "lounge")                                                      # went over twice
    coins.held(conn, alice)
    coins.prize(conn, alice, 5000, "test", "test:purse")                                    # enough for five continues and the replays
    ticket = lumisoft.start(conn, alice, "stage", "garden")                                 # prices, on any run
    prices = [lumisoft.carry_on(conn, alice, ticket["run"])["paid"] for _ in range(5)]
    assert prices == [50, 100, 200, 400, 400]
    lumisoft._save_progress(conn, alice, {**lumisoft._progress(conn, alice), "cleared": ["garden", "tower"]})   # the lounge is open
    _, unpaid = _play(conn, alice, recorded, pay_continues=False)
    assert unpaid["summary"]["continues"] == 0 and unpaid["summary"]["over"]               # no paid continue, no continuing
    assert unpaid["summary"]["score"] < recorded["summary"]["score"]
    before = coins.peek(conn, alice)
    _, paid = _play(conn, alice, recorded)
    assert paid["summary"] == recorded["summary"]                                           # the score kept across both
    assert coins.peek(conn, alice) == before - lumisoft.ENTRY - 50 - 100 + paid["paid"]


def test_a_run_longer_than_the_clock_allows_pays_nothing(conn, alice):
    recorded = _run("stage", "garden")
    ticket = lumisoft.start(conn, alice, "stage", "garden", now=0.0)
    out = lumisoft.finish(conn, alice, ticket["run"], recorded["log"], now=5.0)             # a 66 s run, claimed after 5 s
    assert out["paid"] == 0 and "add up" in out["refused"] and out["summary"]["score"] == 0


def test_someone_elses_ticket_and_one_run_at_a_time(conn, alice):
    bob = users.create_user(conn, "bob", "password123")
    first = lumisoft.start(conn, alice, "training")["run"]
    with pytest.raises(lumisoft.Refused):
        lumisoft.finish(conn, bob, first, {"end": 0})
    lumisoft.start(conn, alice, "training")                                                  # a new run drops the old ticket
    with pytest.raises(lumisoft.Refused):
        lumisoft.finish(conn, alice, first, {"end": 0})


# ------------------------------------------------------------------ the Daily Range and stage select
def test_the_daily_range_has_one_scored_try(conn, alice):
    bob = users.create_user(conn, "bob", "password123")
    day = lumisoft.day_number(conn)
    first = lumisoft.start(conn, alice, "daily")
    assert not first["practice"] and first["plan"] == rules.plan(lumisoft.DATA, "daily", None, day)
    recorded = {**_run("daily"), "log": _run("daily")["log"]}
    out = lumisoft.finish(conn, alice, first["run"], recorded["log"], now=10_000_000_000.0)
    again = lumisoft.start(conn, alice, "daily")
    assert again["practice"] and again["entry"] == 0
    assert lumisoft.finish(conn, alice, again["run"], {"end": 0})["paid"] == 0               # practice pays nothing
    board = lumisoft.state(conn, bob)["daily"]
    assert not board["tried"] and board["board"][0]["name"] == "alice"
    assert board["board"][0]["score"] == out["summary"]["score"]
    assert lumisoft.state(conn, alice)["daily"]["tried"]


def test_stage_select_opens_in_order(conn, alice):
    with pytest.raises(lumisoft.Refused) as locked:
        lumisoft.start(conn, alice, "stage", "tower")
    assert locked.value.status == 403
    _play(conn, alice, _run("stage", "garden"))
    assert lumisoft.start(conn, alice, "stage", "tower")["plan"]
    with pytest.raises(lumisoft.Refused):
        lumisoft.start(conn, alice, "stage", "training")


def test_bests_keep_the_highest(conn, alice):
    _, out = _play(conn, alice, _run("stage", "garden"))
    assert "stage:garden" in out["bests"]
    best = lumisoft.state(conn, alice)["bests"]["stage:garden"]
    assert best["score"] == out["summary"]["cleared"][0]["score"] and best["rank"] == out["summary"]["cleared"][0]["rank"]
    lumisoft.finish(conn, alice, lumisoft.start(conn, alice, "stage", "garden")["run"], {"end": 0})   # a worse run
    assert lumisoft.state(conn, alice)["bests"]["stage:garden"] == best


# ------------------------------------------------------------------ prizes
def test_prizes_land_in_lumis_house_and_closet(conn, alice):
    _, trained = _play(conn, alice, _run("training"))
    assert [p["id"] for p in trained["won"]] == ["soft-rug"] and "soft-rug" in lumipet.load(conn, alice)["decor"]
    _, story = _play(conn, alice, _run("story"))
    won = {p["id"] for p in story["won"]}
    assert {"soft-trophy", "acc-soft-goggles", "soft-wallpaper", "soft-curtains", "soft-lamp", "soft-ceiling", "soft-bed"} <= won
    pet = lumipet.load(conn, alice)
    assert "acc-soft-goggles" in pet["closet"] and "soft-trophy" in pet["decor"]
    again = _play(conn, alice, _run("story"))[1]
    assert not {"soft-trophy", "acc-soft-goggles"} & {p["id"] for p in again["won"]}         # each prize once
    state = lumisoft.state(conn, alice)
    assert len(state["prizes"]) == len(lumisoft.PRIZES) and all(p["how"] for p in state["prizes"])
    assert {p["id"] for p in state["prizes"] if p["won"]} >= won


def test_every_prize_is_in_lumis_catalogs_and_cant_be_bought():
    for item, need in lumisoft.PRIZES:
        info = lumisoft._prize_info(item)
        assert info and info["how"], item
        piece = lumipet._DECOR.get(item) or lumipet._WEAR.get(item)
        assert piece["price"] == 0 and piece["prize"] == "lumi-soft"


# ------------------------------------------------------------------ the routes
def test_the_routes_take_intentions_and_logs_only(client, conn, directory):
    uid = _login(client, conn, directory)
    _turn_on(client)
    state = client.get("/api/media/games/lumi-soft/state").json()
    assert state["entry"] == lumisoft.ENTRY and state["stages"]["stages"][0]["id"] == "training"
    assert client.post("/api/media/games/lumi-soft/start", json={"mode": "story", "score": 5}).status_code == 422
    assert client.post("/api/media/games/lumi-soft/start", json={"mode": "cheat"}).status_code == 422
    t = client.post("/api/media/games/lumi-soft/start", json={"mode": "training"}).json()
    recorded = _run("training")
    assert client.post("/api/media/games/lumi-soft/finish", json={**recorded["log"], "run": t["run"], "score": 99999}).status_code == 422
    out = client.post("/api/media/games/lumi-soft/finish", json={**recorded["log"], "run": t["run"]}).json()
    assert out["refused"] and "add up" in out["refused"]                                     # 11 s of play, finished at once
    s = client.post("/api/media/games/lumi-soft/start", json={"mode": "stage", "stage": "garden"}).json()
    assert client.post("/api/media/games/lumi-soft/continue", json={"run": s["run"]}).json()["paid"] == 50
    assert client.put("/api/admin/settings/media", json={"lumisoft_daily_cap": 40}).status_code == 200
    assert client.get("/api/admin/settings/media").json()["settings"]["lumisoft_daily_cap"] == 40
    assert uid
