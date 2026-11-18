"""Tiny homebrew games for testing Media → Retro, one per console (tests/retro-browser.mjs, python/retro_preview.py).

    python python/make_retro_test_rom.py <folder>     # writes one game per console into <folder>

No game of anyone else's: each is a few instructions written here by hand, in that console's own machine code. On
each boot it adds 1 to a byte of its battery save, then fills the screen with pink. The NES's also reads both pads every frame
(green while a button's held) and keeps a running total of them, for the netplay test. So a test can tell the emulator
ran (the screen's colour) and that the save went to the server and came back before the next boot (the byte went up
by one per boot). The PS1's is a bare program (a PS-X EXE) with no memory card code: it only paints.

None has its console maker's logo in its header: these emulators start a game without the console's own boot code,
which is what checks it.
"""

from __future__ import annotations

import struct
import sys
from pathlib import Path


def _pack(rom: bytearray, at: int, data: bytes) -> None:
    rom[at:at + len(data)] = data


# ------------------------------------------------------------- Game Boy Advance (ARM, mGBA)
# Each word is one ARM instruction: a MOV/ADD/ORR of an immediate is 0xE3A0..., 0xE28..., 0xE38... with the value as
# an 8-bit number rotated right by twice the 4-bit field before it.
GBA_PROGRAM = (
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
    0xE381101F,   # orr  r1, r1, #0x1F       ... and red: pink, in the GBA's 15-bit BGR
    0xE3A02C96,   # mov  r2, #0x9600         240 x 160 = 38,400 pixels
    0xE0C010B2,   # loop: strh r1, [r0], #2  one pixel, then the next
    0xE2522001,   # subs r2, r2, #1
    0x1AFFFFFC,   # bne  loop                (back 4 words from 8 ahead)
    0xEAFFFFFE,   # b    .                   and stay
)


def gba() -> bytes:
    """4 KB: a header that jumps to the program, the program, and the tag mGBA finds the save's kind by."""
    rom = bytearray(0x1000)
    struct.pack_into("<I", rom, 0x00, 0xEA00002E)          # b 0x080000C0: past the header (0x2E words from 8 ahead)
    _pack(rom, 0xA0, b"LUMI TEST\0\0\0")                    # the game's title, 12 bytes
    _pack(rom, 0xAC, b"LUMI")                               # its code
    _pack(rom, 0xB0, b"00")                                 # its maker
    rom[0xB2] = 0x96                                        # fixed by the format
    rom[0xBD] = (-(sum(rom[0xA0:0xBD]) + 0x19)) & 0xFF      # the header's checksum
    for i, word in enumerate(GBA_PROGRAM):
        struct.pack_into("<I", rom, 0xC0 + 4 * i, word)
    _pack(rom, 0xC0 + 4 * len(GBA_PROGRAM) + 16, b"SRAM_V113")   # as a commercial game's save library leaves it
    return bytes(rom)


# ---------------------------------------------------------------------- NES (6502, FCEUmm)
NES_PROGRAM = bytes((
    0x78,                    # $8000 sei
    0xD8,                    # $8001 cld
    0xA2, 0xFF,              # $8002 ldx #$FF
    0x9A,                    # $8004 txs
    0x2C, 0x02, 0x20,        # $8005 bit $2002     clear a vertical-blank flag left from power-on, then wait out
    0x2C, 0x02, 0x20,        # $8008 bit $2002     the PPU's warm-up (it ignores writes for a frame): one blank ...
    0x10, 0xFB,              # $800B bpl $8008
    0x2C, 0x02, 0x20,        # $800D bit $2002     ... and another
    0x10, 0xFB,              # $8010 bpl $800D
    0xEE, 0x00, 0x60,        # $8012 inc $6000     one more boot counted, in the battery-backed RAM
    0xA9, 0x3F,              # $8015 lda #$3F      the palette ($3F00) ...
    0x8D, 0x06, 0x20,        # $8017 sta $2006
    0xA9, 0x00,              # $801A lda #$00
    0x8D, 0x06, 0x20,        # $801C sta $2006
    0xA2, 0x20,              # $801F ldx #32       every palette entry (the PPU's RAM starts with a pattern,
    0xA9, 0x24,              # $8021 lda #$24      not zeros, so its blank tiles aren't all colour 0) ...
    0x8D, 0x07, 0x20,        # $8023 sta $2007     ... is $24, the NES's pink
    0xCA,                    # $8026 dex
    0xD0, 0xF8,              # $8027 bne $8021
    0xA9, 0x00,              # $8029 lda #$00      the PPU's address back to $0000
    0x8D, 0x06, 0x20,        # $802B sta $2006
    0x8D, 0x06, 0x20,        # $802E sta $2006
    0xA9, 0x0A,              # $8031 lda #$0A      background on
    0x8D, 0x01, 0x20,        # $8033 sta $2001
    # Then, every frame: read both pads and fold them into a running total, so a game's state depends on every
    # press of both players at the frame it came (tests/retro-netplay-browser.mjs: two browsers must agree).
    0x2C, 0x02, 0x20,        # $8036 bit $2002     wait for the vertical blank ...
    0x10, 0xFB,              # $8039 bpl $8036
    0xA9, 0x01,              # $803B lda #1        ... strobe both pads ...
    0x8D, 0x16, 0x40,        # $803D sta $4016
    0xA9, 0x00,              # $8040 lda #0
    0x8D, 0x16, 0x40,        # $8042 sta $4016
    0xA2, 0x08,              # $8045 ldx #8        ... and read their eight buttons:
    0xAD, 0x16, 0x40,        # $8047 lda $4016     player 1's into $10 ...
    0x4A,                    # $804A lsr a
    0x26, 0x10,              # $804B rol $10
    0xAD, 0x17, 0x40,        # $804D lda $4017     ... player 2's into $11
    0x4A,                    # $8050 lsr a
    0x26, 0x11,              # $8051 rol $11
    0xCA,                    # $8053 dex
    0xD0, 0xF1,              # $8054 bne $8047
    0xA5, 0x12,              # $8056 lda $12       the running totals: $12 = $12 * 2 + player 1 ...
    0x0A,                    # $8058 asl a
    0x65, 0x10,              # $8059 adc $10
    0x85, 0x12,              # $805B sta $12
    0xA5, 0x13,              # $805D lda $13       ... $13 = $13 * 2 + player 2
    0x0A,                    # $805F asl a
    0x65, 0x11,              # $8060 adc $11
    0x85, 0x13,              # $8062 sta $13
    0xE6, 0x14,              # $8064 inc $14       and the frames counted
    0xA9, 0x3F,              # $8066 lda #$3F      the palette ($3F00) ...
    0x8D, 0x06, 0x20,        # $8068 sta $2006
    0xA9, 0x00,              # $806B lda #$00
    0x8D, 0x06, 0x20,        # $806D sta $2006
    0xA5, 0x10,              # $8070 lda $10       ... all green while either pad holds a button ...
    0x05, 0x11,              # $8072 ora $11
    0xF0, 0x04,              # $8074 beq $807A
    0xA9, 0x2A,              # $8076 lda #$2A
    0xD0, 0x02,              # $8078 bne $807C
    0xA9, 0x24,              # $807A lda #$24      ... all pink otherwise
    0xA2, 0x20,              # $807C ldx #32       (every entry: the screen's tiles use all of them)
    0x8D, 0x07, 0x20,        # $807E sta $2007
    0xCA,                    # $8081 dex
    0xD0, 0xFA,              # $8082 bne $807E
    0xA9, 0x00,              # $8084 lda #$00      the PPU's address back to $0000
    0x8D, 0x06, 0x20,        # $8086 sta $2006
    0x8D, 0x06, 0x20,        # $8089 sta $2006
    0x4C, 0x36, 0x80,        # $808C jmp $8036     the next frame
))


def nes() -> bytes:
    """iNES: one 16 KB program bank (mapper 0), no graphics ROM (the PPU's own RAM), and a battery."""
    header = b"NES\x1a" + bytes((1, 0, 0x02, 0)) + bytes(8)   # 1 x 16 KB of program; flags 6: battery
    prg = bytearray(0x4000)
    _pack(prg, 0, NES_PROGRAM)
    struct.pack_into("<HHH", prg, 0x3FFA, 0x8036, 0x8000, 0x8036)   # NMI, reset, IRQ ($FFFA-$FFFF)
    return header + bytes(prg)


# ----------------------------------------------------------- Super Nintendo (65816, Snes9x)
SNES_PROGRAM = bytes((
    0x78,                    # sei                 (from reset: the 6502-like mode, 8-bit registers)
    0xA9, 0x80,              # lda #$80
    0x8D, 0x00, 0x21,        # sta $2100           screen blanked while the colour is written
    0x9C, 0x21, 0x21,        # stz $2121           colour 0 (the backdrop) ...
    0xA9, 0x1F,              # lda #$1F
    0x8D, 0x22, 0x21,        # sta $2122
    0xA9, 0x7C,              # lda #$7C
    0x8D, 0x22, 0x21,        # sta $2122           ... is pink ($7C1F, 15-bit BGR)
    0xAF, 0x00, 0x00, 0x70,  # lda $700000         the battery save (SRAM, LoROM) ...
    0x1A,                    # inc a               ... one more boot
    0x8F, 0x00, 0x00, 0x70,  # sta $700000
    0xA9, 0x0F,              # lda #$0F
    0x8D, 0x00, 0x21,        # sta $2100           screen on, full brightness: no layers, so all backdrop
    0x80, 0xFE,              # bra *               and stay
))


def snes() -> bytes:
    """128 KB LoROM with 2 KB of battery-backed SRAM; the program at $00:8000, the header at $00:FFC0."""
    rom = bytearray(0x20000)
    _pack(rom, 0, SNES_PROGRAM)
    _pack(rom, 0x7FC0, b"LUMI TEST".ljust(21))              # title, 21 bytes
    rom[0x7FD5] = 0x20                                      # LoROM
    rom[0x7FD6] = 0x02                                      # ROM + RAM + battery
    rom[0x7FD7] = 0x07                                      # 2^7 KB = 128 KB
    rom[0x7FD8] = 0x01                                      # 2^1 KB = 2 KB of SRAM
    rom[0x7FD9] = 0x01                                      # North America
    for at in range(0x7FE4, 0x8000, 2):                     # every vector to the program (none but reset is used)
        struct.pack_into("<H", rom, at, 0x8000)
    struct.pack_into("<HH", rom, 0x7FDC, 0xFFFF, 0x0000)    # complement and checksum, before summing ...
    total = sum(rom) & 0xFFFF
    struct.pack_into("<HH", rom, 0x7FDC, total ^ 0xFFFF, total)   # ... which those four bytes don't change
    return bytes(rom)


# --------------------------------------------------------- Game Boy Color (SM83, Gambatte)
GBC_PROGRAM = bytes((
    0x3E, 0x00, 0xE0, 0x40,  # $0150 ld a,$00; ldh ($40),a     screen off, so the palettes can be written
    0x3E, 0x80, 0xE0, 0x68,  # $0154 ld a,$80; ldh ($68),a     background palettes from the first, counting on
    0x06, 0x20,              # $0158 ld b,32                   8 palettes x 4 colours ...
    0x3E, 0x1F, 0xE0, 0x69,  # $015A ld a,$1F; ldh ($69),a
    0x3E, 0x7C, 0xE0, 0x69,  # $015E ld a,$7C; ldh ($69),a     ... each pink ($7C1F, 15-bit BGR)
    0x05,                    # $0162 dec b
    0x20, 0xF5,              # $0163 jr nz,$015A
    0x3E, 0x0A,              # $0165 ld a,$0A
    0xEA, 0x00, 0x00,        # $0167 ld ($0000),a              the cartridge's RAM on
    0xFA, 0x00, 0xA0,        # $016A ld a,($A000)              its first byte ...
    0x3C,                    # $016D inc a                     ... one more boot
    0xEA, 0x00, 0xA0,        # $016E ld ($A000),a
    0x3E, 0x91,              # $0171 ld a,$91
    0xE0, 0x40,              # $0173 ldh ($40),a               screen on, background on: every tile is pink
    0x18, 0xFE,              # $0175 jr $0175                  and stay
))


def gbc() -> bytes:
    """32 KB, Game Boy Color mode, an MBC1 with 8 KB of battery-backed RAM."""
    rom = bytearray(0x8000)
    _pack(rom, 0x100, bytes((0x00, 0xC3, 0x50, 0x01)))       # nop; jp $0150: past the header
    _pack(rom, 0x134, b"LUMI TEST")                          # title
    rom[0x143] = 0x80                                        # works on a Game Boy Color: its palettes are used
    rom[0x147] = 0x03                                        # MBC1 + RAM + battery
    rom[0x148] = 0x00                                        # 32 KB
    rom[0x149] = 0x02                                        # 8 KB of RAM
    check = 0
    for b in rom[0x134:0x14D]:
        check = (check - b - 1) & 0xFF
    rom[0x14D] = check                                       # the header's checksum
    _pack(rom, 0x150, GBC_PROGRAM)
    return bytes(rom)


# --------------------------------------------------------------- Genesis (68000, Genesis Plus GX)
GENESIS_PROGRAM = bytes.fromhex(
    "33FC 8004 00C0 0004"        # move.w #$8004,$C00004    VDP register 0
    "33FC 8144 00C0 0004"        # move.w #$8144,$C00004    register 1: display on, Mega Drive mode
    "33FC 8700 00C0 0004"        # move.w #$8700,$C00004    register 7: the backdrop is palette 0, colour 0
    "23FC C000 0000 00C0 0004"   # move.l #$C0000000,$C00004   write colour memory from 0 ...
    "33FC 0E0E 00C0 0000"        # move.w #$0E0E,$C00000    ... pink (blue 7, red 7: %0000BBB0GGG0RRR0)
    "1039 0020 0001"             # move.b $200001,d0        the battery save (odd bytes from $200001) ...
    "5200"                       # addq.b #1,d0             ... one more boot
    "13C0 0020 0001"             # move.b d0,$200001
    "60FE"                       # bra.s *                  and stay
    .replace(" ", ""))


def genesis() -> bytes:
    """64 KB: the 68000's vectors, the cartridge header with its battery RAM, the program at $200."""
    rom = bytearray(0x10000)
    struct.pack_into(">II", rom, 0, 0x00FFFE00, 0x00000200)   # the stack, then where to start
    for at in range(8, 0x100, 4):                              # every exception to the program too
        struct.pack_into(">I", rom, at, 0x00000200)
    _pack(rom, 0x100, b"SEGA MEGA DRIVE ")
    _pack(rom, 0x110, b"(C)LUMI 2026.NOV")
    _pack(rom, 0x120, b"LUMI TEST".ljust(48))
    _pack(rom, 0x150, b"LUMI TEST".ljust(48))
    _pack(rom, 0x180, b"GM 00000000-00")
    _pack(rom, 0x190, b"J".ljust(16))
    struct.pack_into(">IIII", rom, 0x1A0, 0, len(rom) - 1, 0x00FF0000, 0x00FFFFFF)   # ROM and RAM ranges
    _pack(rom, 0x1B0, b"RA\xf8\x20")                           # battery-backed RAM on the odd bytes ...
    struct.pack_into(">II", rom, 0x1B4, 0x00200001, 0x00203FFF)   # ... from $200001 to $203FFF
    _pack(rom, 0x1F0, b"JUE")
    _pack(rom, 0x200, GENESIS_PROGRAM)
    return bytes(rom)


# ------------------------------------------------------------- PlayStation (MIPS, PCSX-ReARMed)
def _mips(*words: int) -> bytes:
    return b"".join(struct.pack("<I", w) for w in words)


PSX_PROGRAM = _mips(
    0x3C081F80,              # lui  t0, 0x1F80          the hardware registers (GP0 at +0x1810, GP1 at +0x1814)
    0xAD001814,              # sw   zero, 0x1814(t0)    GP1(00h): reset the GPU
    0x3C090800, 0x35290001,  # li   t1, 0x08000001      GP1(08h): 320 x 240, NTSC, 15-bit
    0xAD091814,              # sw   t1, 0x1814(t0)
    0x3C0906C6, 0x35290260,  # li   t1, 0x06C60260      GP1(06h): the picture's width on the screen
    0xAD091814,
    0x3C090704, 0x35292018,  # li   t1, 0x07042018      GP1(07h): its height
    0xAD091814,
    0x3C090500,              # lui  t1, 0x0500          GP1(05h): show video memory from (0, 0)
    0xAD091814,
    0x3C0902FF, 0x352900FF,  # li   t1, 0x02FF00FF      GP0(02h): fill a rectangle with pink (red FF, green 00, blue FF) ...
    0xAD091810,
    0xAD001810,              # sw   zero, 0x1810(t0)    ... from (0, 0) ...
    0x3C0900F0, 0x35290140,  # li   t1, 0x00F00140      ... 320 wide, 240 high
    0xAD091810,
    0x3C090300,              # lui  t1, 0x0300          GP1(03h): display on
    0xAD091814,
    0x1000FFFF,              # b    .                   and stay
    0x00000000,              # nop                      (the branch's delay slot)
)


def psx() -> bytes:
    """A PS-X EXE: its 2 KB header, then the program, loaded at 0x80010000 and started there."""
    text = PSX_PROGRAM.ljust(0x800, b"\0")
    header = bytearray(0x800)
    _pack(header, 0, b"PS-X EXE")
    struct.pack_into("<IIII", header, 0x10, 0x80010000, 0, 0x80010000, len(text))   # start, gp, load at, size
    struct.pack_into("<II", header, 0x30, 0x801FFF00, 0)                          # the stack
    _pack(header, 0x4C, b"Sony Computer Entertainment Inc. for North America area")
    return bytes(header) + text


# Console key (media/retro.py CONSOLES) -> (file name, the builder, where its boot count is in the save file, if it
# keeps one). Genesis Plus GX keeps the 64 KB from $200000, so the odd byte at $200001 is the file's second.
GAMES = {
    "nes": ("Lumi Test.nes", nes, 0),
    "snes": ("Lumi Test.sfc", snes, 0),
    "genesis": ("Lumi Test.md", genesis, 1),
    "gbc": ("Lumi Test.gbc", gbc, 0),
    "gba": ("Lumi Test.gba", gba, 0),
    "psx": ("Lumi Test.exe", psx, None),
}


def build() -> bytes:
    """The GBA game (kept for callers from before there was one per console)."""
    return gba()


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("python python/make_retro_test_rom.py <folder>")
    out = Path(sys.argv[1])
    out.mkdir(parents=True, exist_ok=True)
    for key, (name, make, _) in GAMES.items():
        (out / name).write_bytes(make())
        print(f"{key}: {name}")
