"""Shared casino tables with persisted rounds and private server randomness.

Online tables spend the seat's account coins (coins.py); local multiplayer tables (state["couch"], phone remotes around
one TV) play on free-to-play credits that belong to the table. Each seat's "chips" is whichever of those it plays with:
for an account seat it is loaded from the purse at the start of every request and the difference written back at the end,
all in that request's transaction, so the rules below never need to know which kind they are handling.

Who is still at a table is kept in memory (lobby.PRESENCE), not in the database: every poll says "I'm here". A seat
nobody has polled for DROP_SECONDS is given up between rounds, its open stake refunded; a table nobody is at any more
(or whose TV went away) closes the same way. A poll that changes nothing reads the table without taking the write lock
and writes nothing.
"""

from __future__ import annotations

import copy
import json
import secrets
import time

from mnemerynne import coins, lobby
from mnemerynne.db import transaction

GAMES = {"blackjack", "roulette", "horse-racing", "slots"}
RED = {1, 3, 5, 7, 9, 12, 14, 16, 18, 19, 21, 23, 25, 27, 30, 32, 34, 36}
HORSES = ("Moonbeam", "Velvet Thunder", "Starlight", "Cherry Comet", "Lucky Clover", "Blue Ribbon")
SYMBOLS = ("cherry", "lemon", "bell", "diamond", "clover", "star")
SLOT_LINES = 3
BET_SECONDS, PLAY_SECONDS, RESULT_SECONDS = 45, 30, 10
IDLE_SECONDS = 7200
DROP_SECONDS = 120                # a seat (or a TV) not heard from this long is let go; Back to Games and back is fine
GONE = "You're not at this table any more. Join it again from the lobby."
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


def _seat(actor, name, account=None):
    """Make an authenticated seat; the browser never supplies an identity or a balance. account: whose coins it plays
    with, or None for table-only credits."""
    return {"id": secrets.token_hex(8), "actor": actor, "name": name[:60], "chips": 1000, "account": account,
            "bet": 0, "choice": "", "cards": [], "stood": False, "payout": 0, "reels": [], "line_payouts": []}


def _unit(state):
    """What this table's money is called."""
    return "credits" if state.get("couch") else "coins"


def _load_purses(conn, state, peek=False):
    """Give every account seat its purse's balance now, so a win or bet at another table already counts here.
    peek: only reading (a poll), so a purse that hasn't opened yet isn't opened."""
    for p in state["players"]:
        if p.get("account") is not None:
            balance = coins.peek(conn, p["account"]) if peek else coins.held(conn, p["account"])
            if balance is None:
                p["account"] = None                       # that account was deleted: the seat plays on with credits
            else:
                p["chips"] = balance


def _pay_purses(conn, state, held, why):
    """Write what the rules did to each account seat. held: each seat's balance before they ran. Seats sharing an account
    (a phone and its TV) are added together."""
    owed = {}
    for p in state["players"]:
        if p["id"] in held and p.get("account") is not None:
            owed[p["account"]] = owed.get(p["account"], 0) + p["chips"] - held[p["id"]]
    for account, delta in owed.items():
        coins.table(conn, account, delta, state["game"], why)


def _refund_stakes(conn, state):
    """A table that ended by itself hands back whatever is still riding on it. In results, stakes are already settled."""
    if state["phase"] == "results":
        return
    for p in state["players"]:
        if p.get("account") is not None and p["bet"] and coins.held(conn, p["account"]) is not None:
            coins.table(conn, p["account"], p["bet"] * wager_lines(state), state["game"], "refund")


def _key(code):
    """This table in lobby.PRESENCE."""
    return ("casino", code)


def _here(state, actor):
    """Whether this player (or TV) has been heard from at the table recently enough to keep their place."""
    return lobby.PRESENCE.quiet_for(_key(state["code"]), actor) < DROP_SECONDS


def _abandoned(state):
    """Nobody is at this table any more: no seat has been heard from, or a TV table's TV went away."""
    if state.get("couch"):
        return not _here(state, state["display_actor"])
    return not any(_here(state, p["actor"]) for p in state["players"])


def _absent(state):
    """Seats to let go now. Only between hands: a seat in a playing round stays until it settles. A TV table's seats are
    remotes the TV speaks for, so they stay while the TV does."""
    if state.get("couch") or state["phase"] == "playing":
        return []
    return [p for p in state["players"] if not _here(state, p["actor"])]


def _remove_seat(conn, state, seat, now, refund=False):
    """The one way a seat leaves (Leave, the TV removing it, or nobody there). Leaving on purpose forfeits a stake, as it
    always has; refund: the seat was let go because nobody was there, so a stake still in betting comes back to the
    account. A blackjack round whose last open hand left finishes."""
    if refund and state["phase"] == "betting" and seat["bet"] and seat.get("account") is not None \
            and coins.held(conn, seat["account"]) is not None:
        coins.table(conn, seat["account"], seat["bet"] * wager_lines(state), state["game"], "refund")
    state["players"].remove(seat)
    if state["game"] == "blackjack" and state["phase"] == "playing" and all(p["stood"] for p in state["players"] if p["bet"]):
        _finish(state, now)


def _close(conn, state):
    """Delete a table, handing back what's still riding on it. Inside the caller's transaction."""
    _refund_stakes(conn, state)
    conn.execute("DELETE FROM casino_rooms WHERE code=?", (state["code"],))
    lobby.PRESENCE.forget(_key(state["code"]))


def _ended(row, now):
    """A table to close: idle too long, or nobody at it any more."""
    return row["updated_at"] <= now - IDLE_SECONDS or _abandoned(json.loads(row["state"]))


def _sweep(conn, now):
    """Close tables idle too long or that everyone has left, refunding open stakes. Inside the caller's transaction."""
    for row in conn.execute("SELECT state, updated_at FROM casino_rooms").fetchall():
        if _ended(row, now):
            _close(conn, json.loads(row["state"]))


def sweep(conn):
    """Close finished-with tables now, if there are any, so a stake doesn't wait for someone to open a new table to come
    home. Looks without the write lock first: usually there is nothing to do."""
    now = time.time()
    if not any(_ended(row, now) for row in conn.execute("SELECT state, updated_at FROM casino_rooms").fetchall()):
        return
    with transaction(conn):
        _sweep(conn, now)


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
        p.pop("account", None)                            # whose purse it is stays on the server
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
        if _abandoned(s):
            continue                                      # everyone left: it closes at the next sweep
        own_display = bool(display and s.get("display_actor") == display)
        result.append({"code": s["code"], "host": s["players"][0]["name"] if s["players"] else s.get("display_name", "TV table"),
                       "count": len(s["players"]), "phase": s["phase"],
                       "mine": own_display or s.get("display_actor") == actor or any(p["actor"] == actor for p in s["players"]),
                       "tv": own_display})  # Only the owning screen gets a spectator-resume link in its lobby.
    return result


def create(conn, game, actor, name, couch=False, account=None):
    """Create a durable table with bounded per-player and global room counts. account: whose coins the creator's seat
    plays with; a couch table has no seat yet and never uses coins."""
    now = time.time()
    with transaction(conn):
        _sweep(conn, now)
        if conn.execute("SELECT count(*) FROM casino_rooms").fetchone()[0] >= 100:
            raise Refused("The lobby is full. Join an existing table.", 429)
        if sum(r["mine"] for r in rooms(conn, game, actor)) >= 3:
            raise Refused("Leave one of your tables before creating another.", 429)
        code = lobby.new_code(conn, "casino_rooms")
        lobby.PRESENCE.seen(_key(code), actor)
        state = {"code": code, "game": game, "players": [] if couch else [_seat(actor, name, account)], "revision": 1, "round": 0}
        if couch:
            state.update(couch=True, display_actor=actor, display_name=name[:60], inputs={})
        _load_purses(conn, state)
        _reset(state, now)
        conn.execute("INSERT INTO casino_rooms VALUES (?, ?, ?, ?)", (code, game, json.dumps(state), now))
    return _public(state, actor)


def room(conn, game, code, actor, name, action=None, revision=None, amount=0, choice="", account=None):
    """Serialize joins, wagers, deadlines and settlements across all app workers. account: whose coins a new seat plays
    with; a couch table's seats always use credits instead."""
    now = time.time()
    lobby.PRESENCE.seen(_key(code), actor)
    if action is None:                                    # a poll: look without the write lock, and write nothing
        state = _read(conn, game, code, now)
        _load_purses(conn, state, peek=True)
        if not any(p["actor"] == actor for p in state["players"]):
            raise Refused(GONE, 403)
        if now < state["deadline"] and not _absent(state):
            return _public(state, actor)                  # nothing is due: the usual case
    with transaction(conn):
        state = _read(conn, game, code, now)
        _load_purses(conn, state)                         # before the snapshot: another table's win isn't a change here
        before = json.dumps(state)
        for gone in _absent(state):
            if gone["actor"] != actor:
                _remove_seat(conn, state, gone, now, refund=True)   # nobody's there: let go, and its stake comes back
        player = next((p for p in state["players"] if p["actor"] == actor), None)
        if action == "join" and player is None:
            if len(state["players"]) >= 8:
                raise Refused("This table has eight players. Choose another table.")
            player = _seat(actor, name, None if state.get("couch") else account)
            state["players"].append(player)
            _load_purses(conn, state)
        if player is None:
            raise Refused(GONE if action is None else "Join this table first.", 403)
        held = {p["id"]: p["chips"] for p in state["players"] if p.get("account") is not None}
        _advance(state, now)
        if action == "leave":
            _remove_seat(conn, state, player, now)        # no revision check: leaving never needs to be retried
        elif action not in (None, "join"):
            if revision != state["revision"]:
                raise Refused("The table changed. Please try your move again.")
            _act(state, player, action, amount, choice, now)
        _pay_purses(conn, state, held, "refill" if action == "refill" else "refund" if action == "bet" else None)
        if not state["players"] and not state.get("couch"):
            conn.execute("DELETE FROM casino_rooms WHERE code=?", (code,))
            lobby.PRESENCE.forget(_key(code))
            return {"left": True}
        if before != json.dumps(state):
            state["revision"] += 1
            _save(conn, state, now)                       # only a real change is written (and keeps the table alive)
        return {"left": True} if action == "leave" else _public(state, actor)


def couch(conn, game, code, display, *, command=None, expected_round=None, expected_phase=None,
          remove=None, close=False, revision=None):
    """One shared display, independently controlled seats, and atomic, replay-safe remote input."""
    now = time.time()
    lobby.PRESENCE.seen(_key(code), display)              # the TV is here: its table stays open
    if command is None and remove is None and not close:  # the TV's poll: look without the write lock
        state = _read(conn, game, code, now)
        if state.get("display_actor") != display:
            raise Refused("This table belongs to another TV.", 403)
        if now < state["deadline"]:
            return _public(state, display)
    with transaction(conn):
        state = _read(conn, game, code, now)
        if state.get("display_actor") != display:
            raise Refused("This table belongs to another TV.", 403)
        if close or remove is not None:
            if revision != state["revision"]:
                raise Refused("The table changed. Check it before removing a player or ending the table.")
            if close:
                conn.execute("DELETE FROM casino_rooms WHERE code=?", (code,))
                lobby.PRESENCE.forget(_key(code))
                return {"left": True}
        before = json.dumps(state)
        _advance(state, now)
        if remove is not None:
            seat = next((p for p in state["players"] if p["id"] == remove), None)
            if seat is not None:
                _remove_seat(conn, state, seat, now)      # the same path as Leave: the round never gets stuck
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
    if action == "bet":
        if phase != "betting":
            raise Refused("Bets are locked until the next round.")
        lines = wager_lines(state)
        if amount < 10 or amount > 500 or amount % 10 or amount * lines > player["chips"] + player["bet"] * lines:
            raise Refused(f"Choose 10–500 {_unit(state)} per line in steps of 10; your balance must cover all lines."
                          if lines > 1 else f"Choose 10–500 {_unit(state)} in steps of 10, within your balance.", 422)
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
            raise Refused(f"Free refills are available between rounds when you have fewer than {minimum} {_unit(state)}.")
        player["chips"] = 1000
    else:
        raise Refused("That move is not available.", 422)
