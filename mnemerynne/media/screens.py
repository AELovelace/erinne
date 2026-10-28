"""Screens: a browser showing Media (the TV) that its owner casts to from another device (the phone).

Pairing: the owner, signed in on the TV, names it; the TV gets a screen key in a long-lived cookie
(only its hash is stored). The key opens Media, and nothing else, as its owner, so the TV keeps
working after sign-in sessions expire; removing the screen revokes it.

Pairing with a code, for a TV with no keyboard (the kiosk in screen/): an unpaired browser on
/media/screen asks for a code (Pairings, in memory) and shows it with a QR code; the owner types or
scans it on their phone and names the screen; the TV, asking every few seconds, then gets its key.
Codes last ten minutes, and each person may only try a few wrong ones.

Casting: the phone posts a command (play this film, album, station, live source or YouTube link;
pause, seek, volume, next, stop) for one of its owner's screens. The web app hands it to the screen's
open event stream (Hub, in memory: this is one uvicorn process), the screen's page acts on it and
reports what it is playing back every few seconds, which is what the phone's remote shows.

Taking over: a screen box (is_box) listens on every page, not only the TV page. Someone using the TV
(any page but the TV page) gets TAKEOVER_SECONDS to cancel a command before the TV goes back to the TV
page to carry it out; an admin may override that. Each command has an id, and the screen answers how
it went (Hub.answer: waiting, done, kept), which the phone asks after (Hub.answer_of).

Only members of the screens group (Settings → Media; AD or Entra groups) may pair screens and cast;
with no group set, nobody may. It is checked live on every request, the screen key's included: a
screen whose owner leaves the group stops working at once. Owners may always remove their screens.
"""

from __future__ import annotations

import asyncio
import hashlib
import secrets
import sqlite3
import time

from mnemerynne import group_refs, media

COOKIE = "mnemerynne_screen"
COOKIE_DAYS = 400               # browsers cap cookies here; it's renewed whenever the screen connects
MAX_PER_USER = 10
MAX_SAFE = 10.0                 # underscan: at most this percentage in from each edge
STATE_SECONDS = 15              # a screen silent this long counts as gone from the remote's point of view
TAKEOVER_SECONDS = 10           # someone using the TV has this long to keep it before a command takes over
COMMAND_SECONDS = 120           # how long a command's answer (waiting, done, kept) is kept for the phone

PAIR_COOKIE = "mnemerynne_screen_pairing"
PAIR_SECONDS = 600              # how long a pairing code lasts
COLLECT_SECONDS = 120           # at least this long, once claimed, for the TV to collect its key
PAIR_ALPHABET = "BCDFGHJKLMNPQRSTVWXZ23456789"   # no vowels (no words), nothing that looks like 0/O or 1/I
PAIR_LENGTH = 8                 # 28**8: about 3.8e11 codes
MAX_PAIRINGS = 200              # codes waiting at once, server-wide: unpaired browsers needn't sign in
CLAIM_TRIES = 10                # wrong codes a person may type per CLAIM_WINDOW
CLAIM_WINDOW = 600


class ScreenError(ValueError):
    """A refusal; the message is safe to show."""
    status = 422


class ScreenBusy(ScreenError):
    status = 429


def may_use(conn: sqlite3.Connection, user, directory) -> bool:
    """In the screens group, and the Media group (Settings → Media)? No group set: nobody."""
    return (media.may_use(conn, user, directory)
            and group_refs.in_any(conn, user, directory, media.load_settings(conn)["screens_group"]))


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def clean_name(name: str) -> str:
    name = " ".join((name or "").split())
    if not name or len(name) > 40:
        raise ScreenError("Name the screen (up to 40 characters), e.g. Living room.")
    return name


def create(conn: sqlite3.Connection, user_id: int, name: str) -> tuple[int, str]:
    """(screen id, key) in the caller's transaction. A screen of the same name is re-paired: it gets a new
    key (the old one stops working) and keeps its id, so its signage and the phones' choice of it stay."""
    name = clean_name(name)
    token = secrets.token_urlsafe(32)
    same = conn.execute("SELECT id FROM media_screens WHERE user_id = ? AND lower(name) = lower(?)",
                        (user_id, name)).fetchone()
    if same is not None:
        conn.execute("UPDATE media_screens SET name = ?, token_hash = ?, created_at = datetime('now'), "
                     "last_seen_at = NULL WHERE id = ?", (name, _hash(token), same["id"]))
        return same["id"], token
    if conn.execute("SELECT count(*) FROM media_screens WHERE user_id = ?", (user_id,)).fetchone()[0] >= MAX_PER_USER:
        raise ScreenError(f"At most {MAX_PER_USER} screens; remove one first.")
    screen_id = conn.execute("INSERT INTO media_screens(user_id, name, token_hash) VALUES (?, ?, ?)",
                             (user_id, name, _hash(token))).lastrowid
    return screen_id, token


def by_token(conn: sqlite3.Connection, token: str | None) -> sqlite3.Row | None:
    """The screen a key belongs to, with its owner (active accounts only)."""
    if not token or len(token) > 200:
        return None
    return conn.execute("SELECT s.*, u.username FROM media_screens s JOIN users u ON u.id = s.user_id "
                        "WHERE s.token_hash = ? AND u.active = 1", (_hash(token),)).fetchone()


def owned(conn: sqlite3.Connection, user_id: int, screen_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM media_screens WHERE id = ? AND user_id = ?", (screen_id, user_id)).fetchone()


def of_user(conn: sqlite3.Connection, user_id: int) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM media_screens WHERE user_id = ? ORDER BY name COLLATE NOCASE",
                        (user_id,)).fetchall()


def of_others(conn: sqlite3.Connection, user_id: int) -> list[sqlite3.Row]:
    """Everyone else's screens, with their owners' names: admins may cast to them too."""
    return conn.execute("SELECT s.*, u.username AS owner FROM media_screens s JOIN users u ON u.id = s.user_id "
                        "WHERE s.user_id != ? ORDER BY u.username COLLATE NOCASE, s.name COLLATE NOCASE",
                        (user_id,)).fetchall()


def mark_box(conn: sqlite3.Connection, screen_id: int) -> None:
    """This screen is a screen box (its start page opened the TV page as ?kiosk=1)."""
    conn.execute("UPDATE media_screens SET is_box = 1 WHERE id = ? AND is_box = 0", (screen_id,))


def set_safe_area(conn: sqlite3.Connection, screen_id: int, x: float, y: float) -> tuple[float, float]:
    """Underscan for a TV that crops its picture: percent in from the sides (x) and the top and bottom (y),
    in half-percent steps. Returns what was stored."""
    def step(v: float) -> float:
        return round(max(0.0, min(MAX_SAFE, float(v))) * 2) / 2
    x, y = step(x), step(y)
    conn.execute("UPDATE media_screens SET safe_x = ?, safe_y = ? WHERE id = ?", (x, y, screen_id))
    return x, y


def seen(conn: sqlite3.Connection, screen_id: int) -> None:
    conn.execute("UPDATE media_screens SET last_seen_at = datetime('now') WHERE id = ?", (screen_id,))


class Hub:
    """Open screen streams and what each screen last said it was playing. Used from the event loop only."""

    def __init__(self, clock=time.monotonic) -> None:
        self._listeners: dict[int, set[asyncio.Queue]] = {}
        self._state: dict[int, tuple[float, dict]] = {}
        self._answers: dict[str, tuple[float, int, dict]] = {}   # command id: (when, screen, its answer)
        self._clock = clock

    def listen(self, screen_id: int) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=32)
        self._listeners.setdefault(screen_id, set()).add(queue)
        return queue

    def unlisten(self, screen_id: int, queue: asyncio.Queue) -> None:
        listeners = self._listeners.get(screen_id)
        if listeners is not None:
            listeners.discard(queue)
            if not listeners:
                del self._listeners[screen_id]

    def online(self, screen_id: int) -> bool:
        return bool(self._listeners.get(screen_id))

    def push(self, screen_id: int, command: dict) -> int:
        """Hand a command to every open stream of the screen; how many took it."""
        sent = 0
        for queue in list(self._listeners.get(screen_id, ())):
            try:
                queue.put_nowait(command)
                sent += 1
            except asyncio.QueueFull:   # a stuck tab: it gets the next one
                pass
        return sent

    def close(self, screen_id: int) -> None:
        """The screen was removed: end its streams."""
        self.push(screen_id, {"action": "unpaired"})

    def set_state(self, screen_id: int, state: dict) -> None:
        self._state[screen_id] = (self._clock(), state)

    def state(self, screen_id: int) -> dict | None:
        hit = self._state.get(screen_id)
        if hit is None or self._clock() - hit[0] > STATE_SECONDS:
            return None
        return hit[1]

    # -- commands that can wait for someone using the TV
    def send(self, screen_id: int, command: dict) -> str | None:
        """Push a command with a new id (its answer starts as "sent"); None when no stream took it."""
        now = self._clock()
        for key in [k for k, (at, _, _) in self._answers.items() if now - at > COMMAND_SECONDS]:
            del self._answers[key]
        command_id = secrets.token_urlsafe(9)
        if not self.push(screen_id, command | {"id": command_id}):
            return None
        self._answers[command_id] = (now, screen_id, {"state": "sent"})
        return command_id

    def answer(self, screen_id: int, command_id: str, answer: dict) -> bool:
        """The screen says how its command went; only the screen it was sent to may."""
        hit = self._answers.get(command_id)
        if hit is None or hit[1] != screen_id:
            return False
        self._answers[command_id] = (hit[0], screen_id, answer)
        return True

    def answer_of(self, screen_id: int, command_id: str) -> dict | None:
        hit = self._answers.get(command_id)
        if hit is None or hit[1] != screen_id or self._clock() - hit[0] > COMMAND_SECONDS:
            return None
        return hit[2]


def clean_code(code: str) -> str:
    """What was typed, as the code: case, spaces and dashes don't matter."""
    return "".join(ch for ch in (code or "").upper() if ch.isalnum())[:40]


def show_code(code: str) -> str:
    return code[:4] + "-" + code[4:]


class Pairings:
    """Codes unpaired TVs are showing, until someone claims them and the TV collects its key. Used from
    the event loop only; lost on restart (the TV just asks for a new code)."""

    def __init__(self) -> None:
        self._codes: dict[str, dict] = {}        # code -> {secret, until, token, screen}
        self._secrets: dict[str, str] = {}       # the TV's secret (its cookie) -> code
        self._wrong: dict[int, list[float]] = {}

    def _sweep(self, now: float) -> None:
        for code in [c for c, p in self._codes.items() if p["until"] <= now]:
            self._secrets.pop(self._codes.pop(code)["secret"], None)

    def start(self) -> tuple[str, str]:
        """(code, secret): the TV shows the code and keeps the secret, to collect its key with."""
        now = time.monotonic()
        self._sweep(now)
        if len(self._codes) >= MAX_PAIRINGS:
            raise ScreenBusy("Too many screens are waiting to be paired. Try again in a few minutes.")
        code = "".join(secrets.choice(PAIR_ALPHABET) for _ in range(PAIR_LENGTH))
        while code in self._codes:
            code = "".join(secrets.choice(PAIR_ALPHABET) for _ in range(PAIR_LENGTH))
        secret = secrets.token_urlsafe(32)
        self._codes[code] = {"secret": secret, "until": now + PAIR_SECONDS, "token": None, "screen": None}
        self._secrets[secret] = code
        return code, secret

    def waiting(self, secret: str | None) -> tuple[str, float] | None:
        """(code, seconds left) of the TV's pairing while it is still unclaimed; None once gone or claimed."""
        now = time.monotonic()
        self._sweep(now)
        code = self._secrets.get(secret or "")
        if code is None or self._codes[code]["token"] is not None:
            return None
        return code, self._codes[code]["until"] - now

    def check(self, user_id: int, code: str) -> str:
        """The code, if it is one waiting to be claimed; counts the person's wrong tries."""
        now = time.monotonic()
        self._sweep(now)
        tries = [t for t in self._wrong.get(user_id, []) if t > now - CLAIM_WINDOW]
        self._wrong[user_id] = tries
        if len(tries) >= CLAIM_TRIES:
            raise ScreenBusy("Too many wrong codes. Wait ten minutes and try again.")
        code = clean_code(code)
        hit = self._codes.get(code)
        if hit is None or hit["token"] is not None:
            tries.append(now)
            raise ScreenError("That code isn't on any screen waiting to pair. Check it, or get a new one "
                              "by reloading the screen.")
        return code

    def claim(self, code: str, screen_id: int, token: str) -> None:
        """The screen was made (check first): the TV collects its key on its next ask."""
        hit = self._codes[code]
        hit.update(token=token, screen=screen_id, until=max(hit["until"], time.monotonic() + COLLECT_SECONDS))

    def collect(self, secret: str | None) -> tuple[int, str] | None:
        """(screen id, key) once the TV's code was claimed, and only once."""
        code = self._secrets.get(secret or "")
        hit = self._codes.get(code) if code else None
        if hit is None or hit["token"] is None:
            return None
        del self._codes[code], self._secrets[secret]
        return hit["screen"], hit["token"]
