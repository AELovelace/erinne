"""Prism Drop's rules and the game in progress. They live on the server so a finished game can pay coins: a browser could
claim any score, so this rolls every drop and the page only animates what comes back (static/games/prism-drop/game.js).

Ported from MommyBot's ball drop without the wager: pick one of ten pockets, drop through twenty rows of pins, ten drops
a game. draw(n) returns a whole number from 0 to n-1; play uses a secure one, the tests a scripted one.
"""

from __future__ import annotations

import secrets
import sqlite3
from typing import Callable

from mnemerynne import coins
from mnemerynne.db import transaction

WIDTH, HEIGHT, DROPS = 10, 20, 10                          # ten pins across, twenty rows, ten drops a game
OBSTACLE_COUNTS = {"block": 11, "bomb": 9, "coin": 8}      # special pegs shuffled onto each drop's field
COIN_MIN, COIN_MAX = 1, 5                                  # a coin peg's bonus, once per peg per drop
POINTS = (20, 15, 10)                                      # landing that many pockets from the pick: 0, 1, 2
BLAST_DIRECTIONS = ((-1, -1), (0, -1), (1, -1), (-1, 0), (1, 0), (-1, 1), (0, 1), (1, 1))   # column, row
PLATFORM_ROWS = ((5, 9), (12, 16))                         # each platform gets one row from its band
PLATFORM_SPAN = 3                                          # how many columns wide a platform is
PLATFORM_RISE = 4                                          # how many rows a bounce throws the ball back up
PLATFORM_BOUNCES = 2                                       # bounces each platform gives per drop, so the ball always comes down

Draw = Callable[[int], int]


def random_platforms(draw: Draw) -> list[dict]:
    """Two platforms for one drop: which row, where it starts (its leftmost column), and which way it's heading."""
    return [{"row": low + draw(high - low + 1), "left": 1 + draw(WIDTH - PLATFORM_SPAN + 1), "dir": -1 if draw(2) == 0 else 1}
            for low, high in PLATFORM_ROWS]


def platform_left(platform: dict, tick: int) -> int:
    """A platform's leftmost column after `tick` steps of the ball: one column a step, turning round at the walls.
    The page works this out the same way (game.js platformLeft), so what it draws is what the ball hit."""
    return _reflect(platform["left"] + platform["dir"] * tick, 1, WIDTH - PLATFORM_SPAN + 1)


def _bounce(point: dict, platform: dict, tick: int) -> tuple[int, int]:
    """Where a platform throws the ball, like a breakout paddle: the edge it struck sends it that way, and the
    platform's own motion pushes it along. Returns (column, row)."""
    left = platform_left(platform, tick)
    offset = point["column"] - (left + 1)                  # -1 left edge, 0 middle, 1 right edge
    motion = platform_left(platform, tick + 1) - left      # -1 sliding left, 1 sliding right
    return _reflect(point["column"] + offset * 2 + motion, 1, WIDTH), max(0, point["row"] - PLATFORM_RISE)


def random_obstacles(draw: Draw, skip_rows: tuple[int, ...] = ()) -> list[dict]:
    """28 special pegs in unique cells across rows 1-19; row 0 (the entry row) and any skip_rows stay clear."""
    cells = [{"row": 1 + i // WIDTH, "column": 1 + i % WIDTH} for i in range((HEIGHT - 1) * WIDTH) if 1 + i // WIDTH not in skip_rows]
    layout = []
    for kind, count in OBSTACLE_COUNTS.items():
        for _ in range(count):
            pick = draw(len(cells))
            layout.append({**cells[pick], "type": kind})
            cells[pick] = cells[-1]                        # without replacement: every peg gets its own cell
            cells.pop()
    return layout


def _reflect(value: int, low: int, high: int) -> int:
    """Bounce a position back inside low..high, like a ball off the walls."""
    while value < low or value > high:
        value = low * 2 - value if value < low else high * 2 - value
    return value


def drop(draw: Draw, obstacles: list[dict], platforms: list[dict] | tuple = ()) -> dict:
    """One drop: the path through the field, which pegs and platforms it hit, and the coin-peg bonus. Each step of the
    path is one tick of the platforms' movement."""
    cells = {(o["row"], o["column"]): o["type"] for o in obstacles}
    on_row = {p["row"]: i for i, p in enumerate(platforms)}
    bounces = [0] * len(platforms)
    spent: set[tuple[int, int]] = set()
    trajectory = [{"row": 0, "column": 4 + draw(4)}]       # enters at pin 4, 5, 6 or 7
    bonus = 0
    while trajectory[-1]["row"] < HEIGHT:
        point = trajectory[-1]
        key = (point["row"], point["column"])
        kind = cells.get(key)
        tick = len(trajectory) - 1
        index = on_row.get(point["row"])
        falling = tick > 0 and trajectory[-2]["row"] < point["row"]   # a platform only catches a ball coming down onto it
        struck = (index is not None and falling and bounces[index] < PLATFORM_BOUNCES
                  and platform_left(platforms[index], tick) <= point["column"] < platform_left(platforms[index], tick) + PLATFORM_SPAN)
        if struck:
            bounces[index] += 1
            point["hit"] = "platform"
            point["platform"] = index
            column, row = _bounce(point, platforms[index], tick)
        elif kind == "bomb" and key not in spent:
            spent.add(key)                                 # each bomb fires once, so the ball always comes down
            point["hit"] = "bomb"
            direction = BLAST_DIRECTIONS[draw(len(BLAST_DIRECTIONS))]
            distance = 2 + draw(2)
            column = _reflect(point["column"] + direction[0] * distance, 1, WIDTH)
            row = min(HEIGHT, abs(point["row"] + direction[1] * distance))   # off the ceiling; a downward blast may finish
        else:
            if kind == "block":
                point["hit"] = "block"
            if kind == "coin" and key not in spent:
                spent.add(key)
                point["hit"] = "coin"
                point["coins"] = COIN_MIN + draw(COIN_MAX - COIN_MIN + 1)
                bonus += point["coins"]
            shove = 2 if kind == "block" else 1            # a block shoves two
            column = _reflect(point["column"] + (-1 if draw(2) == 0 else 1) * shove, 1, WIDTH)
            row = point["row"] + 1
        trajectory.append({"row": row, "column": column})
    return {"obstacles": obstacles, "platforms": list(platforms), "trajectory": trajectory, "landing": trajectory[-1]["column"], "bonus": bonus}


def landing_points(pick: int, landing: int) -> int:
    """20 for the right pocket, 15 one away, 10 two away, nothing further."""
    distance = abs(pick - landing)
    return POINTS[distance] if distance < len(POINTS) else 0


def roll(pick: int, draw: Draw = secrets.randbelow) -> dict:
    """A whole drop for this pick: a fresh field, the path, and what it scored, in the shape the page animates."""
    platforms = random_platforms(draw)
    result = drop(draw, random_obstacles(draw, tuple(p["row"] for p in platforms)), platforms)   # platform rows keep clear of pegs
    result["pick"] = pick
    result["landingPoints"] = landing_points(pick, result["landing"])
    result["points"] = result["landingPoints"] + result["bonus"]
    return result


def current(conn: sqlite3.Connection, user_id: int) -> dict:
    """The game this account has in progress, so a reload or another device picks it up."""
    row = conn.execute("SELECT drops, score FROM prism_runs WHERE user_id = ?", (user_id,)).fetchone()
    return {"drops": row["drops"], "score": row["score"]} if row is not None else {"drops": 0, "score": 0}


def play(conn: sqlite3.Connection, user_id: int, pick: int, draw: Draw = secrets.randbelow) -> dict:
    """Roll this account's next drop. The tenth ends the game and pays its coins, up to the day's limit."""
    with transaction(conn):                                # one drop at a time, however many tabs are open
        run = current(conn, user_id)
        result = roll(pick, draw)
        drops, score = run["drops"] + 1, run["score"] + result["points"]
        over = drops >= DROPS
        earned = paid = 0
        if over:
            earned = score // coins.PRISM_POINTS_PER_COIN
            paid = coins.reward(conn, user_id, earned, "prism-drop", coins.PRISM_DAILY_CAP)
            conn.execute("DELETE FROM prism_runs WHERE user_id = ?", (user_id,))
        else:
            conn.execute("INSERT INTO prism_runs(user_id, drops, score) VALUES (?, ?, ?) ON CONFLICT(user_id) DO UPDATE SET "
                         "drops = excluded.drops, score = excluded.score, updated_at = datetime('now')", (user_id, drops, score))
        balance = coins.peek(conn, user_id)
    return {"round": result, "drops": drops, "score": score, "over": over, "earned": earned, "paid": paid, "coins": balance}
