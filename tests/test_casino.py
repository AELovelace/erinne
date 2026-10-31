"""Server rule, concurrency and reconnect checks for the multiplayer play-chip tables."""

import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from mnemerynne import casino, db


def move(conn, state, actor, action, **kwargs):
    return casino.room(conn, state["game"], state["code"], actor, actor, action,
                       revision=state["revision"], **kwargs)


def test_aces_roulette_zero_and_slot_paytable():
    assert casino.total([0, 13, 8]) == 21
    assert casino.total([0, 12]) == 21
    assert casino.total([9, 22, 1]) == 22
    for pick in ("red", "black", "odd", "even", "low", "high"):
        assert casino.roulette_return(pick, 0) == 0
    assert casino.roulette_return("0", 0) == 36
    assert casino.roulette_return("red", 1) == 2
    assert casino.roulette_return("black", 1) == 0
    assert casino.roulette_return("high", 36) == 2
    assert casino.slots_return(["star"] * 3) == 24
    assert casino.slots_return(["bell"] * 3) == 12
    assert casino.slots_return(["bell", "bell", "star"]) == 1
    assert casino.slots_return(["bell", "lemon", "star"]) == 0


@pytest.mark.parametrize("hand,dealer,expected", [
    ([0, 12], [9, 8], 125),       # Natural returns stake plus 3:2, not an ordinary win.
    ([0, 12], [13, 25], 50),     # Both naturals push.
    ([9, 4, 5], [13, 25], 0),    # Three-card 21 loses to a dealer natural.
    ([9, 22, 1], [9, 8], 0),    # Bust cannot win against a lower dealer hand.
    ([9, 8], [22, 21], 50),      # Equal totals push.
    ([9, 8], [9, 7], 100),       # An ordinary winning hand pays 1:1.
])
def test_blackjack_settlement(hand, dealer, expected):
    p = casino._seat("one", "One")
    p.update(cards=hand, bet=50, chips=950)
    s = {"game": "blackjack", "players": [p], "dealer": dealer, "deck": [9]}
    casino._finish(s, 100)
    assert p["payout"] == expected and p["chips"] == 950 + expected


def test_two_players_share_round_and_cannot_replay_moves(conn, monkeypatch):
    monkeypatch.setattr(casino.RNG, "randrange", lambda n: 1)
    s = casino.create(conn, "roulette", "alice", "Alice")
    s = move(conn, s, "bob", "join")
    assert len(s["players"]) == 2
    s = move(conn, s, "alice", "bet", amount=50, choice="red")
    old = s
    s = move(conn, s, "bob", "bet", amount=20, choice="1")
    with pytest.raises(casino.Refused):
        move(conn, old, "alice", "bet", amount=50, choice="red")
    assert [p["chips"] for p in s["players"]] == [950, 980]
    with pytest.raises(casino.Refused):
        move(conn, s, "bob", "start")
    s = move(conn, s, "alice", "start")
    with pytest.raises(casino.Refused):
        move(conn, s, "alice", "bet", amount=50, choice="black")
    monkeypatch.setattr(casino.time, "time", lambda: s["deadline"] + 1)
    result = casino.room(conn, "roulette", s["code"], "bob", "Bob")
    assert result["result"] == {"number": 1, "color": "red"}
    assert [p["chips"] for p in result["players"]] == [1050, 1700]
    again = casino.room(conn, "roulette", s["code"], "alice", "Alice")
    assert again["players"] == result["players"]  # Reading twice must never pay twice.
    assert "deck" not in result and all("actor" not in p for p in result["players"])


def test_blackjack_hole_card_is_private_and_deadline_stands(conn, monkeypatch):
    monkeypatch.setattr(casino.RNG, "shuffle", lambda cards: None)
    s = casino.create(conn, "blackjack", "alice", "Alice")
    s = move(conn, s, "alice", "bet", amount=50)
    s = move(conn, s, "alice", "start")
    assert s["phase"] == "playing" and s["dealer"] == [51, None]
    assert "deck" not in s
    now = s["deadline"] + 1
    monkeypatch.setattr(casino.time, "time", lambda: now)
    s = casino.room(conn, "blackjack", s["code"], "alice", "Alice")
    assert s["phase"] == "results" and None not in s["dealer"]
    assert s["players"][0]["chips"] == 1000  # Both hands total twenty.
    now = s["deadline"] + 1
    s = casino.room(conn, "blackjack", s["code"], "alice", "Alice")
    assert s["phase"] == "betting" and s["round"] == 2 and s["players"][0]["bet"] == 0


@pytest.mark.parametrize("game", ["slots", "horse-racing"])
def test_shared_spin_or_race_persists_across_connections(conn, settings, monkeypatch, game):
    monkeypatch.setattr(casino.RNG, "shuffle", lambda order: None)
    monkeypatch.setattr(casino.RNG, "choice", lambda symbols: "star")
    s = casino.create(conn, game, "alice", "Alice")
    s = move(conn, s, "bob", "join")
    s = move(conn, s, "alice", "bet", amount=50, choice="0")
    s = move(conn, s, "bob", "bet", amount=20, choice="1")
    s = move(conn, s, "alice", "start")
    assert s["result"] is None
    monkeypatch.setattr(casino.time, "time", lambda: s["deadline"] + 1)
    other = db.connect(settings.db_path)
    try:
        result = casino.room(other, game, s["code"], "bob", "Bob")
    finally:
        other.close()
    expected = [2150, 1460] if game == "slots" else [1200, 980]
    assert [p["chips"] for p in result["players"]] == expected
    assert result["phase"] == "results"


def test_limits_identity_bet_replacement_and_host_transfer(conn):
    s = casino.create(conn, "roulette", "alice", "Alice")
    for i in range(7):
        s = move(conn, s, str(i), "join")
    with pytest.raises(casino.Refused):
        move(conn, s, "ninth", "join")
    with pytest.raises(casino.Refused) as denied:
        casino.room(conn, "roulette", s["code"], "outsider", "Outsider")
    assert denied.value.status == 403
    with pytest.raises(casino.Refused):
        casino.room(conn, "slots", s["code"], "alice", "Alice")
    for amount, choice in [(15, "red"), (510, "red"), (10, "37"), (10, "unknown")]:
        with pytest.raises(casino.Refused):
            move(conn, s, "alice", "bet", amount=amount, choice=choice)
    s = move(conn, s, "alice", "bet", amount=500, choice="red")
    s = move(conn, s, "alice", "bet", amount=20, choice="black")
    assert s["players"][0]["chips"] == 980
    move(conn, s, "alice", "leave")
    s = casino.room(conn, "roulette", s["code"], "0", "0")
    assert len(s["players"]) == 7 and s["players"][0]["name"] == "0"
    s = move(conn, s, "0", "bet", amount=10, choice="0")
    assert move(conn, s, "0", "start")["phase"] == "playing"


def test_simultaneous_move_is_applied_once(conn, settings):
    s = casino.create(conn, "slots", "alice", "Alice")

    def bet():
        connection = db.connect(settings.db_path)
        try:
            return move(connection, s, "alice", "bet", amount=50)
        except casino.Refused:
            return None
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: bet(), range(2)))
    assert sum(r is not None for r in results) == 1
    assert casino.room(conn, "slots", s["code"], "alice", "Alice")["players"][0]["chips"] == 950


def test_expiry_empty_table_and_free_refill(conn, monkeypatch):
    s = casino.create(conn, "slots", "alice", "Alice")
    with pytest.raises(casino.Refused):
        move(conn, s, "alice", "refill")
    raw = json.loads(conn.execute("SELECT state FROM casino_rooms WHERE code=?", (s["code"],)).fetchone()[0])
    raw["players"][0]["chips"] = 0
    conn.execute("UPDATE casino_rooms SET state=? WHERE code=?", (json.dumps(raw), s["code"]))
    s = move(conn, s, "alice", "refill")
    assert s["players"][0]["chips"] == 1000
    assert move(conn, s, "alice", "leave") == {"left": True}
    assert not casino.rooms(conn, "slots", "alice")
    s = casino.create(conn, "slots", "alice", "Alice")
    monkeypatch.setattr(casino.time, "time", lambda: s["server_time"] + casino.IDLE_SECONDS + 1)
    assert not casino.rooms(conn, "slots", "alice")
    with pytest.raises(casino.Refused) as expired:
        casino.room(conn, "slots", s["code"], "alice", "Alice")
    assert expired.value.status == 404
