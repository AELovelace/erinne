"""A tiny homebrew Game Boy Advance ROM for testing Media → Retro (tests/retro-browser.mjs, python/retro_preview.py).

    python python/make_retro_test_rom.py out.gba

No game of anyone else's: a few ARM instructions written here, by hand. On each boot it adds 1 to the first byte of
its battery save (SRAM), then fills the screen with pink. So a test can tell the emulator ran (the screen's colour),
and that the save went to the server and came back before the next boot (the byte went up by one per boot).

It has no Nintendo logo in its header: mGBA starts it without a BIOS (its own high-level one), which doesn't check.
"""

from __future__ import annotations

import struct
import sys
from pathlib import Path

PINK = 0x7C1F          # the screen's colour, in the GBA's 15-bit BGR: full red and blue (magenta-pink)
# 0x7C1F as 8-bit RGB, as a screenshot shows it: red and blue at full, no green.
PINK_RGB = (255, 0, 255)

# The program, from 0x080000C0 (right after the header). Each word is one ARM instruction, worked out by hand:
# a MOV/ADD/ORR of an immediate is 0xE3A0..., 0xE28..., 0xE38... with the value as an 8-bit number rotated right
# by twice the 4-bit field before it.
PROGRAM = (
    0xE3A00301,   # mov  r0, #0x04000000     the display registers (1 ror 6)
    0xE3A01B01,   # mov  r1, #0x400          BG2 on ...
    0xE2811003,   # add  r1, r1, #3          ... in mode 3 (one 15-bit colour per pixel): DISPCNT = 0x0403
    0xE1C010B0,   # strh r1, [r0]
    0xE3A0340E,   # mov  r3, #0x0E000000     the battery save (SRAM) (0x0E ror 8)
    0xE5D34000,   # ldrb r4, [r3]            its first byte ...
    0xE2844001,   # add  r4, r4, #1          ... plus one ...
    0xE5C34000,   # strb r4, [r3]            ... kept: one more boot counted
    0xE3A00406,   # mov  r0, #0x06000000     the screen's pixels (VRAM) (0x06 ror 8)
    0xE3A01C7C,   # mov  r1, #0x7C00         blue ...
    0xE381101F,   # orr  r1, r1, #0x1F       ... and red: PINK
    0xE3A02C96,   # mov  r2, #0x9600         240 x 160 = 38,400 pixels
    0xE0C010B2,   # loop: strh r1, [r0], #2  one pixel, then the next
    0xE2522001,   # subs r2, r2, #1
    0x1AFFFFFC,   # bne  loop                (back 4 words from 8 ahead)
    0xEAFFFFFE,   # b    .                   and stay
)
# mGBA picks the save's kind by finding one of these in the ROM, as a commercial game's save library leaves there.
SAVE_TAG = b"SRAM_V113"


def build() -> bytes:
    """The ROM's bytes: a header that jumps to the program, the program, the save tag, padded to 4 KB."""
    rom = bytearray(0x1000)
    struct.pack_into("<I", rom, 0x00, 0xEA00002E)          # b 0x080000C0: past the header (0x2E words from 8 ahead)
    rom[0xA0:0xAC] = b"LUMI TEST\0\0\0"                     # the game's title, 12 bytes
    rom[0xAC:0xB0] = b"LUMI"                                # its code
    rom[0xB0:0xB2] = b"00"                                  # its maker
    rom[0xB2] = 0x96                                        # fixed by the format
    rom[0xBD] = (-(sum(rom[0xA0:0xBD]) + 0x19)) & 0xFF      # the header's checksum
    for i, word in enumerate(PROGRAM):
        struct.pack_into("<I", rom, 0xC0 + 4 * i, word)
    at = 0xC0 + 4 * len(PROGRAM) + 16                       # the tag, a little after the program, word-aligned
    rom[at:at + len(SAVE_TAG)] = SAVE_TAG
    return bytes(rom)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("python python/make_retro_test_rom.py <out.gba>")
    Path(sys.argv[1]).write_bytes(build())
    print(f"Wrote {sys.argv[1]} ({len(build())} bytes).")
