"""Retro: console games from the share, played in the browser (EmulatorJS), with saves kept per person.

The Games folder (Settings → Entertainment → Media → Retro) is a share library of its own, like Signage, with a
folder per console (NES, SNES, Genesis, Game Boy, Game Boy Color, GBA, PS1). Its ROMs are ordinary share files, so who
sees which game follows the share's permissions exactly as in Files. Nothing is catalogued: the share scan already
lists every file with its content's sha256, and the shelves are read from that.

Some games need more than their own file: a PS1 .cue names its .bin tracks beside it (Shelf.tracks), and a few
formats need the console's BIOS, a file the household puts in the console's folder (Console.bios, Shelf.bios).

Saves are keyed by that sha256 (the game), not by the file: renaming, moving or adding a ROM again keeps its
saves, and two copies of one ROM share them. They live in media.db (migrations/media/0002_retro.sql), so the
nightly snapshot of media.db backs them up.
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from mnemerynne import fs, media, share
from mnemerynne.config import Settings

log = logging.getLogger("mnemerynne.media")

LIBRARY_KEY = "retro_library_id"     # meta: the Games folder's library, kept through a rename in Windows
MADE_KEY = "retro_consoles_made"     # meta: the consoles whose folders were made, so a later one's appears once


@dataclass(frozen=True)
class Console:
    key: str                  # in URLs and the API ('gba')
    name: str                 # shown on the shelf ('Game Boy Advance')
    folder: str               # its folder in the Games folder, made when Retro is turned on
    system: str               # EmulatorJS's name for the console ('gba', 'segaMD'): its controls and menus
    core: str                 # the EmulatorJS core that plays it (static/vendor/emulatorjs/cores/<core>-wasm.data)
    exts: frozenset[str]      # its games' suffixes; a .zip or .7z holding one works too (ARCHIVE_EXT)
    aliases: tuple[str, ...] = ()           # other folder names taken as this console's (any case)
    other_cores: tuple[tuple[str, str], ...] = ()   # (suffix, core) for a format its usual core can't play
    bios: tuple[str, ...] = ()              # the BIOS file names its cores look for, best first (lower case)
    bios_exts: frozenset[str] = frozenset()  # suffixes that won't start without one of them

    def core_for(self, name: str) -> str:
        """The core for one game: by its suffix where another core plays that format, else the console's."""
        return dict(self.other_cores).get(Path(name).suffix.lower(), self.core)

    def needs_bios(self, name: str) -> bool:
        return Path(name).suffix.lower() in self.bios_exts


# One row per console, in shelf order (Doll's list, plus Game Boy; PS1 the stretch). Adding a console is a row here
# and its core in python/fetch_emulatorjs.py (docs/plans/retro.md, "How a console is added").
CONSOLES: dict[str, Console] = {c.key: c for c in (
    Console("nes", "NES", "NES", "nes", "fceumm", frozenset({".nes", ".unf", ".unif", ".fds"}),
            aliases=("Nintendo", "Famicom"), bios=("disksys.rom",), bios_exts=frozenset({".fds"})),   # the Disk System
    Console("snes", "Super Nintendo", "SNES", "snes", "snes9x", frozenset({".sfc", ".smc", ".swc", ".fig"}),
            aliases=("Super Nintendo", "Super NES", "Super Famicom")),
    Console("genesis", "Genesis", "Genesis", "segaMD", "genesis_plus_gx", frozenset({".md", ".gen", ".smd", ".bin"}),
            aliases=("Mega Drive", "Sega Genesis", "Megadrive")),
    Console("gb", "Game Boy", "Game Boy", "gb", "gambatte", frozenset({".gb", ".gbc", ".dmg"}), aliases=("GB",)),
    Console("gbc", "Game Boy Color", "Game Boy Color", "gb", "gambatte", frozenset({".gbc", ".gb"}), aliases=("GBC",)),
    Console("gba", "Game Boy Advance", "GBA", "gba", "mgba", frozenset({".gba"}), aliases=("Game Boy Advance",)),
    # A disc is its .cue (its .bin tracks beside it), a .pbp, a .chd, or a .zip/.7z of the whole disc. PCSX-ReARMed
    # needs no BIOS; .chd is Beetle PSX's, which needs a real one (scph5501.bin and its siblings) in the PS1 folder.
    Console("psx", "PlayStation", "PS1", "psx", "pcsx_rearmed", frozenset({".cue", ".pbp", ".chd", ".exe"}),
            aliases=("PlayStation", "PSX", "PSone"), other_cores=((".chd", "mednafen_psx_hw"),),
            bios=("scph5501.bin", "scph5500.bin", "scph5502.bin", "scph1001.bin", "scph7001.bin", "scph101.bin"),
            bios_exts=frozenset({".chd"})),
)}
# Where each core keeps a game's own save in the emulator's files: /data/saves/<this>/<game>.srm (RetroArch sorts saves
# by the core's name). The player puts the server's copy there before the game boots; seen in a browser 2026-11-14.
SAVE_FOLDERS = {"fceumm": "FCEUmm", "snes9x": "Snes9x", "genesis_plus_gx": "Genesis Plus GX", "gambatte": "Gambatte",
                "mgba": "mGBA", "pcsx_rearmed": "PCSX-ReARMed", "mednafen_psx_hw": "Beetle PSX HW"}
ARCHIVE_EXT = frozenset({".zip", ".7z"})     # EmulatorJS opens these itself (static/vendor/emulatorjs/compression)
COVER_EXT = (".png", ".jpg", ".jpeg", ".webp")   # a picture named like the ROM, beside it, is its box art
MAX_CUE = 256 << 10         # a .cue is a few lines; anything bigger isn't one

SLOTS = ("sram", "auto", "1", "2", "3", "4")     # what each is: migrations/media/0002_retro.sql
STATE_SLOTS = SLOTS[1:]
MAX_SRAM = 1 << 20          # the biggest cartridge saves are 128 KB (a PS1 memory card too); 1 MB leaves room
MAX_STATE = 16 << 20        # a GBA state is about 430 KB; a disc console's are bigger
MAX_SHOT = 1 << 20          # a PNG of the screen, a few tens of KB

_TAGS = re.compile(r"\s*[\(\[][^\)\]]*[\)\]]")   # No-Intro's "(USA)", "(Rev 1)", "[!]"...
_CUE_FILE = re.compile(r'^\s*FILE\s+(?:"([^"]+)"|(\S+))', re.IGNORECASE | re.MULTILINE)   # FILE "x.bin" BINARY


class RetroError(media.MediaError):
    """A refusal; the message is safe to show."""


# ------------------------------------------------------------------ switch
def enabled(conn: sqlite3.Connection) -> bool:
    """Retro is part of Media: on only while Media is, and an admin turned Retro on too (off by default)."""
    return media.enabled(conn) and bool(media.load_settings(conn)["retro_enabled"])


def library(conn: sqlite3.Connection) -> sqlite3.Row | None:
    """The Games folder's library (Settings → Entertainment → Media → Retro)."""
    return media._meta_library(conn, LIBRARY_KEY, "retro_folder")


def _make_console_folders(lib: sqlite3.Row, consoles) -> list[str]:
    """Each console's folder in the Games folder on disk, unless one by its name or an alias is there (any case).
    The share scan adds their rows, as for anything made through SMB. Returns the folders made."""
    base = share.current().root / Path(lib["share_path"])
    existing = {p.name.lower() for p in base.iterdir() if p.is_dir()} if base.is_dir() else set()
    made = []
    for c in consoles:
        if not {n.lower() for n in (c.folder, *c.aliases)} & existing:
            share.make_dir(base / c.folder)
            made.append(c.folder)
    return made


def _mark_made(conn: sqlite3.Connection, keys) -> None:
    conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)", (MADE_KEY, json.dumps(sorted(keys))))


def ensure_library(conn: sqlite3.Connection) -> sqlite3.Row:
    """The Games folder and a folder per console on the share, made where missing (in the caller's transaction)."""
    lib = media._ensure_share_library(conn, LIBRARY_KEY, "retro_folder", (), "Retro")
    _make_console_folders(lib, CONSOLES.values())
    _mark_made(conn, CONSOLES)
    return lib


def ensure_console_folders(conn: sqlite3.Connection) -> list[str]:
    """Consoles added since Retro was turned on get their folders, once (an admin may remove one on purpose); the
    catalog pass calls this. Nothing while Retro is off or the share is away. Returns the folders made."""
    rt = share.current()
    if rt is None or not enabled(conn):
        return []
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (MADE_KEY,)).fetchone()
    try:
        made = set(json.loads(row["value"])) if row else {"gba"}   # turned on before this key: GBA was all there was
    except ValueError:
        made = set()
    missing = [c for key, c in CONSOLES.items() if key not in made]
    lib = library(conn) if missing else None
    if lib is None or not share.mounted(rt):
        return []
    folders = _make_console_folders(lib, missing)
    _mark_made(conn, made | {c.key for c in missing})
    if folders:
        log.info("Retro: made %s in the Games folder.", ", ".join(folders))
    return folders


def set_folder(conn: sqlite3.Connection, settings: Settings, name: str) -> bool:
    """Use another top-level folder of the share for the games. True if it changed."""
    cfg = media.load_settings(conn)
    if str(name).strip().lower() in (str(cfg["folder"]).lower(), str(cfg["signage_folder"]).lower()):
        raise RetroError("The Games folder can't be the Media or Signage folder: pick another folder at the top of "
                         "the share.")
    return media._set_folder(conn, settings, name, LIBRARY_KEY, "retro_folder")


# ------------------------------------------------------------------ shelves
def title_of(name: str) -> str:
    """A shelf title from a ROM's file name: 'Metroid_Fusion (USA) [!].gba' -> 'Metroid Fusion'."""
    stem = Path(name).stem
    plain = " ".join(_TAGS.sub("", stem).replace("_", " ").split())
    return plain or stem


def console_of(name: str, console: Console) -> bool:
    """Whether a file in that console's folder is one of its games (by suffix)."""
    ext = Path(name).suffix.lower()
    return ext in console.exts or ext in ARCHIVE_EXT


def cue_files(text: str) -> list[str]:
    """The files a .cue sheet names (its FILE lines), in order, as written."""
    return [m.group(1) or m.group(2) for m in _CUE_FILE.finditer(text)]


def _console_folders(conn: sqlite3.Connection, library_id: int) -> dict[int, Console]:
    """Every live folder under a console's folder (at the top of the Games folder, any case) -> that console."""
    by_name = {n.lower(): c for c in CONSOLES.values() for n in (c.folder, *c.aliases)}
    rows = conn.execute("SELECT id, parent_id, name FROM folders WHERE collection_id = ? AND deleted_at IS NULL",
                        (library_id,)).fetchall()
    parents = {r["id"]: r["parent_id"] for r in rows}
    tops = {r["id"]: by_name[r["name"].lower()] for r in rows if r["parent_id"] is None and r["name"].lower() in by_name}
    out: dict[int, Console] = {}
    for fid in parents:
        cur, seen = fid, 0
        while cur is not None and cur not in tops and seen < 10_000:   # climb to the console's folder
            cur, seen = parents.get(cur), seen + 1
        if cur in tops:
            out[fid] = tops[cur]
    return out


_FILE_COLUMNS = ("f.id, f.collection_id, f.folder_id, f.display_name, f.blob_sha256, f.created_at, b.size "
                 "FROM files f JOIN blobs b ON b.sha256 = f.blob_sha256")


def _files(conn: sqlite3.Connection, library_id: int, folder_ids) -> list[sqlite3.Row]:
    ids = list(folder_ids)
    if not ids:
        return []
    out = []
    for at in range(0, len(ids), 500):   # in batches, under SQLite's limit on parameters
        chunk = ids[at:at + 500]
        out += conn.execute(f"SELECT {_FILE_COLUMNS} WHERE f.collection_id = ? AND f.deleted_at IS NULL "
                            f"AND f.folder_id IN ({','.join('?' * len(chunk))})", (library_id, *chunk)).fetchall()
    return out


def game_count(conn: sqlite3.Connection) -> int:
    """Every ROM in the Games folder's console folders, whoever may read them (Settings shows it)."""
    lib = library(conn)
    if lib is None:
        return 0
    folders = _console_folders(conn, lib["id"])
    return sum(1 for r in _files(conn, lib["id"], folders) if console_of(r["display_name"], folders[r["folder_id"]]))


class Shelf:
    """The Games folder as one person sees it: the ROMs they can read, each with its console and box art."""

    def __init__(self, conn: sqlite3.Connection, user_id: int) -> None:
        self.conn = conn
        self.user_id = user_id
        self.lib = library(conn)
        self.access = fs.Access.of(conn, user_id)
        self.folders = _console_folders(conn, self.lib["id"]) if self.lib is not None else {}
        self._bios: dict[str, sqlite3.Row | None] = {}

    def _readable(self, row) -> bool:
        return self.access.can_read(row["collection_id"], row["folder_id"])

    def games(self) -> list[dict]:
        """Every game this person can read, by console then title, with box art, saves and when last played."""
        if self.lib is None:
            return []
        rows = [r for r in _files(self.conn, self.lib["id"], self.folders) if self._readable(r)]
        covers = {(r["folder_id"], Path(r["display_name"]).stem.lower()): r["id"] for r in rows
                  if Path(r["display_name"]).suffix.lower() in COVER_EXT}
        roms = [r for r in rows if console_of(r["display_name"], self.folders[r["folder_id"]])]
        played = self._played({r["blob_sha256"] for r in roms})
        saved = self._saved({r["blob_sha256"] for r in roms})
        order = list(CONSOLES)
        out = []
        for r in roms:
            console = self.folders[r["folder_id"]]
            cover = covers.get((r["folder_id"], Path(r["display_name"]).stem.lower()))
            slots = saved.get(r["blob_sha256"], set())
            out.append({"id": r["id"], "console": console.key, "title": title_of(r["display_name"]),
                        "name": r["display_name"], "size": r["size"],
                        "cover": f"/media/retro/cover/{cover}" if cover else None,
                        "played_at": played.get(r["blob_sha256"]),
                        "has_save": "sram" in slots, "can_continue": "auto" in slots,
                        "needs_bios": console.needs_bios(r["display_name"]) and self.bios(console) is None})
        out.sort(key=lambda g: (order.index(g["console"]), g["title"].lower(), g["name"].lower()))
        return out

    def _row(self, file_id: int) -> sqlite3.Row | None:
        if self.lib is None:
            return None
        return self.conn.execute(f"SELECT {_FILE_COLUMNS} WHERE f.id = ? AND f.collection_id = ? AND f.deleted_at IS NULL",
                                 (file_id, self.lib["id"])).fetchone()

    def rom(self, file_id: int) -> tuple[sqlite3.Row, Console] | None:
        """A ROM this person can read, with its console; None for anything else (not a ROM, not theirs)."""
        row = self._row(file_id)
        console = self.folders.get(row["folder_id"]) if row is not None else None
        if console is None or not console_of(row["display_name"], console):
            return None
        self.access.confirm(row["collection_id"], row["folder_id"])   # opening it: ask the share about this folder now
        return (row, console) if self._readable(row) else None

    def cover(self, file_id: int) -> sqlite3.Row | None:
        """A box-art picture in a console's folder this person can read."""
        row = self._row(file_id)
        if row is None or row["folder_id"] not in self.folders or Path(row["display_name"]).suffix.lower() not in COVER_EXT:
            return None
        return row if self._readable(row) else None

    def bios(self, console: Console) -> sqlite3.Row | None:
        """The console's BIOS this person can read: the first of Console.bios found anywhere in its folders."""
        if console.key not in self._bios:
            found = None
            if self.lib is not None and console.bios:
                folders = [fid for fid, c in self.folders.items() if c is console]
                by_name = {r["display_name"].lower(): r for r in _files(self.conn, self.lib["id"], folders)
                           if r["display_name"].lower() in console.bios and self._readable(r)}
                found = next((by_name[n] for n in console.bios if n in by_name), None)
            self._bios[console.key] = found
        return self._bios[console.key]

    def tracks(self, row: sqlite3.Row, settings: Settings) -> tuple[list[tuple[str, sqlite3.Row]], list[str]]:
        """For a .cue: the files it names, found beside it ([(the name as the .cue writes it, its row)]), and the
        names that aren't there (any case). Not a .cue, or not readable as one: nothing."""
        if Path(row["display_name"]).suffix.lower() != ".cue":
            return [], []
        from mnemerynne.ingest import store                 # the share's path to the file (not needed for shelves)
        try:
            path = store.content_path(self.conn, settings, row["id"])
            if path.stat().st_size > MAX_CUE:
                return [], []
            text = path.read_bytes().decode("utf-8", errors="replace")
        except OSError:
            return [], []
        beside = {r["display_name"].lower(): r for r in _files(self.conn, self.lib["id"], [row["folder_id"]])}
        found, missing = [], []
        for name in cue_files(text):
            hit = beside.get(Path(name.replace("\\", "/")).name.lower())   # a track named with a folder: its own name
            if hit is not None and self._readable(hit):
                found.append((name.replace("\\", "/"), hit))
            else:
                missing.append(name)
        return found, missing

    def _played(self, shas: set[str]) -> dict[str, str]:
        return {r["rom_sha"]: r["last_played_at"] for r in _in(self.conn, "SELECT rom_sha, last_played_at FROM "
                "retro_plays WHERE user_id = ? AND rom_sha IN ({})", self.user_id, shas)}

    def _saved(self, shas: set[str]) -> dict[str, set[str]]:
        out: dict[str, set[str]] = {}
        for r in _in(self.conn, "SELECT rom_sha, slot FROM retro_saves WHERE user_id = ? AND rom_sha IN ({})",
                     self.user_id, shas):
            out.setdefault(r["rom_sha"], set()).add(r["slot"])
        return out


def _in(conn: sqlite3.Connection, sql: str, user_id: int, values: set[str]) -> list[sqlite3.Row]:
    """sql (with one '{}' for the IN list) for this person over values, in batches under SQLite's limit."""
    items, out = sorted(values), []
    for at in range(0, len(items), 500):
        chunk = items[at:at + 500]
        out += conn.execute(sql.format(",".join("?" * len(chunk))), (user_id, *chunk)).fetchall()
    return out


# -------------------------------------------------------------------- plays
def played(conn: sqlite3.Connection, user_id: int, rom_sha: str) -> None:
    """A game started (Continue playing, at the top of the shelves, lists the latest)."""
    conn.execute("INSERT INTO retro_plays(user_id, rom_sha, plays) VALUES (?, ?, 1) ON CONFLICT(user_id, rom_sha) "
                 "DO UPDATE SET plays = plays + 1, last_played_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')",
                 (user_id, rom_sha))


# -------------------------------------------------------------------- saves
def _check_slot(slot: str) -> None:
    if slot not in SLOTS:
        raise RetroError("No such save slot.")


def saves(conn: sqlite3.Connection, user_id: int, rom_sha: str) -> list[dict]:
    """This person's saves of one game: each slot there is, with its size and time (no data)."""
    return [{"slot": r["slot"], "size": r["size"], "updated_at": r["updated_at"], "has_shot": bool(r["has_shot"])}
            for r in conn.execute("SELECT slot, size, updated_at, shot IS NOT NULL AS has_shot FROM retro_saves "
                                  "WHERE user_id = ? AND rom_sha = ? ORDER BY slot", (user_id, rom_sha))]


def get_save(conn: sqlite3.Connection, user_id: int, rom_sha: str, slot: str, shot: bool = False) -> bytes | None:
    """One slot's data (or, with shot, its screenshot); None if there isn't one."""
    _check_slot(slot)
    row = conn.execute(f"SELECT {'shot' if shot else 'data'} AS body FROM retro_saves WHERE user_id = ? "
                       "AND rom_sha = ? AND slot = ?", (user_id, rom_sha, slot)).fetchone()
    return bytes(row["body"]) if row is not None and row["body"] is not None else None


def put_save(conn: sqlite3.Connection, user_id: int, rom_sha: str, slot: str, data: bytes) -> None:
    """Keep one slot's data, replacing what was there (and its old screenshot, which no longer matches)."""
    _check_slot(slot)
    limit = MAX_SRAM if slot == "sram" else MAX_STATE
    if not data:
        raise RetroError("That save is empty.")
    if len(data) > limit:
        raise RetroError(f"A save is at most {limit >> 20} MB.")
    conn.execute("INSERT INTO retro_saves(user_id, rom_sha, slot, data, size) VALUES (?, ?, ?, ?, ?) "
                 "ON CONFLICT(user_id, rom_sha, slot) DO UPDATE SET data = excluded.data, size = excluded.size, "
                 "shot = NULL, updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')",
                 (user_id, rom_sha, slot, data, len(data)))


def put_shot(conn: sqlite3.Connection, user_id: int, rom_sha: str, slot: str, png: bytes) -> bool:
    """A state's screenshot (a PNG), shown on its slot. False if the slot has no state to go with it."""
    if slot not in STATE_SLOTS:
        raise RetroError("Only save states have a screenshot.")
    if not png.startswith(b"\x89PNG\r\n\x1a\n") or len(png) > MAX_SHOT:
        raise RetroError("A screenshot is a PNG of at most 1 MB.")
    return conn.execute("UPDATE retro_saves SET shot = ? WHERE user_id = ? AND rom_sha = ? AND slot = ?",
                        (png, user_id, rom_sha, slot)).rowcount > 0


def delete_save(conn: sqlite3.Connection, user_id: int, rom_sha: str, slot: str) -> bool:
    _check_slot(slot)
    return conn.execute("DELETE FROM retro_saves WHERE user_id = ? AND rom_sha = ? AND slot = ?",
                        (user_id, rom_sha, slot)).rowcount > 0
