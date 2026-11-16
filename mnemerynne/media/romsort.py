"""Retro's Unsorted folder: ROMs dropped there go to their console's folder by what's inside them.

Doll asked (2026-11-15) for one place to dump ROMs, with the NAS putting each in the right console folder from
the ROM's own metadata. The catalog pass (media/catalog.py) calls sort_unsorted after it makes the console
folders. Every file under Unsorted (its subfolders too) is identified, then moved with share.rename. That never
replaces anything, and the share scan picks the move up as it does an SMB one. Saves are keyed by the ROM's sha256
(media/retro.py), so they follow the game.

How a ROM is recognised (identify), by its header, never its name first:
  NES      "NES\\x1a" (iNES / NES 2.0), "FDS\\x1a" or a disk's "*NINTENDO-HVC*", "UNIF"
  SNES     the internal header at $7FC0 (LoROM) or $FFC0 (HiROM), after a 512-byte copier header if there is one:
           its checksum and complement add up to $FFFF and its map mode is a real one
  Genesis  "SEGA" in the cartridge header at $100
  GB/GBC   the header checksum at $14D over $134-$14C, and a known cartridge type; $143 = $80 or $C0 is a Color
           game (it goes to Game Boy Color: $80 ones work on both, and look better in colour)
  GBA      the fixed $96 at $B2 and the header complement at $BD
  PS1      a PS-X EXE; a PBP ("\\0PBP"); a CHD ("MComprHD"; Retro plays no other disc console); a disc image whose
           ISO 9660 volume says "PLAYSTATION" (raw 2352-byte sectors or 2048); a .cue whose first track does
Only where a file carries no such mark does its suffix decide, and only a suffix one console owns (.smd, .sfc...).
A .zip is read from its biggest member (the game, not a readme), a .7z from the start of its first packed stream
(LZMA2, LZMA or stored: 7-Zip's usual, and enough for a header). The standard library has no 7z reader.

What moves with a game: a .cue's tracks (all of them, or the .cue waits), a picture named like the ROM (its box
art), and a console's BIOS goes to that console's folder by its file name. What stays: files still being copied
(changed in the last SETTLE_SECONDS), junk and hidden files, anything not recognised, and a ROM that's already in
its console's folder byte for byte (logged, never deleted). A name that's taken by a different file gets " (2)".
"""

from __future__ import annotations

import hashlib
import json
import logging
import lzma
import sqlite3
import struct
import time
import zipfile
from pathlib import Path

from mnemerynne import share
from mnemerynne.media import retro

log = logging.getLogger("mnemerynne.media")

UNSORTED = "Unsorted"                 # the drop folder at the top of the Games folder
UNSORTED_KEY = "retro_unsorted_made"  # meta: made once, so an admin who removes it on purpose isn't overruled
SETTLE_SECONDS = 120                  # a file changed this recently may still be copying in
SNIFF = 128 << 10                     # bytes read from a ROM: enough for a SNES HiROM header and a disc's volume
MAX_PER_PASS = 200                    # files looked at per catalog pass (the rest wait for the next)

# A file looked at and left where it was: (size, mtime) -> why. Not looked at again until it changes.
_left: dict[str, tuple[int, int, str]] = {}

_SNES_MAP_MODES = {0x20, 0x21, 0x22, 0x23, 0x25, 0x30, 0x31, 0x32, 0x33, 0x35, 0x3A}
_GB_CART_TYPES = {0x00, 0x01, 0x02, 0x03, 0x05, 0x06, 0x08, 0x09, 0x0B, 0x0C, 0x0D, 0x0F, 0x10, 0x11, 0x12, 0x13,
                  0x19, 0x1A, 0x1B, 0x1C, 0x1D, 0x1E, 0x20, 0x22, 0xFC, 0xFD, 0xFE, 0xFF}
_SEVEN_ZIP = b"7z\xbc\xaf\x27\x1c"
# Suffixes only one console uses: the fallback for a file with no mark of its own.
_BY_SUFFIX = {".nes": "nes", ".unf": "nes", ".unif": "nes", ".fds": "nes",
              ".sfc": "snes", ".smc": "snes", ".swc": "snes", ".fig": "snes",
              ".md": "genesis", ".gen": "genesis", ".smd": "genesis",
              ".gb": "gb", ".dmg": "gb", ".gbc": "gbc", ".gba": "gba",
              ".cue": "psx", ".chd": "psx", ".pbp": "psx"}


# ------------------------------------------------------------------ identify
def _snes_header_ok(data: bytes, at: int) -> bool:
    """A SNES internal header at `at` ($xxC0): checksum + complement = $FFFF and a real map mode."""
    if len(data) < at + 0x20:
        return False
    complement, checksum = struct.unpack_from("<HH", data, at + 0x1C)
    return (complement ^ checksum) == 0xFFFF and data[at + 0x15] in _SNES_MAP_MODES


def _gb(data: bytes) -> str | None:
    """Game Boy (or Color) by its header checksum and cartridge type."""
    if len(data) < 0x150 or data[0x147] not in _GB_CART_TYPES or data[0x148] > 0x08:
        return None
    check = 0
    for b in data[0x134:0x14D]:
        check = (check - b - 1) & 0xFF
    if check != data[0x14D]:
        return None
    return "gbc" if data[0x143] in (0x80, 0xC0) else "gb"


def _playstation_volume(data: bytes) -> bool:
    """An ISO 9660 primary volume (sector 16) whose system identifier is PLAYSTATION, in a raw (2352: Mode 1 or
    Mode 2 XA) or a cooked (2048) image."""
    for at in (16 * 2352 + 24, 16 * 2352 + 16, 16 * 2048):
        pvd = data[at:at + 40]
        if pvd[:6] == b"\x01CD001" and pvd[8:19] == b"PLAYSTATION":
            return True
    return False


def identify_bytes(data: bytes) -> str | None:
    """The console a ROM's first bytes belong to (a key of retro.CONSOLES), or None."""
    if data[:4] in (b"NES\x1a", b"FDS\x1a", b"UNIF") or data[1:15] == b"*NINTENDO-HVC*":
        return "nes"
    if data[:8] == b"PS-X EXE" or data[:4] == b"\0PBP" or data[:8] == b"MComprHD" or _playstation_volume(data):
        return "psx"
    if len(data) > 0xBD and data[0xB2] == 0x96 and data[0xBD] == (-(sum(data[0xA0:0xBD]) + 0x19)) & 0xFF:
        return "gba"
    if b"SEGA" in data[0x100:0x110]:
        return "genesis"
    found = _gb(data)
    if found:
        return found
    for copier in (0, 0x200):                    # some dumps carry a 512-byte copier header first
        if _snes_header_ok(data, copier + 0x7FC0) or _snes_header_ok(data, copier + 0xFFC0):
            return "snes"
    return None


def _seven_zip_start(path: Path) -> bytes:
    """The first SNIFF bytes of a .7z's first packed stream: it starts right after the 32-byte signature header.
    Tried as LZMA2 (7-Zip's default), LZMA (older archives, the usual lc3 lp0 pb2), and stored."""
    with path.open("rb") as f:
        head = f.read(32)
        packed = f.read(4 << 20)
    if head[:6] != _SEVEN_ZIP:
        return b""
    for filters in ([{"id": lzma.FILTER_LZMA2, "dict_size": 1 << 26}],
                    [{"id": lzma.FILTER_LZMA1, "dict_size": 1 << 26, "lc": 3, "lp": 0, "pb": 2}]):
        out = _raw_decode(packed, filters)
        if out:
            return out
    return packed[:SNIFF]                        # stored ("copy"): the file's own bytes


def _raw_decode(packed: bytes, filters: list) -> bytes:
    """Up to SNIFF bytes decoded from the start of `packed`. An LZMA stream in a 7z has no end marker, so the
    decoder runs on into what follows it (the archive's index) and fails, keeping nothing: then the longest start
    of `packed` that decodes cleanly is found by halving, and what it gives is enough for a header."""
    def attempt(n: int) -> bytes | None:
        try:
            return lzma.LZMADecompressor(lzma.FORMAT_RAW, filters=filters).decompress(packed[:n], max_length=SNIFF)
        except lzma.LZMAError:
            return None
    whole = attempt(len(packed))
    if whole is not None:
        return whole
    lo, hi, best = 0, len(packed), b""           # attempt(lo) decodes (nothing), attempt(hi) fails
    while hi - lo > 1:
        mid = (lo + hi) // 2
        got = attempt(mid)
        if got is None:
            hi = mid
        else:
            lo, best = mid, got
    return best


def identify(path: Path) -> str | None:
    """The console of a ROM on disk: by its contents (inside a .zip or .7z too), else by a suffix one console owns."""
    suffix = path.suffix.lower()
    inner_name = path.name
    try:
        if suffix == ".zip":
            with zipfile.ZipFile(path) as z:
                members = [m for m in z.infolist() if not m.is_dir()]
                if not members:
                    return None
                biggest = max(members, key=lambda m: m.file_size)   # the game, not a readme
                inner_name = biggest.filename
                with z.open(biggest) as f:
                    data = f.read(SNIFF)
        elif suffix == ".7z":
            data = _seven_zip_start(path)
            inner_name = ""
        elif suffix == ".cue":
            return "psx" if _cue_is_playstation(path) else None
        else:
            with path.open("rb") as f:
                data = f.read(SNIFF)
    except (OSError, zipfile.BadZipFile, RuntimeError, NotImplementedError, lzma.LZMAError):
        return None
    found = identify_bytes(data)
    if found:
        return found
    if suffix not in (".zip", ".7z"):
        return _BY_SUFFIX.get(suffix)
    return _BY_SUFFIX.get(Path(inner_name).suffix.lower())   # inside a zip: its member's suffix


def _cue_is_playstation(cue: Path) -> bool:
    """A .cue is PS1 when its first track's image says so (sort_unsorted waits until its tracks are all here)."""
    tracks = _cue_tracks(cue)
    if not tracks or not all(t.is_file() for t in tracks):
        return False
    try:
        with tracks[0].open("rb") as f:
            return _playstation_volume(f.read(SNIFF))
    except OSError:
        return False


def _cue_tracks(cue: Path) -> list[Path]:
    """The files a .cue names, beside it, found in any case (a .cue often names them in another case than the
    files have, as Retro's shelves allow); a missing one as written. None if it's too big to be a .cue."""
    try:
        if cue.stat().st_size > retro.MAX_CUE:
            return []
        text = cue.read_text("utf-8", errors="replace")
        here = {p.name.lower(): p for p in cue.parent.iterdir() if p.is_file()}
    except OSError:
        return []
    return [here.get(Path(name).name.lower(), cue.parent / Path(name).name) for name in retro.cue_files(text)]


def _bios_console(name: str) -> str | None:
    """A console whose BIOS file this is, by name (retro.Console.bios)."""
    low = name.lower()
    for key, console in retro.CONSOLES.items():
        if low in console.bios:
            return key
    return None


# ------------------------------------------------------------------ moving
def _console_dir(base: Path, console: retro.Console) -> Path:
    """The console's folder in the Games folder: one there by its name or an alias (any case), else made."""
    names = {n.lower() for n in (console.folder, *console.aliases)}
    for p in base.iterdir():
        if p.is_dir() and p.name.lower() in names:
            return p
    share.make_dir(base / console.folder)
    return base / console.folder


def _same_file(a: Path, b: Path) -> bool:
    """The same bytes (sizes first; hashed only when they match)."""
    try:
        if a.stat().st_size != b.stat().st_size:
            return False
        return _sha(a) == _sha(b)
    except OSError:
        return False


def _sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _free_name(folder: Path, name: str) -> Path:
    """`name` in `folder`, or "name (2).ext", "(3)"... when it's taken."""
    target = folder / name
    stem, suffix, n = Path(name).stem, Path(name).suffix, 2
    while target.exists():
        target = folder / f"{stem} ({n}){suffix}"
        n += 1
    return target


def _covers(rom: Path) -> list[Path]:
    """Pictures beside a ROM named like it: its box art (retro.COVER_EXT)."""
    return [rom.with_suffix(ext) for ext in retro.COVER_EXT if rom.with_suffix(ext).is_file()]


def _settled(path: Path, now: float) -> bool:
    try:
        return now - path.stat().st_mtime >= SETTLE_SECONDS
    except OSError:
        return False


def _leave(path: Path, why: str) -> None:
    """Note a file left in Unsorted (logged once, and not looked at again until it changes)."""
    try:
        st = path.stat()
    except OSError:
        return
    key = str(path)
    if _left.get(key, (None, None, None))[:2] != (st.st_size, st.st_mtime_ns):
        log.info("Retro: left %s in Unsorted: %s", path.name, why)
    _left[key] = (st.st_size, st.st_mtime_ns, why)


def _already_left(path: Path) -> bool:
    try:
        st = path.stat()
    except OSError:
        return True
    seen = _left.get(str(path))
    return seen is not None and seen[:2] == (st.st_size, st.st_mtime_ns)


# The suffix a console's shelf knows a game by, for a ROM that came under one it doesn't (mystery.bin -> .gba).
CANONICAL = {"nes": ".nes", "snes": ".sfc", "genesis": ".md", "gb": ".gb", "gbc": ".gbc", "gba": ".gba"}
DISC_IMAGES = (".bin", ".img", ".iso")   # a PS1 disc image on its own: it's given a .cue (the shelf lists discs by it)


def _rom_name(rom: Path, key: str) -> str:
    """The name the ROM goes by in its console's folder: its own, unless that console's shelf wouldn't list it
    (retro.console_of); then the console's own suffix. Box art keeps matching: the stem stays."""
    console = retro.CONSOLES[key]
    if retro.console_of(rom.name, console) or key not in CANONICAL:
        return rom.name
    return rom.stem + CANONICAL[key]


def _cue_for(image: Path) -> bytes:
    """A one-track .cue for a PS1 disc image that came without one: raw 2352-byte sectors (a .bin) or 2048."""
    raw = image.stat().st_size % 2352 == 0
    mode = "MODE2/2352" if raw else "MODE1/2048"
    return f'FILE "{image.name}" BINARY\r\n  TRACK 01 {mode}\r\n    INDEX 01 00:00:00\r\n'.encode()


def _move(files: list[Path], into: Path, key: str, game: bool = True) -> list[str]:
    """Move a game's files (the ROM first) into a console folder: the ROM under the name its shelf lists it by,
    the rest as they are, "(2)" for a name that's taken. A lone PS1 disc image gets a .cue beside it. `game`
    False: a BIOS, moved as it is."""
    moved = []
    for i, f in enumerate(files):
        target = _free_name(into, _rom_name(f, key) if i == 0 and game else f.name)
        share.rename(f, target, "sort the game")
        moved.append(target.name)
    rom = into / moved[0]
    if game and key == "psx" and rom.suffix.lower() in DISC_IMAGES:
        cue = _free_name(into, rom.stem + ".cue")
        with cue.open("xb") as out:                  # "x": never over a file that appeared meanwhile
            out.write(_cue_for(rom))
        moved[0] = cue.name                          # the game, as the shelf lists it
    return moved


def sort_unsorted(conn: sqlite3.Connection, now: float | None = None) -> list[tuple[str, str]]:
    """Move what's recognised in the Games folder's Unsorted folder into its console's folder. Returns
    (file name, console key) for each game moved. Nothing while Retro is off or the share is away."""
    rt = share.current()
    if rt is None or not retro.enabled(conn) or not share.mounted(rt):
        return []
    lib = retro.library(conn)
    if lib is None:
        return []
    base = rt.root / Path(lib["share_path"])
    drop = next((p for p in base.iterdir() if p.is_dir() and p.name.lower() == UNSORTED.lower()), None) \
        if base.is_dir() else None
    if drop is None:
        return []
    now = time.time() if now is None else now
    files = sorted(p for p in drop.rglob("*") if p.is_file() and not share.is_junk(p.name))
    taken: set[Path] = set()      # tracks and box art that go with a game already handled this pass
    moved: list[tuple[str, str]] = []
    looked = 0
    for path in files:
        if looked >= MAX_PER_PASS:
            break                                 # the rest next pass
        if path in taken or not path.exists() or _already_left(path) or not _settled(path, now):
            continue
        suffix = path.suffix.lower()
        if suffix in retro.COVER_EXT:
            continue                              # box art goes with its ROM, or stays
        group = [path]
        key = _bios_console(path.name)
        game = key is None                        # a BIOS isn't a game: moved as it is
        if key is None:
            if suffix == ".bin" and any(path in _cue_tracks(c) for c in path.parent.glob("*.[cC][uU][eE]")):
                continue                          # a disc's track: it goes with its .cue
            if suffix == ".cue":
                tracks = _cue_tracks(path)
                if not tracks or not all(t.is_file() and _settled(t, now) for t in tracks):
                    continue                      # its tracks aren't all here yet: the whole disc waits
                group += tracks
            looked += 1
            key = identify(path)
        if key is None:
            _leave(path, "not recognised as a game for any console here")
            continue
        group += _covers(path)
        into = _console_dir(base, retro.CONSOLES[key])
        if len(group) > 1 and any((into / f.name).exists() for f in group):
            _leave(path, f"some of its files are already in {into.name}; it's left whole rather than renamed")
            continue
        there = into / (_rom_name(path, key) if game else path.name)
        if there.exists() and _same_file(path, there):
            _leave(path, f"already in {into.name}")
            continue
        try:
            names = _move(group, into, key, game)
        except share.ShareError as exc:
            _leave(path, str(exc))
            continue
        taken.update(group)
        moved.append((names[0], key))
        log.info("Retro: sorted %s into %s", names[0], into.name)
    return moved


def ensure_unsorted(conn: sqlite3.Connection) -> bool:
    """The Unsorted folder in the Games folder, made once (an admin may remove it on purpose: it isn't made
    again). In the caller's transaction, beside retro.ensure_console_folders. True if it was made now."""
    rt = share.current()
    if rt is None or not retro.enabled(conn):
        return False
    if conn.execute("SELECT 1 FROM meta WHERE key = ?", (UNSORTED_KEY,)).fetchone() is not None:
        return False
    lib = retro.library(conn)
    if lib is None or not share.mounted(rt):
        return False
    base = rt.root / Path(lib["share_path"])
    made = not any(p.is_dir() and p.name.lower() == UNSORTED.lower() for p in base.iterdir()) if base.is_dir() else True
    if made:
        share.make_dir(base / UNSORTED)
        log.info("Retro: made the Unsorted folder in the Games folder")
    conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)", (UNSORTED_KEY, json.dumps(True)))
    return made
