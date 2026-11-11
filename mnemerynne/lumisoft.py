"""Lumi-Soft on the server: run tickets, entry and continue fees, the replay check, coins, best scores, the Daily Range
board and the Lumi-Soft prizes for Lumi's house and closet.

The page plays the run with static/games/lumi-soft/rules.js and sends its log (every shot, every reload, how many
continues). Nothing it claims about points is believed: lumisoft_rules.replay() plays the same run again here and that
score is what pays. Coins: a run costs ENTRY (the Training Range and Daily Range retries are free and pay nothing),
each continue costs more than the last (arcade style; the score is kept), and a finished run pays its score divided by
POINTS_PER_COIN, up to the Media setting 'lumisoft_daily_cap' a day. Every call here runs inside the caller's transaction."""

from __future__ import annotations

import json
import sqlite3
import time
from datetime import date
from pathlib import Path

from mnemerynne import coins, lumipet, lumisoft_rules as rules, media

GAME = "lumi-soft"
ENTRY = 25                                  # a run, like the arcade games
CONTINUES = (50, 100, 200, 400)             # each continue costs more; after the fourth they stay at 400
POINTS_PER_COIN = 250                       # Doll's first guess (2026-11-11): tune once people have played
MODES = ("training", "story", "stage", "daily")
SLACK_TICKS = 600                           # a run may claim 10 s more play than the wall clock allows (slow tabs, rounding)
DATA = json.loads((Path(__file__).parent / "lumisoft_data" / "stages.json").read_text(encoding="utf-8"))
STAGES = [s["id"] for s in DATA["stages"] if not s.get("training")]

# The Lumi-Soft prizes (lumipet_data/house.json and closet.json, marked "prize": "lumi-soft"), and what wins each.
# The test is a name in _won(); the piece's name and its "how" text come from Lumi Pet's catalogs.
PRIZES = (
    ("soft-rug", "training"), ("soft-wallpaper", "clear:garden"), ("soft-curtains", "clear:tower"),
    ("soft-lamp", "clear:lounge"), ("soft-ceiling", "clear:dungeon"), ("soft-bed", "clear:fair"),
    ("soft-trophy", "story"), ("acc-soft-goggles", "story"), ("soft-wallart", "rank-s"), ("soft-frame", "badges-25"),
    ("soft-shelf", "pops-500"), ("soft-toychest", "boss-clean"), ("soft-door", "dailies-10"), ("soft-sidetable", "combo"),
    ("soft-plant", "no-friends"), ("soft-view", "perfect"), ("soft-floor", "hearts"),
)


class Refused(ValueError):
    """A safe, actionable explanation for the player; nothing was changed."""

    def __init__(self, message: str, status: int = 409) -> None:
        super().__init__(message)
        self.status = status


def daily_cap(conn: sqlite3.Connection) -> int:
    """The most one person may earn from Lumi-Soft a day (Media settings 'lumisoft_daily_cap'). 0: no limit."""
    return int(media.load_settings(conn).get("lumisoft_daily_cap") or 0)


def day_number(conn: sqlite3.Connection) -> int:
    """Today on the server's calendar as a whole number, which picks the Daily Range for everyone."""
    return date.fromisoformat(coins.today(conn)).toordinal()


def continue_price(paid: int) -> int:
    """What the next continue costs after `paid` of them."""
    return CONTINUES[min(paid, len(CONTINUES) - 1)]


# ------------------------------------------------------------------ progress
def _progress(conn: sqlite3.Connection, user_id: int) -> dict:
    row = conn.execute("SELECT data FROM lumisoft_progress WHERE user_id = ?", (user_id,)).fetchone()
    try:
        data = json.loads(row["data"]) if row else {}
    except ValueError:                                     # unreadable: start again rather than fail
        data = {}
    return {"pops": 0, "dailies": 0, "daily_day": 0, "cleared": [], "won": [], "flags": [], **data}


def _save_progress(conn: sqlite3.Connection, user_id: int, data: dict) -> None:
    conn.execute("INSERT INTO lumisoft_progress(user_id, data) VALUES (?, ?) ON CONFLICT(user_id) DO UPDATE SET data = excluded.data",
                 (user_id, json.dumps(data, separators=(",", ":"))))


def _flags(mode: str, summary: dict, practice: bool) -> set[str]:
    """What this run achieved, as the names PRIZES use (progress-wide ones like pops-500 are checked separately)."""
    got = set()
    if mode == "training" and summary["done"]:
        got.add("training")
    if mode == "story" and summary["done"] and not practice:
        got.add("story")
    for row in summary["cleared"]:
        if row["stage"] == "training":
            continue
        got.add("clear:" + row["stage"])
        if row["rank"] == "S":
            got.add("rank-s")
        if row["bossLost"] == 0:
            got.add("boss-clean")
        if row["friends"] == 0:
            got.add("no-friends")
        if row["perfect"]:
            got.add("perfect")
        if row["lost"] == 0:
            got.add("hearts")
    if summary["badges"] >= 25:
        got.add("badges-25")
    if summary["best"] >= rules.COMBO_MAX:                # ×3.0
        got.add("combo")
    return got


def _prize_info(item: str) -> dict | None:
    """A prize's name and how it's won, from Lumi Pet's catalogs (None if the catalog doesn't have it)."""
    piece = lumipet._DECOR.get(item) or lumipet._WEAR.get(item)
    return {"id": item, "name": piece["name"], "how": piece.get("how", ""), "closet": item in lumipet._WEAR} if piece else None


def _give(conn: sqlite3.Connection, user_id: int, items: list[str]) -> None:
    """Put won prizes in Lumi's house or closet (a piece she already has is left alone)."""
    known = [i for i in items if _prize_info(i)]
    if not known:
        return
    pet = lumipet.load(conn, user_id, seen=False)          # caught up and saved; not a visit
    for item in known:
        bag = pet["closet"] if item in lumipet._WEAR else pet["decor"]
        if item not in bag:
            bag.append(item)
    lumipet._save(conn, user_id, pet)


# ------------------------------------------------------------------ what the page needs
def board(conn: sqlite3.Connection, name: str, limit: int = 10) -> list[dict]:
    """A board's best scores, best first, with each player's name."""
    return [{"name": r["username"], "score": r["score"], "rank": r["rank"]} for r in conn.execute(
        "SELECT u.username, b.score, b.rank FROM lumisoft_bests b JOIN users u ON u.id = b.user_id WHERE b.board = ? "
        "ORDER BY b.score DESC, b.at LIMIT ?", (name, limit))]


def state(conn: sqlite3.Connection, user_id: int) -> dict:
    """Everything the title screen shows: the stages, prices, this person's bests and prizes, and today's Daily Range."""
    progress, day = _progress(conn, user_id), day_number(conn)
    segs = rules.plan(DATA, "daily", None, day)
    bests = {r["board"]: {"score": r["score"], "rank": r["rank"]} for r in conn.execute(
        "SELECT board, score, rank FROM lumisoft_bests WHERE user_id = ? AND board NOT LIKE 'daily:%'", (user_id,))}
    prizes = [dict(info, won=item in progress["won"]) for item, _ in PRIZES if (info := _prize_info(item))]
    return {"stages": DATA, "entry": ENTRY, "continues": list(CONTINUES), "points_per_coin": POINTS_PER_COIN,
            "coins": coins.peek(conn, user_id), "bests": bests, "cleared": progress["cleared"], "pops": progress["pops"],
            "dailies": progress["dailies"], "prizes": prizes,
            "daily": {"day": day, "stage": DATA["stages"][segs[0]["stage"]]["id"], "scene": segs[0]["scene"],
                      "tried": progress["daily_day"] == day, "board": board(conn, f"daily:{day}")}}


# ------------------------------------------------------------------ a run
def start(conn: sqlite3.Connection, user_id: int, mode: str, stage: str | None = None, now: float | None = None) -> dict:
    """A ticket for a new run. Charges ENTRY unless it's training or a Daily Range retry (practice). A Daily Range's
    first try of the day is the scored one, even if it's quit: it's marked tried as it starts."""
    if mode not in MODES:
        raise Refused("Choose Story, a stage, the Daily Range or the Training Range.", 422)
    if mode == "stage":
        progress = _progress(conn, user_id)
        if stage not in STAGES:
            raise Refused("There's no such stage.", 404)
        if STAGES.index(stage) > 0 and STAGES[STAGES.index(stage) - 1] not in progress["cleared"]:
            raise Refused("Clear the stage before it first (in Story or Stage select).", 403)
    else:
        stage = None
    day = day_number(conn) if mode == "daily" else 0
    progress = _progress(conn, user_id)
    practice = mode == "training" or (mode == "daily" and progress["daily_day"] == day)
    if not practice and not coins.spend(conn, user_id, ENTRY, GAME, "entry"):
        raise Refused(f"A run costs {ENTRY} coins.", 402)
    if mode == "daily" and not practice:
        progress["daily_day"] = day
        progress["dailies"] += 1
        _save_progress(conn, user_id, progress)
    conn.execute("DELETE FROM lumisoft_runs WHERE user_id = ? AND finished = 0", (user_id,))   # one run at a time
    conn.execute("DELETE FROM lumisoft_runs WHERE finished = 1 AND started_at < ?", ((now or time.time()) - 86400,))
    run = conn.execute("INSERT INTO lumisoft_runs(user_id, mode, stage, day, practice, started_at) VALUES (?, ?, ?, ?, ?, ?)",
                       (user_id, mode, stage, day, int(practice), time.time() if now is None else now)).lastrowid
    return {"run": run, "mode": mode, "stage": stage, "day": day, "practice": practice, "plan": rules.plan(DATA, mode, stage, day),
            "entry": 0 if practice else ENTRY, "coins": coins.peek(conn, user_id), "next_continue": 0 if practice else continue_price(0)}


def _run(conn: sqlite3.Connection, user_id: int, run: int) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM lumisoft_runs WHERE id = ? AND user_id = ? AND finished = 0", (run, user_id)).fetchone()
    if row is None:
        raise Refused("That run has already finished, or a newer one started.", 404)
    return row


def carry_on(conn: sqlite3.Connection, user_id: int, run: int) -> dict:
    """Pay for a continue (free on a practice run). The page continues once this answers; the replay allows as many
    continues as were paid for here."""
    row = _run(conn, user_id, run)
    if row["mode"] == "training":
        raise Refused("The Training Range has no game over.", 409)
    price = 0 if row["practice"] else continue_price(row["continues"])
    if price and not coins.spend(conn, user_id, price, GAME, "continue"):
        raise Refused(f"A continue costs {price} coins.", 402)
    conn.execute("UPDATE lumisoft_runs SET continues = continues + 1 WHERE id = ?", (run,))
    return {"continues": row["continues"] + 1, "paid": price, "coins": coins.peek(conn, user_id),
            "next_continue": 0 if row["practice"] else continue_price(row["continues"] + 1)}


def finish(conn: sqlite3.Connection, user_id: int, run: int, log: dict, now: float | None = None) -> dict:
    """The run is over (cleared, out of continues, or quit). Replay it, pay it once, keep its best scores, and hand out
    any prizes it won. Returns what the server made of it: the page shows this score, not its own."""
    row = _run(conn, user_id, run)
    conn.execute("UPDATE lumisoft_runs SET finished = 1 WHERE id = ?", (run,))
    segs = rules.plan(DATA, row["mode"], row["stage"], row["day"])
    allowed = int(((time.time() if now is None else now) - row["started_at"]) * 60) + SLACK_TICKS
    claimed = {"shots": log.get("shots") or [], "reloads": log.get("reloads") or [], "end": int(log.get("end") or 0),
               "continues": min(int(log.get("continues") or 0), row["continues"])}   # no more continues than were paid for
    result = {"run": run, "earned": 0, "paid": 0, "capped": False, "refused": None, "practice": bool(row["practice"]), "won": [], "bests": []}
    if claimed["end"] > allowed:
        result["refused"] = "That run doesn't add up, so it can't pay."
        state_ = rules.replay(DATA, segs, {**claimed, "shots": [], "reloads": [], "end": 0})
    else:
        state_ = rules.replay(DATA, segs, claimed)
    summary = rules.summary(state_)
    result["summary"] = summary
    if result["refused"]:
        result["coins"] = coins.peek(conn, user_id)
        return result
    stamp = time.time() if now is None else now
    if not row["practice"]:
        result["earned"] = summary["score"] // POINTS_PER_COIN
        cap, ref = daily_cap(conn), f"{GAME}:{run}"
        result["paid"] = coins.reward(conn, user_id, result["earned"], GAME, cap, ref) if cap else coins.prize(conn, user_id, result["earned"], GAME, ref)
        result["capped"] = result["paid"] < result["earned"]
        boards = [(f"stage:{c['stage']}", c["score"], c["rank"]) for c in summary["cleared"] if c["stage"] != "training"]
        if row["mode"] == "story":
            boards.append(("story", summary["score"], ""))
        if row["mode"] == "daily":
            boards.append((f"daily:{row['day']}", summary["score"], summary["cleared"][0]["rank"] if summary["cleared"] else ""))
        for name, score, grade in boards:
            old = conn.execute("SELECT score FROM lumisoft_bests WHERE user_id = ? AND board = ?", (user_id, name)).fetchone()
            if old is None or score > old["score"]:
                conn.execute("INSERT INTO lumisoft_bests(user_id, board, score, rank, at) VALUES (?, ?, ?, ?, ?) ON CONFLICT(user_id, board) "
                             "DO UPDATE SET score = excluded.score, rank = excluded.rank, at = excluded.at", (user_id, name, score, grade, stamp))
                result["bests"].append(name)
    progress = _progress(conn, user_id)
    progress["pops"] += summary["pops"]
    for c in summary["cleared"]:
        if c["stage"] != "training" and c["stage"] not in progress["cleared"]:
            progress["cleared"].append(c["stage"])
    flags = set(progress["flags"]) | _flags(row["mode"], summary, bool(row["practice"]) and row["mode"] != "training")
    if progress["pops"] >= 500:
        flags.add("pops-500")
    if progress["dailies"] >= 10:
        flags.add("dailies-10")
    progress["flags"] = sorted(flags)
    new = [item for item, need in PRIZES if need in flags and item not in progress["won"]]
    progress["won"] += new
    _save_progress(conn, user_id, progress)
    _give(conn, user_id, new)
    result["won"] = [info for item in new if (info := _prize_info(item))]
    result["coins"] = coins.peek(conn, user_id)
    return result
