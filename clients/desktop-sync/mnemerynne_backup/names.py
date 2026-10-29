"""Which names can go to the share, by the same rules as the server (mnemerynne/share.py, mnemerynne/sync.py).

Anything else is skipped and listed with its reason, never renamed. tests/test_backup_client.py checks these
rules against the server's.
"""

from __future__ import annotations

import re
import unicodedata

BAD_CHARS = set('\\/:*?"<>|')
RESERVED = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}
JUNK_NAMES = {"thumbs.db", "desktop.ini", ".ds_store"}
JUNK_SUFFIXES = (".tmp", ".crdownload", ".part")


def is_junk(name: str) -> bool:
    """Hidden, Office lock files, Explorer and macOS litter, partial downloads: never backed up."""
    low = name.lower()
    return name.startswith((".", "~$")) or low in JUNK_NAMES or low.endswith(JUNK_SUFFIXES)


def _cleaned(name: str) -> str:
    out = "".join(ch for ch in unicodedata.normalize("NFC", name) if unicodedata.category(ch)[0] != "C")
    return re.sub(r"\s+", " ", out).strip()[:200]


def problem(name: str) -> str | None:
    """Why this name can't be stored on the share as it is, or None when it can."""
    bad = sorted(set(name) & BAD_CHARS)
    if bad:
        return f"names on the share can't contain {' '.join(bad)}"
    if name.endswith((".", " ")):
        return "names on the share can't end with a dot or a space"
    if name.split(".")[0].strip().lower() in RESERVED:
        return f"“{name}” is a reserved name in Windows"
    if _cleaned(name) != name:
        return "control characters, extra spaces, over 200 characters or not NFC"
    return None
