"""Who is in Lumi Town right now (2026-11-14), and what they say there (local chat, 2026-11-19): each person's Lumi
shows on the street, or in the place, where they are, for everyone else in town (static/games/lumi-pet/game.js
townHere, drawFolk), and what someone says is heard by everyone in the same place (a street's map, or inside one place).

The town page tells the server where its Lumi stands every few seconds, and at once when she moves (POST
/api/media/games/lumi-pet/town); the answer is everyone else seen within SEEN_SECONDS, and the last few lines said
where she is. In memory, like the game lobbies' presence (lobby.py): one web process, and a restart just means a few
seconds until everyone has said where they are again. Someone who turned "Show me in town" off (the pet save's
town_seen) is never recorded, is forgotten at once, and can't speak (nobody could see who was talking).

Chat (docs/plans/lumi-pet-social.md, phase 2) is ephemeral, by Doll's choice: the last LINES_KEPT lines of each place,
in memory, for LINE_SECONDS, then gone; never written to a database. A line is typed text (cleaned: no control
characters, one line, at most TEXT_MAX characters) or one of the PHRASES, so a TV remote or a gamepad can chat
without a keyboard. Pages hear new lines and moves at once on the event bus (CHANNEL, events.py), through their
stream (GET .../town/stream); a TV (whose play pass a stream can't carry) hears them on its next POST instead.
Someone may say a line every SAY_GAP seconds and SAY_BURST lines in SAY_WINDOW (events.count, so across processes)."""

from __future__ import annotations

import re
import secrets
import threading
import time
import unicodedata
from collections import deque

SEEN_SECONDS = 20          # a page that stopped asking (closed, hidden, gone to a game) drops out after this
MAX_PEOPLE = 200           # a guard: never more than this many remembered at once

CHANNEL = "lumi-town"      # the event bus channel: {"kind": "said", "line": {...}} or {"kind": "moved"}
TEXT_MAX = 140             # a typed line, at most (after cleaning)
LINES_KEPT = 30            # lines remembered per place, for someone who walks in
LINE_SECONDS = 600         # ...and only for this long (ten minutes): an empty room has no stale chatter
LINES_SHOWN = 8            # how many of them a POST hands back (a TV's way to hear them)
SAY_GAP, SAY_BURST, SAY_WINDOW = 1.5, 10, 30.0   # one line per 1.5 s, at most 10 in 30 s

# Quick phrases: chat with a remote, a gamepad or a finger, no keyboard needed. id → (icon, words). The ids are what a
# page sends; the words are only ever these, so a phrase can't be anything else.
PHRASES = {
    "hi": ("👋", "Hi!"),
    "hello": ("🏘", "Hello, neighbour!"),
    "play": ("🎮", "Want to play together?"),
    "follow": ("✨", "Follow me!"),
    "outfit": ("👗", "I love your outfit!"),
    "gg": ("🏆", "Good game!"),
    "brb": ("⏳", "Back in a minute!"),
    "bye": ("🌙", "See you later!"),
    "heart": ("💖", "💖"),
    "laugh": ("😂", "😂"),
}


class Town:
    """Where each person was last seen: {user_id: {"at", "street", "inside", "seen"}}, and what's been said where."""

    def __init__(self, clock=time.monotonic) -> None:
        self.clock = clock
        self.lock = threading.Lock()
        self.people: dict[int, dict] = {}
        self.lines: dict[str, deque] = {}            # place → its last LINES_KEPT lines, oldest first

    def _sweep(self, now: float) -> None:
        self.people = {uid: p for uid, p in self.people.items() if now - p["seen"] < SEEN_SECONDS}

    def visit(self, user_id: int, at: str, street: str, inside: bool = False) -> bool:
        """This person's Lumi is at `at` (a place id) on `street` (inside that place, or out on the street's map), now.
        True when that's somewhere new (they arrived in town, or moved), so the others hear of it at once."""
        with self.lock:
            now = self.clock()
            self._sweep(now)
            was = self.people.get(user_id)
            if was is None and len(self.people) >= MAX_PEOPLE:
                return False
            self.people[user_id] = {"at": at, "street": street, "inside": inside, "seen": now}
            return was is None or place(was["at"], was["street"], was["inside"]) != place(at, street, inside)

    def leave(self, user_id: int) -> bool:
        """Forget this person (Show me in town turned off). True when they were here (the others hear they've gone)."""
        with self.lock:
            return self.people.pop(user_id, None) is not None

    def here(self, but: int | None = None) -> list[tuple[int, str, str, bool]]:
        """Everyone seen lately, except `but`: [(user_id, at, street, inside)], the most recently seen first."""
        with self.lock:
            now = self.clock()
            self._sweep(now)
            found = sorted(((uid, p) for uid, p in self.people.items() if uid != but), key=lambda item: -item[1]["seen"])
            return [(uid, p["at"], p["street"], p["inside"]) for uid, p in found]

    def where(self, user_id: int) -> str | None:
        """The place (place()) this person is in now, or None if they aren't in town (or are hidden)."""
        with self.lock:
            self._sweep(self.clock())
            p = self.people.get(user_id)
            return place(p["at"], p["street"], p["inside"]) if p else None

    def seen_at(self, user_id: int) -> float | None:
        """When this person last said where they are in town (this Town's clock), or None."""
        with self.lock:
            self._sweep(self.clock())
            p = self.people.get(user_id)
            return p["seen"] if p else None

    def said(self, line: dict) -> None:
        """Remember a line in its place (line["place"]), with the time it was said."""
        with self.lock:
            kept = self.lines.setdefault(line["place"], deque(maxlen=LINES_KEPT))
            kept.append((self.clock(), line))

    def recent(self, where: str, count: int = LINES_SHOWN) -> list[dict]:
        """The last `count` lines said in a place within LINE_SECONDS, oldest first."""
        with self.lock:
            now = self.clock()
            kept = self.lines.get(where)
            if not kept:
                return []
            while kept and now - kept[0][0] >= LINE_SECONDS:   # the old ones fall away
                kept.popleft()
            return [line for _, line in list(kept)[-count:]]


def place(at: str, street: str, inside: bool) -> str:
    """Where a line is heard: inside one place ("in:arcade"), or out on a street's map ("street:high")."""
    return f"in:{at}" if inside else f"street:{street}"


_SPACES = re.compile(r"\s+")


def clean(text: str) -> str:
    """Typed words made safe to show: control and format characters (newlines, bidi tricks, zero-widths) gone,
    runs of spaces made one, trimmed. Empty or too long (over TEXT_MAX) raises ValueError, with words for the person."""
    kept = "".join(" " if ch.isspace() else ch for ch in text if ch.isspace() or not unicodedata.category(ch).startswith("C"))
    words = _SPACES.sub(" ", kept).strip()
    if not words:
        raise ValueError("Say something first!")
    if len(words) > TEXT_MAX:
        raise ValueError(f"That's a lot to say at once! Keep it to {TEXT_MAX} characters.")
    return words


def line(user_id: int, name: str, lumi: str, where: str, *, text: str | None = None, phrase: str | None = None) -> dict:
    """A line to say: typed `text` (cleaned) or a `phrase` id. Raises ValueError when it can't be said."""
    if phrase is not None:
        if phrase not in PHRASES:
            raise ValueError("That isn't one of the phrases.")
        words = PHRASES[phrase][1]
    elif text is not None:
        words = clean(text)
    else:
        raise ValueError("Say something first!")
    return {"id": secrets.token_hex(6), "uid": user_id, "name": name, "lumi": lumi, "place": where, "text": words,
            "phrase": phrase, "at": int(time.time() * 1000)}


def phrases() -> list[dict]:
    """The quick phrases, for the page's list: [{id, icon, text}]."""
    return [{"id": key, "icon": icon, "text": words} for key, (icon, words) in PHRASES.items()]


TOWN = Town()
