"""Make Sokoban levels for Lumi Pet's dungeon locks (mnemerynne/lumipet_data/puzzles.json).

Run from the repository:   .venv/Scripts/python.exe python/make_lumi_sokoban.py [--count 16] [--seed 7]

Each level is built backwards from its solved position (boxes on their goals, then Lumi "pulls" them around), so it
can always be solved; a forward search then finds the shortest solution and only levels of a sensible length are kept.
Levels are small on purpose: they are played with a TV remote's arrows.

The level format is the usual one: # wall, space floor, . goal, $ box, * box on a goal, @ Lumi, + Lumi on a goal.
The dungeon's own code (mnemerynne/lumidungeon.py) plays and checks moves; this script only reuses its rules.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from collections import deque
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mnemerynne import lumidungeon  # noqa: E402  (needs the path above)

STEP = {"u": (0, -1), "d": (0, 1), "l": (-1, 0), "r": (1, 0)}


def solve(rows: list[str], limit: int = 200_000) -> str | None:
    """The shortest list of moves that solves a level, or None if there is none (or it is too big to search)."""
    walls, goals, boxes, lumi = lumidungeon.sokoban_parse(rows)
    start = (lumi, frozenset(boxes))
    seen, queue = {start}, deque([(start, "")])
    while queue and len(seen) < limit:
        (at, held), path = queue.popleft()
        if held == goals:
            return path
        for key, (dx, dy) in STEP.items():
            to = (at[0] + dx, at[1] + dy)
            if to in walls:
                continue
            moved = held
            if to in held:
                beyond = (to[0] + dx, to[1] + dy)
                if beyond in walls or beyond in held:
                    continue
                moved = held - {to} | {beyond}
            state = (to, moved)
            if state not in seen:
                seen.add(state)
                queue.append((state, path + key))
    return None


def make(rng: random.Random, width: int, height: int, boxes: int) -> list[str] | None:
    """One level, pulled backwards from solved. None when the random walk didn't move the boxes enough."""
    cells = [(x, y) for y in range(1, height - 1) for x in range(1, width - 1)]
    walls = {(x, y) for y in range(height) for x in range(width)} - set(cells)
    for cell in rng.sample(cells, rng.randrange(1, 4)):       # a few pillars to steer around
        walls.add(cell)
    free = [cell for cell in cells if cell not in walls]
    if len(free) < boxes + 6:
        return None
    goals = set(rng.sample(free, boxes))
    held = set(goals)
    lumi = rng.choice([cell for cell in free if cell not in held])
    for _ in range(rng.randrange(60, 160)):
        dx, dy = rng.choice(list(STEP.values()))
        to, behind = (lumi[0] + dx, lumi[1] + dy), (lumi[0] - dx, lumi[1] - dy)
        if to in walls or to in held:
            continue
        if behind in held and rng.random() < 0.8:             # pull the box behind her along
            held.remove(behind)
            held.add(lumi)
        lumi = to
    if len(held & goals) > 0:                                 # every box should need pushing home
        return None
    rows = []
    for y in range(height):
        row = ""
        for x in range(width):
            cell = (x, y)
            row += "#" if cell in walls else "+" if cell == lumi and cell in goals else "@" if cell == lumi else \
                "*" if cell in held and cell in goals else "$" if cell in held else "." if cell in goals else " "
        rows.append(row)
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--count", type=int, default=16)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--out", type=Path, default=ROOT / "mnemerynne" / "lumipet_data" / "sokoban.json")
    args = parser.parse_args()
    rng, levels, seen = random.Random(args.seed), [], set()
    while len(levels) < args.count:
        easy = len(levels) < args.count // 2                  # the first half are one- and two-box warm-ups
        rows = make(rng, rng.choice([6, 7]), rng.choice([6, 7]), rng.choice([1, 2]) if easy else rng.choice([2, 3]))
        if rows is None or "\n".join(rows) in seen:
            continue
        answer = solve(rows)
        if answer is None or not (8 if easy else 14) <= len(answer) <= (26 if easy else 48):
            continue
        seen.add("\n".join(rows))
        levels.append({"id": f"crate-{len(levels) + 1}", "rows": rows, "best": len(answer)})
    levels.sort(key=lambda level: level["best"])              # easiest first: deeper floors draw from further along
    for number, level in enumerate(levels, 1):
        level["id"] = f"crate-{number}"
    args.out.write_text(json.dumps({"levels": levels}, indent=1) + "\n", encoding="utf-8")
    print(f"Wrote {len(levels)} levels to {args.out}; shortest solutions {[level['best'] for level in levels]}")


if __name__ == "__main__":
    main()
