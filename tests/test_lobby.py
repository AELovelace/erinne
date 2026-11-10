"""The shared table lobby (lobby.py) as casino tables use it: polls that write nothing, seats let go when nobody is
there (their open stakes refunded), abandoned tables and TV tables closing, and a restart's grace period."""

import pytest

from mnemerynne import casino, coins, lobby, users


@pytest.fixture
def clock(monkeypatch):
    """A Presence on a clock the test moves by hand."""
    now = [1000.0]
    monkeypatch.setattr(lobby, "PRESENCE", lobby.Presence(clock=lambda: now[0]))
    return now


@pytest.fixture
def alice(conn):
    return users.create_user(conn, "alice", "password123")


@pytest.fixture
def bob(conn):
    return users.create_user(conn, "bob", "password123")


def _poll(conn, state, uid):
    return casino.room(conn, state["game"], state["code"], f"user:{uid}", "Player", account=uid)


def _move(conn, state, uid, action, **kwargs):
    return casino.room(conn, state["game"], state["code"], f"user:{uid}", "Player", action,
                       revision=state["revision"], account=uid, **kwargs)


def test_a_poll_that_changes_nothing_writes_nothing(conn, settings, alice, clock):
    from mnemerynne import db

    table = casino.create(conn, "roulette", f"user:{alice}", "Alice", account=alice)
    before = conn.execute("SELECT updated_at, state FROM casino_rooms").fetchone()
    other = db.connect(settings.db_path)
    try:
        other.execute("BEGIN IMMEDIATE")                  # someone else holds the write lock...
        conn.execute("PRAGMA busy_timeout = 50")
        assert _poll(conn, table, alice)["code"] == table["code"]   # ...and a poll doesn't need it
    finally:
        other.execute("ROLLBACK")
        other.close()
        conn.execute("PRAGMA busy_timeout = 10000")
    assert tuple(conn.execute("SELECT updated_at, state FROM casino_rooms").fetchone()) == tuple(before)


def test_a_seat_nobody_is_at_is_let_go_and_its_stake_comes_back(conn, alice, bob, clock):
    table = casino.create(conn, "roulette", f"user:{alice}", "Alice", account=alice)
    table = _move(conn, table, bob, "join")
    table = _move(conn, table, bob, "bet", amount=100, choice="red")
    assert coins.peek(conn, bob) == 900
    clock[0] += casino.DROP_SECONDS - 1
    assert len(_poll(conn, table, alice)["players"]) == 2  # not yet: a quick look elsewhere keeps the seat
    clock[0] += 2
    seats = _poll(conn, table, alice)["players"]
    assert [p["name"] for p in seats] == ["Alice"]
    assert coins.peek(conn, bob) == 1000                   # let go, not left: the stake came back
    with pytest.raises(casino.Refused) as gone:
        _poll(conn, table, bob)
    assert gone.value.status == 403 and "Join it again" in str(gone.value)


def test_a_seat_in_a_playing_round_stays_until_it_settles(conn, alice, bob, clock):
    table = casino.create(conn, "blackjack", f"user:{alice}", "Alice", account=alice)
    table = _move(conn, table, bob, "join")
    table = _move(conn, table, alice, "bet", amount=50)
    table = _move(conn, table, bob, "bet", amount=50)
    table = _move(conn, table, alice, "start")
    if table["phase"] != "playing":
        pytest.skip("the dealer drew a natural: no round to play")
    clock[0] += casino.DROP_SECONDS + 1
    assert len(_poll(conn, table, alice)["players"]) == 2  # mid-hand: Bob's hand plays out first


def test_a_table_everyone_left_is_hidden_then_closed_with_stakes_refunded(conn, alice, clock):
    table = casino.create(conn, "slots", f"user:{alice}", "Alice", account=alice)
    _move(conn, table, alice, "bet", amount=50)
    assert coins.peek(conn, alice) == 850                  # three lines of 50
    clock[0] += casino.DROP_SECONDS + 1
    assert casino.rooms(conn, "slots", f"user:{alice}") == []
    casino.sweep(conn)
    assert conn.execute("SELECT count(*) FROM casino_rooms").fetchone()[0] == 0
    assert coins.peek(conn, alice) == 1000


def test_a_tv_table_closes_when_its_tv_goes_away(conn, clock):
    import secrets
    table = casino.create(conn, "roulette", "tv", "Living room", couch=True)
    casino.couch(conn, "roulette", table["code"], "tv", command={"actor": "remote", "name": "Remote", "action": "enter",
                 "id": secrets.token_hex(8)}, expected_round=table["round"], expected_phase=table["phase"])
    clock[0] += casino.DROP_SECONDS - 1
    assert casino.rooms(conn, "roulette", "someone")      # the remotes don't poll; the TV does, for them
    clock[0] += 2
    assert casino.rooms(conn, "roulette", "someone") == []
    casino.sweep(conn)
    assert conn.execute("SELECT count(*) FROM casino_rooms").fetchone()[0] == 0


def test_after_a_restart_everyone_gets_the_whole_grace_period(conn, alice, bob, monkeypatch, clock):
    table = casino.create(conn, "roulette", f"user:{alice}", "Alice", account=alice)
    _move(conn, table, bob, "join")
    clock[0] += casino.DROP_SECONDS * 10
    monkeypatch.setattr(lobby, "PRESENCE", lobby.Presence(clock=lambda: clock[0]))   # a fresh process: nobody heard yet
    assert len(_poll(conn, table, alice)["players"]) == 2
