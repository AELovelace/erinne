"""Lumi Dungeons with friends: a party of up to three people, each with their own Lumi, on one shared floor.

A party is made, joined by its code, and set out from a lobby once everyone is ready. It plays on its own devices
(online: phones, computers, a TV playing someone's own Lumi) or around one TV (couch: each remote is its holder's own
account and pet, see web/games.py party_tv_input). Both are the same party; only how presses arrive differs.

The floor comes from lumidungeon.generate, so it is the same dungeon a solo trip explores. What is new is who decides:

- Direction and other party choices are VOTES. Each member votes one of the moment's options (a way out, the stairs,
  home, stepping back from a door, or a member to take a puzzle). A majority settles it at once (2 of 3, 2 of 2);
  otherwise VOTE_SECONDS after the first vote the option with the most votes wins, and a tie is settled by the dice.
  Everyone voting with no majority (one each) is settled by the dice at once too.
- A PUZZLE (a vault's crates, lamps or runes, or a room's cups or cards) is taken one member at a time. The party
  votes who goes first; then comes whoever has tried the fewest puzzles this trip (ties by seat order), then the last
  member. That order is fixed for this puzzle: if everyone fails, it starts again from the first. Each attempt has a
  limit (LIMITS: time for crates and lamps, guesses for runes, flips for cards), and "Pass it on" ends it early. Only
  the holder's moves count; everyone else watches their board live. Cups are one pick by the chosen member.
- In a FIGHT everyone fights their own copy of the creature at once (each in their own lumi_battles row, tagged with
  the party). The room is cleared once everyone has finished and someone won. A member who loses FAINTS: their own
  satchel is dropped and they follow the party as a ghost, still voting and taking puzzle turns, but finding nothing.
  If everyone faints, the trip is over.

Each member's finds go in their own satchel (loot and decor are rolled against their own pet), and only count once the
party walks out of the entrance together. Coins are paid once per member, by "lumi-party:<code>:<trip>:<user>". Keys go
on one shared ring. Coins pay the account's own purse, on the couch too: this is each pet's progress, not a table.

Like lumidungeon, the page is told only rooms the party has entered and never a puzzle's answer; it sends choices and
moves, which are replayed here. The party is one JSON document in lumi_parties (migration 0073); who is still there
lives in memory (lobby.PRESENCE), and streams wait on HUB. Times are milliseconds (lumipet.now_ms).
"""

from __future__ import annotations

import json
import random
import sqlite3
from collections import Counter

from mnemerynne import coins, lobby, lumibattle, lumidungeon, showdown

GAME = "lumi-pet"
MAX_MEMBERS = 3
VOTE_SECONDS = 20                       # after the first vote, the most votes win
LIMITS = {"sokoban": ("seconds", 180), "lights": ("seconds", 120), "runes": ("guesses", 3), "match": ("flips", 6)}
HOLDER_QUIET = 30                       # seconds a puzzle holder may go unheard before their attempt fails
FIGHT_QUIET = 60                        # seconds a fighter may go unheard before their fight counts as run from
AWAY_SECONDS = 8                        # unheard this long: shown as away
IDLE_MS = 2 * 3600 * 1000               # a party nobody has changed in this long breaks up
MAX_INPUTS = 64                         # couch presses remembered, so a replayed press does nothing
LOG = 12
WAYS = ("n", "e", "s", "w")
WORDS = {"n": "north", "e": "east", "s": "south", "w": "west", "descend": "the stairs down", "home": "home",
         "back": "a step back from the door"}
PUZZLE_NAMES = {"sokoban": "crates", "lights": "lamps", "runes": "runes", "match": "cards", "cups": "cups"}
HUB = showdown.Hub()                    # streams wait here for a party's change (wake), from any thread


class PartyError(ValueError):
    """Refused; the message is safe to show."""

    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


# --------------------------------------------------------------------------- saved
def _table(code: str) -> tuple:
    return ("lumi-party", code)                           # the key lobby.PRESENCE files this party's members under


def _actor(member: dict, state: dict) -> str:
    """Who to ask lobby.PRESENCE about: the member's own page, or for a couch member the TV they sit at."""
    return state["display"] if member.get("remote") is not None and state.get("display") else f"user:{member['user']}"


def _quiet(state: dict, member: dict) -> float:
    return lobby.PRESENCE.quiet_for(_table(state["code"]), _actor(member, state))


def _read(conn: sqlite3.Connection, code: str) -> dict:
    row = conn.execute("SELECT state FROM lumi_parties WHERE code = ?", ((code or "").upper()[:16],)).fetchone()
    if row is None:
        raise PartyError("This party has broken up. Start a new one, or join another.", 404)
    return json.loads(row["state"])


def _write(conn: sqlite3.Connection, state: dict, now: int) -> None:
    if state.get("run"):
        state["run"]["log"] = state["run"]["log"][-LOG:]
    conn.execute("UPDATE lumi_parties SET state = ?, updated_at = ? WHERE code = ?",
                 (json.dumps(state, separators=(",", ":")), now / 1000, state["code"]))


def code_of(conn: sqlite3.Connection, user_id: int) -> str | None:
    """The party this person is in, if any."""
    row = conn.execute("SELECT code FROM lumi_party_members WHERE user_id = ?", (user_id,)).fetchone()
    return row["code"] if row else None


def mine(conn: sqlite3.Connection, user_id: int) -> dict | None:
    """The party this person is in, as saved (None if they aren't in one)."""
    code = code_of(conn, user_id)
    if code is None:
        return None
    try:
        return _read(conn, code)
    except PartyError:                                    # a stale row: the party is gone
        conn.execute("DELETE FROM lumi_party_members WHERE user_id = ?", (user_id,))
        return None


def on_trip(conn: sqlite3.Connection, user_id: int) -> dict | None:
    """The party, if this person is out on a party trip right now (their Lumi is in the party's dungeon)."""
    state = mine(conn, user_id)
    return state if state and state["phase"] == "trip" else None


def member_of(state: dict, user_id: int | None) -> dict | None:
    return next((m for m in state["members"] if m["user"] == user_id), None)


def _bag() -> dict:
    return {"coins": 0, "potions": 0, "trophies": [], "mods": []}


def _ms() -> int:
    from mnemerynne import lumipet                        # imported late: lumipet imports this module
    return lumipet.now_ms()


# --------------------------------------------------------------------------- joining
def _pet(conn: sqlite3.Connection, user_id: int, now: int) -> dict:
    from mnemerynne import lumipet
    return lumipet.load(conn, user_id, now)


def _save_pet(conn: sqlite3.Connection, user_id: int, pet: dict) -> None:
    from mnemerynne import lumipet
    lumipet._save(conn, user_id, pet)


def _can_go(conn: sqlite3.Connection, user_id: int, pet: dict, now: int, code: str | None = None) -> None:
    """Refuse if this person's Lumi can't join a party: in another party, on a trip of her own, in a fight, or
    napping off a lost fight."""
    other = code_of(conn, user_id)
    if other is not None and other != code:
        raise PartyError("You're already in a party. Leave it first.")
    run = lumidungeon.current(conn, user_id)
    if run and not run["over"]:
        raise PartyError("Your Lumi is out exploring on her own. Bring her home first.")
    if lumibattle.current(conn, user_id):
        raise PartyError("Your Lumi is in the middle of a fight. Finish it first.")
    if pet["faint_until"] > now:
        raise PartyError("Your Lumi is still napping off her last fight.")


def _member(conn: sqlite3.Connection, user_id: int, name: str, pet: dict, remote: int | None) -> dict:
    """A new member, with their Lumi's numbers as she is now (refreshed again when the party sets out)."""
    numbers = lumibattle.stats(pet)
    return {"user": user_id, "name": name, "remote": remote, "ready": False, "level": pet["level"], "glow": pet["glow"],
            "hp": lumibattle.health(pet), "hpMax": numbers["hp"], "mp": numbers["mp"], "mpMax": numbers["mp"],
            "owned": list(pet["decor"]), "mods": list(pet["mods_owned"]),     # what loot rolls must not give twice
            "satchel": _bag(), "fight": None, "fought": None, "fainted": False, "puzzles": 0, "vote": None, "ended": None}


def create(conn: sqlite3.Connection, user_id: int | None, name: str, now: int, *, mode: str = "online",
           display: str | None = None, screen: str = "") -> dict:
    """A new party. Online: the creator is its first member and host. Couch: the TV (display) owns it, and the first
    remote to press OK becomes the host. Call inside a transaction."""
    code = lobby.new_code(conn, "lumi_parties")
    state = {"code": code, "mode": mode, "display": display, "screen": screen, "host": None, "phase": "lobby",
             "env": None, "members": [], "run": None, "last": None, "revision": 1, "inputs": [], "changed": now}
    conn.execute("INSERT INTO lumi_parties(code, state, updated_at) VALUES (?, ?, ?)", (code, json.dumps(state), now / 1000))
    if user_id is not None:
        state = _join(conn, state, user_id, name, now, None)
        _write(conn, state, now)
    return state


def _join(conn: sqlite3.Connection, state: dict, user_id: int, name: str, now: int, remote: int | None) -> dict:
    if member_of(state, user_id):
        return state                                      # already here (another device, or a second remote)
    if state["phase"] != "lobby":
        raise PartyError("This party has already set out. Catch them next time!")
    if len(state["members"]) >= MAX_MEMBERS:
        raise PartyError(f"A party is at most {MAX_MEMBERS}. This one is full.")
    pet = _pet(conn, user_id, now)
    _can_go(conn, user_id, pet, now, state["code"])
    state["members"].append(_member(conn, user_id, name, pet, remote))
    conn.execute("INSERT OR REPLACE INTO lumi_party_members(user_id, code) VALUES (?, ?)", (user_id, state["code"]))
    if state["host"] is None:
        state["host"] = user_id
    _note(state, f"{name} joins the party.")
    return state


def _leave(conn: sqlite3.Connection, state: dict, member: dict, now: int) -> None:
    """A member goes. On a trip that's giving up: their satchel stays behind, and their bruises go home with them. If
    they were taking a puzzle, the next one in the circle carries on."""
    run = state["run"] if state["phase"] == "trip" else None
    handed_on = False
    if run:
        pet = _pet(conn, member["user"], now)
        if member["fight"]:
            _end_fight_row(conn, member["user"], member["fight"])
        lumibattle.hurt(pet, 0 if member["fainted"] else member["hp"])
        _save_pet(conn, member["user"], pet)
        puzzle = run["puzzle"]
        if puzzle and member["user"] in puzzle["order"]:
            at, was_holder = puzzle["order"].index(member["user"]), holder(state) == member["user"]
            puzzle["order"].remove(member["user"])
            if puzzle["order"] and puzzle["phase"] == "attempt":
                if at < puzzle["turn"]:
                    puzzle["turn"] -= 1                   # the circle closes up behind them
                elif was_holder:
                    puzzle["turn"] = at % len(puzzle["order"])
                    handed_on = True                      # the next one in the circle carries on
    state["members"].remove(member)
    conn.execute("DELETE FROM lumi_party_members WHERE user_id = ?", (member["user"],))
    if handed_on:
        _begin_attempt(state, now)
        _note(state, f"{member['name']} has gone home. " + _turn_words(state))
    _after_leave(conn, state, member, now)


def _after_leave(conn: sqlite3.Connection, state: dict, member: dict, now: int) -> None:
    _note(state, f"{member['name']} heads home.")
    if state["host"] == member["user"]:
        state["host"] = state["members"][0]["user"] if state["members"] else None
    if state["phase"] == "trip" and state["run"]:
        if not state["members"]:
            state["phase"], state["run"] = "lobby", None
        else:
            _settle_fights(conn, state, now, random.SystemRandom())   # their leaving may finish a fight room


def _end_fight_row(conn: sqlite3.Connection, user_id: int, battle_id: int) -> None:
    """Close a member's fight as run from (they left, or went quiet for FIGHT_QUIET)."""
    conn.execute("UPDATE lumi_battles SET status = 'ran' WHERE id = ? AND user_id = ? AND status = 'active'", (battle_id, user_id))


# --------------------------------------------------------------------------- the lobby
def _open_envs(state: dict) -> list[dict]:
    """Where this party may go: what its lowest-levelled Lumi has opened."""
    level = min((m["level"] for m in state["members"]), default=1)
    return [{**{k: env[k] for k in ("id", "name", "family", "about", "opens", "palette")}, "open": level >= env["opens"]}
            for env in lumidungeon.CATALOG["environments"]]


def _set_out(conn: sqlite3.Connection, state: dict, now: int, rng) -> None:
    """Everyone is ready: make the floor and go. Each Lumi sets out as hurt as she is."""
    if len(state["members"]) < 2:
        raise PartyError("A party needs at least two. Share the code: " + state["code"] + ".")
    if not all(m["ready"] for m in state["members"]):
        raise PartyError("Not everyone is ready yet.")
    envs = [e for e in _open_envs(state) if e["open"]]
    env = state["env"] if any(e["id"] == state["env"] for e in envs) else envs[0]["id"]
    for m in state["members"]:
        pet = _pet(conn, m["user"], now)
        if pet["task"]:
            raise PartyError(f"{m['name']}'s Lumi is busy {'napping' if pet['task']['kind'] == 'nap' else 'playing'}.")
        _can_go(conn, m["user"], pet, now, state["code"])
        fresh = _member(conn, m["user"], m["name"], pet, m["remote"])
        m.update({k: fresh[k] for k in ("level", "glow", "hp", "hpMax", "mp", "mpMax", "owned", "mods")},
                 satchel=_bag(), fight=None, fought=None, fainted=False, puzzles=0, vote=None, ended=None)
        pet["rest"], pet["restUntil"] = "awake", now + 15 * 60_000     # a trip wakes her (lumipet.AWAKE_MS)
        _save_pet(conn, m["user"], pet)
    floor = lumidungeon.generate(env, 1, rng, {"decor": [], "mods_owned": []})
    state["env"], state["phase"], state["last"] = env, "trip", None
    state["run"] = {"id": now, "env": env, "depth": 1, "rooms": floor["rooms"], "at": floor["start"], "prev": None,
                    "facing": None, "keys": 0, "tales": {}, "log": [], "event": {"scenario": "enter"}, "say": "",
                    "scene": "", "over": None, "ended": None, "vote_since": None, "puzzle": None, "fighting": False}
    _narrate(state, rng)


# --------------------------------------------------------------------------- votes
def options(state: dict) -> list[str]:
    """What the party may vote for right now."""
    run = state["run"]
    if state["phase"] != "trip" or run is None or run["fighting"]:
        return []
    room, puzzle, out = run["rooms"][run["at"]], run["puzzle"], []
    if run["facing"]:
        out.append("back")
    else:
        out += [way for way in room["exits"]]
        if room["kind"] == "stairs":
            out.append("descend")
        if room["kind"] == "start":
            out.append("home")
    if puzzle and puzzle["phase"] == "pick":
        out += [f"m{m['user']}" for m in state["members"]]
    return out


def _vote(state: dict, member: dict, choice: str, now: int, rng) -> None:
    if choice not in options(state):
        raise PartyError("That isn't one of the choices right now.")
    member["vote"] = None if member["vote"] == choice else choice          # voting again takes it back
    run = state["run"]
    if run["vote_since"] is None and member["vote"] is not None:
        run["vote_since"] = now
    if not any(m["vote"] for m in state["members"]):
        run["vote_since"] = None


def _tally(conn: sqlite3.Connection, state: dict, now: int, rng, force: bool = False) -> bool:
    """Settle the vote if it is settled: a majority, everyone voted, or the time is up. True if it was."""
    run = state["run"]
    allowed = options(state)
    votes = {m["user"]: m["vote"] for m in state["members"] if m["vote"] in allowed}
    if not votes:
        run["vote_since"] = None
        return False
    counts = Counter(votes.values())
    top = max(counts.values())
    leaders = sorted(choice for choice, n in counts.items() if n == top)
    everyone = len(votes) == len(state["members"])
    late = run["vote_since"] is not None and now - run["vote_since"] >= VOTE_SECONDS * 1000
    if not (top * 2 > len(state["members"]) or everyone or late or force):
        return False
    dice = False
    if len(leaders) == 1:
        choice = leaders[0]
    elif all(c.startswith("m") for c in leaders):     # a tie between members: the one who has tried fewest puzzles
        seats = {m["user"]: i for i, m in enumerate(state["members"])}
        choice = min(leaders, key=lambda c: (member_of(state, int(c[1:]))["puzzles"], seats[int(c[1:])]))
    else:
        choice, dice = leaders[rng.randrange(len(leaders))], True
    for m in state["members"]:
        m["vote"] = None
    run["vote_since"] = None
    said = _choice_words(state, choice)
    _resolve(conn, state, choice, now, rng, ("The dice choose " if dice else "The party chooses ") + said + ".")
    return True


def _choice_words(state: dict, choice: str) -> str:
    if choice.startswith("m"):
        found = member_of(state, int(choice[1:]))
        return (found["name"] if found else "someone") + " to go first"
    return WORDS.get(choice, choice)


def _resolve(conn: sqlite3.Connection, state: dict, choice: str, now: int, rng, said: str) -> None:
    run = state["run"]
    if choice in WAYS:
        _move(conn, state, choice, now, rng)
    elif choice == "back":
        run["facing"], run["puzzle"] = None, None
        run["event"] = {"scenario": "again"}
    elif choice == "descend":
        floor = lumidungeon.generate(run["env"], run["depth"] + 1, rng, {"decor": [], "mods_owned": []})
        run.update(depth=run["depth"] + 1, rooms=floor["rooms"], at=floor["start"], prev=None, facing=None, tales={},
                   keys=0, puzzle=None, event={"scenario": "descend"})    # a key only fits the floor it was found on
    elif choice == "home":
        _bank(conn, state, now)
        _note(state, said)
        return
    elif choice.startswith("m"):
        _pick(state, int(choice[1:]), now)
        run["event"] = {"scenario": "party_turn"}
        _narrate(state, rng, said + " " + _turn_words(state))
        return
    _narrate(state, rng, said)


# --------------------------------------------------------------------------- walking
def _move(conn: sqlite3.Connection, state: dict, way: str, now: int, rng) -> None:
    run = state["run"]
    room = run["rooms"][run["at"]]
    if way not in room["exits"]:
        raise PartyError("There's no way through there.")
    to = lumidungeon._step(run["at"], way)
    target = run["rooms"][to]
    run["puzzle"] = None                                  # walking away from a game leaves it for later
    if target.get("lock") and not target["done"]:
        run["facing"] = to
        kind = target["lock"]["kind"]
        run["event"] = {"scenario": "locked_key" if kind == "key" else "locked_puzzle", "lock": kind}
        if kind != "key":
            run["puzzle"] = _new_puzzle(to, kind, door=True)
    else:
        _enter(conn, state, to, now, rng)


def _new_puzzle(at: str, kind: str, door: bool) -> dict:
    return {"at": at, "kind": kind, "door": door, "phase": "pick", "order": [], "turn": 0, "started": None,
            "used": 0, "moves": "", "presses": []}


def _enter(conn: sqlite3.Connection, state: dict, to: str, now: int, rng) -> None:
    """The whole party steps into a room and it happens: a creature to fight, something to pick up, a game."""
    run = state["run"]
    run["prev"], run["at"], run["facing"], run["puzzle"] = run["at"], to, None, None
    room = run["rooms"][to]
    first, room["seen"] = not room["seen"], True
    if room["kind"] == "enemy" and not room["done"]:
        _start_fights(conn, state, room, now, rng)
    elif room["kind"] in ("treasure", "key", "vault") and not room["done"]:
        run["event"] = {"scenario": "see_" + room["kind"]}
    elif room["kind"] == "game" and not room["done"]:
        run["event"] = {"scenario": "game_" + room["game"]["kind"]}
        run["puzzle"] = _new_puzzle(to, room["game"]["kind"], door=False)
    else:
        run["event"] = {"scenario": {"stairs": "stairs", "start": "start"}.get(room["kind"], "empty" if first else "again")}


# --------------------------------------------------------------------------- fights
def _start_fights(conn: sqlite3.Connection, state: dict, room: dict, now: int, rng) -> None:
    """Everyone still standing meets their own copy of the creature, at the party's level."""
    run = state["run"]
    entry = lumibattle._ENEMIES[room["enemy"]["id"]]
    standing = [m for m in state["members"] if not m["fainted"]]
    level = round(sum(m["level"] for m in standing) / max(1, len(standing)))
    level = max(1, min(lumibattle.MAX_LEVEL, level + rng.randrange(3) - 1 + (run["depth"] - 1)))
    for m in standing:
        pet = _pet(conn, m["user"], now)
        try:
            fight = lumibattle.start(conn, m["user"], pet, entry["tier"], now, rng, enemy=entry["id"], level=level,
                                     carry={"hp": m["hp"], "mp": m["mp"]}, dungeon=True, party=state["code"])
        except lumibattle.BattleError:
            m["fought"] = "ran"                           # she can't fight right now (a fight elsewhere): she hangs back
            continue
        _save_pet(conn, m["user"], pet)
        m["fight"], m["fought"] = fight["id"], None
    run["fighting"] = any(m["fight"] for m in state["members"])
    run["event"] = {"scenario": "enemy", "enemy": entry["name"]}
    if not run["fighting"]:
        _settle_fights(conn, state, now, rng)


def after_fight(conn: sqlite3.Connection, user_id: int, pet: dict, fight: dict, now: int | None = None, rng=None) -> None:
    """A member's party fight just ended (lumipet.fight_act calls this). Their numbers carry on; a win fills their
    satchel, and may drop a piece of the place's decor set for them; a defeat makes them a ghost. Once everyone has
    finished, the room is settled. Saves the party; the caller saves the pet."""
    rng = rng or random.SystemRandom()
    now = _ms() if now is None else now
    try:
        state = _read(conn, fight.get("party", ""))
    except PartyError:
        return
    member = member_of(state, user_id)
    if member is None or member["fight"] != fight["id"] or not state["run"]:
        return
    run, lumi = state["run"], fight["player"]
    member["fight"], member["hp"], member["mp"] = None, max(1, lumi["hp"]), lumi["mp"]
    if fight["status"] == "victory":
        member["satchel"]["coins"] += fight["reward"]["coins"]
        member["fought"] = "victory"
        piece = lumidungeon._set_piece(lumidungeon._ENVS[run["env"]], "enemy", run["depth"], rng, _owned(member))
        if piece:
            member["satchel"]["trophies"].append(piece)
            _note(state, f"{member['name']} finds the {lumidungeon.FOUND_NAMES.get(piece, 'treasure')}!")
    elif fight["status"] == "ran":
        member["fought"] = "ran"
    else:
        _faint(pet, member, state)
    _settle_fights(conn, state, now, rng)
    _changed(conn, state, now)


def _faint(pet: dict | None, member: dict, state: dict) -> None:
    """Beaten: she drops her satchel and follows the party as a ghost."""
    if pet is not None:
        lumibattle.hurt(pet, 0)
    member.update(fought="defeat", fainted=True, hp=0, satchel=_bag())
    _note(state, f"{member['name']}'s Lumi faints, and drops everything she found. She follows along as a little ghost.")


def _settle_fights(conn: sqlite3.Connection, state: dict, now: int, rng) -> None:
    """Once nobody is still fighting: the room is cleared if anyone won; if everyone ran (or fainted) the party steps
    back; if everyone has fainted the trip is over."""
    run = state["run"]
    if not run or not run["fighting"] and run["event"].get("scenario") != "enemy":
        return
    if any(m["fight"] for m in state["members"]):
        return
    run["fighting"] = False
    enemy = run["event"].get("enemy", "creature")
    won = [m for m in state["members"] if m["fought"] == "victory"]
    for m in state["members"]:
        m["fought"] = None
    if all(m["fainted"] for m in state["members"]):
        run.update(over="defeat", event={"scenario": "defeat", "enemy": enemy})
        _narrate(state, rng)
        _finish(conn, state, now, "defeat")
        return
    if won:
        run["rooms"][run["at"]]["done"] = True
        run["rooms"][run["at"]].pop("loot", None)          # drops were rolled for each winner instead
        run["event"] = {"scenario": "victory", "enemy": enemy}
        _narrate(state, rng, " and ".join(m["name"] for m in won) + (" wins!" if len(won) == 1 else " win!"))
    else:
        run["at"], run["prev"] = run["prev"] or run["at"], run["at"]
        run["event"] = {"scenario": "fled", "enemy": enemy}
        _narrate(state, rng)


# --------------------------------------------------------------------------- puzzles
def _pick(state: dict, chosen: int, now: int) -> None:
    """The party chose who goes first: then whoever has tried fewest puzzles (ties by seat), then the last. Fixed until
    the puzzle is done."""
    seats = {m["user"]: i for i, m in enumerate(state["members"])}
    rest = sorted((m for m in state["members"] if m["user"] != chosen), key=lambda m: (m["puzzles"], seats[m["user"]]))
    puzzle = state["run"]["puzzle"]
    puzzle.update(order=[chosen] + [m["user"] for m in rest], turn=0)
    _begin_attempt(state, now)


def _begin_attempt(state: dict, now: int) -> None:
    puzzle = state["run"]["puzzle"]
    holder = member_of(state, puzzle["order"][puzzle["turn"]])
    holder["puzzles"] += 1                                # this is "tried a puzzle", for the next order
    puzzle.update(phase="attempt", started=now, used=0, moves="", presses=[])


def _turn_words(state: dict) -> str:
    puzzle = state["run"]["puzzle"]
    holder = member_of(state, puzzle["order"][puzzle["turn"]])
    kind, limit = puzzle["kind"], LIMITS.get(puzzle["kind"])
    how = f" ({limit[1] // 60} minutes)" if limit and limit[0] == "seconds" else f" ({limit[1]} {limit[0]})" if limit else ""
    return f"{holder['name']}'s turn at the {PUZZLE_NAMES[kind]}{how}."


def _fail(state: dict, now: int, why: str) -> None:
    """The holder's attempt is over: the next one in the circle tries, and after the last, the first again."""
    puzzle = state["run"]["puzzle"]
    if not puzzle or not puzzle["order"]:
        return
    puzzle["turn"] = (puzzle["turn"] + 1) % len(puzzle["order"])
    _begin_attempt(state, now)
    _note(state, why + " " + _turn_words(state))


def holder(state: dict) -> int | None:
    """Who is working the puzzle right now."""
    puzzle = state["run"]["puzzle"] if state.get("run") else None
    if not puzzle or puzzle["phase"] != "attempt" or not puzzle["order"]:
        return None
    return puzzle["order"][puzzle["turn"]]


def _sokoban_progress(rows: list[str], moves: str) -> tuple[bool, bool]:
    """(every move legal, every crate home), replaying the attempt so far."""
    walls, goals, boxes, (x, y) = lumidungeon.sokoban_parse(rows)
    held, step = set(boxes), {"u": (0, -1), "d": (0, 1), "l": (-1, 0), "r": (1, 0)}
    for move in moves:
        if move not in step:
            return False, False
        dx, dy = step[move]
        to = (x + dx, y + dy)
        if to in walls:
            return False, False
        if to in held:
            beyond = (to[0] + dx, to[1] + dy)
            if beyond in walls or beyond in held:
                return False, False
            held.remove(to)
            held.add(beyond)
        x, y = to
    return True, held == goals


def _puzzle_act(conn: sqlite3.Connection, state: dict, member: dict, action: str, now: int, rng, moves: str,
                picks: list) -> None:
    """The holder's move at the puzzle: crates and lamps as they go (watched live), a rune guess, a cup, a card, or
    passing it on."""
    run, puzzle = state["run"], state["run"]["puzzle"]
    if not puzzle or puzzle["phase"] != "attempt":
        raise PartyError("Nobody is at a puzzle right now. Vote who goes first.")
    if holder(state) != member["user"]:
        raise PartyError(f"It's {member_of(state, holder(state))['name']}'s turn.")
    if action == "pass":
        _fail(state, now, f"{member['name']} passes it on.")
        return
    room = run["rooms"][puzzle["at"]]
    if puzzle["door"]:
        lock = room["lock"]
        if action == "sokoban" and lock["kind"] == "sokoban":
            level = next(level for level in lumidungeon.SOKOBAN if level["id"] == lock["level"])
            legal, solved = _sokoban_progress(level["rows"], moves[:lumidungeon.MAX_MOVES])
            if not legal:
                raise PartyError("That crate won't move that way.")
            puzzle["moves"] = moves
            if solved:
                _open_door(conn, state, member, now, rng)
        elif action == "lights" and lock["kind"] == "lights":
            if len(picks) > 200 or any(not isinstance(c, int) or not 0 <= c < lock["size"] ** 2 for c in picks):
                raise PartyError("That lamp isn't on the board.")
            puzzle["presses"] = list(picks)
            if lumidungeon.lights_solved(lock["board"], lock["size"], picks):
                _open_door(conn, state, member, now, rng)
        elif action == "rune" and lock["kind"] == "runes":
            if len(picks) != lumidungeon.RUNE_SLOTS or any(r not in lumidungeon.RUNES for r in picks):
                raise PartyError(f"Choose {lumidungeon.RUNE_SLOTS} runes.")
            marks = lumidungeon.rune_marks(picks, lock["code"])
            exact, near = marks.count("right"), marks.count("near")
            lock["tries"].append({"guess": list(picks), "exact": exact, "near": near, "marks": marks, "by": member["name"]})
            puzzle["used"] += 1
            if exact == lumidungeon.RUNE_SLOTS:
                _open_door(conn, state, member, now, rng)
                return
            if len(lock["tries"]) >= lumidungeon.RUNE_TRIES:          # the door thinks of a new code
                lock["code"] = [lumidungeon.RUNES[rng.randrange(len(lumidungeon.RUNES))] for _ in range(lumidungeon.RUNE_SLOTS)]
                lock["tries"] = []
                run["event"] = {"scenario": "runes_reset"}
            else:
                run["event"] = {"scenario": "runes_try", "exact": exact, "near": near}
            _narrate(state, rng)
            if puzzle["used"] >= LIMITS["runes"][1]:
                _fail(state, now, f"That was {member['name']}'s last guess.")
        else:
            raise PartyError("That isn't how this door opens.")
        return
    game = room["game"]
    pick = picks[0] if len(picks) == 1 and isinstance(picks[0], int) else -1
    if action == "cup" and game["kind"] == "cups":
        if not 0 <= pick < lumidungeon.CUPS:
            raise PartyError("Choose a cup.")
        won = pick == game["prize"]
        room["done"], run["puzzle"] = True, None
        paid = _share_prize(state, run, rng, 2.5 if won else 0.3)
        run["event"] = {"scenario": "game_win" if won else "game_lose", "coins": paid, "prize": game["prize"]}
        _narrate(state, rng, f"{member['name']} lifts cup {pick + 1}.")
    elif action == "flip" and game["kind"] == "match":
        if not 0 <= pick < len(game["deck"]) or pick in game["found"] or pick == game["open"]:
            raise PartyError("Choose a card that's face down.")
        game["flips"] += 1
        puzzle["used"] += 1
        if game["open"] is None:
            game["open"], game["last"] = pick, None
            run["event"] = {"scenario": "match_flip"}
        else:
            first, game["open"] = game["open"], None
            game["last"] = [first, pick]
            pair = game["deck"][first] == game["deck"][pick]
            if pair:
                game["found"] += [first, pick]
            run["event"] = {"scenario": "match_pair" if pair else "match_miss"}
        if len(game["found"]) == len(game["deck"]) or game["flips"] >= lumidungeon.FLIPS:
            room["done"], run["puzzle"] = True, None
            pairs = len(game["found"]) // 2
            paid = _share_prize(state, run, rng, 0.3 + 0.7 * pairs)
            run["event"] = {"scenario": "game_win" if pairs == lumidungeon.PAIRS else "game_lose", "coins": paid, "pairs": pairs}
            _narrate(state, rng)
            return
        _narrate(state, rng)
        if game["open"] is None and puzzle["used"] >= LIMITS["match"][1]:           # a turn ends after a whole pair
            _fail(state, now, f"{member['name']} has used their flips.")
    else:
        raise PartyError("There's no game like that here.")


def _share_prize(state: dict, run: dict, rng, scale: float) -> int:
    """A little game's prize: the same coins for everyone still standing."""
    won = lumidungeon._coins(run["depth"], rng, scale)
    for m in state["members"]:
        if not m["fainted"]:
            m["satchel"]["coins"] += won
    return won


def _open_door(conn: sqlite3.Connection, state: dict, member: dict, now: int, rng) -> None:
    """Solved: the door opens and everyone walks through."""
    run = state["run"]
    door = run["rooms"][run["facing"]]
    door["lock"] = {"kind": door["lock"]["kind"]}         # the puzzle is spent; forget its answer
    _enter(conn, state, run["facing"], now, rng)
    _narrate(state, rng, f"{member['name']} opens the door!")


# --------------------------------------------------------------------------- picking things up, and going home
def _owned(member: dict) -> set:
    return set(member["owned"]) | set(member["satchel"]["trophies"])


def _take(state: dict, now: int, rng) -> None:
    """Open the chest, take the key or the vault: everyone still standing gets their own share."""
    run = state["run"]
    room = run["rooms"][run["at"]]
    if room["kind"] not in ("treasure", "key", "vault") or room["done"]:
        raise PartyError("There's nothing here to pick up.")
    room["done"] = True
    env = lumidungeon._ENVS[run["env"]]
    loot = room.get("loot", {})
    if room["kind"] == "key":
        run["keys"] += 1
        run["event"] = {"scenario": "key", "loot": {"keys": 1}}
        return
    shared, told = {"coins": loot.get("coins", 0), "potions": loot.get("potions", 0)}, None
    for m in state["members"]:
        if m["fainted"]:
            continue                                      # a ghost finds nothing
        bag = m["satchel"]
        if room["kind"] == "vault":                       # each their own vault: coins, a trophy they lack, maybe a mod
            mine = lumidungeon._vault_loot(env, run["depth"], rng, {"decor": list(_owned(m)), "mods_owned": m["mods"]},
                                           {"trophies": [], "mods": bag["mods"]})
        else:
            mine = dict(shared)
            piece = lumidungeon._set_piece(env, "treasure", run["depth"], rng, _owned(m))
            if piece:
                mine["trophy"] = piece
        told = told or mine                              # the story tells the first share; everyone has their own
        bag["coins"] += mine.get("coins", 0)
        bag["potions"] += mine.get("potions", 0)
        if mine.get("trophy"):
            bag["trophies"].append(mine["trophy"])
            _note(state, f"{m['name']} finds the {lumidungeon.FOUND_NAMES.get(mine['trophy'], 'treasure')}!")
        if mine.get("mod"):
            bag["mods"].append(mine["mod"])
            _note(state, f"{m['name']} finds a {lumibattle._MODS[mine['mod']]['name']} mod!")
    run["event"] = {"scenario": room["kind"], "loot": told or shared}


def _bank(conn: sqlite3.Connection, state: dict, now: int) -> None:
    """Out of the entrance together: each satchel goes home to its own Lumi. Coins are paid once per member."""
    run = state["run"]
    for m in state["members"]:
        pet = _pet(conn, m["user"], now)
        if not m["fainted"]:
            lumibattle.hurt(pet, m["hp"])                  # her bruises come home with her, to mend there
        bag = m["satchel"]
        ref = f"lumi-party:{state['code']}:{run['id']}:{m['user']}"
        paid = coins.prize(conn, m["user"], bag["coins"], GAME, ref) if bag["coins"] else 0
        kept = min(bag["potions"], max(0, lumibattle.POTION_CAP - pet["potions"]))
        pet["potions"] += kept
        pet["decor"] += [t for t in bag["trophies"] if t not in pet["decor"]]
        pet["mods_owned"] += [x for x in bag["mods"] if x not in pet["mods_owned"]]
        _save_pet(conn, m["user"], pet)
        m["ended"] = {"coins": paid, "potions": kept, "trophies": bag["trophies"], "mods": bag["mods"], "fainted": m["fainted"]}
    run.update(over="home", event={"scenario": "home"})
    _narrate(state, random.SystemRandom())
    _finish(conn, state, now, "home")


def _finish(conn: sqlite3.Connection, state: dict, now: int, how: str) -> None:
    """The trip is over: back to the lobby together, with how it went kept to read."""
    run = state["run"]
    state["last"] = {"how": how, "depth": run["depth"], "env": run["env"], "say": run.get("say", ""),
                     "members": [{"user": m["user"], "name": m["name"], "ended": m["ended"] or {"coins": 0, "lost": True}}
                                 for m in state["members"]]}
    state["phase"], state["run"] = "lobby", None
    for m in state["members"]:
        m.update(ready=False, vote=None, fight=None, fought=None, ended=None)


def _abandon(conn: sqlite3.Connection, state: dict, now: int) -> None:
    """The whole party gives up (the TV closed it, or nobody came back): satchels stay behind, bruises go home."""
    if state["phase"] == "trip" and state["run"]:
        for m in list(state["members"]):
            pet = _pet(conn, m["user"], now)
            if m["fight"]:
                _end_fight_row(conn, m["user"], m["fight"])
            lumibattle.hurt(pet, 0 if m["fainted"] else m["hp"])
            _save_pet(conn, m["user"], pet)
        state["run"]["event"] = {"scenario": "abandon"}
        _finish(conn, state, now, "abandoned")


def _potion(conn: sqlite3.Connection, state: dict, member: dict, now: int) -> None:
    if member["fainted"]:
        raise PartyError("A little ghost can't drink a potion. She'll mend at home.")
    if member["fight"]:
        raise PartyError("Drink it in the fight, with the Potion button.")
    if member["hp"] >= member["hpMax"]:
        raise PartyError("Your Lumi is already at full health.")
    if member["satchel"]["potions"] > 0:
        member["satchel"]["potions"] -= 1
    else:
        pet = _pet(conn, member["user"], now)
        if pet["potions"] < 1:
            raise PartyError("Your Lumi has no potions with her.")
        pet["potions"] -= 1
        _save_pet(conn, member["user"], pet)
    member["hp"] = min(member["hpMax"], member["hp"] + max(1, int(member["hpMax"] * lumibattle.POTION_MEND)))
    _note(state, f"{member['name']}'s Lumi drinks a potion.")


def heal(conn: sqlite3.Connection, user_id: int, share: float) -> bool:
    """Bought food eaten on a party trip (lumipet._eat): mends this member's trip health. False if not on one."""
    state = on_trip(conn, user_id)
    member = member_of(state, user_id) if state else None
    if member is None:
        return False
    if not member["fainted"] and not member["fight"]:
        member["hp"] = min(member["hpMax"], member["hp"] + int(member["hpMax"] * share))
        _changed(conn, state, _ms())
    return True


def satchel_potion(conn: sqlite3.Connection, user_id: int, battle_id: int, take: bool) -> bool:
    """A potion found on this party trip, drunk in a fight (lumipet.fight_act): whether one is there for this fight,
    and with take, use it up."""
    state = on_trip(conn, user_id)
    member = member_of(state, user_id) if state else None
    if member is None or member["fight"] != battle_id or member["satchel"]["potions"] < 1:
        return False
    if take:
        member["satchel"]["potions"] -= 1
        _changed(conn, state, _ms())
    return True


# --------------------------------------------------------------------------- the story
def _note(state: dict, words: str) -> None:
    """A party line (who joined, who fainted, whose turn): kept with the story, never handed to a model."""
    run = state.get("run")
    if run is not None:
        run["log"].append(words)
        run["say"] = (run["say"] + " " + words).strip() if run.get("noted") else words   # within one action, lines add up
        run["noted"] = True
    else:
        state.setdefault("notes", [])
        state["notes"] = (state["notes"] + [words])[-6:]


def _narrate(state: dict, rng, before: str = "") -> None:
    """The moment's line from the library (lumitales.py), after a party line if there is one."""
    from mnemerynne import lumitales                      # imported late: it reads the dungeon's catalogs

    run = state["run"]
    if run["event"]["scenario"] == "party_turn":
        told = before
    else:
        told = (before + " " if before else "") + lumitales.line(run, rng)
    run["say"] = (run["say"] + " " + told).strip() if run.get("noted") else told   # after any party line of this action
    run["noted"] = True
    run["scene"] = lumitales.scene(run)
    run["log"].append(told)


# --------------------------------------------------------------------------- time
def _due(state: dict, now: int) -> bool:
    """Whether _advance would change anything, so a look can skip the write lock when it wouldn't."""
    if now - state["changed"] >= IDLE_MS:
        return True
    run = state["run"]
    if state["phase"] != "trip" or not run:
        return False
    if run["vote_since"] is not None and now - run["vote_since"] >= VOTE_SECONDS * 1000:
        return True
    puzzle = run["puzzle"]
    if puzzle and puzzle["phase"] == "attempt":
        limit = LIMITS.get(puzzle["kind"])
        if limit and limit[0] == "seconds" and now - puzzle["started"] >= limit[1] * 1000:
            return True
        who = member_of(state, holder(state))
        if who and _quiet(state, who) >= HOLDER_QUIET:
            return True
    return any(m["fight"] and _quiet(state, m) >= FIGHT_QUIET for m in state["members"])


def _advance(conn: sqlite3.Connection, state: dict, now: int, rng) -> None:
    """Catch the party up on its clocks: a vote's time running out, a puzzle attempt's, a holder or fighter gone quiet."""
    run = state["run"]
    if state["phase"] != "trip" or not run:
        return
    if run["vote_since"] is not None and now - run["vote_since"] >= VOTE_SECONDS * 1000:
        _tally(conn, state, now, rng, force=True)
    run = state["run"]
    puzzle = run["puzzle"] if run else None
    if puzzle and puzzle["phase"] == "attempt":
        limit = LIMITS.get(puzzle["kind"])
        who = member_of(state, holder(state))
        if limit and limit[0] == "seconds" and now - puzzle["started"] >= limit[1] * 1000:
            _fail(state, now, f"Time's up for {who['name'] if who else 'them'}.")
        elif who and _quiet(state, who) >= HOLDER_QUIET:
            _fail(state, now, f"{who['name']} seems to have stepped away.")
    for m in state["members"]:
        if run and m["fight"] and _quiet(state, m) >= FIGHT_QUIET:
            _end_fight_row(conn, m["user"], m["fight"])
            m["fight"], m["fought"] = None, "ran"
            _note(state, f"{m['name']} hangs back from the fight.")
            _settle_fights(conn, state, now, rng)


def _changed(conn: sqlite3.Connection, state: dict, now: int) -> None:
    state["revision"] += 1
    state["changed"] = now
    _write(conn, state, now)
    HUB.wake(state["code"])


def sweep(conn: sqlite3.Connection, now: int | None = None) -> None:
    """Break up parties nobody has changed in IDLE_MS: a trip still out is given up. Call inside a transaction."""
    now = _ms() if now is None else now
    for row in conn.execute("SELECT code, state FROM lumi_parties WHERE updated_at < ?", ((now - IDLE_MS) / 1000,)).fetchall():
        _close(conn, json.loads(row["state"]), now)


def _close(conn: sqlite3.Connection, state: dict, now: int) -> None:
    _abandon(conn, state, now)
    conn.execute("DELETE FROM lumi_party_members WHERE code = ?", (state["code"],))
    conn.execute("DELETE FROM lumi_parties WHERE code = ?", (state["code"],))
    lobby.PRESENCE.forget(_table(state["code"]))
    HUB.wake(state["code"])


# --------------------------------------------------------------------------- one thing a member does
FREE = ("take", "look", "unlock", "potion")               # anyone may, at once
VOTED = {"move": None, "back": "back", "descend": "descend", "leave": "home"}   # the solo page's buttons, as votes
PUZZLE = ("sokoban", "lights", "rune", "cup", "flip", "pass")


def act(conn: sqlite3.Connection, code: str, user_id: int, action: str, now: int | None = None, rng=None, *,
        name: str = "", way: str | None = None, target: str = "", item: str = "", moves: str = "",
        picks: list | None = None, remote: int | None = None) -> dict:
    """One thing a member (or someone joining) does. Call inside a transaction. Answers the party as they see it.
    Actions: join, leave, ready, env (item), start; vote (target: a way, descend, home, back or "m<user>"), and the
    solo page's own buttons, which mean the same here: move (way) votes that way, back/descend/leave vote, take, look,
    unlock and potion happen at once, and sokoban, lights, rune, cup and flip are the puzzle holder's moves; pass hands
    the puzzle on; abandon is this member giving up the trip."""
    rng = rng or random.SystemRandom()
    now = _ms() if now is None else now
    state = _read(conn, code)
    if state.get("run"):
        state["run"]["noted"] = False                     # this action's lines replace the last action's
    before = json.dumps(state, sort_keys=True)
    _advance(conn, state, now, rng)
    if action == "join":
        _join(conn, state, user_id, name or "Someone", now, remote)
    else:
        member = member_of(state, user_id)
        if member is None:
            raise PartyError("You aren't in this party. Join it first.", 403)
        if remote is not None:
            member["remote"] = remote
        lobby.PRESENCE.seen(_table(state["code"]), _actor(member, state))
        _member_act(conn, state, member, action, now, rng, way=way, target=target, item=item, moves=moves, picks=picks or [])
    if not state["members"]:
        _close(conn, state, now)
        return {"code": state["code"], "closed": True}
    if json.dumps(state, sort_keys=True) != before:
        _changed(conn, state, now)
    return view(conn, state, user_id, now=now)


def _member_act(conn: sqlite3.Connection, state: dict, member: dict, action: str, now: int, rng, *, way, target, item,
                moves, picks) -> None:
    phase, run = state["phase"], state["run"]
    if action == "leave" and phase != "trip":
        _leave(conn, state, member, now)
        return
    if action == "abandon" and phase == "trip":
        _leave(conn, state, member, now)
        return
    if phase == "lobby":
        if action == "ready":
            member["ready"] = not member["ready"]
        elif action == "env":
            if state["host"] != member["user"]:
                raise PartyError("The host chooses where to go.")
            if not any(e["id"] == item and e["open"] for e in _open_envs(state)):
                raise PartyError("Everyone's Lumi needs to have opened that place first.")
            state["env"] = item
        elif action == "start":
            if state["host"] != member["user"]:
                raise PartyError("The host sets out, once everyone is ready.")
            _set_out(conn, state, now, rng)
        else:
            raise PartyError("The party hasn't set out yet.")
        return
    if phase != "trip" or run is None:
        raise PartyError("The party isn't out exploring.")
    if action in VOTED or action == "vote":
        if run["fighting"]:
            raise PartyError("Everyone's still fighting.")
        choice = target if action == "vote" else (way if action == "move" else VOTED[action])
        _vote(state, member, choice or "", now, rng)
        _tally(conn, state, now, rng)
    elif action in FREE:
        if run["fighting"]:
            raise PartyError("Everyone's still fighting.")
        if action == "take":
            if run["facing"]:
                raise PartyError("There's a door in the way.")
            _take(state, now, rng)
            _narrate(state, rng, f"{member['name']} opens it up.")
        elif action == "look":
            room = run["rooms"][run["at"]]
            room["looks"] = room.get("looks", 0) + 1
            run["event"] = {"scenario": "look", "n": room["looks"]}
            _narrate(state, rng)
        elif action == "unlock":
            door = run["rooms"].get(run["facing"] or "")
            if door is None or door["done"] or door["lock"]["kind"] != "key":
                raise PartyError("There's no keyhole in front of the party.")
            if run["keys"] < 1:
                raise PartyError("The party needs a key for this door. There's one somewhere on this floor.")
            run["keys"] -= 1
            _open_door(conn, state, member, now, rng)
        else:
            _potion(conn, state, member, now)
    elif action in PUZZLE:
        if run["fighting"]:
            raise PartyError("Everyone's still fighting.")
        _puzzle_act(conn, state, member, action, now, rng, moves, picks)
    else:
        raise PartyError("Your Lumi doesn't know that one.")


def look(conn: sqlite3.Connection, code: str, user_id: int | None, now: int | None = None, *, display: str | None = None) -> dict:
    """The party as this member (or the couch TV) sees it now, caught up on its clocks (the write lock only when due)."""
    now = _ms() if now is None else now
    state = _read(conn, code)
    if user_id is not None and member_of(state, user_id) is not None:
        lobby.PRESENCE.seen(_table(state["code"]), _actor(member_of(state, user_id), state))
    if display is not None and display == state.get("display"):
        lobby.PRESENCE.seen(_table(state["code"]), display)
    if _due(state, now):
        rng = random.SystemRandom()
        if now - state["changed"] >= IDLE_MS:
            _close(conn, state, now)
            raise PartyError("This party has broken up. Start a new one, or join another.", 404)
        if state.get("run"):
            state["run"]["noted"] = False
        before = json.dumps(state, sort_keys=True)
        _advance(conn, state, now, rng)
        if json.dumps(state, sort_keys=True) != before:
            _changed(conn, state, now)
    return view(conn, state, user_id, now=now, display=display is not None and display == state.get("display"))


def parties(conn: sqlite3.Connection, user_id: int | None, now: int | None = None) -> list[dict]:
    """Parties still in their lobby with room to join, and the caller's own, newest first."""
    out = []
    for row in conn.execute("SELECT state FROM lumi_parties ORDER BY updated_at DESC LIMIT 50").fetchall():
        state = json.loads(row["state"])
        mine_ = member_of(state, user_id) is not None
        if mine_ or (state["phase"] == "lobby" and len(state["members"]) < MAX_MEMBERS):
            host = member_of(state, state["host"])
            out.append({"code": state["code"], "mode": state["mode"], "phase": state["phase"], "mine": mine_,
                        "host": host["name"] if host else state.get("screen") or "", "members": [m["name"] for m in state["members"]]})
    return out


# --------------------------------------------------------------------------- the couch: presses from remotes around one TV
def couch(conn: sqlite3.Connection, code: str, display: str, *, user_id: int, name: str, remote: int, press: str,
          action: str, turn: int = 0, now: int | None = None, rng=None, **fields) -> dict:
    """A remote's press at a couch party: it is its holder's own member (user_id is the remote's account, checked by
    the web layer), and press is the signed press's id, so a replay does nothing. OK from a remote not yet seated
    joins. Fights are played through lumipet.fight_act by the caller. Call inside a transaction."""
    now = _ms() if now is None else now
    state = _read(conn, code)
    if state.get("display") != display:
        raise PartyError("This TV isn't this party's.", 403)
    if press in state["inputs"]:
        return view(conn, state, None, now=now, display=True)           # the same press twice: nothing new
    state["inputs"] = (state["inputs"] + [press])[-MAX_INPUTS:]
    lobby.PRESENCE.seen(_table(state["code"]), display)
    _write(conn, state, now)
    if member_of(state, user_id) is None and action != "join":
        action = "join"                                   # a remote nobody has seated yet: its OK sits it down
    if action == "fight":                                 # a turn in this member's own fight (it ends back in after_fight)
        from mnemerynne import lumipet
        member = member_of(state, user_id)
        if not member["fight"]:
            raise PartyError("Your Lumi isn't fighting right now.")
        try:
            lumipet.fight_act(conn, user_id, member["fight"], turn, fields.get("target", ""), now, rng)
        except lumipet.PetError as refused:
            raise PartyError(str(refused)) from None
        return view(conn, _read(conn, code), None, now=now, display=True)
    out = act(conn, code, user_id, action, now, rng, name=name, remote=remote, **fields)
    if out.get("closed"):
        return out
    return view(conn, _read(conn, code), None, now=now, display=True)


def close(conn: sqlite3.Connection, code: str, display: str, now: int | None = None) -> dict:
    """The couch TV ends its party: a trip still out is given up, and everyone is free again."""
    now = _ms() if now is None else now
    state = _read(conn, code)
    if state.get("display") != display:
        raise PartyError("This TV isn't this party's.", 403)
    _close(conn, state, now)
    return {"code": code, "closed": True}


# --------------------------------------------------------------------------- what a page is told
def _public_puzzle(state: dict, now: int) -> dict | None:
    puzzle = state["run"]["puzzle"]
    if not puzzle:
        return None
    limit = LIMITS.get(puzzle["kind"]) if puzzle["door"] or puzzle["kind"] == "match" else None
    out = {"kind": puzzle["kind"], "phase": puzzle["phase"], "order": puzzle["order"], "turn": puzzle["turn"],
           "holder": holder(state), "door": puzzle["door"], "used": puzzle["used"], "moves": puzzle["moves"],
           "presses": puzzle["presses"], "limit": None, "ends": None}
    if limit:
        out["limit"] = {"kind": limit[0], "count": limit[1]}
        if limit[0] == "seconds" and puzzle["started"]:
            out["ends"] = puzzle["started"] + limit[1] * 1000
    return out


def _run_view(state: dict, me: dict | None, now: int) -> dict:
    """The trip as lumidungeon.view shapes a solo one, so the page draws it the same way: only rooms the party has
    entered, a blank for each doorway seen, never a puzzle's answer. The numbers are this member's own (the couch TV
    sees the party's first standing Lumi), with the party's own parts under "party"."""
    run = state["run"]
    shown = me or next((m for m in state["members"] if not m["fainted"]), state["members"][0])
    rooms = {}
    for key, room in run["rooms"].items():
        if room["seen"]:
            rooms[key] = {"kind": room["kind"], "done": room["done"], "exits": room["exits"]}
            for way in room["exits"]:
                rooms.setdefault(lumidungeon._step(key, way), {"kind": "unknown"})
    here = run["rooms"][run["at"]]
    env = lumidungeon._ENVS[run["env"]]
    out = {"id": run["id"], "env": {k: env[k] for k in ("id", "name", "family", "palette")}, "depth": run["depth"],
           "at": run["at"], "rooms": {k: r for k, r in rooms.items() if run["rooms"][k]["seen"] or r["kind"] == "unknown"},
           "room": {"kind": here["kind"], "done": here["done"], "exits": here["exits"]},
           "satchel": {**shown["satchel"], "keys": run["keys"]}, "hp": shown["hp"], "hpMax": shown["hpMax"],
           "mp": shown["mp"], "mpMax": shown["mpMax"], "over": run["over"], "ended": None, "event": run["event"],
           "say": run.get("say", ""), "scene": run.get("scene", ""), "story": run["log"],
           "fighting": bool(shown["fight"]), "facing": None, "game": None}
    if run["facing"]:
        out["facing"] = {"at": run["facing"], "lock": lumidungeon._public_lock(run["rooms"][run["facing"]]["lock"])}
    if here["kind"] == "game":
        out["game"] = lumidungeon._public_game(here["game"], here["done"])
    out["party"] = {"code": state["code"], "options": options(state), "puzzle": _public_puzzle(state, now),
                    "votes": {str(m["user"]): m["vote"] for m in state["members"] if m["vote"]},
                    "ends": run["vote_since"] + VOTE_SECONDS * 1000 if run["vote_since"] else None,
                    "fighting": run["fighting"], "words": {k: _choice_words(state, k) for k in options(state)}}
    return out


def view(conn: sqlite3.Connection, state: dict, user_id: int | None, *, now: int | None = None, display: bool = False) -> dict:
    """The party for a page: members (no accounts beyond their ids, no remotes), where it is, and the trip."""
    now = _ms() if now is None else now
    me = member_of(state, user_id)
    members = []
    for i, m in enumerate(state["members"]):
        fight = None
        if display and m["fight"]:                        # the couch TV draws everyone's fight side by side
            found = lumibattle.current(conn, m["user"])
            if found:
                fight = {k: found[k] for k in ("id", "turn", "status", "player", "enemy", "log")}
        members.append({"user": m["user"], "name": m["name"], "seat": i, "host": m["user"] == state["host"],
                        "ready": m["ready"], "level": m["level"], "glow": m["glow"], "hp": m["hp"], "hpMax": m["hpMax"],
                        "mp": m["mp"], "mpMax": m["mpMax"], "fainted": m["fainted"], "satchel": m["satchel"],
                        "puzzles": m["puzzles"], "vote": m["vote"], "fighting": bool(m["fight"]),
                        "away": _quiet(state, m) >= AWAY_SECONDS, "you": m is me, "couch": m.get("remote") is not None,
                        "remote": m.get("remote") if display else None, "fight": fight})
    return {"code": state["code"], "mode": state["mode"], "phase": state["phase"], "host": state["host"],
            "env": state["env"], "envs": _open_envs(state) if state["members"] else [], "members": members,
            "you": me["user"] if me else None, "display": display, "screen": state.get("screen", ""),
            "run": _run_view(state, me, now) if state["phase"] == "trip" and state["run"] else None,
            "last": state["last"], "notes": state.get("notes", []), "revision": state["revision"], "server_time": now,
            "rules": {"vote_seconds": VOTE_SECONDS, "limits": {k: {"kind": v[0], "count": v[1]} for k, v in LIMITS.items()},
                      "max": MAX_MEMBERS}}
