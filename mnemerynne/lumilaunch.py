"""Lumi Launch on the server (Doll's break game, 2026-11-14): a go's ticket and entry fee, the replay check, coins and
the household's best launches.

A go is three launches on one seeded course. The page plays it with static/games/lumi-launch/rules.js and sends only
each launch's presses ([frame, button], ...). Nothing it says about distance is believed: lumilaunch_rules.replay()
flies Lumi again here from the same presses, and that distance is what pays: a metre count divided by
METRES_PER_COIN, up to coins.LAUNCH_DAILY_CAP a day, once per go. Couch turns on a TV are free play and never come
here (Doll's rule: local multiplayer uses free credits, online uses the account's coins).

Every call here runs inside the caller's transaction."""

from __future__ import annotations

import sqlite3
import time

from mnemerynne import arcade, coins, lumilaunch_rules as rules

GAME = "lumi-launch"
ENTRY = 25                                  # a go of three launches, like a run in the other arcade games
METRES_PER_COIN = 20                        # a typical go (~1,350 m with the bot's random play) pays about 68
SLACK_FRAMES = 600                          # a go may claim 10 s more play than the wall clock allows (slow tabs, rounding)
BUTTONS = ("ok", "up", "down")              # what a launch's log may hold (rules.press)


class Refused(ValueError):
    """A safe, actionable explanation for the player; nothing was changed."""

    def __init__(self, message: str, status: int = 409) -> None:
        super().__init__(message)
        self.status = status


# ------------------------------------------------------------------ what the page shows
def board(conn: sqlite3.Connection, limit: int = 10) -> list[dict]:
    """The household's longest goes, longest first, with each player's name and their longest single launch."""
    return [{"name": r["username"], "go": r["best_go"], "shot": r["best_shot"]} for r in conn.execute(
        "SELECT u.username, b.best_go, b.best_shot FROM lumi_launch_bests b JOIN users u ON u.id = b.user_id "
        "WHERE b.best_go > 0 ORDER BY b.best_go DESC, b.at LIMIT ?", (limit,))]


def _best(conn: sqlite3.Connection, user_id: int) -> dict:
    """This person's own bests (zeros before their first go)."""
    row = conn.execute("SELECT best_go, best_shot FROM lumi_launch_bests WHERE user_id = ?", (user_id,)).fetchone()
    return {"go": row["best_go"], "shot": row["best_shot"]} if row else {"go": 0, "shot": 0}


def state(conn: sqlite3.Connection, user_id: int) -> dict:
    """The title screen: the price, how it pays, this person's coins and bests, and the household board."""
    return {"entry": ENTRY, "metres_per_coin": METRES_PER_COIN, "cap": coins.LAUNCH_DAILY_CAP, "shots": rules.SHOTS,
            "coins": coins.peek(conn, user_id), "best": _best(conn, user_id), "board": board(conn)}


# ------------------------------------------------------------------ a go
def start(conn: sqlite3.Connection, user_id: int, now: float | None = None) -> dict:
    """A ticket for a new go: charges ENTRY and picks the course. Starting again drops an unfinished go (unpaid)."""
    if not coins.spend(conn, user_id, ENTRY, GAME, "entry"):
        raise Refused(f"A go costs {ENTRY} coins.", 402)
    stamp = time.time() if now is None else now
    conn.execute("DELETE FROM lumi_launch_runs WHERE user_id = ? AND finished = 0", (user_id,))   # one go at a time
    conn.execute("DELETE FROM lumi_launch_runs WHERE finished = 1 AND started_at < ?", (stamp - 86400,))   # a day's history is plenty
    seed = arcade.new_seed()
    run = conn.execute("INSERT INTO lumi_launch_runs(user_id, seed, started_at) VALUES (?, ?, ?)",
                       (user_id, seed, stamp)).lastrowid
    return {"run": run, "seed": seed, "entry": ENTRY, "shots": rules.SHOTS, "coins": coins.peek(conn, user_id)}


def _tidy(shots: list) -> list[list[list]]:
    """The logs as rules.replay wants them, or Refused if they can't be a real go: at most SHOTS launches, known
    buttons only, and each launch's frames in order (the page records them as they happen)."""
    if len(shots) > rules.SHOTS:
        raise Refused(f"A go has {rules.SHOTS} launches.", 422)
    out = []
    for log in shots:
        last, clean = 0, []
        for frame, button in log:
            if button not in BUTTONS or frame < last:
                raise Refused("That go doesn't add up.", 422)
            last = frame
            clean.append([int(frame), button])
        out.append(clean)
    return out


def finish(conn: sqlite3.Connection, user_id: int, run: int, shots: list, now: float | None = None) -> dict:
    """The go is over (all three launches, or quit early: what was launched still counts). Replay it, pay it once and
    keep the bests. Returns what the server made of it: the page shows these distances, not its own."""
    row = conn.execute("SELECT * FROM lumi_launch_runs WHERE id = ? AND user_id = ? AND finished = 0",
                       (run, user_id)).fetchone()
    if row is None:
        raise Refused("That go has already finished, or a newer one started.", 404)
    logs = _tidy(shots)
    stamp = time.time() if now is None else now
    conn.execute("UPDATE lumi_launch_runs SET finished = 1 WHERE id = ?", (run,))
    summary = rules.replay(row["seed"], logs)
    allowed = int((stamp - row["started_at"]) * 60) + SLACK_FRAMES
    result = {"run": run, "summary": summary, "earned": 0, "paid": 0, "capped": False, "refused": None,
              "new_best": {"go": False, "shot": False}}
    if summary["frames"] > allowed:                      # more flying than there was time for: not a real go
        result["refused"] = "That go doesn't add up, so it can't pay."
        result.update(best=_best(conn, user_id), coins=coins.peek(conn, user_id))
        return result
    result["earned"] = summary["total"] // METRES_PER_COIN
    result["paid"] = coins.reward(conn, user_id, result["earned"], GAME, coins.LAUNCH_DAILY_CAP, f"{GAME}:{run}")
    result["capped"] = result["paid"] < result["earned"]
    conn.execute("UPDATE lumi_launch_runs SET score = ?, paid = ? WHERE id = ?", (summary["total"], result["paid"], run))
    old = _best(conn, user_id)
    longest = max([s["m"] for s in summary["shots"]] or [0])
    result["new_best"] = {"go": summary["total"] > old["go"], "shot": longest > old["shot"]}
    if any(result["new_best"].values()):
        conn.execute("INSERT INTO lumi_launch_bests(user_id, best_go, best_shot, at) VALUES (?, ?, ?, ?) "
                     "ON CONFLICT(user_id) DO UPDATE SET best_go = MAX(best_go, excluded.best_go), "
                     "best_shot = MAX(best_shot, excluded.best_shot), at = excluded.at",
                     (user_id, summary["total"], longest, stamp))
    result.update(best=_best(conn, user_id), coins=coins.peek(conn, user_id), board=board(conn))
    return result
