"""Breakout and Snake (arcade.py, showdown.py): the rules the server checks runs with, solo runs and their coins, and
showdown tables: seats, getting ready, bets, settling a race, and seats let go when a page goes away."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from mnemerynne import arcade, coins, lobby, showdown, users
from test_media import _login, _no_network, _turn_on, checker, client, conn, directory, root, settings  # noqa: F401

STATIC = Path(__file__).resolve().parents[1] / "mnemerynne" / "web" / "static" / "games"


@pytest.fixture
def clock(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(lobby, "PRESENCE", lobby.Presence(clock=lambda: now[0]))
    return now


@pytest.fixture
def people(conn):
    return {name: users.create_user(conn, name, "password123") for name in ("alice", "bob", "carol")}


def _claim(conn, uid, game, run, **claim):
    """Finish a run the way the API does: check it, then hand a race's result to its table."""
    from mnemerynne.db import transaction
    with transaction(conn):
        result = arcade.finish(conn, uid, game, run, claim)
        if result["room"]:
            showdown.record(conn, game, result["room"], run, result)
    return result


def _act(conn, code, uid, action, **kw):
    return showdown.act(conn, "breakout", code, f"user:{uid}", f"P{uid}", uid, action, **kw)


# ---------------------------------------------------------------- the rules
def test_breakout_levels_come_from_the_seed():
    first = arcade.breakout_field(424242, 1)
    assert first == arcade.breakout_field(424242, 1) and first != arcade.breakout_field(424242, 2)
    for seed in (1, 7, 99999, arcade.MODULUS - 2):
        for level in (1, 2, 3, 50):
            field = arcade.breakout_field(seed, level)
            assert len(field) == 108 and sum(1 for c in field if c) >= 30 and set(field) <= set(range(7))


def test_breakout_claims_must_fit_the_levels_and_the_time():
    seed = 31337
    one, two = arcade.breakout_bricks(seed, 1), arcade.breakout_bricks(seed, 2)
    assert arcade.check_breakout(seed, one + 3, 2, 600)["distance"] == 100 + one + 3
    with pytest.raises(arcade.Refused):
        arcade.check_breakout(seed, one - 1, 2, 600)       # level 2 without clearing level 1
    with pytest.raises(arcade.Refused):
        arcade.check_breakout(seed, one + two + 1, 2, 600)  # more bricks than level 2 holds
    with pytest.raises(arcade.Refused):
        arcade.check_breakout(seed, 40, 1, 2)               # forty bricks in two seconds


def test_snake_plays_again_exactly():
    straight = arcade.snake_replay(5, [], 9)
    assert straight["alive"] and straight["length"] == 3
    walled = arcade.snake_replay(5, [], 10)                 # head at column 10, heading right: the wall at move 10
    assert walled == {"score": straight["score"], "length": walled["length"], "alive": False, "died": 10}
    with pytest.raises(arcade.Refused):
        arcade.snake_replay(5, [(1, 2)], 3)                 # straight back into itself


def test_snake_finds_and_eats_its_food():
    """Steer to every food in turn (a simple greedy walk) and check the replay agrees with the walk's own score."""
    seed, board = 2024, None
    turns, moves, score = [], 0, 0
    body, heading, index = list(arcade.SNAKE_START), 0, 0
    board = [[0] * arcade.SNAKE_H for _ in range(arcade.SNAKE_W)]
    for x, y in body:
        board[x][y] = 1
    food = arcade.snake_food(seed, index, board)
    board[food[0]][food[1]] = food[2]
    index += 1
    while moves < 300 and score < 12:
        hx, hy = body[0]
        want = 0 if food[0] > hx else 2 if food[0] < hx else 1 if food[1] > hy else 3
        if want == (heading + 2) % 4:
            want = 1 if hy < arcade.SNAKE_H - 1 else 3
        nx, ny = hx + arcade.STEPS[want][0], hy + arcade.STEPS[want][1]
        if not (0 <= nx < arcade.SNAKE_W and 0 <= ny < arcade.SNAKE_H) or (board[nx][ny] == 1 and (nx, ny) != body[-1]):
            break
        moves += 1
        if want != heading:
            turns.append((moves, want))
            heading = want
        ate = board[nx][ny] in arcade.FOOD_POINTS
        if not ate:
            tail = body.pop()
            board[tail[0]][tail[1]] = 0
        else:
            score += arcade.FOOD_POINTS[board[nx][ny]]
        body.insert(0, (nx, ny))
        board[nx][ny] = 1
        if ate:
            food = arcade.snake_food(seed, index, board)
            index += 1
            board[food[0]][food[1]] = food[2]
    played = arcade.snake_replay(seed, turns, moves)
    assert played["score"] == score > 0 and played["alive"]
    assert arcade.check_snake(seed, turns, moves, False, 600)["score"] == score
    with pytest.raises(arcade.Refused):
        arcade.check_snake(seed, turns, moves, True, 600)  # it says it died; it didn't
    with pytest.raises(arcade.Refused):
        arcade.check_snake(seed, turns, moves, False, 1)   # all those moves in one second


@pytest.mark.skipif(shutil.which("node") is None, reason="needs Node")
def test_the_page_and_the_server_build_the_same_games():
    """rules.js (the page) and arcade.py (the server) must agree on every level and every food."""
    script = f"""
      const B = require({json.dumps(str(STATIC / 'breakout' / 'rules.js'))});
      const S = require({json.dumps(str(STATIC / 'snake' / 'rules.js'))});
      const out = {{ fields: [], foods: [] }};
      for (const seed of [1, 7, 31337, 2147483645]) for (const level of [1, 2, 9]) out.fields.push(B.field(seed, level));
      const game = S.newGame(2024);
      for (let i = 0; i < 40; i++) {{ S.step(game, i % 7 === 0 ? (game.heading + 1) % 4 : game.heading); if (!game.alive) break; }}
      out.snake = {{ score: game.score, length: game.body.length, alive: game.alive, moves: game.moves, turns: game.turns }};
      console.log(JSON.stringify(out));
    """
    out = json.loads(subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True).stdout)
    expected = [arcade.breakout_field(seed, level) for seed in (1, 7, 31337, 2147483645) for level in (1, 2, 9)]
    assert out["fields"] == expected
    snake = out["snake"]
    played = arcade.snake_replay(2024, [tuple(t) for t in snake["turns"]], snake["moves"])
    assert (played["score"], played["length"], played["alive"]) == (snake["score"], snake["length"], snake["alive"])


def test_teacup_tower_climbs_must_fit_the_time():
    assert arcade.check_tower(30, 60) == {"score": 30, "distance": 30}
    assert arcade.check_tower(5, 0) == {"score": 5, "distance": 5}       # the first few floors take no time to check
    with pytest.raises(arcade.Refused):
        arcade.check_tower(200, 10)                                     # twenty floors a second


def test_a_teacup_tower_showdown_goes_to_the_higher_floor(conn, people, clock):
    alice, bob = people["alice"], people["bob"]
    table = showdown.create(conn, "teacup-tower", f"user:{alice}", "Alice", alice)
    assert table["name"] == "Alice's Teacup Tower table"
    showdown.act(conn, "teacup-tower", table["code"], f"user:{bob}", "Bob", bob, "join")
    showdown.act(conn, "teacup-tower", table["code"], f"user:{alice}", "Alice", alice, "ready")
    showdown.act(conn, "teacup-tower", table["code"], f"user:{bob}", "Bob", bob, "ready")
    runs = {uid: showdown.look(conn, "teacup-tower", table["code"], f"user:{uid}")["race"]["run"] for uid in (alice, bob)}
    _claim(conn, alice, "teacup-tower", runs[alice], score=3)
    _claim(conn, bob, "teacup-tower", runs[bob], score=5)              # five floors: all a climb this quick can claim
    done = showdown.look(conn, "teacup-tower", table["code"], f"user:{bob}")
    assert done["last"]["winner"] == "Bob" and coins.peek(conn, bob) == 1000 - arcade.ENTRY + 5


# ---------------------------------------------------------------- solo runs
def test_a_solo_run_costs_its_entry_and_pays_its_score_once(conn, people):
    from mnemerynne.db import transaction
    alice = people["alice"]
    with transaction(conn):
        run = arcade.start(conn, alice, "breakout")
    assert coins.peek(conn, alice) == 1000 - arcade.ENTRY
    result = _claim(conn, alice, "breakout", run["run"], score=10, level=1)
    assert result["paid"] == 10 and coins.peek(conn, alice) == 1000 - arcade.ENTRY + 10
    with pytest.raises(arcade.Refused):
        _claim(conn, alice, "breakout", run["run"], score=10, level=1)   # paid once


def test_a_run_needs_its_entry(conn, people):
    from mnemerynne.db import transaction
    coins.held(conn, people["bob"])                                 # open Bob's purse, then empty most of it
    conn.execute("UPDATE coin_accounts SET balance = 10 WHERE user_id = ?", (people["bob"],))
    with pytest.raises(arcade.Refused) as refused:
        with transaction(conn):
            arcade.start(conn, people["bob"], "snake")
    assert refused.value.status == 402 and coins.peek(conn, people["bob"]) == 10


# ---------------------------------------------------------------- showdowns
def _race(conn, people, clock):
    """Alice and Bob at a Breakout table, Carol watching with 10 on Bob."""
    alice, bob, carol = people["alice"], people["bob"], people["carol"]
    table = showdown.create(conn, "breakout", f"user:{alice}", "Alice", alice)
    assert table["role"] == "p1" and table["name"] == "Alice's Breakout table"
    assert _act(conn, table["code"], bob, "join")["role"] == "p2"
    assert _act(conn, table["code"], carol, "join")["role"] == "watch"
    after = _act(conn, table["code"], carol, "bet", target="p2", amount=10)
    assert after["bets"]["p2"] == 10 and coins.peek(conn, carol) == 990
    _act(conn, table["code"], alice, "ready")
    racing = _act(conn, table["code"], bob, "ready")
    assert racing["phase"] == "racing" and racing["race"]["run"]
    assert coins.peek(conn, alice) == coins.peek(conn, bob) == 1000 - arcade.ENTRY
    with pytest.raises(arcade.Refused):
        _act(conn, table["code"], carol, "bet", target="p1", amount=5)   # bets closed
    runs = {who: showdown.look(conn, "breakout", table["code"], f"user:{uid}")["race"]["run"]
            for who, uid in (("p1", alice), ("p2", bob))}
    assert showdown.look(conn, "breakout", table["code"], f"user:{carol}")["race"]["run"] is None   # watchers get none
    return table["code"], runs


def test_a_race_pays_both_scores_and_the_winners_bets(conn, people, clock):
    code, runs = _race(conn, people, clock)
    alice, bob, carol = people["alice"], people["bob"], people["carol"]
    _claim(conn, alice, "breakout", runs["p1"], score=4, level=1)
    assert showdown.look(conn, "breakout", code, f"user:{alice}")["phase"] == "racing"   # waits for Bob
    _claim(conn, bob, "breakout", runs["p2"], score=9, level=1)
    done = showdown.look(conn, "breakout", code, f"user:{bob}")
    assert done["phase"] == "result" and done["last"]["winner"] == f"P{bob}"
    assert done["role"] == "p1"                                     # the winner sits as P1
    assert coins.peek(conn, alice) == 1000 - arcade.ENTRY + 4
    assert coins.peek(conn, bob) == 1000 - arcade.ENTRY + 9
    assert coins.peek(conn, carol) == 1000 - 10 + 20               # double back on Bob


def test_a_racer_who_goes_away_forfeits_and_bets_on_them_lose(conn, people, clock):
    code, runs = _race(conn, people, clock)
    alice, bob, carol = people["alice"], people["bob"], people["carol"]
    _claim(conn, alice, "breakout", runs["p1"], score=2, level=1)
    clock[0] += showdown.DROP_SECONDS + 1
    for uid in (alice, carol):
        lobby.PRESENCE.seen(("arcade", code), f"user:{uid}")       # Alice and Carol are still here; Bob's page closed
    done = showdown.look(conn, "breakout", code, f"user:{alice}")
    assert done["phase"] == "result" and done["last"]["winner"] == "Alice"
    assert [s["name"] for s in done["seats"] if s["role"] in ("p1", "p2")] == ["Alice"]
    assert coins.peek(conn, bob) == 1000 - arcade.ENTRY and coins.peek(conn, carol) == 990


def test_a_table_everyone_leaves_mid_race_settles_and_refunds_bets(conn, people, clock):
    code, runs = _race(conn, people, clock)
    clock[0] += showdown.DROP_SECONDS + 1
    showdown.sweep(conn)
    assert conn.execute("SELECT count(*) FROM arcade_rooms").fetchone()[0] == 0
    assert coins.peek(conn, people["carol"]) == 1000               # no winner: the bet came back
    late = _claim(conn, people["alice"], "breakout", runs["p1"], score=2, level=1)
    assert late["room"] == code and "paid" not in late             # a late finish has no table left to pay it
    assert coins.peek(conn, people["alice"]) == 1000 - arcade.ENTRY


def test_leaving_between_races_hands_bets_back(conn, people, clock):
    alice, bob, carol = people["alice"], people["bob"], people["carol"]
    table = showdown.create(conn, "breakout", f"user:{alice}", "Alice", alice)
    _act(conn, table["code"], bob, "join")
    _act(conn, table["code"], carol, "join")
    _act(conn, table["code"], carol, "bet", target="p1", amount=25)
    _act(conn, table["code"], bob, "leave")                        # the race Carol bet on can't happen now
    assert coins.peek(conn, carol) == 1000
    seat = _act(conn, table["code"], carol, "seat")                # she takes the empty racer seat
    assert seat["role"] == "p2"


def test_ready_needs_both_entries_or_nobody_pays(conn, people, clock):
    alice, bob = people["alice"], people["bob"]
    coins.held(conn, bob)
    conn.execute("UPDATE coin_accounts SET balance = 5 WHERE user_id = ?", (bob,))
    table = showdown.create(conn, "breakout", f"user:{alice}", "Alice", alice)
    _act(conn, table["code"], bob, "join")
    _act(conn, table["code"], alice, "ready")
    with pytest.raises(arcade.Refused) as refused:
        _act(conn, table["code"], bob, "ready")
    assert refused.value.status == 402 and coins.peek(conn, alice) == 1000 and coins.peek(conn, bob) == 5


def test_one_table_per_game_and_never_racing_yourself(conn, people, clock):
    alice = people["alice"]
    first = showdown.create(conn, "breakout", f"user:{alice}", "Alice", alice)
    showdown.create(conn, "breakout", f"user:{alice}", "Alice", alice)     # opening another leaves the first...
    assert conn.execute("SELECT count(*) FROM arcade_rooms WHERE code = ?", (first["code"],)).fetchone()[0] == 0
    second = showdown.tables(conn, "breakout", f"user:{alice}")[0]
    tv = showdown.act(conn, "breakout", second["code"], f"screen:1:{alice}", "TV", alice, "join")
    assert tv["role"] == "watch"                                           # ...and her TV can't race her


def test_seats_let_go_when_a_page_goes_away(conn, people, clock):
    alice, bob = people["alice"], people["bob"]
    table = showdown.create(conn, "snake", f"user:{alice}", "Alice", alice)
    showdown.act(conn, "snake", table["code"], f"user:{bob}", "Bob", bob, "join")
    clock[0] += showdown.DROP_SECONDS + 1
    lobby.PRESENCE.opened(("arcade", table["code"]), f"user:{alice}")     # Alice's stream is open; Bob's isn't
    seats = showdown.look(conn, "snake", table["code"], f"user:{alice}")["seats"]
    assert [s["name"] for s in seats] == ["Alice"]


# ---------------------------------------------------------------------- API
def test_the_page_plays_a_solo_run(client, conn, directory):
    uid = _login(client, conn, directory)
    _turn_on(client)
    run = client.post("/api/media/games/snake/run").json()
    assert run["seed"] and run["coins"] == 1000 - arcade.ENTRY
    finish = "/api/media/games/snake/run/finish"
    assert client.post(finish, json={"run": run["run"], "moves": 10, "died": True, "coins": 999}).status_code == 422
    assert client.post(finish, json={"run": run["run"], "moves": "10", "died": True}).status_code == 422
    assert client.post(finish, json={"run": run["run"], "moves": 9, "died": True}).status_code == 422   # it didn't die there
    out = client.post(finish, json={"run": run["run"], "moves": 10, "died": True}).json()
    assert out["score"] == 0 and out["paid"] == 0 and coins.peek(conn, uid) == 1000 - arcade.ENTRY
    assert client.post("/api/media/games/prism-drop/run").status_code == 404
    client.cookies.clear()
    assert client.post("/api/media/games/snake/run").status_code == 401


def test_the_page_keeps_tables_to_their_players(client, conn, directory):
    _login(client, conn, directory)
    _turn_on(client)
    table = client.post("/api/media/games/breakout/tables").json()
    code = table["code"]
    assert client.get("/api/media/games/breakout/tables").json()["tables"][0]["mine"]
    board = {"score": 3, "level": 1, "bat": 0.5, "balls": [[0.5, 0.9]], "bricks": "0" * 108}
    assert client.post(f"/api/media/games/breakout/tables/{code}/board", json=board).status_code == 409   # not racing
    assert client.post(f"/api/media/games/breakout/tables/{code}", json={"action": "bet", "target": "p3"}).status_code == 422
    assert client.post(f"/api/media/games/breakout/tables/{code}", json={"action": "leave"}).json() == {"left": True}
    assert client.get(f"/api/media/games/breakout/tables/{code}").status_code == 404
    assert client.get(f"/api/media/games/breakout/tables/{code}/stream").status_code == 404
