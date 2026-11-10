"""Breakout, Snake and Teacup Tower showdowns: two racers play the same seeded game side by side, and up to five watchers see both
boards live and can bet on who wins. Ported from Doll's GameMaker arcade broker, with its lobby rebuilt cleanly.

A table (arcade_rooms, one JSON document) goes waiting -> racing -> result -> waiting:
- waiting: P1 and P2 each press Ready; the race starts when both have, and only then are both charged ENTRY coins
  (both or neither). Watchers may bet on P1 or P2 meanwhile. A watcher can take an empty racer seat; a racer can stand
  down to watch. Any change of racer refunds the bets, since they were on someone else.
- racing: each racer's page plays the game from the race's seed and finishes through arcade.finish, which checks the
  run; live boards go through HUB, in memory, never the database. The race settles when both racers have finished,
  or RACE_SECONDS (+ grace) after it began, or when nobody is left.
- result: for RESULT_SECONDS everyone sees who won, then the table waits again with the winner as P1.

Settling, by Roulette's rules: each racer is paid their own verified score (one coin a point); the higher distance wins
(then score, then P1); bets on the winner pay double and the rest go to the house. No winner (both left before
finishing): every bet comes back.

Leaving and disconnecting (lobby.PRESENCE): a seat is there while its page holds the table's stream open. A seat whose
stream has been closed for DROP_SECONDS is let go: a watcher's open bets come back; a racer between races stands up
(bets refunded); a racer mid-race forfeits that race. A table with nobody left closes, settling first. There is no host:
nobody can start a race for someone else or spend their coins.
"""

from __future__ import annotations

import asyncio
import json
import secrets
import threading
import time

from mnemerynne import arcade, coins, lobby
from mnemerynne.arcade import ENTRY, Refused
from mnemerynne.db import transaction

GAMES = arcade.GAMES
WATCHERS = 5                     # with the two racers, seven at a table
DROP_SECONDS = 30                # a page gone this long gives up its seat (a reload is quicker)
AWAY_SECONDS = 5                 # shown as "away" after this long without a stream
RESULT_SECONDS = 12
RACE_SECONDS = 600               # a race's longest; the pages end their games at this time and finish
FINISH_GRACE = 30                # after that, how long the server waits for a finish to arrive
BET_CHIPS = (1, 5, 10, 25, 100)
BET_LIMIT = 500                  # the most one watcher may have riding on one race
MAX_TABLES = 50                  # per game
IDLE_SECONDS = 7200              # a table nobody has changed in this long closes, whoever is still "there"
NAMES = {"breakout": "Breakout", "snake": "Snake", "teacup-tower": "Teacup Tower"}


def _key(code: str) -> tuple:
    return ("arcade", code)


def _quiet(state: dict, seat: dict) -> float:
    return lobby.PRESENCE.quiet_for(_key(state["code"]), seat["actor"])


def _racer(state: dict, role: str) -> dict | None:
    return next((s for s in state["seats"] if s["role"] == role), None)


def _seat_of(state: dict, actor: str) -> dict | None:
    return next((s for s in state["seats"] if s["actor"] == actor), None)


def _read(conn, game: str, code: str) -> dict:
    row = conn.execute("SELECT state FROM arcade_rooms WHERE code = ? AND game = ?", (code, game)).fetchone()
    if row is None:
        raise Refused("This table has closed. Choose another or start one.", 404)
    return json.loads(row["state"])


def _save(conn, state: dict, now: float) -> None:
    conn.execute("UPDATE arcade_rooms SET state = ?, updated_at = ? WHERE code = ?", (json.dumps(state), now, state["code"]))


# ------------------------------------------------------------------ money
def _refund_bets(conn, state: dict, bettor: str | None = None) -> None:
    """Hand back open bets: one watcher's (bettor: their seat id), or everyone's."""
    keep = []
    for bet in state["bets"]:
        if bettor is None or bet["bettor"] == bettor:
            if coins.held(conn, bet["account"]) is not None:
                coins.table(conn, bet["account"], bet["amount"], state["game"], "refund")
        else:
            keep.append(bet)
    state["bets"] = keep


def _settle(conn, state: dict, now: float) -> None:
    """End the race: pay each racer's verified score, decide the winner, pay the bets, show the result."""
    race, game = state["race"], state["game"]
    lines, finished = [], []
    for racer in race["racers"]:
        result = race["results"].get(racer["seat"])
        paid = 0
        if result and not result.get("forfeit") and coins.held(conn, racer["account"]) is not None:
            paid = coins.prize(conn, racer["account"], result["score"], game, ref=f"{game}:{racer['run']}")
        line = {"seat": racer["seat"], "name": racer["name"], "role": racer["role"], "paid": paid,
                "forfeit": not result or bool(result.get("forfeit")),
                **({k: result[k] for k in ("score", "distance", "level", "length") if k in result} if result else {})}
        lines.append(line)
        if not line["forfeit"]:
            finished.append(line)
    winner = max(finished, key=lambda r: (r["distance"], r["score"], r["role"] == "p1")) if finished else None
    for bet in state["bets"]:
        if winner is None:
            if coins.held(conn, bet["account"]) is not None:
                coins.table(conn, bet["account"], bet["amount"], game, "refund")
        elif bet["target"] == winner["seat"] and coins.held(conn, bet["account"]) is not None:
            coins.table(conn, bet["account"], bet["amount"] * 2, game)                     # double back on the winner
    state["last"] = {"winner": winner["name"] if winner else None, "winner_seat": winner["seat"] if winner else None,
                     "racers": lines, "bets": len(state["bets"])}
    state.update(phase="result", race=None, bets=[], result_until=now + RESULT_SECONDS)
    state["seats"] = [s for s in state["seats"] if not s.get("left_race")]               # racers who'd gone, gone now
    for seat in state["seats"]:
        seat["ready"] = False
    if winner and (seat := next((s for s in state["seats"] if s["id"] == winner["seat"]), None)) is not None \
            and seat["role"] == "p2":
        other = _racer(state, "p1")
        seat["role"] = "p1"                                                              # the winner sits as P1
        if other is not None:
            other["role"] = "p2"


def _start_race(conn, state: dict, now: float) -> None:
    """Both racers are ready: charge them both (or neither) and give the race its seed and run tickets."""
    p1, p2 = _racer(state, "p1"), _racer(state, "p2")
    for seat in (p1, p2):
        if (coins.held(conn, seat["account"]) or 0) < ENTRY:
            seat["ready"] = False
            raise Refused(f"{seat['name']} needs {ENTRY} coins to race.", 402)
    seed = arcade.new_seed()
    racers = []
    for seat in (p1, p2):
        coins.spend(conn, seat["account"], ENTRY, state["game"], "entry")                # checked above: can't fail
        run = arcade.start(conn, seat["account"], state["game"], room=state["code"], seed=seed)
        racers.append({"seat": seat["id"], "role": seat["role"], "name": seat["name"], "account": seat["account"],
                       "run": run["run"]})
        seat["ready"] = False
    state.update(phase="racing", race={"seed": seed, "started": now, "ends": now + RACE_SECONDS, "racers": racers,
                                       "results": {}})
    HUB.clear(state["code"])


def _remove(conn, state: dict, seat: dict) -> None:
    """The one way a seat leaves (Leave, standing on a full table, or nobody there). Between races: their own bets come
    back, and if they were racing, everyone's (the race they bet on can't happen). Mid-race: a racer forfeits."""
    racing = state["phase"] == "racing"
    racer = racing and any(r["seat"] == seat["id"] for r in state["race"]["racers"])
    if racer:
        state["race"]["results"].setdefault(seat["id"], {"forfeit": True})
        seat["left_race"] = True                                                         # gone after the settle
        seat["role"] = "gone"
        return
    if not racing:
        _refund_bets(conn, state, None if seat["role"] in ("p1", "p2") else seat["id"])
    state["seats"].remove(seat)


def _advance(conn, state: dict, now: float) -> None:
    """What time and absence have done since anyone looked: seats let go, the race settled, the result cleared."""
    for seat in list(state["seats"]):
        if seat["role"] != "gone" and _quiet(state, seat) >= DROP_SECONDS:
            _remove(conn, state, seat)
    if state["phase"] == "racing":
        race = state["race"]
        if now >= race["ends"] + FINISH_GRACE:
            for racer in race["racers"]:
                race["results"].setdefault(racer["seat"], {"forfeit": True})            # never finished: forfeits
        if all(r["seat"] in race["results"] for r in race["racers"]):
            _settle(conn, state, now)
    elif state["phase"] == "result" and now >= state["result_until"]:
        state["phase"] = "waiting"


def _due(state: dict, now: float) -> bool:
    """Whether _advance would change anything, so a look can skip the write lock when it wouldn't."""
    if any(s["role"] != "gone" and _quiet(state, s) >= DROP_SECONDS for s in state["seats"]):
        return True
    if state["phase"] == "racing":
        race = state["race"]
        return now >= race["ends"] + FINISH_GRACE or all(r["seat"] in race["results"] for r in race["racers"])
    return state["phase"] == "result" and now >= state["result_until"]


def _close(conn, state: dict, now: float) -> None:
    """Close a table: a race still running settles first (finished racers are paid, bets decided), open bets come back."""
    if state["phase"] == "racing":
        for racer in state["race"]["racers"]:
            state["race"]["results"].setdefault(racer["seat"], {"forfeit": True})
        _settle(conn, state, now)
    _refund_bets(conn, state)
    conn.execute("DELETE FROM arcade_rooms WHERE code = ?", (state["code"],))
    lobby.PRESENCE.forget(_key(state["code"]))
    HUB.clear(state["code"])


def _empty(state: dict) -> bool:
    return not any(s["role"] != "gone" for s in state["seats"])


# ----------------------------------------------------------------- public
def _public(state: dict, actor: str) -> dict:
    """What a page may see: no actors or accounts; the seed only once the race has started."""
    me = _seat_of(state, actor)
    seats = [{"id": s["id"], "name": s["name"], "role": s["role"], "ready": s["ready"],
              "away": _quiet(state, s) >= AWAY_SECONDS, "you": s is me} for s in state["seats"] if s["role"] != "gone"]
    race = None
    if state["race"]:
        r = state["race"]
        mine = next((x for x in r["racers"] if me is not None and x["seat"] == me["id"]), None)
        race = {"seed": r["seed"], "started": r["started"], "ends": r["ends"],
                "racers": [{"seat": x["seat"], "role": x["role"], "name": x["name"], "done": x["seat"] in r["results"]}
                           for x in r["racers"]],
                "run": mine["run"] if mine and mine["seat"] not in r["results"] else None}
    totals = {"p1": 0, "p2": 0}
    for bet in state["bets"]:
        target = next((s for s in state["seats"] if s["id"] == bet["target"]), None)
        if target is not None and target["role"] in totals:
            totals[target["role"]] += bet["amount"]
    mine = [{"role": next((s["role"] for s in state["seats"] if s["id"] == b["target"]), "?"), "amount": b["amount"]}
            for b in state["bets"] if me is not None and b["bettor"] == me["id"]]
    return {"code": state["code"], "game": state["game"], "name": state["name"], "phase": state["phase"],
            "revision": state["revision"], "seats": seats, "you": me["id"] if me else None,
            "role": me["role"] if me else None, "race": race, "bets": {**totals, "mine": mine},
            "last": state["last"], "result_until": state["result_until"], "server_time": time.time(),
            "entry": ENTRY, "chips": list(BET_CHIPS), "bet_limit": BET_LIMIT, "race_seconds": RACE_SECONDS}


def _summary(state: dict, actor: str) -> dict:
    """One line in the lobby."""
    live = [s for s in state["seats"] if s["role"] != "gone"]
    return {"code": state["code"], "name": state["name"], "phase": state["phase"],
            "racers": sum(s["role"] in ("p1", "p2") for s in live), "watchers": sum(s["role"] == "watch" for s in live),
            "mine": any(s["actor"] == actor for s in live)}


# ------------------------------------------------------------------- lobby
def _stale(row, now: float) -> bool:
    state = json.loads(row["state"])
    return row["updated_at"] <= now - IDLE_SECONDS or all(_quiet(state, s) >= DROP_SECONDS for s in state["seats"])


def sweep(conn) -> None:
    """Close tables nobody is at any more (or that nobody has changed in hours). Looks without the write lock first."""
    now = time.time()
    if not any(_stale(row, now) for row in conn.execute("SELECT state, updated_at FROM arcade_rooms").fetchall()):
        return
    with transaction(conn):
        for row in conn.execute("SELECT state, updated_at FROM arcade_rooms").fetchall():
            if _stale(row, now):
                _close(conn, json.loads(row["state"]), now)


def tables(conn, game: str, actor: str) -> list[dict]:
    """This game's tables, newest change first, leaving out any nobody is at."""
    sweep(conn)
    rows = conn.execute("SELECT state FROM arcade_rooms WHERE game = ? ORDER BY updated_at DESC LIMIT ?",
                        (game, MAX_TABLES)).fetchall()
    return [_summary(json.loads(r["state"]), actor) for r in rows]


def _new_seat(actor: str, name: str, account: int, role: str) -> dict:
    return {"id": secrets.token_hex(6), "actor": actor, "name": name[:40], "account": account, "role": role, "ready": False}


def _leave_others(conn, game: str, actor: str, keep: str | None, now: float) -> None:
    """One table per game at a time: sitting down somewhere leaves the others, unless you're racing at one."""
    for row in conn.execute("SELECT state FROM arcade_rooms WHERE game = ? AND code != ?", (game, keep or "")).fetchall():
        state = json.loads(row["state"])
        seat = _seat_of(state, actor)
        if seat is None or seat["role"] == "gone":
            continue
        if state["phase"] == "racing" and any(r["seat"] == seat["id"] for r in state["race"]["racers"]):
            raise Refused("You're racing at another table. Finish or leave that race first.")
        _remove(conn, state, seat)
        if _empty(state):
            _close(conn, state, now)
        else:
            state["revision"] += 1
            _save(conn, state, now)
            HUB.wake(state["code"])


def create(conn, game: str, actor: str, name: str, account: int) -> dict:
    """A new table, named for whoever opened it, with them in the P1 seat."""
    if game not in GAMES:
        raise Refused("No such game.", 404)
    now = time.time()
    sweep(conn)
    with transaction(conn):
        if conn.execute("SELECT count(*) FROM arcade_rooms WHERE game = ?", (game,)).fetchone()[0] >= MAX_TABLES:
            raise Refused("There are as many tables as there can be. Join one of them.", 429)
        _leave_others(conn, game, actor, None, now)
        code = lobby.new_code(conn, "arcade_rooms")
        lobby.PRESENCE.seen(_key(code), actor)
        state = {"code": code, "game": game, "name": f"{name[:40]}'s {NAMES[game]} table", "revision": 1,
                 "phase": "waiting", "seats": [_new_seat(actor, name, account, "p1")], "race": None, "bets": [],
                 "last": None, "result_until": 0}
        conn.execute("INSERT INTO arcade_rooms VALUES (?, ?, ?, ?)", (code, game, json.dumps(state), now))
    return _public(state, actor)


def look(conn, game: str, code: str, actor: str) -> dict:
    """A table as this player sees it now. Catches the table up on its timers, taking the write lock only when one is due."""
    now = time.time()
    state = _read(conn, game, code)
    if not _due(state, now):
        return _public(state, actor)
    return act(conn, game, code, actor, "", 0, None)


def act(conn, game: str, code: str, actor: str, name: str, account: int, action: str | None, *,
        target: str = "", amount: int = 0) -> dict:
    """One thing a player does at a table (or None: just bring it up to date)."""
    now = time.time()
    if action is not None:
        lobby.PRESENCE.seen(_key(code), actor)
    with transaction(conn):
        state = _read(conn, game, code)
        before = json.dumps(state)
        if action == "join":
            _leave_others(conn, game, actor, code, now)
        _advance(conn, state, now)
        seat = _seat_of(state, actor)
        if seat is not None and seat["role"] == "gone":
            seat = None
        if action == "join":
            if seat is None:
                seat = _join(state, actor, name, account)
        elif action is not None and seat is None:
            raise Refused("You're not at this table any more. Join it again from the lobby.", 403)
        elif action == "leave":
            _remove(conn, state, seat)
        elif action == "ready":
            _ready(conn, state, seat, now)
        elif action == "seat":
            _take_seat(conn, state, seat)
        elif action == "stand":
            _stand(conn, state, seat)
        elif action == "bet":
            _bet(conn, state, seat, target, amount)
        elif action is not None:
            raise Refused("That isn't something you can do here.", 422)
        if _empty(state):
            _close(conn, state, now)
            return {"left": True}
        if before != json.dumps(state):
            state["revision"] += 1
            _save(conn, state, now)
            HUB.wake(code)
        return {"left": True} if action == "leave" else _public(state, actor)


def _join(state: dict, actor: str, name: str, account: int) -> dict:
    """Sit down: in an empty racer seat between races (unless the other racer is the same account), else to watch."""
    for role in ("p1", "p2"):
        other = _racer(state, "p2" if role == "p1" else "p1")
        if state["phase"] != "racing" and _racer(state, role) is None and (other is None or other["account"] != account):
            seat = _new_seat(actor, name, account, role)   # bets need both racers, so none can be riding yet
            state["seats"].append(seat)
            return seat
    if sum(s["role"] == "watch" for s in state["seats"]) >= WATCHERS:
        raise Refused("This table is full. Choose another or start one.")
    seat = _new_seat(actor, name, account, "watch")
    state["seats"].append(seat)
    return seat


def _ready(conn, state: dict, seat: dict, now: float) -> None:
    if seat["role"] not in ("p1", "p2") or state["phase"] == "racing":
        raise Refused("Only the two racers get ready, between races.")
    if state["phase"] == "result":
        state["phase"] = "waiting"
    seat["ready"] = not seat["ready"]
    p1, p2 = _racer(state, "p1"), _racer(state, "p2")
    if p1 and p2 and p1["ready"] and p2["ready"]:
        _start_race(conn, state, now)


def _take_seat(conn, state: dict, seat: dict) -> None:
    if seat["role"] != "watch" or state["phase"] == "racing":
        raise Refused("Watchers take an empty racer seat between races.")
    for role in ("p1", "p2"):
        other = _racer(state, "p2" if role == "p1" else "p1")
        if _racer(state, role) is None and (other is None or other["account"] != seat["account"]):
            _refund_bets(conn, state, seat["id"])     # a racer can't have bets riding
            seat.update(role=role, ready=False)
            return
    raise Refused("Both racer seats are taken.")


def _stand(conn, state: dict, seat: dict) -> None:
    if seat["role"] not in ("p1", "p2") or state["phase"] == "racing":
        raise Refused("Racers can stand down between races.")
    if sum(s["role"] == "watch" for s in state["seats"]) >= WATCHERS:
        raise Refused("There's no room to watch. Leave the table instead.")
    _refund_bets(conn, state)                         # the pair changed: every bet comes back
    for other in state["seats"]:
        other["ready"] = False
    seat.update(role="watch", ready=False)


def _bet(conn, state: dict, seat: dict, target: str, amount: int) -> None:
    if seat["role"] != "watch":
        raise Refused("Only watchers bet.")
    if state["phase"] == "racing":
        raise Refused("Bets close when the race starts.")
    racer = _racer(state, target) if target in ("p1", "p2") else None
    if racer is None or _racer(state, "p1" if target == "p2" else "p2") is None:
        raise Refused("Bet once both racers are seated.")
    if amount not in BET_CHIPS:
        raise Refused(f"Bets come in chips of {', '.join(map(str, BET_CHIPS))}.", 422)
    if sum(b["amount"] for b in state["bets"] if b["bettor"] == seat["id"]) + amount > BET_LIMIT:
        raise Refused(f"You can have up to {BET_LIMIT} coins riding on one race.")
    if not coins.spend(conn, seat["account"], amount, state["game"], "bet"):
        raise Refused("You don't have enough coins for that bet.", 402)
    if state["phase"] == "result":
        state["phase"] = "waiting"
    same = next((b for b in state["bets"] if b["bettor"] == seat["id"] and b["target"] == racer["id"]), None)
    if same is not None:
        same["amount"] += amount                                                         # chips stack on one bet
    else:
        state["bets"].append({"bettor": seat["id"], "account": seat["account"], "target": racer["id"], "amount": amount})


def record(conn, game: str, code: str, run_id: int, result: dict) -> None:
    """A racer's checked run (arcade.finish) comes back to the table; the race settles once both are in. Inside the
    caller's transaction. A table that has closed already, or a race that has moved on, simply ignores it."""
    row = conn.execute("SELECT state FROM arcade_rooms WHERE code = ? AND game = ?", (code, game)).fetchone()
    if row is None:
        return
    state, now = json.loads(row["state"]), time.time()
    race = state["race"]
    racer = next((r for r in race["racers"] if r["run"] == run_id), None) if race else None
    if racer is None or racer["seat"] in race["results"]:
        return
    race["results"][racer["seat"]] = {k: result[k] for k in ("score", "distance", "level", "length") if k in result}
    if all(r["seat"] in race["results"] for r in race["racers"]):
        _settle(conn, state, now)
    state["revision"] += 1
    _save(conn, state, now)
    HUB.wake(code)


# -------------------------------------------------------------- live boards
class Hub:
    """The racers' live boards (score, bricks or snake, ball and bat), in memory: they change several times a second
    and matter only while the race lasts. Streams wait here for a board or a table change (wake)."""

    def __init__(self) -> None:
        self.loop: asyncio.AbstractEventLoop | None = None
        self.boards: dict[str, dict[str, dict]] = {}       # code -> seat id -> latest board
        self.waiters: dict[str, set[asyncio.Event]] = {}
        self.versions: dict[str, list[int]] = {}           # code -> [table changes, board changes]
        self._lock = threading.Lock()

    def version(self, code: str) -> tuple[int, int]:
        with self._lock:
            v = self.versions.setdefault(code, [0, 0])
            return v[0], v[1]

    def _bump(self, code: str, which: int) -> None:
        with self._lock:
            self.versions.setdefault(code, [0, 0])[which] += 1
            waiters = list(self.waiters.get(code, ()))
        loop = self.loop
        if loop is None or loop.is_closed():
            return
        for event in waiters:
            loop.call_soon_threadsafe(event.set)

    def wake(self, code: str) -> None:
        """The table changed (from any thread): every stream for it looks again."""
        self._bump(code, 0)

    def push(self, code: str, seat: str, board: dict) -> None:
        """A racer's latest board (from any thread)."""
        with self._lock:
            self.boards.setdefault(code, {})[seat] = board
        self._bump(code, 1)

    def snapshot(self, code: str) -> dict[str, dict]:
        with self._lock:
            return dict(self.boards.get(code, {}))

    def clear(self, code: str) -> None:
        """A new race (or a closed table): the old boards go."""
        with self._lock:
            self.boards.pop(code, None)
        self._bump(code, 1)

    def listen(self, code: str, event: asyncio.Event) -> None:
        self.loop = asyncio.get_running_loop()
        with self._lock:
            self.waiters.setdefault(code, set()).add(event)

    def unlisten(self, code: str, event: asyncio.Event) -> None:
        with self._lock:
            self.waiters.get(code, set()).discard(event)
            if not self.waiters.get(code):
                self.waiters.pop(code, None)


HUB = Hub()


def racing_seat(conn, game: str, code: str, actor: str) -> str | None:
    """The seat this player races in right now, if they're mid-race and not finished: who may send a live board."""
    row = conn.execute("SELECT state FROM arcade_rooms WHERE code = ? AND game = ?", (code, game)).fetchone()
    if row is None:
        return None
    state = json.loads(row["state"])
    seat = _seat_of(state, actor)
    race = state["race"]
    if seat is None or race is None or state["phase"] != "racing":
        return None
    racer = next((r for r in race["racers"] if r["seat"] == seat["id"]), None)
    return racer["seat"] if racer is not None and racer["seat"] not in race["results"] else None
