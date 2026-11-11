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
    expected = [4450, 2380] if game == "slots" else [1200, 980]  # Three stakes reserved and three star lines returned.
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
    assert casino.room(conn, "slots", s["code"], "alice", "Alice")["players"][0]["chips"] == 850


@pytest.mark.parametrize("rows,returns", [
    ([["star"] * 3, ["bell", "bell", "lemon"], ["star", "bell", "lemon"]], [240, 10, 0]),
    ([["star", "bell", "lemon"], ["bell"] * 3, ["star", "bell", "lemon"]], [0, 120, 0]),
    ([["star", "bell", "lemon"], ["star", "bell", "lemon"], ["lemon"] * 3], [0, 0, 120]),
    ([["star"] * 3] * 3, [240, 240, 240]),
    ([["star", "bell", "lemon"], ["bell", "star", "lemon"], ["lemon", "bell", "star"]], [0, 0, 0]),  # Diagonal stars do not pay.
    ([["star", "bell", "lemon"]] * 3, [0, 0, 0]),  # Three matching columns do not pay either.
])
def test_slots_settles_only_three_horizontal_lines(monkeypatch, rows, returns):
    outcomes = iter(symbol for row in rows for symbol in row)
    monkeypatch.setattr(casino.RNG, "choice", lambda _: next(outcomes))
    player = casino._seat("alice", "Alice")
    player.update(bet=10, chips=970)  # Ten chips on each of three lines have already been reserved.
    state = {"game": "slots", "slot_lines": 3, "players": [player]}
    casino._finish(state, 100)
    assert player["reels"] == rows
    assert player["line_payouts"] == returns
    assert player["payout"] == sum(returns)
    assert player["chips"] == 970 + sum(returns)


def test_slot_bets_reserve_replace_and_validate_all_three_stakes(conn):
    s = casino.create(conn, "slots", "alice", "Alice")
    assert s["slot_lines"] == 3
    s = move(conn, s, "alice", "bet", amount=50)
    assert s["players"][0]["chips"] == 850
    s = move(conn, s, "alice", "bet", amount=20)
    assert s["players"][0]["chips"] == 940
    with pytest.raises(casino.Refused):
        move(conn, s, "alice", "bet", amount=340)
    s = move(conn, s, "alice", "bet", amount=330)
    assert s["players"][0]["chips"] == 10
    with pytest.raises(casino.Refused):
        move(conn, s, "alice", "refill")  # Reserved bets cannot be combined with a free refill.


def test_legacy_single_line_round_finishes_before_switching_to_three(conn, monkeypatch):
    s = casino.create(conn, "slots", "alice", "Alice")
    raw = json.loads(conn.execute("SELECT state FROM casino_rooms WHERE code=?", (s["code"],)).fetchone()[0])
    raw.pop("slot_lines")  # Simulate an existing room saved by the previous release.
    conn.execute("UPDATE casino_rooms SET state=? WHERE code=?", (json.dumps(raw), s["code"]))
    monkeypatch.setattr(casino.RNG, "choice", lambda _: "star")
    s = move(conn, s, "alice", "bet", amount=50)
    assert s["players"][0]["chips"] == 950
    s = move(conn, s, "alice", "start")
    now = s["deadline"] + 1
    monkeypatch.setattr(casino.time, "time", lambda: now)
    s = casino.room(conn, "slots", s["code"], "alice", "Alice")
    assert s["players"][0]["reels"] == ["star"] * 3
    assert s["players"][0]["chips"] == 2150  # Honor the original one-line wager, without charging more.
    assert casino.room(conn, "slots", s["code"], "alice", "Alice")["players"] == s["players"]
    now = s["deadline"] + 1
    s = casino.room(conn, "slots", s["code"], "alice", "Alice")
    assert s["slot_lines"] == 3 and s["round"] == 2
    assert s["players"][0]["reels"] == [] and s["players"][0]["line_payouts"] == []


@pytest.mark.parametrize("chips,allowed", [(29, True), (30, False)])
def test_slot_refill_uses_the_minimum_cost_of_all_lines(conn, chips, allowed):
    s = casino.create(conn, "slots", "alice", "Alice")
    raw = json.loads(conn.execute("SELECT state FROM casino_rooms WHERE code=?", (s["code"],)).fetchone()[0])
    raw["players"][0]["chips"] = chips
    conn.execute("UPDATE casino_rooms SET state=? WHERE code=?", (json.dumps(raw), s["code"]))
    if allowed:
        assert move(conn, s, "alice", "refill")["players"][0]["chips"] == 1000
    else:
        with pytest.raises(casino.Refused):
            move(conn, s, "alice", "refill")


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


def test_each_account_seat_shows_its_own_lumi(conn):
    """A coin seat carries its Lumi's glow and outfit (drawn sitting at the table); credit seats get none; looking saves nothing."""
    from mnemerynne import lumipet, users
    alice, bob = users.create_user(conn, "alice", "password123"), users.create_user(conn, "bob", "password123")
    pet = lumipet._fresh(0)                                                           # Alice's Lumi: lagoon, a bow, one retired item
    pet.update(glow="lagoon", outfit={"head": "acc-bow", "neck": "acc-gone-forever"})
    conn.execute("INSERT INTO lumi_pets(user_id, data) VALUES (?, ?)", (alice, json.dumps(pet)))
    saved = conn.execute("SELECT data, updated_at FROM lumi_pets WHERE user_id = ?", (alice,)).fetchone()
    s = casino.create(conn, "blackjack", f"user:{alice}", "Alice", account=alice)
    s = casino.room(conn, "blackjack", s["code"], f"user:{bob}", "Bob", "join", account=bob)
    s = casino.room(conn, "blackjack", s["code"], "guest", "Guest", "join")           # a table-credits seat
    looks = {p["name"]: p.get("look") for p in s["players"]}
    assert looks["Alice"] == {"glow": "lagoon", "outfit": {"head": "acc-bow"}, "colour": None, "name": "Lumi"}   # retired things aren't drawn (colour and name: 8c73's RGB colour and naming)
    assert looks["Bob"] == {"glow": "rose", "outfit": {}, "colour": None, "name": "Lumi"}   # no Lumi yet: a fresh one's look
    assert looks["Guest"] is None                                                     # the page draws a plain Lumi
    assert tuple(conn.execute("SELECT data, updated_at FROM lumi_pets WHERE user_id = ?", (alice,)).fetchone()) == tuple(saved)
    assert conn.execute("SELECT COUNT(*) FROM lumi_pets WHERE user_id = ?", (bob,)).fetchone()[0] == 0   # Bob's Lumi isn't made by looking
    assert "account" not in json.dumps(s["players"])                                  # whose purse it is stays on the server
