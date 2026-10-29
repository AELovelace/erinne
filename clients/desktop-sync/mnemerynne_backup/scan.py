"""Walking a backed-up folder: every file that can go to the share, and every one that can't, with why."""

from __future__ import annotations

import fnmatch
import os
import stat
import sys
from dataclasses import dataclass, field

from mnemerynne_backup import names

# Windows: OneDrive and other cloud "online-only" placeholders. Reading one would download it just to back it up.
_CLOUD_ATTRS = 0x1000 | 0x40000 | 0x400000    # OFFLINE, RECALL_ON_OPEN, RECALL_ON_DATA_ACCESS


@dataclass
class Scan:
    files: dict[str, tuple[int, int]] = field(default_factory=dict)   # relative path -> (size, mtime_ns)
    dirs: set[str] = field(default_factory=set)
    skipped: list[tuple[str, str]] = field(default_factory=list)      # (relative path, why)


def excluded(rel: str, patterns: list[str]) -> bool:
    """A pattern matches a name anywhere in the path ("node_modules") or the whole relative path ("Old/*.iso")."""
    parts = rel.split("/")
    return any(fnmatch.fnmatchcase(p.lower(), pat.lower()) for pat in patterns for p in parts) or \
        any(fnmatch.fnmatchcase(rel.lower(), pat.lower()) for pat in patterns)


def walk(base: str, excludes: list[str] | None = None) -> Scan:
    excludes = excludes or []
    out = Scan()
    if not os.path.isdir(base):
        raise FileNotFoundError(base)

    def visit(path: str, rel_dir: str) -> None:
        try:
            entries = sorted(os.scandir(path), key=lambda e: e.name)
        except OSError as exc:
            out.skipped.append((rel_dir or ".", f"can't open the folder: {exc.strerror or exc}"))
            return
        seen: dict[str, str] = {}
        for entry in entries:
            name = entry.name
            rel = f"{rel_dir}/{name}" if rel_dir else name
            if names.is_junk(name) or excluded(rel, excludes):
                continue                                    # not listed: chosen, or never wanted
            try:
                if entry.is_symlink() or (sys.platform == "win32" and entry.is_junction()):
                    out.skipped.append((rel, "a link to somewhere else; links aren't followed"))
                    continue
                st = entry.stat(follow_symlinks=False)
            except OSError as exc:
                out.skipped.append((rel, f"can't read it: {exc.strerror or exc}"))
                continue
            if why := names.problem(name):
                out.skipped.append((rel, why))
                continue
            if name.lower() in seen:
                out.skipped.append((rel, f"differs from “{seen[name.lower()]}” only in capitals; the share can't "
                                         f"hold both"))
                continue
            seen[name.lower()] = name
            if getattr(st, "st_file_attributes", 0) & _CLOUD_ATTRS:
                out.skipped.append((rel, "an online-only cloud file; make it available offline to back it up"))
                continue
            if stat.S_ISDIR(st.st_mode):
                out.dirs.add(rel)
                visit(entry.path, rel)
            elif stat.S_ISREG(st.st_mode):
                out.files[rel] = (st.st_size, st.st_mtime_ns)
            else:
                out.skipped.append((rel, "not an ordinary file"))

    visit(base, "")
    return out
