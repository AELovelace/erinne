"""StickBob Runner's coins. The game runs in the browser (static/games/stickbob-runner/), so the server never sees the run
itself; Doll chose to pay for it anyway, with these guard rails instead of a replay:

- Pressing Run asks for a ticket, which records when the server saw the run start. One ticket per person at a time.
- At game over the page sends the ticket with its distance and kills. A ticket pays once and is then gone.
- A claim faster than StickBob can run, or with more kills than could fit in the time, pays nothing.
- An admin can cap what one person earns from it a day (Settings → Entertainment → Media → Games). 0, the default, is no cap.

Someone could still script a believable claim. The cap is the knob if that ever happens; coins have no cash value.
"""

from __future__ import annotations

import sqlite3
import time

from mnemerynne import coins, media

GAME = "stickbob-runner"                  # the slug in games.py, and the `game` on its coin_transactions
METERS_PER_COIN = 10                      # a run pays its distance divided by this...
COINS_PER_KILL = 1                        # ...plus this for each SlopBot the player took down
MAX_METERS_PER_SECOND = 27.0              # top speed is 8 px a step × 60 steps ÷ 18.29 px a metre = 26.25 m/s (rules.js)
SPARE_METERS = 20                         # a little room for the first moments and clock differences
MAX_KILLS_PER_SECOND = 3                  # far more than the levels hold: only a wild claim fails this
SPARE_KILLS = 5


def daily_cap(conn: sqlite3.Connection) -> int:
    """The most one person may earn from StickBob Runner a day (Media settings 'runner_daily_cap'). 0: no limit."""
    return int(media.load_settings(conn).get("runner_daily_cap") or 0)


def start(conn: sqlite3.Connection, user_id: int, now: float | None = None) -> int:
    """A ticket for a new run. Any older ticket of this person's is dropped, so one run at a time can earn."""
    conn.execute("DELETE FROM runner_runs WHERE user_id = ?", (user_id,))
    return conn.execute("INSERT INTO runner_runs(user_id, started_at) VALUES (?, ?)",
                        (user_id, time.time() if now is None else now)).lastrowid


def finish(conn: sqlite3.Connection, user_id: int, run: int, meters: int, kills: int, now: float | None = None) -> dict:
    """Pay a finished run, if it adds up. Returns what it earned and what was paid (less than earned when today's cap
    is reached), the balance, and `refused` with a reason when it pays nothing for a reason other than the cap."""
    row = conn.execute("SELECT started_at FROM runner_runs WHERE id = ? AND user_id = ?", (run, user_id)).fetchone()
    result = {"earned": 0, "paid": 0, "capped": False, "refused": None}
    if row is None:                       # used already, replaced by a newer run, or someone else's
        result["refused"] = "This run was already counted, or a newer one started."
    else:
        conn.execute("DELETE FROM runner_runs WHERE id = ?", (run,))   # used up, whatever happens next
        seconds = max(0.0, (time.time() if now is None else now) - row["started_at"])
        if meters > MAX_METERS_PER_SECOND * seconds + SPARE_METERS or kills > MAX_KILLS_PER_SECOND * seconds + SPARE_KILLS:
            result["refused"] = "That run doesn't add up, so it can't pay."
        else:
            earned = meters // METERS_PER_COIN + kills * COINS_PER_KILL
            ref, cap = f"{GAME}:{user_id}:{run}", daily_cap(conn)
            paid = coins.reward(conn, user_id, earned, GAME, cap, ref) if cap else coins.prize(conn, user_id, earned, GAME, ref)
            result.update(earned=earned, paid=paid, capped=paid < earned)
    result["coins"] = coins.peek(conn, user_id)
    return result
