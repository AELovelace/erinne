"""Kiosk settings for a Mnemerynne screen box (deploy/screen/): the admin unlock, and the screen's
settings the box may change while unlocked.

The box's settings page is served by the box's own helper (deploy/screen/wifi-setup.py on
127.0.0.1:9187), which can't tell an admin from anyone at the TV. So the TV asks the server:

1. The TV page (signed in as its screen) asks for a code (Unlocks.start) and shows it with a QR code.
2. A Mnemerynne admin opens /media/kiosk?code=… on their phone and approves (Unlocks.approve).
3. The TV, asking every few seconds, collects a one-time grant (Unlocks.collect) and opens the box's
   settings page with it in the URL's fragment.
4. The helper redeems the grant with the server (Unlocks.redeem), server to server, for a session:
   a token good for SESSION_SECONDS that lets it read and change that one screen's settings (the top
   bar), and that says which admin approved.

Everything is in memory (one uvicorn process): a restart just means approving again. Codes use the
pairing alphabet and length (screens.py), last CODE_SECONDS, and each admin may type only a few wrong
ones. Grants and session tokens are 32 random bytes; only their SHA-256 is kept.
"""

from __future__ import annotations

import hashlib
import secrets
import sqlite3
import time

from mnemerynne.media import screens
from mnemerynne.media.screens import ScreenBusy, ScreenError

CODE_SECONDS = 300            # how long the TV's code waits for an admin
GRANT_SECONDS = 120           # how long the TV has to hand its grant to the box's helper
SESSION_SECONDS = 600         # an unlocked settings page, renewed while it is used
MAX_WAITING = 200             # codes waiting at once, server-wide
APPROVE_TRIES = 10            # wrong codes an admin may type per APPROVE_WINDOW
APPROVE_WINDOW = 600


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class Unlocks:
    """Codes on TVs waiting for an admin, grants waiting for the box's helper, and helper sessions."""

    def __init__(self, clock=time.monotonic) -> None:
        self.clock = clock
        self._codes: dict[str, dict] = {}        # code -> {secret, screen, until, admin, grant}
        self._secrets: dict[str, str] = {}       # the TV's secret -> code
        self._grants: dict[str, dict] = {}       # sha256(grant) -> {screen, admin, until}
        self._sessions: dict[str, dict] = {}     # sha256(session) -> {screen, admin, until}
        self._wrong: dict[int, list[float]] = {}

    def _sweep(self, now: float) -> None:
        for code in [c for c, p in self._codes.items() if p["until"] <= now]:
            self._secrets.pop(self._codes.pop(code)["secret"], None)
        for table in (self._grants, self._sessions):
            for key in [k for k, v in table.items() if v["until"] <= now]:
                del table[key]

    # -- the TV
    def start(self, screen_id: int) -> tuple[str, str]:
        """(code, secret) for the TV: it shows the code and asks with the secret."""
        now = self.clock()
        self._sweep(now)
        if len(self._codes) >= MAX_WAITING:
            raise ScreenBusy("Too many screens are waiting for an admin. Try again in a few minutes.")
        code = "".join(secrets.choice(screens.PAIR_ALPHABET) for _ in range(screens.PAIR_LENGTH))
        while code in self._codes:
            code = "".join(secrets.choice(screens.PAIR_ALPHABET) for _ in range(screens.PAIR_LENGTH))
        secret = secrets.token_urlsafe(32)
        self._codes[code] = {"secret": secret, "screen": screen_id, "until": now + CODE_SECONDS,
                             "admin": None, "grant": None}
        self._secrets[secret] = code
        return code, secret

    def poll(self, screen_id: int, secret: str | None) -> dict:
        """What the TV asks every few seconds: waiting (seconds left), approved (the grant, once) or expired."""
        now = self.clock()
        self._sweep(now)
        code = self._secrets.get(secret or "")
        hit = self._codes.get(code) if code else None
        if hit is None or hit["screen"] != screen_id:
            return {"state": "expired"}
        if hit["admin"] is None:
            return {"state": "waiting", "expires_in": int(hit["until"] - now)}
        grant = secrets.token_urlsafe(32)
        self._grants[_hash(grant)] = {"screen": screen_id, "admin": hit["admin"], "until": now + GRANT_SECONDS}
        del self._codes[code]
        del self._secrets[secret]
        return {"state": "approved", "grant": grant, "admin": hit["admin"]["name"]}

    # -- the admin's phone
    def check(self, user_id: int, code: str) -> tuple[str, int]:
        """(code, screen id) of a code waiting for approval; counts the admin's wrong tries."""
        now = self.clock()
        self._sweep(now)
        tries = [t for t in self._wrong.get(user_id, []) if t > now - APPROVE_WINDOW]
        self._wrong[user_id] = tries
        if len(tries) >= APPROVE_TRIES:
            raise ScreenBusy("Too many wrong codes. Wait ten minutes and try again.")
        code = screens.clean_code(code)
        hit = self._codes.get(code)
        if hit is None or hit["admin"] is not None:
            tries.append(now)
            raise ScreenError("That code isn't on any screen waiting for an admin. Check it, or open Kiosk "
                              "settings on the TV again for a new one.")
        return code, hit["screen"]

    def approve(self, code: str, admin_id: int, admin_name: str) -> None:
        hit = self._codes[code]
        hit["admin"] = {"id": admin_id, "name": admin_name}
        hit["until"] = max(hit["until"], self.clock() + GRANT_SECONDS)

    # -- the box's helper
    def redeem(self, grant: str) -> tuple[str, dict]:
        """A grant (once) for a session token: (token, {screen, admin: {id, name}, expires_in})."""
        now = self.clock()
        self._sweep(now)
        hit = self._grants.pop(_hash(grant or ""), None)
        if hit is None:
            raise ScreenError("That approval has run out or was used already. Approve again from a phone.")
        token = secrets.token_urlsafe(32)
        self._sessions[_hash(token)] = {"screen": hit["screen"], "admin": hit["admin"], "until": now + SESSION_SECONDS}
        return token, {"screen": hit["screen"], "admin": hit["admin"], "expires_in": SESSION_SECONDS}

    def session(self, token: str | None) -> dict | None:
        """The session's {screen, admin: {id, name}}, renewed for another SESSION_SECONDS; None when unknown or over."""
        now = self.clock()
        self._sweep(now)
        hit = self._sessions.get(_hash(token or ""))
        if hit is None:
            return None
        hit["until"] = now + SESSION_SECONDS
        return {"screen": hit["screen"], "admin": hit["admin"]}

    def end(self, token: str | None) -> None:
        self._sessions.pop(_hash(token or ""), None)


# ------------------------------------------------------------- screen settings
def settings_of(row: sqlite3.Row) -> dict:
    return {"id": row["id"], "name": row["name"], "top_bar": bool(row["top_bar"])}


def set_top_bar(conn: sqlite3.Connection, screen_id: int, on: bool) -> None:
    conn.execute("UPDATE media_screens SET top_bar = ? WHERE id = ?", (1 if on else 0, screen_id))
