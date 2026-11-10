"""The coins the Media games share: one purse per account, on this server only.

Built the way LumiBot's SadGirlCoin is (C:/Scripts/LumiBot/src/sadgirlEconomyStore.js): accounts with running totals, a
permanent list of every movement, and a house account like Momiji Casino that bets go to and wins come from. It is not
connected to SadGirlCoin, and coins have no cash value.

Who pays what:
- Online casino tables bet and win from the seat's account (casino.py). A TV at an online table is the account it's paired to.
- Local multiplayer (phone remotes around one TV) never touches a purse: those seats play on table-only credits.
- Prism Drop and Lumi Pet pay rewards, and only for what the server saw itself, because a browser can claim anything.

Every function that changes money runs inside its caller's transaction (db.transaction), so a move and the game state
that caused it are saved together or not at all.
"""

from __future__ import annotations

import sqlite3

HOUSE = "__HOUSE__"
START = 1000                  # a new purse
PRISM_POINTS_PER_COIN = 10    # a finished Prism Drop game pays its score divided by this
PRISM_DAILY_CAP = 200         # the most Prism Drop pays one account in a day
PET_VISIT = 25                # the first Lumi Pet visit of the day


def _account(user_id: int) -> str:
    return f"user:{user_id}"


def _move(conn: sqlite3.Connection, source: str | None, target: str, amount: int, kind: str, game: str,
          ref: str | None = None) -> bool:
    """Write one movement and apply it to both balances. False: this `ref` has already been paid, so nothing moved."""
    written = conn.execute("INSERT OR IGNORE INTO coin_transactions(from_account, to_account, amount, type, game, ref) "
                           "VALUES (?, ?, ?, ?, ?, ?)", (source, target, amount, kind, game, ref))
    if written.rowcount == 0:
        return False
    if source is not None:                                # NULL source: new coins, from nowhere
        conn.execute("UPDATE coin_accounts SET balance = balance - ?, total_spent = total_spent + ?, updated_at = datetime('now') "
                     "WHERE id = ?", (amount, amount, source))
    conn.execute("UPDATE coin_accounts SET balance = balance + ?, total_earned = total_earned + ?, updated_at = datetime('now') "
                 "WHERE id = ?", (amount, amount, target))
    return True                                           # the CHECK on balance refuses an overdraft, rolling the whole thing back


def held(conn: sqlite3.Connection, user_id: int) -> int | None:
    """This account's balance, opening its purse the first time. None: the account no longer exists."""
    row = conn.execute("SELECT balance FROM coin_accounts WHERE user_id = ?", (user_id,)).fetchone()
    if row is not None:
        return row["balance"]
    if conn.execute("SELECT 1 FROM users WHERE id = ?", (user_id,)).fetchone() is None:
        return None
    conn.execute("INSERT INTO coin_accounts(id, user_id) VALUES (?, ?)", (_account(user_id), user_id))
    _move(conn, None, _account(user_id), START, "start", "")
    return START


def peek(conn: sqlite3.Connection, user_id: int) -> int:
    """The balance to show; writes nothing, so a purse that hasn't opened yet reads as what it will start with."""
    row = conn.execute("SELECT balance FROM coin_accounts WHERE user_id = ?", (user_id,)).fetchone()
    return row["balance"] if row is not None else START


def table(conn: sqlite3.Connection, user_id: int, delta: int, game: str, why: str | None = None) -> None:
    """What a casino table did to this account: minus is a bet to the house, plus a win from it. why: 'refund' for stakes
    handed back, 'refill' for the free refill, which is new coins."""
    account = _account(user_id)
    if delta < 0:
        _move(conn, account, HOUSE, -delta, f"{game}_bet", game)
    elif delta > 0 and why == "refill":
        _move(conn, None, account, delta, "refill", game)
    elif delta > 0:
        _move(conn, HOUSE, account, delta, f"{game}_refund" if why == "refund" else f"{game}_win", game)


def spend(conn: sqlite3.Connection, user_id: int, amount: int, game: str, what: str = "buy") -> bool:
    """Pay the house for something a game sells (a snack, a toy). False: not enough coins, and nothing moved.
    Checks the balance first, so a refusal never has to roll back the caller's transaction."""
    balance = held(conn, user_id)                         # opens the purse on a first purchase
    if amount <= 0 or balance is None or balance < amount:
        return False
    return _move(conn, _account(user_id), HOUSE, amount, f"{game}_{what}", game)


def today(conn: sqlite3.Connection) -> str:
    """The server's date, the one every daily limit counts by."""
    return conn.execute("SELECT date('now', 'localtime')").fetchone()[0]


def reward(conn: sqlite3.Connection, user_id: int, amount: int, game: str, cap: int, ref: str | None = None) -> int:
    """Pay new coins for something the server checked, up to `cap` from this game today. Returns what was actually paid."""
    if amount <= 0 or held(conn, user_id) is None:
        return 0
    account = _account(user_id)
    paid_today = conn.execute("SELECT COALESCE(SUM(amount), 0) FROM coin_transactions WHERE to_account = ? AND type = 'reward' "
                              "AND game = ? AND created_at >= datetime('now', '-2 days') "
                              "AND date(created_at, 'localtime') = date('now', 'localtime')", (account, game)).fetchone()[0]
    paying = min(amount, max(0, cap - paid_today))
    if paying <= 0:
        return 0
    return paying if _move(conn, None, account, paying, "reward", game, ref) else 0


def prize(conn: sqlite3.Connection, user_id: int, amount: int, game: str, ref: str) -> int:
    """New coins for something the server ran itself and that can only be won once, like a Lumi Pet fight: no daily
    limit (Doll's call: coins are for fun), but `ref` is required, so the same win can never pay twice."""
    if amount <= 0 or held(conn, user_id) is None:
        return 0
    return amount if _move(conn, None, _account(user_id), amount, "reward", game, ref) else 0


def pet_visit(conn: sqlite3.Connection, user_id: int) -> int:
    """Lumi Pet's first visit of the day, by the server's date. Nothing is lost by missing a day: no streaks."""
    return reward(conn, user_id, PET_VISIT, "lumi-pet", PET_VISIT, ref=f"lumi-pet:{user_id}:{today(conn)}")
