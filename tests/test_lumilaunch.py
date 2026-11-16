"""Lumi Launch on the server (mnemerynne/lumilaunch.py, lumilaunch_rules.py): the replay lands Lumi on exactly the spot
the page's rules.js did on recorded goes, the angle tables match, the course is seeded, a go costs its entry and pays
its distance once, the daily cap, the wall-clock check, refused logs, bests and the board, the routes, and couch turns'
Who's this remote? on a TV. The recorded goes are made by tests/js/lumi_launch_bot.mjs (node tests/js/lumi_launch_bot.mjs
rewrites them after a rules change)."""

import json
import re
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from mnemerynne import coins, db, lumilaunch, lumilaunch_rules as rules, media, users
from test_lumi_tv_player import _couch
from test_media import _login, _no_network, _turn_on, checker, client, conn, directory, root, settings  # noqa: F401

ROOT = Path(__file__).parent
RUNS = json.loads((ROOT / "fixtures" / "lumi_launch_runs.json").read_text(encoding="utf-8"))
RULES_JS = ROOT.parent / "mnemerynne" / "web" / "static" / "games" / "lumi-launch" / "rules.js"


@pytest.fixture
def alice(conn, games_conn):
    return users.create_user(conn, "alice", "password123")


def _go(games_conn, uid, recorded, start=1000.0, late=5.0):
    """Start a go and finish it with a recorded go's presses, `late` seconds after its frames could have been flown."""
    ticket = lumilaunch.start(games_conn, uid, now=start)
    games_conn.execute("UPDATE lumi_launch_runs SET seed = ? WHERE id = ?", (recorded["seed"], ticket["run"]))   # the recorded course
    frames = recorded["result"]["frames"]
    return ticket, lumilaunch.finish(games_conn, uid, ticket["run"], recorded["shots"], now=start + frames / 60 + late)


# ------------------------------------------------------------------ the rules
@pytest.mark.parametrize("recorded", RUNS, ids=[str(r["seed"]) for r in RUNS])
def test_the_server_flies_her_exactly_where_the_page_did(recorded):
    """The same presses give the same flight in Python as in rules.js: to the last bit of every position and speed."""
    for log, end in zip(recorded["shots"], recorded["ends"]):
        s = rules.play_shot(recorded["seed"], log)
        assert (s["x"], s["y"], s["vx"], s["t"], s["end"], rules.metres(s), s["dust"]) == \
               (end["x"], end["y"], end["vx"], end["t"], end["end"], end["m"], end["dust"])
    assert rules.replay(recorded["seed"], recorded["shots"]) == recorded["result"]


def test_the_recorded_goes_cover_what_matters():
    """Landings in honey and on a nap mat, an idle cannon that fires itself, nudged needles and puffs."""
    ends = {e["end"] for r in RUNS for e in r["ends"]}
    assert {"rest", "honey", "nap"} <= ends
    logs = [p for r in RUNS for log in r["shots"] for p in log]
    assert any(b == "up" for _, b in logs) and any(b == "down" for _, b in logs)
    assert any(not log for r in RUNS for log in r["shots"])                                 # the idle launch: no presses at all
    assert any(r["result"]["dust"] for r in RUNS)                                           # some stardust was caught


def test_both_rules_files_hold_the_same_angles():
    """python/make_lumi_launch_tables.py writes the same literal numbers into rules.js and lumilaunch_rules.py."""
    block = re.search(r"const ANGLES = \[(.*?)\];", RULES_JS.read_text(encoding="utf-8"), re.S).group(1)
    js = [tuple(float(n) for n in pair) for pair in re.findall(r"\[([-\d.]+), ([-\d.]+)\]", block)]
    assert js == [tuple(p) for p in rules.ANGLES] and len(js) == rules.AIM_STEPS + 1
    assert rules.ANGLES[0] == (0.965926, 0.258819) and rules.ANGLES[30] == (0.707107, 0.707107)   # 15° and 45°


def test_the_course_is_seeded_and_gets_trickier():
    """A seed always lays the same pieces; every kind turns up; hazards grow more common further out."""
    assert [rules.piece(42, i) for i in range(50)] == [rules.piece(42, i) for i in range(50)]
    assert [rules.piece(42, i) for i in range(50)] != [rules.piece(43, i) for i in range(50)]
    kinds = [p["kind"] for seed in range(1, 40) for i in range(60) if (p := rules.piece(seed, i))]
    assert set(kinds) == set(rules.SIZE)
    def hazards(lo, hi):
        found = [p for seed in range(1, 200) for i in range(lo, hi) if (p := rules.piece(seed, i))]
        return sum(p["kind"] in rules.HAZARDS for p in found) / len(found)
    assert hazards(0, 5) < 0.12 < 0.25 < hazards(40, 45)
    assert rules.piece(42, -1) is None


def test_presses_do_what_the_page_says():
    """OK locks the angle, then fires; a nudge stops the swing; a puff needs puffs left; an idle cannon fires itself."""
    s = rules.new_shot(7)
    rules.press(s, "up"); rules.step(s); rules.step(s)
    assert s["manual"] and s["aim"] == 31                                                   # nudged once, then it stays put
    rules.press(s, "ok"); assert s["phase"] == "power"
    for _ in range(40):
        rules.step(s)
    assert s["power"] == 40
    rules.press(s, "ok"); assert s["phase"] == "fly" and s["vx"] > 0 and s["vy"] > 0
    for _ in range(4):
        rules.press(s, "ok")
    assert s["puffs"] == 0
    idle = rules.play_shot(7, [])
    assert idle["end"] and idle["t"] > rules.AIM_MAX + rules.POWER_MAX                       # it waited 30 s twice, then fired


# ------------------------------------------------------------------ coins
def test_a_go_costs_its_entry_and_pays_its_distance_once(games_conn, alice):
    recorded = max(RUNS, key=lambda r: r["result"]["total"])
    ticket, out = _go(games_conn, alice, recorded)
    assert ticket["entry"] == lumilaunch.ENTRY and ticket["shots"] == 3
    assert out["summary"] == recorded["result"] and not out["refused"]                     # the server's own replay
    assert out["earned"] == recorded["result"]["total"] // lumilaunch.METRES_PER_COIN
    assert out["paid"] == out["earned"] and not out["capped"]                             # no daily limit unless one's set
    assert out["coins"] == coins.START - lumilaunch.ENTRY + out["paid"]
    with pytest.raises(lumilaunch.Refused):                                                 # the ticket is used up
        lumilaunch.finish(games_conn, alice, ticket["run"], recorded["shots"], now=99999.0)


def test_the_daily_cap(conn, settings, games_conn, alice):
    with db.transaction(conn):
        media.save_settings(conn, settings, {"launch_daily_cap": 300})                      # Settings → Media → Games
    assert lumilaunch.state(games_conn, alice)["cap"] == 300
    recorded = max(RUNS, key=lambda r: r["result"]["total"])
    paid = [_go(games_conn, alice, recorded, start=1000.0 + n * 1000)[1] for n in range(6)]
    assert sum(o["paid"] for o in paid) == 300                                              # 300 a day, however long she flies
    assert paid[-1]["paid"] == 0 and paid[-1]["capped"]


def test_no_coins_no_go(games_conn, alice):
    assert coins.spend(games_conn, alice, coins.peek(games_conn, alice), "test", "drain")
    with pytest.raises(lumilaunch.Refused) as refused:
        lumilaunch.start(games_conn, alice, now=1000.0)
    assert refused.value.status == 402


def test_a_go_longer_than_the_clock_allows_pays_nothing(games_conn, alice):
    recorded = max(RUNS, key=lambda r: r["result"]["frames"])
    ticket = lumilaunch.start(games_conn, alice, now=1000.0)
    out = lumilaunch.finish(games_conn, alice, ticket["run"], recorded["shots"], now=1001.0)   # a minute of flying in a second
    assert out["refused"] and out["paid"] == 0 and out["coins"] == coins.START - lumilaunch.ENTRY


@pytest.mark.parametrize("shots", [[[[0, "fly"]]], [[[30, "ok"], [10, "ok"]]], [[], [], [], []]],
                         ids=["unknown button", "frames out of order", "four launches"])
def test_logs_that_cant_be_real_are_refused(games_conn, alice, shots):
    ticket = lumilaunch.start(games_conn, alice, now=1000.0)
    with pytest.raises(lumilaunch.Refused) as refused:
        lumilaunch.finish(games_conn, alice, ticket["run"], shots, now=5000.0)
    assert refused.value.status == 422


def test_quitting_early_still_counts_what_she_flew(games_conn, alice):
    recorded = RUNS[0]
    ticket = lumilaunch.start(games_conn, alice, now=1000.0)
    games_conn.execute("UPDATE lumi_launch_runs SET seed = ? WHERE id = ?", (recorded["seed"], ticket["run"]))
    out = lumilaunch.finish(games_conn, alice, ticket["run"], recorded["shots"][:1], now=2000.0)
    assert out["summary"]["total"] == recorded["ends"][0]["m"] and len(out["summary"]["shots"]) == 1


def test_one_go_at_a_time_and_only_your_own(games_conn, conn, alice):
    bob = users.create_user(conn, "bob", "password123")
    first = lumilaunch.start(games_conn, alice, now=1000.0)
    with pytest.raises(lumilaunch.Refused):                                                 # bob can't finish alice's go
        lumilaunch.finish(games_conn, bob, first["run"], [], now=2000.0)
    lumilaunch.start(games_conn, alice, now=1100.0)                                         # a new go drops the unfinished one
    with pytest.raises(lumilaunch.Refused):
        lumilaunch.finish(games_conn, alice, first["run"], [], now=2000.0)


def test_bests_keep_the_longest_and_the_board_names_them(games_conn, conn, alice):
    bob = users.create_user(conn, "bob", "password123")
    long, short = sorted(RUNS, key=lambda r: r["result"]["total"])[-1], sorted(RUNS, key=lambda r: r["result"]["total"])[0]
    _, first = _go(games_conn, alice, long)
    assert first["new_best"] == {"go": True, "shot": True}
    _, second = _go(games_conn, alice, short, start=5000.0)
    assert second["new_best"]["go"] is False and second["best"]["go"] == long["result"]["total"]
    _go(games_conn, bob, short, start=9000.0)
    board = lumilaunch.board(games_conn)
    assert [r["name"] for r in board] == ["alice", "bob"] and board[0]["go"] == long["result"]["total"]
    assert board[0]["shot"] == max(e["m"] for e in long["ends"])
    state = lumilaunch.state(games_conn, alice)
    assert state["best"]["go"] == long["result"]["total"] and state["board"] == board


def test_a_removed_user_takes_their_launches_along():
    games = db.FEATURE_DBS["games"]
    assert ("lumi_launch_runs", "user_id") in games.user_columns and ("lumi_launch_bests", "user_id") in games.user_columns


# ------------------------------------------------------------------ the routes
def test_the_routes_take_presses_only(client, conn, directory, games_conn):
    _login(client, conn, directory)
    _turn_on(client)
    state = client.get("/api/media/games/lumi-launch/state").json()
    assert state["entry"] == lumilaunch.ENTRY and state["metres_per_coin"] == lumilaunch.METRES_PER_COIN
    assert client.post("/api/media/games/lumi-launch/start", json={}).status_code == 200
    t = client.post("/api/media/games/lumi-launch/start", json={}).json()
    recorded = RUNS[0]
    assert client.post("/api/media/games/lumi-launch/finish", json={"run": t["run"], "shots": recorded["shots"], "metres": 9999}).status_code == 422
    assert client.post("/api/media/games/lumi-launch/finish", json={"run": t["run"], "shots": [[[0, "zoom"]]]}).status_code == 422
    assert client.post("/api/media/games/lumi-launch/finish", json={"run": t["run"], "shots": [[]] * 4}).status_code == 422
    out = client.post("/api/media/games/lumi-launch/finish", json={"run": t["run"], "shots": recorded["shots"]}).json()
    assert out["refused"] and "add up" in out["refused"]                                    # a whole go, finished at once
    assert client.post("/api/media/games/lumi-launch/finish", json={"run": t["run"], "shots": []}).status_code == 404
    page = client.get("/media/games/lumi-launch").text
    assert 'id="ll-view"' in page and "lumi-launch/rules.js" in page and "games/gamepad.js" in page


def test_couch_turns_ask_whose_remote_pressed_ok(client, conn, directory, games_conn):
    """On a TV, a TV guest's OK joins couch turns as her: her name and her own Lumi's look. Nothing is charged."""
    owner_id, wife_id, tv, press, screen_id, remote_id = _couch(client, conn, directory)
    joined = tv.post("/api/media/games/lumi-launch/tv/player", json={"controller": press()})
    assert joined.status_code == 200, joined.text
    body = joined.json()
    assert body["name"] == "jane" and body["owner"] is False and body["remote"] == remote_id and "glow" in body["look"]
    assert coins.peek(games_conn, wife_id) == coins.START and coins.peek(games_conn, owner_id) == coins.START
    assert tv.post("/api/media/games/lumi-launch/tv/player", json={"controller": press("down")}).status_code == 422
    assert tv.post("/api/media/games/lumi-launch/tv/player", json={"controller": "forged"}).status_code == 403
    phone = TestClient(client.app)                                  # jane's own browser: signed in, but not a TV
    assert phone.post("/login", data={"username": "jane", "password": "password123"}, follow_redirects=False).status_code == 303
    assert phone.post("/api/media/games/lumi-launch/tv/player", json={"controller": press()}).status_code in (401, 403)
