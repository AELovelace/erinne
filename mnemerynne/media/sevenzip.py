"""The names of the files in a .7z, read from its header with the standard library (no 7z package here).

Retro needs them to offer the ROMs in an archive that holds several (an original and its hacks): "Which ROM?"
(web/retro.py). A 7z keeps its file list in a header at its end, usually itself LZMA-compressed ("encoded"):
  [32-byte signature header: where the end header is] ... [end header]
  end header = kHeader (plain) or kEncodedHeader (its streams info: where the packed header is, and its coder,
  LZMA or LZMA2 with their properties; unpacked with lzma's raw decoder, it is a kHeader)
  kHeader = [archive properties] [additional streams] [main streams info] [files info: kName = UTF-16 names, ...]
Every structure is walked in order (7-Zip's 7zFormat.txt), as the file list comes after the streams info, which
has no length of its own. Anything unexpected (another coder for the header, encryption, a damaged file) gives
None: the archive then plays as it always has.
"""

from __future__ import annotations

import lzma
import struct
from pathlib import Path

SIGNATURE = b"7z\xbc\xaf\x27\x1c"
MAX_HEADER = 16 << 20            # a header this big is no ROM archive

K_END, K_HEADER, K_ARCHIVE_PROPERTIES, K_ADDITIONAL_STREAMS, K_MAIN_STREAMS, K_FILES_INFO = 0x00, 0x01, 0x02, 0x03, 0x04, 0x05
K_PACK_INFO, K_UNPACK_INFO, K_SUBSTREAMS_INFO, K_SIZE, K_CRC, K_FOLDER = 0x06, 0x07, 0x08, 0x09, 0x0A, 0x0B
K_CODERS_UNPACK_SIZE, K_NUM_UNPACK_STREAM, K_NAME, K_ENCODED_HEADER = 0x0C, 0x0D, 0x11, 0x17
LZMA_ID, LZMA2_ID = b"\x03\x01\x01", b"\x21"


class Bad(Exception):
    """Not a 7z this reader follows."""


class _Reader:
    def __init__(self, data: bytes) -> None:
        self.data, self.at = data, 0

    def byte(self) -> int:
        if self.at >= len(self.data):
            raise Bad("ran off the end")
        b = self.data[self.at]
        self.at += 1
        return b

    def take(self, n: int) -> bytes:
        if n < 0 or self.at + n > len(self.data):
            raise Bad("ran off the end")
        out = self.data[self.at:self.at + n]
        self.at += n
        return out

    def number(self) -> int:
        """7z's variable-length number: the first byte's leading 1 bits say how many bytes follow."""
        first, mask, value = self.byte(), 0x80, 0
        for i in range(8):
            if not first & mask:
                return value | ((first & (mask - 1)) << (8 * i))
            value |= self.byte() << (8 * i)
            mask >>= 1
        return value

    def bits(self, n: int) -> list[bool]:
        out, b, mask = [], 0, 0
        for _ in range(n):
            if not mask:
                b, mask = self.byte(), 0x80
            out.append(bool(b & mask))
            mask >>= 1
        return out

    def digests(self, n: int) -> list[bool]:
        """A CRC list: which of n are defined (each then 4 bytes)."""
        defined = [True] * n if self.byte() else self.bits(n)
        self.take(4 * sum(defined))
        return defined


def _streams_info(r: _Reader) -> dict:
    """PackInfo, UnPackInfo (folders: coders and unpack sizes) and SubStreamsInfo, walked to their end."""
    info: dict = {"pack_pos": 0, "pack_sizes": [], "folders": []}
    while True:
        t = r.byte()
        if t == K_END:
            return info
        if t == K_PACK_INFO:
            info["pack_pos"] = r.number()
            count = r.number()
            while True:
                k = r.byte()
                if k == K_END:
                    break
                if k == K_SIZE:
                    info["pack_sizes"] = [r.number() for _ in range(count)]
                elif k == K_CRC:
                    r.digests(count)
                else:
                    raise Bad("pack info")
        elif t == K_UNPACK_INFO:
            if r.byte() != K_FOLDER:
                raise Bad("unpack info")
            folders = [None] * r.number()
            if r.byte():
                raise Bad("external folders")
            for i in range(len(folders)):
                coders, outs, ins = [], 0, 0
                for _ in range(r.number()):
                    flag = r.byte()
                    coder = {"id": r.take(flag & 0x0F), "props": b""}
                    n_in, n_out = (r.number(), r.number()) if flag & 0x10 else (1, 1)
                    if flag & 0x20:
                        coder["props"] = r.take(r.number())
                    if flag & 0x80:
                        raise Bad("alternative coders")
                    ins, outs = ins + n_in, outs + n_out
                    coders.append(coder)
                for _ in range(outs - 1):                 # bind pairs
                    r.number(), r.number()
                packed = ins - (outs - 1)
                if packed > 1:
                    for _ in range(packed):
                        r.number()
                folders[i] = {"coders": coders, "outs": outs, "sizes": [], "crc": False}
            if r.byte() != K_CODERS_UNPACK_SIZE:
                raise Bad("unpack sizes")
            for f in folders:
                f["sizes"] = [r.number() for _ in range(f["outs"])]
            while True:
                k = r.byte()
                if k == K_END:
                    break
                if k == K_CRC:
                    for f, has in zip(folders, r.digests(len(folders))):
                        f["crc"] = has
                else:
                    raise Bad("unpack info end")
            info["folders"] = folders
        elif t == K_SUBSTREAMS_INFO:
            folders = info["folders"]
            streams = [1] * len(folders)
            k = r.byte()
            if k == K_NUM_UNPACK_STREAM:
                streams = [r.number() for _ in folders]
                k = r.byte()
            if k == K_SIZE:
                for n in streams:
                    for _ in range(max(0, n - 1)):
                        r.number()
                k = r.byte()
            if k == K_CRC:
                unknown = sum(n for f, n in zip(folders, streams) if not (n == 1 and f["crc"]))
                r.digests(unknown)
                k = r.byte()
            if k != K_END:
                raise Bad("substreams")
        else:
            raise Bad(f"streams info {t:#x}")


def _unpack_header(path: Path, info: dict) -> bytes:
    """An encoded header: its one packed stream, decoded with its one coder (LZMA, LZMA2 or none)."""
    if len(info["folders"]) != 1 or not info["pack_sizes"]:
        raise Bad("encoded header shape")
    folder = info["folders"][0]
    if len(folder["coders"]) != 1:
        raise Bad("encoded header coders")
    coder, size = folder["coders"][0], folder["sizes"][-1]
    if size > MAX_HEADER or info["pack_sizes"][0] > MAX_HEADER:
        raise Bad("header too big")
    with path.open("rb") as f:
        f.seek(32 + info["pack_pos"])
        packed = f.read(info["pack_sizes"][0])
    if coder["id"] == LZMA_ID and len(coder["props"]) == 5:
        d = coder["props"][0]
        filters = [{"id": lzma.FILTER_LZMA1, "lc": d % 9, "lp": (d // 9) % 5, "pb": d // 45,
                    "dict_size": struct.unpack("<I", coder["props"][1:5])[0]}]
    elif coder["id"] == LZMA2_ID and len(coder["props"]) == 1:
        filters = [{"id": lzma.FILTER_LZMA2, "dict_size": 1 << 26}]   # any dictionary as big as the real one decodes
    elif coder["id"] in (b"", b"\x00"):
        return packed[:size]                       # stored ("copy")
    else:
        raise Bad("header coder")
    try:
        out = lzma.LZMADecompressor(lzma.FORMAT_RAW, filters=filters).decompress(packed, max_length=size)
    except lzma.LZMAError as exc:
        raise Bad("header doesn't decode") from exc
    if len(out) < size:
        raise Bad("header short")
    return out


def _file_names(r: _Reader) -> list[str]:
    """A kHeader's file names (kFilesInfo's kName), after walking what comes before them."""
    t = r.byte()
    if t == K_ARCHIVE_PROPERTIES:
        while r.byte() != K_END:
            r.take(r.number())
        t = r.byte()
    if t == K_ADDITIONAL_STREAMS:
        _streams_info(r)
        t = r.byte()
    if t == K_MAIN_STREAMS:
        _streams_info(r)
        t = r.byte()
    if t == K_END:
        return []                                  # an empty archive
    if t != K_FILES_INFO:
        raise Bad("files info")
    count = r.number()
    while True:
        kind = r.number()
        if kind == K_END:
            return []
        data = r.take(r.number())
        if kind == K_NAME:
            if not data or data[0]:
                raise Bad("external names")
            names = data[1:].decode("utf-16-le", errors="replace").split("\0")
            return [n.replace("\\", "/") for n in names[:count]]


def names(path: Path) -> list[str] | None:
    """The file (and folder) names in a .7z, or None if this reader can't tell (see the module's docstring)."""
    try:
        with path.open("rb") as f:
            start = f.read(32)
            if len(start) < 32 or start[:6] != SIGNATURE:
                return None
            offset, size = struct.unpack("<QQ", start[12:28])
            if size > MAX_HEADER:
                return None
            f.seek(32 + offset)
            header = f.read(size)
        if len(header) != size or not header:
            return None
        r = _Reader(header)
        t = r.byte()
        if t == K_ENCODED_HEADER:
            r = _Reader(_unpack_header(path, _streams_info(r)))
            t = r.byte()
        if t != K_HEADER:
            return None
        return _file_names(r)
    except (Bad, OSError, struct.error, UnicodeDecodeError):
        return None
