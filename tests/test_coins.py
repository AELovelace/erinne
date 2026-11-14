"""The purse the Media games share (mnemerynne/coins.py): casino tables, rewards, and who pays."""

import json

import pytest
from fastapi.testclient import TestClient

from mnemerynne import casino, coins, prism, retention, users
from mnemerynne.db import transaction
from test_media import (_login, _no_network, _pair, _screens_for, _turn_on, checker, client, conn,  # noqa: F401
                        directory, root, settings)


@pytest.fixture
def alice(conn):
    return users.create_user(conn, "alice", "password123")


@pytest.fixture
def bob(conn):
    return users.create_user(conn, "bob", "password123")


def _table(conn, uid, game="roulette", actor=None):
    return casino.create(conn, game, actor or f"user:{uid}", "Player", account=uid)


def _move(conn, state, uid, action, actor=None, **kwargs):
    return casino.room(conn, state["game"], state["code"], actor or f"user:{uid}", "Player", action,
                       revision=state["revision"], account=uid, **kwargs)


def _ledger(conn, kind):
    return [dict(r) for r in conn.execute("SELECT * FROM coin_transactions WHERE type = ? ORDER BY id", (kind,))]


def _house(conn):
    return conn.execute("SELECT balance FROM coin_accounts WHERE id = ?", (coins.HOUSE,)).fetchone()[0]


def _age(conn, code):
    conn.execute("UPDATE casino_rooms SET updated_at = 0 WHERE code = ?", (code,))


# -------------------------------------------------------------------- the purse
def test_a_purse_starts_at_a_thousand_and_reading_it_writes_nothing(conn, alice):
    assert coins.peek(conn, alice) == coins.START
    assert conn.execute("SELECT count(*) FROM coin_accounts WHERE user_id = ?", (alice,)).fetchone()[0] == 0
    with transaction(conn):
        assert coins.held(conn, alice) == coins.START
        assert coins.held(conn, alice) == coins.START
    assert len(_ledger(conn, "start")) == 1


def test_a_purse_cant_be_overdrawn(conn, alice):
    import sqlite3
    with pytest.raises(sqlite3.IntegrityError), transaction(conn):
        coins.held(conn, alice)
        coins.table(conn, alice, -5000, "roulette")
    assert coins.peek(conn, alice) == coins.START


def test_a_deleted_account_has_no_purse(conn):
    with transaction(conn):
        assert coins.held(conn, 9999) is None


# ------------------------------------------------------------------ online tables
def test_a_bet_goes_to_the_house_and_a_win_comes_back(conn, alice, monkeypatch):
    monkeypatch.setattr(casino.RNG, "randrange", lambda n: 1)
    house = _house(conn)
    s = _table(conn, alice)
    s = _move(conn, s, alice, "bet", amount=50, choice="red")
    assert s["players"][0]["chips"] == 950 and coins.peek(conn, alice) == 950 and _house(conn) == house + 50
    assert _ledger(conn, "roulette_bet")[0]["to_account"] == coins.HOUSE
    s = _move(conn, s, alice, "start")
    monkeypatch.setattr(casino.time, "time", lambda: s["deadline"] + 1)
    done = casino.room(conn, "roulette", s["code"], f"user:{alice}", "Player", account=alice)
    assert done["players"][0]["payout"] == 100 and coins.peek(conn, alice) == 1050 and _house(conn) == house - 50
    casino.room(conn, "roulette", s["code"], f"user:{alice}", "Player", account=alice)
    assert coins.peek(conn, alice) == 1050 and len(_ledger(conn, "roulette_win")) == 1    # reading twice must never pay twice


def test_two_tables_share_one_purse_and_cant_spend_it_twice(conn, alice):
    one, two = _table(conn, alice, "roulette"), _table(conn, alice, "horse-racing")
    _move(conn, one, alice, "bet", amount=500, choice="red")
    two = casino.room(conn, "horse-racing", two["code"], f"user:{alice}", "Player", account=alice)
    assert two["players"][0]["chips"] == 500                # the other table's bet already shows here
    two = _move(conn, two, alice, "bet", amount=500, choice="0")
    assert coins.peek(conn, alice) == 0
    three = _table(conn, alice, "blackjack")
    with pytest.raises(casino.Refused):
        _move(conn, three, alice, "bet", amount=10)


def test_another_tables_change_doesnt_bump_this_ones_revision(conn, alice):
    one, two = _table(conn, alice, "roulette"), _table(conn, alice, "horse-racing")
    _move(conn, one, alice, "bet", amount=50, choice="red")
    again = casino.room(conn, "horse-racing", two["code"], f"user:{alice}", "Player", account=alice)
    assert again["revision"] == two["revision"]


def test_lowering_a_bet_is_a_refund(conn, alice):
    s = _table(conn, alice)
    s = _move(conn, s, alice, "bet", amount=100, choice="red")
    s = _move(conn, s, alice, "bet", amount=30, choice="red")
    assert coins.peek(conn, alice) == 970 and _ledger(conn, "roulette_refund")[0]["amount"] == 70


def test_slots_charges_every_line(conn, alice):
    s = _table(conn, alice, "slots")
    _move(conn, s, alice, "bet", amount=50)
    assert coins.peek(conn, alice) == 850


def test_the_free_refill_tops_up_the_purse(conn, alice):
    s = _table(conn, alice)
    with pytest.raises(casino.Refused):
        _move(conn, s, alice, "refill")
    with transaction(conn):
        coins.table(conn, alice, -995, "roulette")
    s = casino.room(conn, "roulette", s["code"], f"user:{alice}", "Player", account=alice)
    s = _move(conn, s, alice, "refill")
    assert s["players"][0]["chips"] == 1000 and coins.peek(conn, alice) == 1000 and _ledger(conn, "refill")[0]["from_account"] is None


def test_leaving_forfeits_the_stake(conn, alice):
    s = _table(conn, alice)
    s = _move(conn, s, alice, "bet", amount=100, choice="red")
    assert _move(conn, s, alice, "leave") == {"left": True}
    assert coins.peek(conn, alice) == 900


@pytest.mark.parametrize("game,lines", [("roulette", 1), ("slots", 3)])
def test_a_table_that_expires_hands_back_open_stakes(conn, alice, game, lines):
    s = _table(conn, alice, game)
    _move(conn, s, alice, "bet", amount=20, choice="red" if game == "roulette" else "")
    assert coins.peek(conn, alice) == 1000 - 20 * lines
    _age(conn, s["code"])
    casino.sweep(conn)
    assert coins.peek(conn, alice) == 1000 and conn.execute("SELECT count(*) FROM casino_rooms").fetchone()[0] == 0
    casino.sweep(conn)                                      # the table has gone, so it can't pay twice
    assert coins.peek(conn, alice) == 1000


def test_a_settled_round_isnt_refunded_when_it_expires(conn, alice, monkeypatch):
    monkeypatch.setattr(casino.RNG, "randrange", lambda n: 2)    # black: the red bet loses
    s = _table(conn, alice)
    s = _move(conn, s, alice, "bet", amount=100, choice="red")
    s = _move(conn, s, alice, "start")
    deadline = s["deadline"]
    monkeypatch.setattr(casino.time, "time", lambda: deadline + 1)
    assert casino.room(conn, "roulette", s["code"], f"user:{alice}", "Player", account=alice)["phase"] == "results"
    monkeypatch.undo()
    _age(conn, s["code"])
    casino.sweep(conn)
    assert coins.peek(conn, alice) == 900


def test_creating_a_table_also_brings_home_expired_stakes(conn, alice, bob):
    s = _table(conn, alice)
    _move(conn, s, alice, "bet", amount=40, choice="red")
    _age(conn, s["code"])
    _table(conn, bob)
    assert coins.peek(conn, alice) == 1000


def test_two_seats_on_one_account_are_added_together(conn, alice, monkeypatch):
    monkeypatch.setattr(casino.RNG, "randrange", lambda n: 1)
    phone, tv = f"user:{alice}", f"screen:7:{alice}"
    s = casino.create(conn, "roulette", phone, "Phone", account=alice)
    s = casino.room(conn, "roulette", s["code"], tv, "TV", "join", account=alice)
    s = _move(conn, s, alice, "bet", actor=phone, amount=100, choice="red")
    s = _move(conn, s, alice, "bet", actor=tv, amount=50, choice="red")
    assert coins.peek(conn, alice) == 850
    s = _move(conn, s, alice, "start", actor=phone)
    monkeypatch.setattr(casino.time, "time", lambda: s["deadline"] + 1)
    casino.room(conn, "roulette", s["code"], phone, "Phone", account=alice)
    assert coins.peek(conn, alice) == 1150


def test_a_seat_never_shows_whose_purse_it_is(conn, alice):
    s = _table(conn, alice)
    assert "account" not in s["players"][0] and "actor" not in s["players"][0]


def test_a_seat_whose_account_is_deleted_plays_on_with_credits(conn, alice, bob):
    s = _table(conn, alice)
    s = casino.room(conn, "roulette", s["code"], f"user:{bob}", "Bob", "join", account=bob)
    conn.execute("DELETE FROM users WHERE id = ?", (bob,))
    s = casino.room(conn, "roulette", s["code"], f"user:{alice}", "Player", account=alice)
    assert len(s["players"]) == 2


# ------------------------------------------------------------------- local tables
def _couch(conn):
    return casino.create(conn, "roulette", "screen:display", "Lounge", couch=True)


def _press(conn, s, actor, action, key):
    return casino.couch(conn, "roulette", s["code"], "screen:display", expected_round=s["round"], expected_phase=s["phase"],
                        command={"id": key, "action": action, "actor": actor, "name": actor})


def test_local_multiplayer_uses_credits_and_never_a_purse(conn, alice):
    s = _couch(conn)
    assert s["couch"] is True
    s = _press(conn, s, "remote:1", "enter", "a")
    s = _press(conn, s, "remote:2", "enter", "b")
    assert [p["chips"] for p in s["players"]] == [1000, 1000]         # 1,000 each, at this table
    s = _press(conn, s, "remote:1", "enter", "c")
    assert s["players"][0]["chips"] == 950
    assert conn.execute("SELECT count(*) FROM coin_accounts WHERE user_id IS NOT NULL").fetchone()[0] == 0
    assert conn.execute("SELECT count(*) FROM coin_transactions").fetchone()[0] == 0


def test_joining_a_local_table_from_a_browser_still_uses_credits(conn, alice):
    s = _couch(conn)
    s = casino.room(conn, "roulette", s["code"], f"user:{alice}", "Alice", "join", account=alice)
    s = _move(conn, s, alice, "bet", amount=50, choice="red")
    assert s["players"][0]["chips"] == 950 and coins.peek(conn, alice) == 1000
    assert conn.execute("SELECT count(*) FROM coin_transactions").fetchone()[0] == 0


def test_a_local_tables_refusals_say_credits(conn, alice):
    s = _couch(conn)
    s = casino.room(conn, "roulette", s["code"], f"user:{alice}", "Alice", "join", account=alice)
    with pytest.raises(casino.Refused, match="credits"):
        _move(conn, s, alice, "bet", amount=15, choice="red")


def test_an_online_tables_refusals_say_coins(conn, alice):
    with pytest.raises(casino.Refused, match="coins"):
        _move(conn, _table(conn, alice), alice, "bet", amount=15, choice="red")


# ----------------------------------------------------------------------- rewards
def test_a_reward_stops_at_the_days_cap(conn, alice):
    with transaction(conn):
        assert coins.reward(conn, alice, 150, "prism-drop", 200) == 150
        assert coins.reward(conn, alice, 150, "prism-drop", 200) == 50     # only what's left of the day's limit
        assert coins.reward(conn, alice, 150, "prism-drop", 200) == 0
        assert coins.reward(conn, alice, 150, "other", 200) == 150         # each game has its own limit
    assert coins.peek(conn, alice) == 1000 + 350


def test_yesterdays_rewards_dont_count_toward_today(conn, alice):
    with transaction(conn):
        coins.reward(conn, alice, 200, "prism-drop", 200)
    conn.execute("UPDATE coin_transactions SET created_at = datetime('now', '-1 day', '-1 hour') WHERE type = 'reward'")
    with transaction(conn):
        assert coins.reward(conn, alice, 200, "prism-drop", 200) == 200


def test_a_reference_only_ever_pays_once(conn, alice):
    with transaction(conn):
        assert coins.reward(conn, alice, 10, "x", 1000, ref="once") == 10
        assert coins.reward(conn, alice, 10, "x", 1000, ref="once") == 0
    assert coins.peek(conn, alice) == 1010


def test_lumi_pets_visit_pays_once_a_day(conn, alice):
    with transaction(conn):
        assert coins.pet_visit(conn, alice) == coins.PET_VISIT
        assert coins.pet_visit(conn, alice) == 0
    assert coins.peek(conn, alice) == 1000 + coins.PET_VISIT


def test_prism_pays_when_the_game_ends_and_then_starts_fresh(conn, alice):
    for n in range(prism.DROPS - 1):
        out = prism.play(conn, alice, 5, lambda k: 0)
        assert out["drops"] == n + 1 and not out["over"] and out["paid"] == 0
    assert prism.current(conn, alice)["drops"] == prism.DROPS - 1 and coins.peek(conn, alice) == 1000
    out = prism.play(conn, alice, 5, lambda k: 0)
    assert out["over"] and out["earned"] == out["score"] // coins.PRISM_POINTS_PER_COIN and out["paid"] == out["earned"]
    assert out["coins"] == coins.peek(conn, alice) == 1000 + out["paid"]
    assert prism.current(conn, alice) == {"drops": 0, "score": 0}


def test_prism_scores_above_the_days_limit_pay_only_up_to_it(conn, alice):
    with transaction(conn):
        coins.reward(conn, alice, coins.PRISM_DAILY_CAP, "prism-drop", coins.PRISM_DAILY_CAP)
    for _ in range(prism.DROPS):
        out = prism.play(conn, alice, 5, lambda k: 0)
    assert out["paid"] == 0


def test_old_history_goes_but_the_balance_stays(conn, settings, alice):
    with transaction(conn):
        coins.reward(conn, alice, 10, "x", 1000)
    conn.execute("UPDATE coin_transactions SET created_at = datetime('now', '-400 days')")
    assert retention.purge(conn, settings)["coin_transactions"] >= 1
    assert coins.peek(conn, alice) == 1010


# --------------------------------------------------------------------------- API
@pytest.fixture
def on(client, conn, directory):
    _login(client, conn, directory)
    _turn_on(client)
    return client


def _uid(conn):
    return conn.execute("SELECT id FROM users WHERE username = 'boss'").fetchone()[0]


def test_the_page_reads_coins_and_plays_prism(on, conn):
    assert on.get("/api/media/games/coins").json() == {"coins": 1000}
    r = on.post("/api/media/games/prism-drop/drop", json={"pick": 5})
    assert r.status_code == 200 and r.json()["drops"] == 1 and r.json()["round"]["pick"] == 5
    assert on.get("/api/media/games/prism-drop/run").json()["drops"] == 1
    assert on.post("/api/media/games/prism-drop/drop", json={"pick": 11}).status_code == 422
    assert on.post("/api/media/games/prism-drop/drop", json={"pick": 5, "score": 9999}).status_code == 422   # a browser can't name a score


def test_lumi_pets_visit_ignores_anything_the_page_claims(on, conn):
    first = on.post("/api/media/games/lumi-pet/visit", json={"paid": 99999})
    assert first.json() == {"paid": coins.PET_VISIT, "coins": 1000 + coins.PET_VISIT}
    assert on.post("/api/media/games/lumi-pet/visit").json()["paid"] == 0


def test_reading_coins_brings_home_expired_stakes(on, conn, games_conn):
    s = on.post("/api/media/games/roulette/rooms", json={"mode": "individual"}).json()
    assert on.post(f"/api/media/games/roulette/rooms/{s['code']}", json={"action": "bet", "amount": 50, "choice": "red", "revision": s["revision"]}).status_code == 200
    assert on.get("/api/media/games/coins").json() == {"coins": 950}
    _age(games_conn, s["code"])
    assert on.get("/api/media/games/coins").json() == {"coins": 1000}


def test_a_signed_in_browser_spends_its_account(on, conn, games_conn):
    s = on.post("/api/media/games/slots/rooms", json={"mode": "individual"}).json()
    moved = on.post(f"/api/media/games/slots/rooms/{s['code']}", json={"action": "bet", "amount": 10, "revision": s["revision"]})
    assert moved.json()["players"][0]["chips"] == 970 and coins.peek(games_conn, _uid(conn)) == 970


def _tv(client, key):
    tv = TestClient(client.app)
    tv.cookies.set("mnemerynne_screen", key)
    return tv


def test_a_tv_at_an_online_table_spends_the_account_its_paired_to(on, conn, games_conn):
    _, key = _pair(on)
    tv = _tv(on, key)
    s = tv.post("/api/media/games/roulette/rooms", json={"mode": "individual"}).json()
    assert s["players"][0]["chips"] == 1000
    moved = tv.post(f"/api/media/games/roulette/rooms/{s['code']}", json={"action": "bet", "amount": 50, "choice": "red", "revision": s["revision"]})
    assert moved.status_code == 200, moved.text
    assert coins.peek(games_conn, _uid(conn)) == 950 and tv.get("/api/media/games/coins").json() == {"coins": 950}
    saved = json.loads(games_conn.execute("SELECT state FROM casino_rooms WHERE code = ?", (s["code"],)).fetchone()[0])
    assert saved["players"][0]["actor"].startswith("screen:")


def test_a_local_tv_table_leaves_the_owners_coins_alone(on, conn, games_conn):
    _screens_for(on)
    _, key = _pair(on)
    tv = _tv(on, key)
    s = tv.post("/api/media/games/roulette/rooms", json={"mode": "tv"})
    assert s.status_code == 200, s.text
    assert s.json()["couch"] is True and s.json()["players"] == []
    assert coins.peek(games_conn, _uid(conn)) == 1000
    assert games_conn.execute("SELECT count(*) FROM coin_transactions").fetchone()[0] == 0


def test_a_tvs_prism_game_pays_its_owner(on, conn, games_conn):
    _, key = _pair(on)
    tv = _tv(on, key)
    for _ in range(prism.DROPS):
        out = tv.post("/api/media/games/prism-drop/drop", json={"pick": 5}).json()
    assert out["over"] and coins.peek(games_conn, _uid(conn)) == 1000 + out["paid"]


def test_coins_need_someone_who_may_use_media(client):
    assert client.get("/api/media/games/coins").status_code in (401, 403, 404)
