"""Breakout and Snake, ported from Doll's GameMaker arcade (C:/Users/langley/GameMakerProjects/Roulette): what the
server needs to check a run before it pays.

Teacup Tower is our own climber in the same shell (Doll's idea): Lumi bounces up an endless tower of teacups. Its
runs are checked like Breakout's, by what's possible: no more floors than the time allows.

All three run in the browser (static/games/breakout/, static/games/snake/, static/games/teacup-tower/). The server hands each run a seed, and the
page builds its levels or places its food from that seed with the same arithmetic as here, so the server can check:
- Snake exactly: the page sends the moves it turned on, and snake_replay plays the whole game again here. The score
  paid is the one this file worked out, never the page's.
- Breakout by what's possible: breakout_field rebuilds every level from the seed, so a claimed score must lie between
  the bricks of the levels cleared and those plus the level reached, and come no faster than the time allows.

The random numbers are plain integer arithmetic that JavaScript's doubles do exactly (every product stays below 2**53),
so this file and rules.js always agree. tests/js and tests/test_arcade.py check that they do.

Coins follow Roulette's rules (Doll's call): a run costs ENTRY coins and pays its score, one coin a point.
"""

from __future__ import annotations

import json
import secrets
import sqlite3
import time

from mnemerynne import coins

GAMES = ("breakout", "snake", "teacup-tower")
ENTRY = 25                                # what a solo run or a showdown race costs
MODULUS = 2147483647                      # 2**31 - 1: every seed and random number lives below it


GLOWS = ("rose", "lagoon", "starlight", "zephyr", "wisp", "umbra")   # Lumi's glow colours (lumipet.GLOWS)


def lumi_look(conn: sqlite3.Connection, user_id: int | None) -> dict:
    """How this person's Lumi looks (her glow and what she wears), so the arcade games draw their own Lumi: leading the
    conga, curled up as Yarn Pop's ball, carrying Prism Drop's ball, climbing Teacup Tower. Only reads her save (no
    catching up, nothing written); someone without a Lumi gets a fresh one's look. What she wears is checked against
    the wardrobe catalog, so a retired item is simply left off."""
    from mnemerynne import lumipet                       # imported late: lumipet's catalogs load with it
    row = conn.execute("SELECT data FROM lumi_pets WHERE user_id = ?", (user_id,)).fetchone() if user_id else None
    try:
        pet = json.loads(row["data"]) if row else {}     # an unreadable save looks fresh
    except ValueError:
        pet = {}
    glow = pet.get("glow") if pet.get("glow") in GLOWS else "rose"
    outfit = pet.get("outfit") if isinstance(pet.get("outfit"), dict) else {}
    worn = {slot: item for slot, item in outfit.items() if isinstance(item, str) and lumipet._WEAR.get(item, {}).get("slot") == slot}
    return {"glow": glow, "outfit": worn}


class Refused(ValueError):
    """A safe, actionable explanation for the player; nothing was changed."""

    def __init__(self, message: str, status: int = 409) -> None:
        super().__init__(message)
        self.status = status


# ---------------------------------------------------------------- numbers
def mix(seed: int, index: int, salt: int) -> int:
    """One number from a seed and a position, the GameMaker game's own formula (snakeSeedValue)."""
    value = (max(1, abs(int(seed))) * 1664525 + (max(0, int(index)) + 1) * 1013904223 + max(1, abs(int(salt))) * 69069) % MODULUS
    return value if value > 0 else value + MODULUS - 1


def level_seed(seed: int, level: int) -> int:
    """The seed for one Breakout level, the GameMaker game's own formula (breakoutLevelSeed)."""
    value = (max(1, abs(int(seed))) * 1664525 + max(1, int(level)) * 1013904223) % MODULUS
    return value if value > 0 else value + MODULUS - 1


class Minstd:
    """Park and Miller's generator: state * 48271 mod 2**31-1. Small enough for JavaScript to do exactly."""

    def __init__(self, seed: int) -> None:
        self.state = int(seed) % MODULUS or 1

    def next(self) -> int:
        self.state = self.state * 48271 % MODULUS
        return self.state

    def between(self, low: int, high: int) -> int:
        """A whole number from low to high, both included (GameMaker's irandom_range)."""
        return low + self.next() % (high - low + 1)


def new_seed() -> int:
    """A fresh seed for a run or a race."""
    return secrets.randbelow(MODULUS - 2) + 1


# ---------------------------------------------------------------- Breakout
BRICK_COLS, BRICK_ROWS = 18, 6            # an 18 x 6 grid of 32 px cells inside the 640 x 480 arena
BRICKS_PER_SECOND = 6                     # the fastest a run may break bricks, multiball and all
_SHAPES = (                               # (roll from, roll to, width, height, cells), as in Breakout.gml
    (0, 4, 8, 3, [(c, r) for r in range(3) for c in range(8)]),                                   # rectangle
    (5, 8, 6, 5, [(c, r) for r in range(5) for c in range(6) if r in (0, 4) or c in (0, 5)]),     # hollow box
    (9, 11, 7, 5, [(1, 0), (2, 0), (4, 0), (5, 0), *[(c, 1) for c in range(7)], *[(c, 2) for c in range(1, 6)],
                   (2, 3), (3, 3), (4, 3), (3, 4)]),                                              # heart
    (12, 15, 7, 6, [(3, 0), (2, 1), (3, 1), (4, 1), *[(c, 2) for c in range(7)], *[(c, 3) for c in range(1, 6)],
                    (2, 4), (4, 4), (2, 5), (4, 5)]),                                             # star
)


def breakout_field(seed: int, level: int) -> list[int]:
    """One level's bricks, row by row (BRICK_COLS x BRICK_ROWS): 0 for none, else its colour 1-6 (red, yellow, blue,
    green, fuchsia, orange). Two or three shapes stamped at random, then filled out to at least 30 bricks."""
    rng = Minstd(level_seed(seed, level))
    grid = [0] * (BRICK_COLS * BRICK_ROWS)

    def mark(col: int, row: int) -> None:
        if 0 <= col < BRICK_COLS and 0 <= row < BRICK_ROWS:
            grid[row * BRICK_COLS + col] = 1

    for _ in range(rng.between(2, 3)):
        roll = rng.between(0, 15)
        _, _, width, height, cells = next(s for s in _SHAPES if s[0] <= roll <= s[1])
        col, row = rng.between(0, max(0, BRICK_COLS - width)), rng.between(0, max(0, BRICK_ROWS - height))
        for c, r in cells:
            mark(col + c, row + r)
    while sum(grid) < 30:                 # topped up with small blocks until the level is worth playing
        width, height = rng.between(3, 6), rng.between(1, 2)
        col, row = rng.between(0, BRICK_COLS - width), rng.between(0, BRICK_ROWS - height)
        for r in range(height):
            for c in range(width):
                mark(col + c, row + r)
    return [rng.between(1, 6) if cell else 0 for cell in grid]


def breakout_bricks(seed: int, level: int) -> int:
    """How many bricks a level has."""
    return sum(1 for cell in breakout_field(seed, level) if cell)


def check_breakout(seed: int, score: int, level: int, seconds: float) -> dict:
    """A claimed Breakout run, if it's possible: every brick of the levels before `level` broken, part of this one, and
    no faster than BRICKS_PER_SECOND."""
    if not 1 <= level <= 500:
        raise Refused("That level isn't possible.", 422)
    cleared = sum(breakout_bricks(seed, n) for n in range(1, level))
    if not cleared <= score <= cleared + breakout_bricks(seed, level):
        raise Refused("That score doesn't match the levels played.", 422)
    if score > seconds * BRICKS_PER_SECOND + 12:
        raise Refused("That run was faster than the game allows.", 422)
    return {"score": score, "level": level, "distance": (level - 1) * 100 + score}


# ------------------------------------------------------------------- Snake
SNAKE_W, SNAKE_H = 20, 15
SNAKE_START = ((10, 7), (9, 7), (8, 7))   # head first, heading right
STEPS = ((1, 0), (0, 1), (-1, 0), (0, -1))   # directions 0-3: right, down, left, up
FOOD_POINTS = {2: 1, 3: 5, 4: 10}         # red, yellow, aqua
MOVES_PER_SECOND = 7.5                    # one move every 8 frames at 60 fps


def snake_food(seed: int, index: int, board: list[list[int]]) -> tuple[int, int, int] | None:
    """Where the index-th food goes and what kind it is (2, 3 or 4), or None when the board is full. Empty cells are
    counted column by column, as the GameMaker game did."""
    empty = [(x, y) for x in range(SNAKE_W) for y in range(SNAKE_H) if board[x][y] == 0]
    if not empty:
        return None
    x, y = empty[mix(seed, index, 17) % len(empty)]
    roll = mix(seed, index, 53) % 100
    return x, y, 2 if roll < 70 else 3 if roll < 92 else 4


def snake_replay(seed: int, turns: list[tuple[int, int]], moves: int) -> dict:
    """Play a Snake run again from its seed and the moves it turned on ([move number, direction], in order). Returns
    the score and length after `moves` moves and the move it died on, if it did (alive: False)."""
    board = [[0] * SNAKE_H for _ in range(SNAKE_W)]
    body = list(SNAKE_START)
    for x, y in body:
        board[x][y] = 1
    food_index, score, heading = 0, 0, 0
    food = snake_food(seed, food_index, board)
    board[food[0]][food[1]] = food[2]
    food_index += 1
    plan = dict(turns)
    for move in range(1, moves + 1):
        if move in plan:
            if plan[move] == (heading + 2) % 4:
                raise Refused("That run turned back on itself.", 422)
            heading = plan[move]
        x, y = body[0][0] + STEPS[heading][0], body[0][1] + STEPS[heading][1]
        if not (0 <= x < SNAKE_W and 0 <= y < SNAKE_H):
            return {"score": score, "length": len(body), "alive": False, "died": move}     # into the wall
        cell = board[x][y]
        ate = cell in FOOD_POINTS
        tail = body[-1]
        if cell == 1 and not ((x, y) == tail and not ate):
            return {"score": score, "length": len(body), "alive": False, "died": move}     # into itself
        if not ate:                                   # the tail moves on first, so the head may take its cell
            board[tail[0]][tail[1]] = 0
            body.pop()
        body.insert(0, (x, y))
        board[x][y] = 1
        if ate:
            score += FOOD_POINTS[cell]
            food = snake_food(seed, food_index, board)
            food_index += 1
            if food is None:                          # the board is full: that's the end, as in the original
                return {"score": score, "length": len(body), "alive": False, "died": move}
            board[food[0]][food[1]] = food[2]
    return {"score": score, "length": len(body), "alive": True, "died": None}


def check_snake(seed: int, turns: list[tuple[int, int]], moves: int, died: bool, seconds: float) -> dict:
    """A Snake run as this server plays it: the claim must end the way the replay does, in no fewer seconds than
    the moves take."""
    if any(b[0] <= a[0] for a, b in zip(turns, turns[1:])) or any(not 1 <= t[0] <= moves for t in turns):
        raise Refused("Those moves are out of order.", 422)
    if moves > seconds * MOVES_PER_SECOND * 1.1 + 10:
        raise Refused("That run was faster than the game allows.", 422)
    played = snake_replay(seed, turns, moves)
    if (played["died"] == moves) != died or (played["died"] is not None and played["died"] != moves):
        raise Refused("That run doesn't play out the way it says.", 422)
    return {"score": played["score"], "length": played["length"],
            "distance": played["score"] + max(0, played["length"] - 3) * 5}


# ------------------------------------------------------------ Teacup Tower
FLOORS_PER_SECOND = 6                     # well above the fastest climb (wall kicks and combos, about 4.6 floors a second)


def check_tower(floors: int, seconds: float) -> dict:
    """A claimed Teacup Tower climb, if the time allows it. The tower is the same for everyone with the seed, and the
    page only claims the highest floor Lumi landed on."""
    if floors > seconds * FLOORS_PER_SECOND + 5:
        raise Refused("That climb was faster than the game allows.", 422)
    return {"score": floors, "distance": floors}


# -------------------------------------------------------------------- runs
def start(conn: sqlite3.Connection, user_id: int, game: str, *, room: str | None = None, seed: int | None = None) -> dict:
    """A ticket for one run. A solo run pays ENTRY here (and forgets this person's unfinished solo runs: closing the
    game forfeits them); a showdown has charged its racers already and passes the race's shared seed."""
    if game not in GAMES:
        raise Refused("No such game.", 404)
    now = time.time()
    if room is None:
        if not coins.spend(conn, user_id, ENTRY, game, "entry"):
            raise Refused(f"A run costs {ENTRY} coins.", 402)
        conn.execute("DELETE FROM arcade_runs WHERE user_id = ? AND game = ? AND room IS NULL AND finished = 0", (user_id, game))
    conn.execute("DELETE FROM arcade_runs WHERE finished = 1 AND started_at < ?", (now - 86400,))   # kept a day, then tidied
    seed = seed or new_seed()
    run = conn.execute("INSERT INTO arcade_runs(user_id, game, seed, room, started_at) VALUES (?, ?, ?, ?, ?)",
                       (user_id, game, seed, room, now)).lastrowid
    return {"run": run, "seed": seed, "entry": ENTRY}


def finish(conn: sqlite3.Connection, user_id: int, game: str, run_id: int, claim: dict) -> dict:
    """Check a finished run and close its ticket. A solo run is paid here, once (ref "<game>:<run>"); a race's result
    goes back to its showdown table, which pays when the race is settled. Returns what the server made of the run."""
    row = conn.execute("SELECT * FROM arcade_runs WHERE id = ? AND user_id = ? AND game = ? AND finished = 0",
                       (run_id, user_id, game)).fetchone()
    if row is None:
        raise Refused("That run has already finished, or isn't yours.", 404)
    seconds = time.time() - row["started_at"]
    if game == "snake":
        result = check_snake(row["seed"], [tuple(t) for t in claim.get("turns") or []], claim.get("moves") or 0,
                             bool(claim.get("died")), seconds)
    elif game == "teacup-tower":
        result = check_tower(claim.get("score") or 0, seconds)
    else:
        result = check_breakout(row["seed"], claim.get("score") or 0, claim.get("level") or 1, seconds)
    conn.execute("UPDATE arcade_runs SET finished = 1 WHERE id = ?", (run_id,))
    result.update(run=run_id, room=row["room"])
    if row["room"] is None:
        result["paid"] = coins.prize(conn, user_id, result["score"], game, ref=f"{game}:{run_id}")
        result["coins"] = coins.peek(conn, user_id)
    return result
