"""Lumi Pet's dungeon: a generated floor of rooms explored one step at a time, like an old text adventure.

A run picks one environment (lumipet_data/dungeon.json) and makes a floor of about a dozen rooms on a small grid.
Some rooms are empty, some hold an enemy, some treasure; one is a locked vault, opened with a key found elsewhere on
the floor or by solving a puzzle; one holds a little game. Stairs lead down to a harder floor with better loot, and
the way home is the room she came in by.

What she finds goes in her satchel, and the satchel only counts once she walks out with it: being beaten in a fight
ends the run and empties it. Her health and MP carry from one fight to the next, which is what makes going deeper a
real choice.

The server makes and keeps the whole floor. The page is only ever told about rooms she has entered, and it sends
choices (a direction, a cup, a list of puzzle moves), never results: a Sokoban answer is replayed here against the
level before the door opens.

Like lumibattle, this module never loads or saves a pet: lumipet.py hands it the pet and saves it afterwards.
`rng` is anything with random() and randrange(n).
"""

from __future__ import annotations

import json
import random
import re
import sqlite3
from pathlib import Path

from mnemerynne import coins, lumibattle

GAME = "lumi-pet"
GRID_W, GRID_H = 5, 4                   # the floor's grid; rooms fill about half of it
ROOMS = (11, 13)                        # fewest and most rooms on a floor
DIRS = {"n": (0, -1), "e": (1, 0), "s": (0, 1), "w": (-1, 0)}
BACK = {"n": "s", "s": "n", "e": "w", "w": "e"}
LOCKS = ("key", "sokoban", "lights", "runes")
RUNES = ("sun", "moon", "star", "leaf", "wave")       # the rune lock's symbols
RUNE_SLOTS, RUNE_TRIES = 4, 8
CUPS = 3
PAIRS, FLIPS = 4, 14                    # Constellation Match: pairs to find, and card turns allowed
MAX_MOVES = 600                         # the longest Sokoban answer the server will replay
LOG = 6                                 # lines of what just happened that a run keeps
# How tough the enemies of a floor are: depth -> {tier: weight}. Deeper than the last entry uses the last.
TIERS_BY_DEPTH = ({"common": 70, "uncommon": 30}, {"common": 30, "uncommon": 50, "rare": 20},
                  {"uncommon": 35, "rare": 45, "epic": 20}, {"rare": 40, "epic": 50, "legendary": 10},
                  {"rare": 20, "epic": 55, "legendary": 25})
ENV_BIAS = 70                           # percent of a floor's enemies drawn from its environment's own elements
MOD_CHANCE = (8, 4)                     # percent chance of a mod in a vault: the base, and more per floor down
_ID = re.compile(r"^[a-z0-9-]{1,40}$")
_COLOUR = re.compile(r"^#[0-9a-f]{6}$")


class DungeonError(ValueError):
    """Refused; the message is safe to show."""


# --------------------------------------------------------------------------- catalogs
def load_catalog(path: Path | None = None) -> dict:
    """The environments, checked at start like every other Lumi Pet catalog."""
    data = json.loads((path or Path(__file__).parent / "lumipet_data" / "dungeon.json").read_text(encoding="utf-8"))
    seen: set[str] = set()
    if not isinstance(data.get("environments"), list) or not data["environments"]:
        raise ValueError("Lumi's dungeon needs some environments.")
    for env in data["environments"]:
        if not isinstance(env, dict) or not _ID.match(str(env.get("id", ""))) or env["id"] in seen or not env.get("name") or not env.get("about"):
            raise ValueError("Lumi's dungeon has a bad or repeated environment.")
        seen.add(env["id"])
        palette = env.get("palette")
        if env.get("family") not in ("mystical", "fantasy", "cyberpunk") or not isinstance(env.get("opens"), int) \
                or not 1 <= env["opens"] <= lumibattle.MAX_LEVEL or not isinstance(env.get("elements"), list) or not env["elements"] \
                or any(e not in lumibattle.ELEMENTS + ("none",) for e in env["elements"]) \
                or not isinstance(palette, dict) or set(palette) != {"wall", "floor", "accent", "glow"} or any(not _COLOUR.match(str(c)) for c in palette.values()):
            raise ValueError(f"The {env['id']} environment has a bad family, level, element or colour.")
    if min(env["opens"] for env in data["environments"]) != 1:
        raise ValueError("One environment must be open from level 1.")
    return data


def sokoban_parse(rows: list[str]) -> tuple[frozenset, frozenset, frozenset, tuple[int, int]]:
    """Read a level: (walls, goals, boxes, where Lumi stands). # wall, . goal, $ box, * box on goal, @ Lumi, + Lumi on goal."""
    walls, goals, boxes, lumi = set(), set(), set(), None
    for y, row in enumerate(rows):
        for x, cell in enumerate(row):
            if cell == "#":
                walls.add((x, y))
            if cell in ".*+":
                goals.add((x, y))
            if cell in "$*":
                boxes.add((x, y))
            if cell in "@+":
                if lumi is not None:
                    raise ValueError("A Sokoban level has two Lumis.")
                lumi = (x, y)
            if cell not in "# .$*@+":
                raise ValueError("A Sokoban level has a character it shouldn't.")
    if lumi is None or not boxes or len(boxes) != len(goals):
        raise ValueError("A Sokoban level needs Lumi, and as many boxes as goals.")
    return frozenset(walls), frozenset(goals), frozenset(boxes), lumi


def sokoban_solved(rows: list[str], moves: str) -> bool:
    """Replay an answer (a string of u, d, l, r) against a level. True only if every move is legal and every box
    ends on a goal. This is the whole of the lock: the page's own copy of the game is only for playing."""
    walls, goals, boxes, (x, y) = sokoban_parse(rows)
    held = set(boxes)
    step = {"u": (0, -1), "d": (0, 1), "l": (-1, 0), "r": (1, 0)}
    for move in moves:
        if move not in step:
            return False
        dx, dy = step[move]
        to = (x + dx, y + dy)
        if to in walls:
            return False
        if to in held:
            beyond = (to[0] + dx, to[1] + dy)
            if beyond in walls or beyond in held:
                return False
            held.remove(to)
            held.add(beyond)
        x, y = to
    return held == goals


def load_sokoban(path: Path | None = None) -> list[dict]:
    """The Sokoban levels (made by python/make_lumi_sokoban.py), easiest first. Each must at least parse; a test
    solves every one, so a level that can't be finished never ships."""
    data = json.loads((path or Path(__file__).parent / "lumipet_data" / "sokoban.json").read_text(encoding="utf-8"))
    levels = data.get("levels")
    if not isinstance(levels, list) or not levels:
        raise ValueError("Lumi's dungeon needs some Sokoban levels.")
    for level in levels:
        rows = level.get("rows") if isinstance(level, dict) else None
        if not _ID.match(str(level.get("id", ""))) or not isinstance(rows, list) or not 4 <= len(rows) <= 9 \
                or any(not isinstance(row, str) or len(row) != len(rows[0]) or not 4 <= len(row) <= 9 for row in rows):
            raise ValueError("Lumi's dungeon has a badly shaped Sokoban level.")
        sokoban_parse(rows)
    return levels


def load_trophies(path: Path | None = None) -> dict[str, list[dict]]:
    """Trophies by environment. They live in the house catalog (house.json, the trophy spot), because a trophy is a
    thing for the house; each names the environment it is found in."""
    data = json.loads((path or Path(__file__).parent / "lumipet_data" / "house.json").read_text(encoding="utf-8"))
    found: dict[str, list[dict]] = {}
    for spot in data["spots"]:
        for item in spot["items"]:
            if item.get("found"):
                found.setdefault(item["found"], []).append(item)
    return found


CATALOG = load_catalog()
SOKOBAN = load_sokoban()
TROPHIES = load_trophies()
_ENVS = {env["id"]: env for env in CATALOG["environments"]}
for _env in TROPHIES:
    if _env not in _ENVS:
        raise ValueError(f"A trophy is found in {_env}, which isn't an environment.")
_FOUND_MODS = [mod for mod in lumibattle.CATALOG["mods"] if not mod["price"]]      # mods that only the dungeon gives


def lights_press(board: list[bool], size: int, cell: int) -> None:
    """Lights Out: pressing a lamp flips it and the ones beside it."""
    x, y = cell % size, cell // size
    for cx, cy in ((x, y), (x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
        if 0 <= cx < size and 0 <= cy < size:
            board[cy * size + cx] = not board[cy * size + cx]


def lights_solved(board: list[bool], size: int, presses: list[int]) -> bool:
    """Replay a list of presses; True if every lamp ends up off."""
    lamps = list(board)
    for cell in presses:
        if not 0 <= cell < size * size:
            return False
        lights_press(lamps, size, cell)
    return not any(lamps)


# --------------------------------------------------------------------------- making a floor
def _weighted(weights: dict[str, int], rng) -> str:
    roll = rng.randrange(sum(weights.values()))
    for key, weight in weights.items():
        if roll < weight:
            return key
        roll -= weight
    return key


def _shuffle(things: list, rng) -> list:
    out = list(things)
    for i in range(len(out) - 1, 0, -1):
        j = rng.randrange(i + 1)
        out[i], out[j] = out[j], out[i]
    return out


def _coins(depth: int, rng, scale: float = 1.0) -> int:
    return int((10 + rng.randrange(16)) * (1 + 0.5 * (depth - 1)) * scale)


def _lock(depth: int, rng) -> dict:
    """A vault's lock. The puzzle kinds carry their own puzzle; deeper floors draw harder ones."""
    kind = LOCKS[rng.randrange(len(LOCKS))]
    if kind == "sokoban":
        reach = min(len(SOKOBAN), 4 + depth * 3)          # deeper floors may draw from further along the (sorted) list
        return {"kind": kind, "level": SOKOBAN[rng.randrange(reach)]["id"]}
    if kind == "lights":
        size = 3 if depth < 3 else 4
        board = [False] * (size * size)
        while not any(board):                             # scrambled from "all off" by real presses, so it can be undone
            for _ in range(3 + depth):
                lights_press(board, size, rng.randrange(size * size))
        return {"kind": kind, "size": size, "board": board}
    if kind == "runes":
        return {"kind": kind, "code": [RUNES[rng.randrange(len(RUNES))] for _ in range(RUNE_SLOTS)], "tries": []}
    return {"kind": "key"}


def _enemy(env: dict, depth: int, rng) -> dict:
    tier = _weighted(TIERS_BY_DEPTH[min(depth, len(TIERS_BY_DEPTH)) - 1], rng)
    pool = [enemy for enemy in lumibattle.CATALOG["enemies"] if enemy["tier"] == tier]
    local = [enemy for enemy in pool if enemy["element"] in env["elements"]]
    chosen = local if local and rng.randrange(100) < ENV_BIAS else pool
    return {"id": chosen[rng.randrange(len(chosen))]["id"]}


def generate(env_id: str, depth: int, rng, pet: dict, held: dict | None = None) -> dict:
    """One floor: {"rooms": {"x,y": room}, "start": "x,y"}. Rooms are grown outwards from the entrance so the floor
    is always in one piece, with a loop or two so it isn't only corridors. The vault is a dead end, so its lock never
    stands between Lumi and anything else: the key, the stairs and the way home can always be reached without it."""
    env, held = _ENVS[env_id], held or {"trophies": [], "mods": []}
    count = ROOMS[0] + rng.randrange(ROOMS[1] - ROOMS[0] + 1)
    start = (rng.randrange(GRID_W), rng.randrange(GRID_H))
    cells, exits = [start], {start: {}}
    while len(cells) < count:
        x, y = cells[rng.randrange(len(cells))]
        way = list(DIRS)[rng.randrange(4)]
        to = (x + DIRS[way][0], y + DIRS[way][1])
        if 0 <= to[0] < GRID_W and 0 <= to[1] < GRID_H and to not in exits:
            cells.append(to)
            exits[to] = {BACK[way]: True}
            exits[(x, y)][way] = True
    leaves = [cell for cell in cells if len(exits[cell]) == 1 and cell != start]
    vault = leaves[rng.randrange(len(leaves))]            # a grown tree of two or more rooms always has such a leaf
    for _ in range(1 + rng.randrange(2)):                 # a loop or two, never through the vault
        x, y = cells[rng.randrange(len(cells))]
        way = list(DIRS)[rng.randrange(4)]
        to = (x + DIRS[way][0], y + DIRS[way][1])
        if to in exits and vault not in ((x, y), to):
            exits[(x, y)][way] = exits[to][BACK[way]] = True

    far, queue = {start: 0}, [start]                      # steps from the entrance, for putting the stairs far away
    for cell in queue:
        for way in exits[cell]:
            to = (cell[0] + DIRS[way][0], cell[1] + DIRS[way][1])
            if to not in far:
                far[to] = far[cell] + 1
                queue.append(to)
    stairs = max((cell for cell in cells if cell not in (start, vault)), key=lambda cell: (far[cell], cell))

    lock = _lock(depth, rng)
    spare = _shuffle([cell for cell in cells if cell not in (start, vault, stairs)], rng)
    kinds = ["enemy"] * (3 + rng.randrange(2)) + ["game", "treasure", "treasure"] + (["key"] if lock["kind"] == "key" else [])
    rooms = {}
    for cell in cells:
        kind = "start" if cell == start else "vault" if cell == vault else "stairs" if cell == stairs else \
            (kinds[spare.index(cell)] if spare.index(cell) < len(kinds) else "empty")
        room = {"kind": kind, "exits": sorted(exits[cell]), "seen": cell == start, "done": kind in ("start", "empty", "stairs")}
        if kind == "enemy":
            room["enemy"] = _enemy(env, depth, rng)
        elif kind == "treasure":
            room["loot"] = {"coins": _coins(depth, rng), "potions": 1 if rng.randrange(100) < 30 else 0}
        elif kind == "key":
            room["loot"] = {"keys": 1}
        elif kind == "game":
            room["game"] = _game(rng)
        elif kind == "vault":
            room["lock"] = lock
            room["loot"] = _vault_loot(env, depth, rng, pet, held)
        rooms[f"{cell[0]},{cell[1]}"] = room
    return {"rooms": rooms, "start": f"{start[0]},{start[1]}"}


def _vault_loot(env: dict, depth: int, rng, pet: dict, held: dict) -> dict:
    """What the vault holds: plenty of coins, a trophy from this environment that she doesn't have yet, and sometimes
    a mod that can't be bought."""
    loot = {"coins": _coins(depth, rng, 3.0), "potions": 1}
    new = [t["id"] for t in TROPHIES.get(env["id"], []) if t["id"] not in pet["decor"] and t["id"] not in held["trophies"]]
    if new:
        loot["trophy"] = new[rng.randrange(len(new))]
    mods = [m["id"] for m in _FOUND_MODS if m["id"] not in pet["mods_owned"] and m["id"] not in held["mods"]]
    if mods and rng.randrange(100) < MOD_CHANCE[0] + MOD_CHANCE[1] * depth:
        loot["mod"] = mods[rng.randrange(len(mods))]
    return loot


def _game(rng) -> dict:
    """A room's little game. Its secret (which cup, where the pairs are) stays on the server."""
    if rng.randrange(2):
        return {"kind": "cups", "prize": rng.randrange(CUPS)}
    deck = _shuffle([symbol for symbol in RUNES[:PAIRS] for _ in range(2)], rng)
    return {"kind": "match", "deck": deck, "found": [], "open": None, "flips": 0, "last": None}


# --------------------------------------------------------------------------- a run, saved
def current(conn: sqlite3.Connection, user_id: int) -> dict | None:
    """This person's run as it stands, finished or not; None if they have never started one."""
    row = conn.execute("SELECT state FROM lumi_runs WHERE user_id = ?", (user_id,)).fetchone()
    return json.loads(row["state"]) if row else None


def _save(conn: sqlite3.Connection, user_id: int, run: dict) -> None:
    run["log"] = run["log"][-LOG:]
    conn.execute("INSERT INTO lumi_runs(user_id, state) VALUES (?, ?) ON CONFLICT(user_id) DO UPDATE SET state = excluded.state, "
                 "updated_at = datetime('now')", (user_id, json.dumps(run, separators=(",", ":"))))


def open_environments(pet: dict) -> list[dict]:
    """Every environment, each marked with whether Lumi's level has opened it yet."""
    return [{**{k: env[k] for k in ("id", "name", "family", "about", "opens", "palette")}, "open": pet["level"] >= env["opens"]}
            for env in CATALOG["environments"]]


def start(conn: sqlite3.Connection, user_id: int, pet: dict, env_id: str, now: int, rng=None) -> dict:
    """Set out. One run at a time; a finished one is replaced. Call inside a transaction."""
    rng = rng or random.SystemRandom()
    env = _ENVS.get(env_id)
    if env is None:
        raise DungeonError("That isn't somewhere Lumi can go.")
    if pet["level"] < env["opens"]:
        raise DungeonError(f"{env['name']} opens at level {env['opens']}.")
    old = current(conn, user_id)
    if old and not old["over"]:
        raise DungeonError("Lumi is already out exploring. Finish that trip first, or head home.")
    if pet["faint_until"] > now:
        raise DungeonError("Lumi is still napping off her last fight.")
    if lumibattle.current(conn, user_id):
        raise DungeonError("Lumi is in the middle of a fight.")
    floor = generate(env_id, 1, rng, pet)
    numbers = lumibattle.stats(pet)
    run = {"id": now, "env": env_id, "depth": 1, "rooms": floor["rooms"], "at": floor["start"], "prev": None, "facing": None,
           "satchel": {"coins": 0, "potions": 0, "keys": 0, "trophies": [], "mods": []}, "hp": numbers["hp"], "mp": numbers["mp"],
           "fight": None, "over": None, "ended": None, "tales": {}, "log": [], "event": {"scenario": "enter"}}
    _save(conn, user_id, run)
    return run


def _room(run: dict) -> dict:
    return run["rooms"][run["at"]]


def _step(at: str, way: str) -> str:
    x, y = (int(n) for n in at.split(","))
    return f"{x + DIRS[way][0]},{y + DIRS[way][1]}"


def _take(run: dict, loot: dict) -> dict:
    """Move a room's loot into the satchel; returns what was taken, for the page to announce."""
    bag = run["satchel"]
    for key in ("coins", "potions", "keys"):
        bag[key] += loot.get(key, 0)
    if loot.get("trophy"):
        bag["trophies"].append(loot["trophy"])
    if loot.get("mod"):
        bag["mods"].append(loot["mod"])
    return loot


def _enter(conn: sqlite3.Connection, user_id: int, pet: dict, run: dict, to: str, now: int, rng) -> dict | None:
    """Arrive in a room and let it happen: pick up what's lying there, or meet who lives there. Returns a fight
    if one began."""
    run["prev"], run["at"], run["facing"] = run["at"], to, None
    room = _room(run)
    first, room["seen"] = not room["seen"], True
    if room["kind"] == "enemy" and not room["done"]:
        entry = lumibattle._ENEMIES[room["enemy"]["id"]]
        level = max(1, min(lumibattle.MAX_LEVEL, pet["level"] + rng.randrange(3) - 1 + (run["depth"] - 1)))
        fight = lumibattle.start(conn, user_id, pet, entry["tier"], now, rng, enemy=entry["id"], level=level,
                                 carry={"hp": run["hp"], "mp": run["mp"]}, dungeon=True)
        run["fight"] = fight["id"]
        run["event"] = {"scenario": "enemy", "enemy": entry["name"]}
        return fight
    if room["kind"] in ("treasure", "key", "vault") and not room["done"]:
        room["done"] = True
        taken = _take(run, room["loot"])
        run["event"] = {"scenario": "key" if room["kind"] == "key" else room["kind"], "loot": taken}
    elif room["kind"] == "game" and not room["done"]:
        run["event"] = {"scenario": "game_" + room["game"]["kind"]}
    else:
        run["event"] = {"scenario": {"stairs": "stairs", "start": "start"}.get(room["kind"], "empty" if first else "again")}
    return None


def _prize(run: dict, rng, scale: float) -> int:
    won = _coins(run["depth"], rng, scale)
    run["satchel"]["coins"] += won
    return won


def act(conn: sqlite3.Connection, user_id: int, pet: dict, action: str, now: int, rng=None, *, way: str | None = None,
        moves: str = "", picks: list | None = None) -> dict | None:
    """One choice in the dungeon. Changes the run (saved here) and perhaps the pet (the caller saves it). Returns a
    fight if the step walked into one. Call inside a transaction."""
    rng = rng or random.SystemRandom()
    run, picks = current(conn, user_id), picks or []
    if run is None or run["over"]:
        raise DungeonError("Lumi isn't out exploring.")
    if run["fight"]:
        raise DungeonError("Lumi is in the middle of a fight.")
    room, fight = _room(run), None

    if action == "move":
        if way not in room["exits"]:
            raise DungeonError("There's no way through there.")
        to = _step(run["at"], way)
        target = run["rooms"][to]
        if target.get("lock") and not target["done"]:
            run["facing"] = to                            # stop at the door; the page shows its lock
            run["event"] = {"scenario": "locked_key" if target["lock"]["kind"] == "key" else "locked_puzzle", "lock": target["lock"]["kind"]}
        else:
            fight = _enter(conn, user_id, pet, run, to, now, rng)
    elif action in ("unlock", "sokoban", "lights", "rune"):
        door = run["rooms"].get(run["facing"] or "")
        if door is None or door["done"]:
            raise DungeonError("There's no locked door in front of Lumi.")
        lock, opened = door["lock"], False
        if action == "unlock" and lock["kind"] == "key":
            if run["satchel"]["keys"] < 1:
                raise DungeonError("Lumi needs a key for this door. There's one somewhere on this floor.")
            run["satchel"]["keys"] -= 1
            opened = True
        elif action == "sokoban" and lock["kind"] == "sokoban":
            level = next(level for level in SOKOBAN if level["id"] == lock["level"])
            if len(moves) > MAX_MOVES or not sokoban_solved(level["rows"], moves):
                raise DungeonError("The crates aren't all in place yet.")
            opened = True
        elif action == "lights" and lock["kind"] == "lights":
            if len(picks) > 200 or not lights_solved(lock["board"], lock["size"], picks):
                raise DungeonError("Some lamps are still lit.")
            opened = True
        elif action == "rune" and lock["kind"] == "runes":
            if len(picks) != RUNE_SLOTS or any(rune not in RUNES for rune in picks):
                raise DungeonError(f"Choose {RUNE_SLOTS} runes.")
            exact = sum(a == b for a, b in zip(picks, lock["code"]))
            near = sum(min(picks.count(r), lock["code"].count(r)) for r in RUNES) - exact
            lock["tries"].append({"guess": list(picks), "exact": exact, "near": near})
            opened = exact == RUNE_SLOTS
            if not opened and len(lock["tries"]) >= RUNE_TRIES:       # out of tries: the door thinks of a new code
                lock["code"], lock["tries"] = [RUNES[rng.randrange(len(RUNES))] for _ in range(RUNE_SLOTS)], []
                run["event"] = {"scenario": "runes_reset"}
            elif not opened:
                run["event"] = {"scenario": "runes_try", "exact": exact, "near": near}
        else:
            raise DungeonError("That isn't how this door opens.")
        if opened:
            door["lock"] = {"kind": lock["kind"]}                       # the puzzle is spent; forget its answer
            _enter(conn, user_id, pet, run, run["facing"], now, rng)
    elif action == "back":
        run["facing"] = None
        run["event"] = {"scenario": "again"}
    elif action in ("cup", "flip"):
        game = room.get("game")
        if room["kind"] != "game" or room["done"] or game["kind"] != {"cup": "cups", "flip": "match"}[action]:
            raise DungeonError("There's no game like that here.")
        pick = picks[0] if len(picks) == 1 and isinstance(picks[0], int) else -1
        if action == "cup":
            if not 0 <= pick < CUPS:
                raise DungeonError("Choose a cup.")
            room["done"], won = True, pick == game["prize"]
            run["event"] = {"scenario": "game_win" if won else "game_lose", "coins": _prize(run, rng, 2.5 if won else 0.3), "prize": game["prize"]}
        else:
            if not 0 <= pick < len(game["deck"]) or pick in game["found"] or pick == game["open"]:
                raise DungeonError("Choose a card that's face down.")
            game["flips"] += 1
            if game["open"] is None:
                game["open"], game["last"] = pick, None
                run["event"] = {"scenario": "match_flip"}
            else:
                first, game["open"] = game["open"], None
                game["last"] = [first, pick]                            # shown once, so a miss can be seen before it turns back
                if game["deck"][first] == game["deck"][pick]:
                    game["found"] += [first, pick]
                run["event"] = {"scenario": "match_pair" if game["deck"][first] == game["deck"][pick] else "match_miss"}
            if len(game["found"]) == len(game["deck"]) or game["flips"] >= FLIPS:
                room["done"] = True
                pairs = len(game["found"]) // 2
                run["event"] = {"scenario": "game_win" if pairs == PAIRS else "game_lose", "coins": _prize(run, rng, 0.3 + 0.7 * pairs), "pairs": pairs}
    elif action == "potion":
        numbers = lumibattle.stats(pet)
        if run["hp"] >= numbers["hp"]:
            raise DungeonError("Lumi is already at full health.")
        if pet["potions"] < 1:
            raise DungeonError("Lumi has no potions with her.")
        pet["potions"] -= 1
        run["hp"] = min(numbers["hp"], run["hp"] + max(1, numbers["hp"] // 2))
        run["event"] = {"scenario": "potion"}
    elif action == "descend":
        if room["kind"] != "stairs":
            raise DungeonError("There are no stairs here.")
        floor = generate(run["env"], run["depth"] + 1, rng, pet, run["satchel"])
        run.update(depth=run["depth"] + 1, rooms=floor["rooms"], at=floor["start"], prev=None, facing=None, tales={})
        run["satchel"]["keys"] = 0                                      # a key only fits the floor it was found on
        run["event"] = {"scenario": "descend"}
    elif action == "leave":
        if room["kind"] != "start":
            raise DungeonError("The way home is back where Lumi came in.")
        _bank(conn, user_id, pet, run)
    elif action == "abandon":
        run.update(over="abandoned", ended={"lost": run["satchel"], "depth": run["depth"]}, event={"scenario": "abandon"})
        run["satchel"] = {"coins": 0, "potions": 0, "keys": 0, "trophies": [], "mods": []}
    else:
        raise DungeonError("Lumi doesn't know that one.")
    _save(conn, user_id, run)
    return fight


def _bank(conn: sqlite3.Connection, user_id: int, pet: dict, run: dict) -> None:
    """Walk out with the satchel: only now is any of it really hers. Coins are paid once, by the run's id."""
    bag = run["satchel"]
    paid = coins.prize(conn, user_id, bag["coins"], GAME, f"lumi-run:{user_id}:{run['id']}") if bag["coins"] else 0
    kept = min(bag["potions"], lumibattle.POTION_CAP - pet["potions"])
    pet["potions"] += kept
    pet["decor"] += [t for t in bag["trophies"] if t not in pet["decor"]]
    pet["mods_owned"] += [m for m in bag["mods"] if m not in pet["mods_owned"]]
    run.update(over="home", ended={"coins": paid, "potions": kept, "trophies": bag["trophies"], "mods": bag["mods"], "depth": run["depth"]},
               event={"scenario": "home"})


def after_fight(conn: sqlite3.Connection, user_id: int, run: dict, fight: dict) -> None:
    """A dungeon fight just ended. A win clears the room and puts its coins in the satchel; running steps back the way
    she came; a defeat ends the run and empties the satchel (her nap is lumibattle's doing). Saves the run."""
    run["fight"] = None
    lumi = fight["player"]
    run["hp"], run["mp"] = max(1, lumi["hp"]), lumi["mp"]
    if fight["status"] == "victory":
        _room(run)["done"] = True
        run["satchel"]["coins"] += fight["reward"]["coins"]
        run["event"] = {"scenario": "victory", "enemy": fight["enemy"]["name"], "coins": fight["reward"]["coins"]}
    elif fight["status"] == "ran":
        run["at"], run["prev"] = run["prev"], run["at"]
        run["event"] = {"scenario": "fled", "enemy": fight["enemy"]["name"]}
    else:
        run.update(over="defeat", ended={"lost": run["satchel"], "depth": run["depth"]}, event={"scenario": "defeat", "enemy": fight["enemy"]["name"]})
        run["satchel"] = {"coins": 0, "potions": 0, "keys": 0, "trophies": [], "mods": []}
    _save(conn, user_id, run)


# --------------------------------------------------------------------------- what the page is told
def _public_lock(lock: dict) -> dict:
    """A lock as the page may see it: the puzzle to play, never its answer."""
    if lock["kind"] == "sokoban" and "level" in lock:
        return {"kind": "sokoban", "rows": next(level for level in SOKOBAN if level["id"] == lock["level"])["rows"]}
    if lock["kind"] == "lights" and "board" in lock:
        return {"kind": "lights", "size": lock["size"], "board": lock["board"]}
    if lock["kind"] == "runes" and "code" in lock:
        return {"kind": "runes", "runes": list(RUNES), "slots": RUNE_SLOTS, "tries": lock["tries"], "left": RUNE_TRIES - len(lock["tries"])}
    return {"kind": lock["kind"]}


def _public_game(game: dict, done: bool) -> dict:
    if game["kind"] == "cups":
        return {"kind": "cups", "cups": CUPS}
    shown = set(game["found"]) | ({game["open"]} if game["open"] is not None else set()) | set(game["last"] or [])
    return {"kind": "match", "cards": [game["deck"][i] if i in shown or done else None for i in range(len(game["deck"]))],
            "found": game["found"], "open": game["open"], "last": game["last"], "left": FLIPS - game["flips"]}


def view(run: dict | None, pet: dict) -> dict | None:
    """The run as the page may know it: only rooms she has stood in, and a blank for each doorway she has seen but
    not gone through. Enemies, loot and puzzle answers in unvisited rooms are never sent."""
    if run is None:
        return None
    env, numbers = _ENVS[run["env"]], lumibattle.stats(pet)
    rooms = {}
    for key, room in run["rooms"].items():
        if room["seen"]:
            rooms[key] = {"kind": room["kind"], "done": room["done"], "exits": room["exits"]}
            for way in room["exits"]:
                rooms.setdefault(_step(key, way), {"kind": "unknown"})
    here = run["rooms"][run["at"]]
    out = {"id": run["id"], "env": {k: env[k] for k in ("id", "name", "family", "palette")}, "depth": run["depth"], "at": run["at"],
           "rooms": {key: room for key, room in rooms.items() if run["rooms"][key]["seen"] or room["kind"] == "unknown"},
           "room": {"kind": here["kind"], "done": here["done"], "exits": here["exits"]},
           "satchel": run["satchel"], "hp": run["hp"], "hpMax": numbers["hp"], "mp": run["mp"], "mpMax": numbers["mp"],
           "over": run["over"], "ended": run["ended"], "event": run["event"], "say": run.get("say", ""), "fighting": bool(run["fight"]), "facing": None, "game": None}
    if run["facing"]:
        out["facing"] = {"at": run["facing"], "lock": _public_lock(run["rooms"][run["facing"]]["lock"])}
    if here["kind"] == "game":
        out["game"] = _public_game(here["game"], here["done"])
    return out
