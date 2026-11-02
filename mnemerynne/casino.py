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
SLOT_LINES = 3
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
    """One horizontal line: three stars return 24x, other triples 12x, any pair 1x."""
    if len(set(reels)) == 1:
        return 24 if reels[0] == "star" else 12
    return 1 if len(set(reels)) == 2 else 0


def wager_lines(state):
    """A slot bet is per line; already-saved single-line rounds keep their original cost until reset."""
    return state.get("slot_lines", 1) if state["game"] == "slots" else 1


def _seat(actor, name):
    """Make an authenticated seat; the browser never supplies an identity or a balance."""
    return {"id": secrets.token_hex(8), "actor": actor, "name": name[:60], "chips": 1000,
            "bet": 0, "choice": "", "cards": [], "stood": False, "payout": 0, "reels": [], "line_payouts": []}


def _reset(state, now):
    """Open the next betting window, preserving each seat's play-chip balance."""
    state.update(phase="betting", deadline=now + BET_SECONDS, round=state["round"] + 1,
                 dealer=[], deck=[], result=None)
    if state["game"] == "slots":
        state["slot_lines"] = SLOT_LINES  # Upgrade old saved rooms only at a new round, never after taking a wager.
    for p in state["players"]:
        p.update(bet=0, choice="", cards=[], stood=False, payout=0, reels=[], line_payouts=[])
        if "controls" in p:
            p["controls"].update(hand="hit", error="")  # A new hand never inherits the previous Stand selection.
            p["controls"]["amount"] = max(10, min(p["controls"]["amount"], p["chips"] // (10 * wager_lines(state)) * 10))


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
            rows = [[RNG.choice(SYMBOLS) for _ in range(3)] for _ in range(wager_lines(state))]
            p["line_payouts"] = [p["bet"] * slots_return(row) for row in rows]
            p["reels"] = rows if wager_lines(state) > 1 else rows[0]  # Preserve the legacy single-line snapshot shape.
            p["payout"] = sum(p["line_payouts"])  # All horizontal returns are added; columns and diagonals do not pay.
    for p in players:
        p["chips"] += p["payout"]  # Stakes were removed when placed, so returns include the original stake.
    state.update(phase="results", deadline=now + RESULT_SECONDS)
    state["deck"] = []  # Discard unused cards; they never belong in a client response.


def _start(state, now):
    """Lock all bets together and deal a hand or begin the shared spin/race."""
    if not any(p["bet"] for p in state["players"]):
        return
    for p in state["players"]:
        if "controls" in p:
            p["controls"]["error"] = ""  # Betting-time refusals are obsolete once the table starts playing.
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
    result.pop("display_actor", None)
    result.pop("inputs", None)  # Signed input receipts and device identities are never public table data.
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


def rooms(conn, game, actor, display=None):
    """List bounded lobby summaries without revealing private round state."""
    rows = conn.execute("SELECT state FROM casino_rooms WHERE game=? AND updated_at>? ORDER BY updated_at DESC LIMIT 100",
                        (game, time.time() - IDLE_SECONDS)).fetchall()
    result = []
    for row in rows:
        s = json.loads(row["state"])
        own_display = bool(display and s.get("display_actor") == display)
        result.append({"code": s["code"], "host": s["players"][0]["name"] if s["players"] else s.get("display_name", "TV table"),
                       "count": len(s["players"]), "phase": s["phase"],
                       "mine": own_display or s.get("display_actor") == actor or any(p["actor"] == actor for p in s["players"]),
                       "tv": own_display})  # Only the owning screen gets a spectator-resume link in its lobby.
    return result


def create(conn, game, actor, name, couch=False):
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
        state = {"code": code, "game": game, "players": [] if couch else [_seat(actor, name)], "revision": 1, "round": 0}
        if couch:
            state.update(couch=True, display_actor=actor, display_name=name[:60], inputs={})
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
        if not state["players"] and not state.get("couch"):
            conn.execute("DELETE FROM casino_rooms WHERE code=?", (code,))
            return {"left": True}
        if before != json.dumps(state):
            state["revision"] += 1
        _save(conn, state, now)
        return {"left": True} if action == "leave" else _public(state, actor)


def couch(conn, game, code, display, *, command=None, expected_round=None, expected_phase=None,
          remove=None, close=False, revision=None):
    """One shared display, independently controlled seats, and atomic, replay-safe remote input."""
    now = time.time()
    with transaction(conn):
        state = _read(conn, game, code, now)
        if state.get("display_actor") != display:
            raise Refused("This table belongs to another TV.", 403)
        if close or remove is not None:
            if revision != state["revision"]:
                raise Refused("The table changed. Check it before removing a player or ending the table.")
            if close:
                conn.execute("DELETE FROM casino_rooms WHERE code=?", (code,))
                return {"left": True}
        before = json.dumps(state)
        _advance(state, now)
        if remove is not None:
            state["players"] = [p for p in state["players"] if p["id"] != remove]
            if state["game"] == "blackjack" and state["phase"] == "playing" and all(p["stood"] for p in state["players"] if p["bet"]):
                _finish(state, now)  # Removing the last active hand must not leave the round stuck.
        if command is not None:
            receipts = state["inputs"]
            state["inputs"] = receipts = {key: at for key, at in receipts.items() if now - at <= 120}
            if command["id"] not in receipts:
                if len(receipts) >= 1024:
                    raise Refused("Too many remote inputs. Please wait a moment.", 429)
                if expected_round != state["round"] or expected_phase != state["phase"]:
                    raise Refused("The round changed. Check the TV and press again.")
                player = next((p for p in state["players"] if p["actor"] == command["actor"]), None)
                if player is None:
                    if command["action"] != "enter":
                        raise Refused(command["name"] + ": press Enter to join this TV table.")
                    if len(state["players"]) >= 8:
                        raise Refused("This table has eight players. Remove a seat before another remote joins.")
                    player = _seat(command["actor"], command["name"])
                    player["controls"] = {"amount": 50, "pick": 0, "hand": "hit", "error": ""}
                    state["players"].append(player)  # First Enter joins only; a second Enter places the player's bet.
                else:
                    player["controls"]["error"] = ""
                    try:
                        _couch_input(state, player, command["action"], now)
                    except Refused as exc:
                        player["controls"]["error"] = str(exc)  # A refusal belongs to this player, not every remote.
                receipts[command["id"]] = now
        if before != json.dumps(state):
            state["revision"] += 1
        _save(conn, state, now)
        return _public(state, display)


def _couch_input(state, player, action, now):
    """Interpret the existing remote keys using only this seat's persisted draft and blackjack choice."""
    draft, phase, game = player["controls"], state["phase"], state["game"]
    choices = [""]  # Keep the remote's indexed pick aligned with the choices shown by the shared renderer.
    if game == "roulette":
        choices = ["red", "black", "odd", "even", "low", "high", *map(str, range(37))]
    elif game == "horse-racing":
        choices = list(map(str, range(6)))
    if phase == "betting":
        if action in ("left", "right"):
            draft["pick"] = (draft["pick"] + (1 if action == "right" else -1)) % len(choices)
        elif action in ("up", "down"):
            limit = min(500, (player["chips"] + player["bet"] * wager_lines(state)) // (10 * wager_lines(state)) * 10)
            draft["amount"] = max(10, min(limit, draft["amount"] + (10 if action == "up" else -10)))
        elif action == "enter":
            if player["chips"] < 10 * wager_lines(state) and not player["bet"]:
                _act(state, player, "refill", 0, "", now)
            else:
                _act(state, player, "bet", draft["amount"], choices[draft["pick"]], now)
        elif action == "play":
            _act(state, player, "start", 0, "", now)
    elif game == "blackjack" and phase == "playing" and player["bet"] and not player["stood"]:
        if action in ("left", "right"):
            draft["hand"] = "stand" if draft["hand"] == "hit" else "hit"
        elif action == "enter":
            _act(state, player, draft["hand"], 0, "", now)


def _act(state, player, action, amount, choice, now):
    """Validate each move against its seat, phase and chip balance before changing state."""
    phase, game = state["phase"], state["game"]
    if action == "leave":
        state["players"].remove(player)  # The first remaining seat becomes host; departed stakes are forfeited.
    elif action == "bet":
        if phase != "betting":
            raise Refused("Bets are locked until the next round.")
        lines = wager_lines(state)
        if amount < 10 or amount > 500 or amount % 10 or amount * lines > player["chips"] + player["bet"] * lines:
            raise Refused("Choose 10–500 chips per line in steps of 10; your balance must cover all lines."
                          if lines > 1 else "Choose 10–500 chips in steps of 10, within your balance.", 422)
        if game == "roulette" and choice not in {"red", "black", "odd", "even", "low", "high", *map(str, range(37))}:
            raise Refused("Choose a roulette number or an outside bet.", 422)
        if game == "horse-racing" and choice not in set(map(str, range(6))):
            raise Refused("Choose one of the six horses.", 422)
        player["chips"] += (player["bet"] - amount) * lines  # Refund the old full stake before reserving every new line.
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
        minimum = 10 * wager_lines(state)
        if phase != "betting" or player["bet"] or player["chips"] >= minimum:
            raise Refused(f"Free refills are available between rounds when you have fewer than {minimum} chips.")
        player["chips"] = 1000
    else:
        raise Refused("That move is not available.", 422)
