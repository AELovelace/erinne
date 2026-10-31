"""Shared casino tables with free play chips, persisted rounds, and private server randomness."""

from __future__ import annotations

import copy
import json
import secrets
import time

from mnemerynne.db import transaction

GAMES = {"blackjack", "roulette", "horse-racing", "slots"}
RED = {1, 3, 5, 7, 9, 12, 14, 16, 18, 19, 21, 23, 25, 27, 30, 32, 34, 36}
HORSES = ("Moonbeam", "Velvet Thunder", "Starlight", "Cherry Comet", "Lucky Clover", "Blue Ribbon")
SYMBOLS = ("cherry", "lemon", "bell", "diamond", "clover", "star")
BET_SECONDS, PLAY_SECONDS, RESULT_SECONDS = 45, 30, 10
IDLE_SECONDS = 7200
RNG = secrets.SystemRandom()


class Refused(ValueError):
    """A safe, actionable explanation for the player."""

    def __init__(self, message, status=409):
        super().__init__(message)
        self.status = status


def total(cards):
    """Count aces as eleven unless the hand would bust."""
    value = sum(min(c % 13 + 1, 10) if c % 13 else 11 for c in cards)
    aces = sum(c % 13 == 0 for c in cards)
    while value > 21 and aces:
        value -= 10
        aces -= 1
    return value


def roulette_return(choice, number):
    """Return the stake multiplier, including the original stake; zero loses every outside bet."""
    if choice.isdigit():
        return 36 if int(choice) == number else 0
    wins = number != 0 and {
        "red": number in RED, "black": number not in RED, "odd": number % 2 == 1,
        "even": number % 2 == 0, "low": number <= 18, "high": number >= 19,
    }.get(choice, False)
    return 2 if wins else 0


def slots_return(reels):
    """Three stars return 24x, other triples 12x, any pair 1x; everything else loses."""
    if len(set(reels)) == 1:
        return 24 if reels[0] == "star" else 12
    return 1 if len(set(reels)) == 2 else 0


def _seat(actor, name):
    """Make an authenticated seat; the browser never supplies an identity or a balance."""
    return {"id": secrets.token_hex(8), "actor": actor, "name": name[:60], "chips": 1000,
            "bet": 0, "choice": "", "cards": [], "stood": False, "payout": 0, "reels": []}


def _reset(state, now):
    """Open the next betting window, preserving each seat's play-chip balance."""
    state.update(phase="betting", deadline=now + BET_SECONDS, round=state["round"] + 1,
                 dealer=[], deck=[], result=None)
    for p in state["players"]:
        p.update(bet=0, choice="", cards=[], stood=False, payout=0, reels=[])


def _finish(state, now):
    """Settle each reserved stake exactly once, entirely on the server."""
    game = state["game"]
    players = [p for p in state["players"] if p["bet"]]
    if game == "blackjack":
        while total(state["dealer"]) < 17:
            state["dealer"].append(state["deck"].pop())  # Dealer stands on every seventeen, including soft seventeen.
        dealer = total(state["dealer"])
        natural = dealer == 21 and len(state["dealer"]) == 2
        state["result"] = {"dealer_total": dealer}
        for p in players:
            value = total(p["cards"])
            blackjack = value == 21 and len(p["cards"]) == 2
            if value > 21:
                p["payout"] = 0
            elif natural:
                p["payout"] = p["bet"] if blackjack else 0
            elif blackjack:
                p["payout"] = p["bet"] * 5 // 2
            elif dealer > 21 or value > dealer:
                p["payout"] = p["bet"] * 2
            elif value == dealer:
                p["payout"] = p["bet"]
    elif game == "roulette":
        number = RNG.randrange(37)
        state["result"] = {"number": number, "color": "green" if number == 0 else "red" if number in RED else "black"}
        for p in players:
            p["payout"] = p["bet"] * roulette_return(p["choice"], number)
    elif game == "horse-racing":
        order = list(range(6))
        RNG.shuffle(order)  # Equal chances for all six horses; finish order is shared by everyone.
        state["result"] = {"order": order}
        for p in players:
            p["payout"] = p["bet"] * 5 if int(p["choice"]) == order[0] else 0
    else:
        state["result"] = {"spun": True}
        for p in players:
            p["reels"] = [RNG.choice(SYMBOLS) for _ in range(3)]
            p["payout"] = p["bet"] * slots_return(p["reels"])
    for p in players:
        p["chips"] += p["payout"]  # Stakes were removed when placed, so returns include the original stake.
    state.update(phase="results", deadline=now + RESULT_SECONDS)
    state["deck"] = []  # Discard unused cards; they never belong in a client response.


def _start(state, now):
    """Lock all bets together and deal a hand or begin the shared spin/race."""
    if not any(p["bet"] for p in state["players"]):
        return
    state.update(phase="playing", deadline=now + (PLAY_SECONDS if state["game"] == "blackjack" else 6))
    if state["game"] == "blackjack":
        state["deck"] = list(range(52))
        RNG.shuffle(state["deck"])
        state["dealer"] = [state["deck"].pop(), state["deck"].pop()]
        for p in state["players"]:
            if p["bet"]:
                p["cards"] = [state["deck"].pop(), state["deck"].pop()]
                p["stood"] = total(p["cards"]) == 21
        if total(state["dealer"]) == 21 or all(p["stood"] for p in state["players"] if p["bet"]):
            _finish(state, now)


def _advance(state, now):
    """Server deadlines keep disconnected players or hosts from holding a table indefinitely."""
    if now < state["deadline"]:
        return
    if state["phase"] == "betting":
        _start(state, now)
        if state["phase"] == "betting":
            state["deadline"] = now + BET_SECONDS  # An empty betting window simply renews.
    elif state["phase"] == "playing":
        _finish(state, now)  # Unfinished blackjack hands automatically stand.
    else:
        _reset(state, now)


def _public(state, actor):
    """Copy a public snapshot without the deck, actor identifiers, or dealer hole card."""
    result = copy.deepcopy(state)
    result.pop("deck", None)
    result["you"] = next((p["id"] for p in result["players"] if p["actor"] == actor), None)
    for p in result["players"]:
        p.pop("actor")
    if result["game"] == "blackjack" and result["phase"] == "playing":
        result["dealer"] = result["dealer"][:1] + [None]
    result["server_time"] = time.time()
    return result


def _read(conn, game, code, now):
    """Read a live room only under the matching game; expired room codes stop working."""
    row = conn.execute("SELECT state FROM casino_rooms WHERE code=? AND game=? AND updated_at>?",
                       (code, game, now - IDLE_SECONDS)).fetchone()
    if row is None:
        raise Refused("This table has closed. Choose another or create a table.", 404)
    return json.loads(row["state"])


def _save(conn, state, now):
    """Persist a versioned snapshot in the caller's write transaction."""
    conn.execute("UPDATE casino_rooms SET state=?, updated_at=? WHERE code=?",
                 (json.dumps(state), now, state["code"]))


def rooms(conn, game, actor):
    """List bounded lobby summaries without revealing private round state."""
    rows = conn.execute("SELECT state FROM casino_rooms WHERE game=? AND updated_at>? ORDER BY updated_at DESC LIMIT 100",
                        (game, time.time() - IDLE_SECONDS)).fetchall()
    result = []
    for row in rows:
        s = json.loads(row["state"])
        result.append({"code": s["code"], "host": s["players"][0]["name"], "count": len(s["players"]),
                       "phase": s["phase"], "mine": any(p["actor"] == actor for p in s["players"])})
    return result


def create(conn, game, actor, name):
    """Create a durable table with bounded per-player and global room counts."""
    now = time.time()
    with transaction(conn):
        conn.execute("DELETE FROM casino_rooms WHERE updated_at<=?", (now - IDLE_SECONDS,))
        if conn.execute("SELECT count(*) FROM casino_rooms").fetchone()[0] >= 100:
            raise Refused("The lobby is full. Join an existing table.", 429)
        if sum(r["mine"] for r in rooms(conn, game, actor)) >= 3:
            raise Refused("Leave one of your tables before creating another.", 429)
        code = secrets.token_hex(4).upper()
        while conn.execute("SELECT 1 FROM casino_rooms WHERE code=?", (code,)).fetchone():
            code = secrets.token_hex(4).upper()
        state = {"code": code, "game": game, "players": [_seat(actor, name)], "revision": 1, "round": 0}
        _reset(state, now)
        conn.execute("INSERT INTO casino_rooms VALUES (?, ?, ?, ?)", (code, game, json.dumps(state), now))
    return _public(state, actor)


def room(conn, game, code, actor, name, action=None, revision=None, amount=0, choice=""):
    """Serialize joins, wagers, deadlines and settlements across all app workers."""
    now = time.time()
    with transaction(conn):
        state = _read(conn, game, code, now)
        before = json.dumps(state)
        player = next((p for p in state["players"] if p["actor"] == actor), None)
        if action == "join" and player is None:
            if len(state["players"]) >= 8:
                raise Refused("This table has eight players. Choose another table.")
            player = _seat(actor, name)
            state["players"].append(player)
        if player is None:
            raise Refused("Join this table first.", 403)
        _advance(state, now)
        if action not in (None, "join"):
            if revision != state["revision"]:
                raise Refused("The table changed. Please try your move again.")
            _act(state, player, action, amount, choice, now)
        if not state["players"]:
            conn.execute("DELETE FROM casino_rooms WHERE code=?", (code,))
            return {"left": True}
        if before != json.dumps(state):
            state["revision"] += 1
        _save(conn, state, now)
        return {"left": True} if action == "leave" else _public(state, actor)


def _act(state, player, action, amount, choice, now):
    """Validate each move against its seat, phase and chip balance before changing state."""
    phase, game = state["phase"], state["game"]
    if action == "leave":
        state["players"].remove(player)  # The first remaining seat becomes host; departed stakes are forfeited.
    elif action == "bet":
        if phase != "betting":
            raise Refused("Bets are locked until the next round.")
        if amount < 10 or amount > 500 or amount % 10 or amount > player["chips"] + player["bet"]:
            raise Refused("Choose 10–500 chips in steps of 10, within your balance.", 422)
        if game == "roulette" and choice not in {"red", "black", "odd", "even", "low", "high", *map(str, range(37))}:
            raise Refused("Choose a roulette number or an outside bet.", 422)
        if game == "horse-racing" and choice not in set(map(str, range(6))):
            raise Refused("Choose one of the six horses.", 422)
        player["chips"] += player["bet"] - amount  # Replacing a bet refunds only the previous reservation.
        player.update(bet=amount, choice=choice)
    elif action == "start":
        if player != state["players"][0] or phase != "betting" or not any(p["bet"] for p in state["players"]):
            raise Refused("The host can start once someone places a bet.")
        _start(state, now)
    elif action in ("hit", "stand"):
        if game != "blackjack" or phase != "playing" or not player["bet"] or player["stood"]:
            raise Refused("There is no active hand for this move.")
        if action == "hit":
            player["cards"].append(state["deck"].pop())
        player["stood"] = action == "stand" or total(player["cards"]) >= 21
        if all(p["stood"] for p in state["players"] if p["bet"]):
            _finish(state, now)
    elif action == "refill":
        if phase != "betting" or player["bet"] or player["chips"] >= 10:
            raise Refused("Free refills are available between rounds when you have fewer than 10 chips.")
        player["chips"] = 1000
    else:
        raise Refused("That move is not available.", 422)
