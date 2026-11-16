"""media/romsort.py: Retro's Unsorted folder. ROMs dropped there are recognised by what's inside them (the test
games' real headers, python/make_retro_test_rom.py; inside a .zip or a .7z too) and moved into their console's
folder, with a disc's tracks and a game's box art; what isn't recognised, is still copying, or is already there
stays put."""

import importlib.util
import io
import lzma
import os
import shutil
import subprocess
import time
import zipfile
from pathlib import Path

import pytest

from conftest import FeatureConn
from mnemerynne import share
from mnemerynne.media import catalog, retro, romsort
from test_retro import _by_title, _login, _put, _turn_on, client, conn, checker, directory, root, settings  # noqa: F401

_spec = importlib.util.spec_from_file_location("make_retro_test_rom",
                                               Path(__file__).parent.parent / "python" / "make_retro_test_rom.py")
roms = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(roms)

SEVEN_ZIP = shutil.which("7z") or shutil.which("7za") or (r"C:\Program Files\7-Zip\7z.exe"
                                                           if os.path.exists(r"C:\Program Files\7-Zip\7z.exe") else None)


def gb() -> bytes:
    """The Game Boy Color test game made a plain Game Boy one (no Color flag, the header checksum redone)."""
    rom = bytearray(roms.gbc())
    rom[0x143] = 0x00
    check = 0
    for b in rom[0x134:0x14D]:
        check = (check - b - 1) & 0xFF
    rom[0x14D] = check
    return bytes(rom)


def ps1_disc() -> bytes:
    """A raw (2352-byte sector) disc image whose ISO 9660 volume, at sector 16, says PLAYSTATION."""
    image = bytearray(17 * 2352)
    pvd = 16 * 2352 + 24                                        # Mode 2 XA: sync, header and subheader first
    image[pvd:pvd + 19] = b"\x01CD001\x01\x00PLAYSTATION"
    return bytes(image)


def test_each_consoles_rom_is_known_by_its_header():
    assert romsort.identify_bytes(roms.nes()) == "nes"
    assert romsort.identify_bytes(roms.snes()) == "snes"
    assert romsort.identify_bytes(b"\0" * 0x200 + roms.snes()) == "snes"     # behind a copier header
    assert romsort.identify_bytes(roms.genesis()) == "genesis"
    assert romsort.identify_bytes(roms.gbc()) == "gbc"
    assert romsort.identify_bytes(gb()) == "gb"
    assert romsort.identify_bytes(roms.gba()) == "gba"
    assert romsort.identify_bytes(roms.psx()) == "psx"                       # a PS-X EXE
    assert romsort.identify_bytes(ps1_disc()) == "psx"
    assert romsort.identify_bytes(b"\0PBP" + b"\0" * 60) == "psx"
    assert romsort.identify_bytes(b"MComprHD" + b"\0" * 60) == "psx"
    assert romsort.identify_bytes(b"just some text\n" * 1000) is None
    assert romsort.identify_bytes(b"") is None


def test_a_zip_is_known_by_its_biggest_member_and_a_bare_suffix_only_last(tmp_path):
    z = tmp_path / "mystery.zip"
    with zipfile.ZipFile(z, "w", zipfile.ZIP_DEFLATED) as f:
        f.writestr("readme.txt", "Have fun")
        f.writestr("game.dat", roms.gba())                     # no telling suffix: its header says GBA
    assert romsort.identify(z) == "gba"
    named = tmp_path / "named.zip"
    with zipfile.ZipFile(named, "w") as f:
        f.writestr("Sonic.smd", b"\0" * 4096)                   # interleaved, no plain header: its suffix decides
    assert romsort.identify(named) == "genesis"
    (tmp_path / "plain.bin").write_bytes(roms.genesis())        # .bin is anyone's: the header decides
    assert romsort.identify(tmp_path / "plain.bin") == "genesis"
    (tmp_path / "blank.bin").write_bytes(b"\0" * 4096)
    assert romsort.identify(tmp_path / "blank.bin") is None
    (tmp_path / "broken.zip").write_bytes(b"PK\x03\x04 not really")
    assert romsort.identify(tmp_path / "broken.zip") is None


def test_a_7z_is_known_by_the_start_of_its_first_stream(tmp_path):
    """The parts romsort reads: the 32-byte signature header, then the packed stream (LZMA2, LZMA, stored)."""
    head = b"7z\xbc\xaf\x27\x1c\x00\x04" + b"\0" * 24
    for i, filters in enumerate(([{"id": lzma.FILTER_LZMA2}], [{"id": lzma.FILTER_LZMA1}])):
        packed = lzma.compress(roms.snes(), format=lzma.FORMAT_RAW, filters=filters)
        (tmp_path / f"{i}.7z").write_bytes(head + packed + b"\0" * 64)
        assert romsort.identify(tmp_path / f"{i}.7z") == "snes", filters
    (tmp_path / "stored.7z").write_bytes(head + roms.nes())
    assert romsort.identify(tmp_path / "stored.7z") == "nes"
    (tmp_path / "fake.7z").write_bytes(b"not a 7z at all")
    assert romsort.identify(tmp_path / "fake.7z") is None


@pytest.mark.skipif(SEVEN_ZIP is None, reason="7-Zip isn't installed here")
def test_real_7z_archives_from_7_zip(tmp_path):
    """Archives as 7-Zip itself makes them: its default (LZMA2), LZMA, stored, and a whole PS1 disc."""
    src = tmp_path / "src"
    src.mkdir()
    (src / "Lumi Test.gba").write_bytes(roms.gba())
    (src / "Disc.bin").write_bytes(ps1_disc())
    (src / "Disc.cue").write_bytes(b'FILE "Disc.bin" BINARY\n  TRACK 01 MODE2/2352\n')
    for name, method, files in (("default.7z", [], ["Lumi Test.gba"]), ("lzma.7z", ["-m0=LZMA"], ["Lumi Test.gba"]),
                                ("stored.7z", ["-mx0"], ["Lumi Test.gba"]), ("disc.7z", [], ["Disc.cue", "Disc.bin"])):
        subprocess.run([SEVEN_ZIP, "a", "-bd", "-y", *method, str(tmp_path / name), *files], cwd=src, check=True,
                       capture_output=True)
    assert romsort.identify(tmp_path / "default.7z") == "gba"
    assert romsort.identify(tmp_path / "lzma.7z") == "gba"
    assert romsort.identify(tmp_path / "stored.7z") == "gba"
    assert romsort.identify(tmp_path / "disc.7z") == "psx"     # 7-Zip packs the .bin (by type) before the .cue


def _sort(conn, settings, now=None):
    """One catalog pass: it makes Unsorted (once) and sorts it."""
    catalog.run_pass(FeatureConn(conn, "media", "media_items"), settings, budget_s=5, core=conn)
    return romsort.sort_unsorted(conn, now=now)


def test_unsorted_roms_go_to_their_consoles_folders(client, conn, directory, root, settings):
    _login(client, conn, directory)
    _turn_on(client)
    _sort(conn, settings)
    games = root / "Games"
    drop = games / "Unsorted"
    assert drop.is_dir()                                         # made with the console folders

    _put(drop / "mystery.bin", roms.gba())                       # a GBA game under a name that says nothing
    _put(drop / "mystery.png", b"\x89PNG box art")               # its box art goes with it
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("Lumi Test.nes", roms.nes())
    _put(drop / "From a friend" / "Lumi NES.zip", buf.getvalue())   # in a subfolder, zipped
    _put(drop / "Lumi Colour.gb", roms.gbc())                    # its header says Color, whatever its suffix
    _put(drop / "Disc 1.cue", b'FILE "disc 1.BIN" BINARY\n  TRACK 01 MODE2/2352\n')   # the track in another case
    _put(drop / "Disc 1.bin", ps1_disc())
    _put(drop / "scph5501.bin", b"\0" * 512 * 1024)               # the PS1's BIOS, by its name
    _put(drop / "Lone Disc.bin", ps1_disc())                     # a PS1 disc image without its .cue: one is written
    _put(drop / "notes.txt", b"what's this")                     # not a game: stays
    fresh = drop / "Copying.gba"
    fresh.write_bytes(roms.gba())                                # changed just now: may still be copying in
    _put(games / "GBA" / "Lumi Test.gba", roms.gba())            # already sorted...
    _put(drop / "Lumi Test.gba", roms.gba())                     # ... so this copy, byte for byte, stays
    _put(games / "SNES" / "Clash.sfc", b"another game")
    _put(drop / "Clash.sfc", roms.snes())                        # a different game by a taken name: (2)

    soon = time.time() + 61                                      # _put's files are 60 s old: past SETTLE_SECONDS
    moved = dict(romsort.sort_unsorted(conn, now=soon))
    assert moved == {"mystery.gba": "gba", "Lumi NES.zip": "nes", "Lumi Colour.gb": "gbc", "Disc 1.cue": "psx",
                     "scph5501.bin": "psx", "Lone Disc.cue": "psx", "Clash (2).sfc": "snes"}
    assert (games / "GBA" / "mystery.gba").read_bytes() == roms.gba()   # a suffix the GBA shelf lists
    assert (games / "GBA" / "mystery.png").is_file()             # the box art came along
    assert (games / "NES" / "Lumi NES.zip").is_file()
    assert (games / "Game Boy Color" / "Lumi Colour.gb").is_file()
    assert (games / "PS1" / "Disc 1.cue").is_file() and (games / "PS1" / "Disc 1.bin").is_file()   # the disc whole
    assert (games / "PS1" / "scph5501.bin").is_file() and not (games / "PS1" / "scph5501.cue").exists()
    assert (games / "PS1" / "Lone Disc.cue").read_bytes() == \
        b'FILE "Lone Disc.bin" BINARY\r\n  TRACK 01 MODE2/2352\r\n    INDEX 01 00:00:00\r\n'
    assert (games / "SNES" / "Clash (2).sfc").read_bytes() == roms.snes()
    assert (games / "SNES" / "Clash.sfc").read_bytes() == b"another game"   # never replaced
    left = sorted(p.relative_to(drop).as_posix() for p in drop.rglob("*") if p.is_file())
    assert left == ["Copying.gba", "Lumi Test.gba", "notes.txt"]

    # Settled later, the copied-in one goes too; the duplicate and the note are not looked at again.
    assert romsort.sort_unsorted(conn, now=time.time() + romsort.SETTLE_SECONDS + 1) == [("Copying.gba", "gba")]

    # The shelves see the sorted games once the share scan has (as for any move on the share).
    share.sync(conn, settings)
    titles = _by_title(client)
    assert {"mystery", "Lumi NES", "Lumi Colour", "Disc 1", "Lone Disc", "Clash", "Copying"} <= set(titles)


def test_a_disc_waits_for_all_its_tracks_and_unsorted_isnt_made_again(client, conn, directory, root, settings):
    _login(client, conn, directory)
    _turn_on(client)
    _sort(conn, settings)
    drop = root / "Games" / "Unsorted"
    _put(drop / "Disc.cue", b'FILE "Disc (Track 1).bin" BINARY\nFILE "Disc (Track 2).bin" BINARY\n')
    _put(drop / "Disc (Track 1).bin", ps1_disc())
    soon = time.time() + 61                                      # _put's files are 60 s old: past SETTLE_SECONDS
    assert romsort.sort_unsorted(conn, now=soon) == []           # track 2 is still on its way
    _put(drop / "Disc (Track 2).bin", b"audio")
    assert romsort.sort_unsorted(conn, now=soon) == [("Disc.cue", "psx")]
    assert sorted(p.name for p in (root / "Games" / "PS1").iterdir()) == ["Disc (Track 1).bin", "Disc (Track 2).bin",
                                                                          "Disc.cue"]
    drop.rmdir()                                                 # an admin removed it on purpose
    _sort(conn, settings)
    assert not drop.exists()
    assert romsort.sort_unsorted(conn, now=soon) == []           # and with no Unsorted, nothing to do
