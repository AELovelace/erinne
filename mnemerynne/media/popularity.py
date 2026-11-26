"""Retro's popularity: how many people have played each of the share's games on RetroAchievements.

Doll's redesign (2026-11-15): the library's first page shows each console's top 50 of the household's own games
by popularity, and RetroAchievements is the source. Its Web API (https://api-docs.retroachievements.org/) is
asked with an admin's Web API key (Settings → Media and entertainment → Media; the `y` parameter), gently: the docs ask for
aggressive caching and give no numbers, so this asks little and keeps everything (migrations/media/0003_retro_
popularity.sql).

How a ROM becomes a game there:
  1. RetroAchievements' list of a console's games, with every ROM hash it knows (API_GetGameList, h=1), fetched
     again after LIST_DAYS. Console ids come from API_GetConsoleIDs, by name.
  2. The ROM's hash, by RetroAchievements' own rules (ra_hash): an MD5 of the file, but for the NES without its
     16-byte iNES/FDS header and for the SNES without a 512-byte copier header. Inside a .zip: its biggest member.
     A PS1 disc's hash needs its file system (the boot executable), and a .7z can't be unpacked whole here, so
     those (and any ROM whose hash isn't listed) are matched by title instead: the shelf title against
     RetroAchievements' titles for that console, both normalised (title_key), exact only, hacks and homebrew
     ("~Hack~ ...") left out.
  3. The game's player count (API_GetGameExtended: NumDistinctPlayers), asked once per game and again after
     PLAYERS_DAYS, at most PLAYERS_PER_PASS a pass, PAUSE seconds apart.
The catalog pass (media/catalog.py) runs this while Retro is on and the key is set. Without a key nothing is
asked and the pages fall back to A-Z.
"""

from __future__ import annotations

import hashlib
import logging
import re
import sqlite3
import time
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx

from mnemerynne import media
from mnemerynne.config import Settings
from mnemerynne.db import transaction
from mnemerynne.media import retro

log = logging.getLogger("mnemerynne.media")

API = "https://retroachievements.org/API/"
LIST_DAYS = 7                    # a console's game list is fetched again after a week
PLAYERS_DAYS = 30                # a game's player count is asked again after a month
PLAYERS_PER_PASS = 25            # player counts asked per catalog pass
PAUSE = 0.4                      # seconds between two of those requests
MAX_ROM = 64 << 20               # bigger than any cartridge (a GBA game is 32 MB): not hashed
HASH_BUDGET = 512 << 20          # bytes hashed per pass
USER_AGENT = "Mnemerynne"        # a plain client name: nothing about the household

# RetroAchievements' system names for each console (API_GetConsoleIDs), lower case: a name, or one side of a
# "NES/Famicom" pair, that equals one of these is that console.
RA_NAMES = {"nes": ("nes", "famicom", "nes/famicom"), "snes": ("snes", "super famicom", "super nintendo", "snes/super famicom"),
            "genesis": ("genesis", "mega drive", "genesis/mega drive", "mega drive/genesis"),
            "gb": ("game boy",), "gbc": ("game boy color",), "gba": ("game boy advance",), "psx": ("playstation",)}


class Refused(Exception):
    """RetroAchievements said no (a wrong key, too many requests): stop for this pass."""


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _older_than(stamp: str | None, days: int) -> bool:
    if not stamp:
        return True
    try:
        then = datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    except ValueError:
        return True
    return datetime.now(UTC) - then > timedelta(days=days)


# ------------------------------------------------------------------ hashing and titles
def ra_hash(data: bytes, console: str) -> str:
    """A ROM's hash by RetroAchievements' rules (docs.retroachievements.org, game identification)."""
    if console == "nes" and data[:4] in (b"NES\x1a", b"FDS\x1a"):
        data = data[16:]                       # the iNES (or FDS) header isn't the game
    elif console == "snes" and len(data) % 8192 == 512:
        data = data[512:]                      # a copier header
    return hashlib.md5(data).hexdigest()       # noqa: S324 - RetroAchievements' identifier, not security


def rom_bytes(path: Path) -> bytes | None:
    """The ROM's own bytes, for hashing: the file, or a .zip's biggest member. None for what can't be hashed here
    (a .7z, a disc, anything over MAX_ROM)."""
    suffix = path.suffix.lower()
    try:
        if suffix == ".zip":
            with zipfile.ZipFile(path) as z:
                members = [m for m in z.infolist() if not m.is_dir()]
                if not members:
                    return None
                biggest = max(members, key=lambda m: m.file_size)
                if biggest.file_size > MAX_ROM:
                    return None
                return z.read(biggest)
        if suffix in (".7z", ".cue", ".chd", ".pbp", ".exe") or path.stat().st_size > MAX_ROM:
            return None
        return path.read_bytes()
    except (OSError, zipfile.BadZipFile, RuntimeError, NotImplementedError):
        return None


_BRACKETS = re.compile(r"[\(\[][^\)\]]*[\)\]]")


def title_key(title: str) -> str:
    """A title reduced for matching: "Legend of Zelda, The - A Link to the Past (USA)" and RetroAchievements'
    "Legend of Zelda, The: A Link to the Past" both become "legend of zelda a link to the past"."""
    t = _BRACKETS.sub(" ", title.lower()).replace("&", " and ")
    t = re.sub(r",\s*the\b", " ", t)
    t = re.sub(r"^\s*the\s+", "", t)
    t = re.sub(r"[^a-z0-9]+", " ", t)
    return " ".join(t.split())


def _titles(title: str) -> list[str]:
    """A RetroAchievements title's keys: some name several versions ("Pokemon Red Version | Pokemon Blue Version")."""
    return [k for k in (title_key(part) for part in title.split("|")) if k]


# ------------------------------------------------------------------ asking RetroAchievements
def _get(client: httpx.Client, key: str, endpoint: str, **params) -> object:
    resp = client.get(API + endpoint, params={"y": key, **params})
    if resp.status_code in (401, 403):
        raise Refused("RetroAchievements didn't accept the Web API key (Settings → Media and entertainment → Media)")
    if resp.status_code == 429:
        raise Refused("RetroAchievements asked us to slow down; trying again on a later pass")
    resp.raise_for_status()
    return resp.json()


def _console_ids(conn: sqlite3.Connection, client: httpx.Client, key: str) -> dict[str, int]:
    """Each console's RetroAchievements system id: kept from an earlier pass, else asked (API_GetConsoleIDs)."""
    known = {r["console"]: r["ra_console_id"] for r in conn.execute("SELECT console, ra_console_id FROM retro_ra_lists")}
    if all(c in known for c in retro.CONSOLES):
        return known
    systems = _get(client, key, "API_GetConsoleIDs.php")
    for system in systems if isinstance(systems, list) else []:
        name = str(system.get("Name", "")).strip().lower()
        sides = {name, *(part.strip() for part in name.split("/"))}
        for console, names in RA_NAMES.items():
            if console not in known and (sides & set(names)) and name not in ("playstation 2", "playstation portable"):
                known[console] = int(system["ID"])
    return known


def _refresh_list(conn: sqlite3.Connection, client: httpx.Client, key: str, console: str, ra_id: int) -> int:
    """A console's games and their hashes (API_GetGameList, h=1), kept. Returns how many games."""
    games = _get(client, key, "API_GetGameList.php", i=ra_id, h=1)
    games = games if isinstance(games, list) else []
    with transaction(conn):
        for g in games:
            try:
                gid = int(g["ID"])
            except (KeyError, TypeError, ValueError):
                continue
            conn.execute("INSERT INTO retro_ra_games(game_id, console, title) VALUES (?, ?, ?) "
                         "ON CONFLICT(game_id) DO UPDATE SET console = excluded.console, title = excluded.title",
                         (gid, console, str(g.get("Title") or "")))
            for md5 in g.get("Hashes") or []:
                conn.execute("INSERT OR REPLACE INTO retro_ra_hashes(md5, game_id) VALUES (?, ?)", (str(md5).lower(), gid))
        conn.execute("INSERT OR REPLACE INTO retro_ra_lists(console, ra_console_id, fetched_at) VALUES (?, ?, ?)",
                     (console, ra_id, _now()))
    return len(games)


def _title_index(conn: sqlite3.Connection, console: str) -> dict[str, int]:
    """RetroAchievements' titles for a console, by title_key -> game id (hacks, homebrew and the like left out)."""
    out: dict[str, int] = {}
    for r in conn.execute("SELECT game_id, title FROM retro_ra_games WHERE console = ? ORDER BY game_id", (console,)):
        if r["title"].lstrip().startswith("~"):
            continue
        for k in _titles(r["title"]):
            out.setdefault(k, r["game_id"])
    return out


# ------------------------------------------------------------------ the share's ROMs
def _share_roms(conn: sqlite3.Connection) -> list[tuple[sqlite3.Row, retro.Console]]:
    """Every ROM in the Games folder's console folders (not per person: what the house has)."""
    lib = retro.library(conn)
    if lib is None:
        return []
    folders = retro._console_folders(conn, lib["id"])
    return [(r, folders[r["folder_id"]]) for r in retro._files(conn, lib["id"], folders)
            if retro.console_of(r["display_name"], folders[r["folder_id"]])]


def match_roms(conn: sqlite3.Connection, settings: Settings, deadline: float) -> int:
    """Find each new ROM's RetroAchievements game (by hash, else title); a ROM found nowhere is tried again after
    its console's list has been fetched anew. Returns how many were looked at."""
    from mnemerynne.ingest import store     # the share's path to a file
    lists = {r["console"]: r["fetched_at"] for r in conn.execute("SELECT console, fetched_at FROM retro_ra_lists")}
    done = {r["rom_sha"]: r for r in conn.execute("SELECT rom_sha, game_id, checked_at FROM retro_ra_roms")}
    titles: dict[str, dict[str, int]] = {}
    budget, looked = HASH_BUDGET, 0
    for row, console in _share_roms(conn):
        if time.monotonic() > deadline or budget <= 0:
            break
        sha = row["blob_sha256"]
        if console.key not in lists:
            continue                                   # its list isn't here yet
        seen = done.get(sha)
        if seen is not None and (seen["game_id"] is not None or seen["checked_at"] >= lists[console.key]):
            continue                                   # found, or not found in this list already
        md5 = game = None
        by = None
        try:
            data = rom_bytes(store.content_path(conn, settings, row["id"]))
        except OSError:
            data = None
        if data is not None:
            budget -= len(data)
            md5 = ra_hash(data, console.key)
            hit = conn.execute("SELECT game_id FROM retro_ra_hashes WHERE md5 = ?", (md5,)).fetchone()
            if hit is not None:
                game, by = hit["game_id"], "hash"
        if game is None:
            index = titles.get(console.key)
            if index is None:
                index = titles[console.key] = _title_index(conn, console.key)
            game = index.get(title_key(retro.title_of(row["display_name"])))
            by = "title" if game is not None else None
        with transaction(conn):
            conn.execute("INSERT OR REPLACE INTO retro_ra_roms(rom_sha, console, md5, game_id, matched_by, checked_at) "
                         "VALUES (?, ?, ?, ?, ?, ?)", (sha, console.key, md5, game, by, _now()))
        done[sha] = {"game_id": game, "checked_at": _now()}
        looked += 1
    return looked


def _players(conn: sqlite3.Connection, client: httpx.Client, key: str, deadline: float, pause: float) -> int:
    """Player counts for the house's games that have none yet, or a month-old one (API_GetGameExtended)."""
    rows = conn.execute("SELECT DISTINCT g.game_id, g.players_at FROM retro_ra_roms r JOIN retro_ra_games g "
                        "ON g.game_id = r.game_id ORDER BY g.players_at IS NOT NULL, g.players_at").fetchall()
    asked = 0
    for r in rows:
        if asked >= PLAYERS_PER_PASS or time.monotonic() > deadline:
            break
        if not _older_than(r["players_at"], PLAYERS_DAYS):
            continue
        if asked:
            time.sleep(pause)
        info = _get(client, key, "API_GetGameExtended.php", i=r["game_id"])
        players = info.get("NumDistinctPlayers") if isinstance(info, dict) else None
        try:
            count = max(0, int(players))
        except (TypeError, ValueError):
            count = 0                                  # no count given: known, but nobody's played it there
        with transaction(conn):
            conn.execute("UPDATE retro_ra_games SET players = ?, players_at = ? WHERE game_id = ?",
                         (count, _now(), r["game_id"]))
        asked += 1
    return asked


def refresh(conn: sqlite3.Connection, settings: Settings, deadline: float, *, client: httpx.Client | None = None,
            pause: float = PAUSE) -> dict:
    """One catalog pass's share of the work: console ids, stale game lists, new ROMs matched, player counts.
    Nothing while Retro is off or no key is set. Returns what it did."""
    out = {"lists": 0, "roms": 0, "players": 0}
    if not retro.enabled(conn):
        return out
    key = media.ra_key(settings, media.load_settings(conn))
    if not key:
        return out
    own = client is None
    client = client or httpx.Client(timeout=20, headers={"User-Agent": USER_AGENT}, follow_redirects=True)
    try:
        ids = _console_ids(conn, client, key)
        stale = {r["console"]: r["fetched_at"] for r in conn.execute("SELECT console, fetched_at FROM retro_ra_lists")}
        for console in retro.CONSOLES:
            if console in ids and _older_than(stale.get(console), LIST_DAYS) and time.monotonic() < deadline:
                _refresh_list(conn, client, key, console, ids[console])
                out["lists"] += 1
        out["roms"] = match_roms(conn, settings, deadline)
        out["players"] = _players(conn, client, key, deadline, pause)
    except Refused as exc:
        log.warning("Retro popularity: %s", exc)
    except (httpx.HTTPError, ValueError) as exc:
        log.info("Retro popularity: RetroAchievements couldn't be asked this time (%s)", exc)
    finally:
        if own:
            client.close()
    return out


def players_by_rom(conn: sqlite3.Connection, shas: set[str]) -> dict[str, int]:
    """Each ROM's player count, for the ROMs that have one (Shelf.games)."""
    out: dict[str, int] = {}
    ids = list(shas)
    for at in range(0, len(ids), 500):
        chunk = ids[at:at + 500]
        for r in conn.execute("SELECT r.rom_sha, g.players FROM retro_ra_roms r JOIN retro_ra_games g ON "
                              f"g.game_id = r.game_id WHERE g.players IS NOT NULL AND r.rom_sha IN ({','.join('?' * len(chunk))})",
                              chunk):
            out[r["rom_sha"]] = r["players"]
    return out


def status(conn: sqlite3.Connection) -> dict:
    """For Settings: how many of the share's ROMs were found on RetroAchievements, and how many have a count."""
    roms = conn.execute("SELECT COUNT(*) AS n, COUNT(game_id) AS found FROM retro_ra_roms").fetchone()
    counted = conn.execute("SELECT COUNT(DISTINCT r.rom_sha) AS n FROM retro_ra_roms r JOIN retro_ra_games g "
                           "ON g.game_id = r.game_id WHERE g.players IS NOT NULL").fetchone()
    return {"checked": roms["n"], "found": roms["found"], "ranked": counted["n"],
            "lists": [r["console"] for r in conn.execute("SELECT console FROM retro_ra_lists ORDER BY console")]}
