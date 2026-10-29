"""One backup pass over one folder: what changed since last time, and sending it to the server.

  1. Walk the folder (scan.py); a file whose size and modified time match the state DB is unchanged.
  2. Hash the rest. A file changed in the last `settle` seconds waits (it may still be being written).
  3. A file that vanished in one place and appeared in another with the same hash is a move; when a whole
     folder moved, that is one folder move.
  4. Send moves, then uploads (smallest first, a few at once), then deletes (to the server's recycle bin).
With `check` (every few hours, or when the state DB is new) the server's list is compared too: files changed or
deleted on the NAS by hand are sent again, and a lost state DB is rebuilt without uploading what's already there.
"""

from __future__ import annotations

import os
import posixpath
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path

from mnemerynne_backup import scan as scanning
from mnemerynne_backup.state import Known, State
from mnemerynne_backup.transport import Offline, Precondition, SignedOut, Transport, TransportError, sha256_file

WORKERS = 4


@dataclass
class Report:
    label: str = ""
    uploaded: int = 0
    moved: int = 0
    deleted: int = 0
    unchanged: int = 0
    bytes: int = 0
    waiting: list[str] = field(default_factory=list)              # changed too recently; next pass
    skipped: list[tuple[str, str]] = field(default_factory=list)  # (path, why), not backed up
    errors: list[tuple[str, str]] = field(default_factory=list)   # (path, what went wrong); tried again next pass
    offline: bool = False
    signed_out: bool = False
    finished_at: float = 0.0

    @property
    def ok(self) -> bool:
        return not (self.errors or self.offline or self.signed_out)

    def json(self) -> dict:
        return {k: getattr(self, k) for k in self.__dataclass_fields__} | {"ok": self.ok}


@dataclass
class _Change:
    rel: str
    size: int
    mtime_ns: int
    sha: str
    version: str | None      # the server's version to replace; None for a new file


def _parent(rel: str) -> str:
    return posixpath.dirname(rel)


def _under(rel: str, folder: str) -> bool:
    return rel.startswith(folder + "/")


def find_moves(new: dict[str, str], missing: dict[str, Known], local_dirs: set[str], known: dict[str, Known]
               ) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    """(folder moves, file moves) explaining new files (path -> hash) by missing ones of the same hash."""
    by_sha: dict[str, list[str]] = defaultdict(list)
    for rel, k in missing.items():
        by_sha[k.sha256].append(rel)
    pairs: list[tuple[str, str]] = []
    for rel, sha in sorted(new.items()):
        olds = by_sha.get(sha)
        if olds:
            # Prefer the same name in another folder (a move) over a copy under another name.
            olds.sort(key=lambda o: posixpath.basename(o) != posixpath.basename(rel))
            pairs.append((olds.pop(0), rel))
    # A folder move: every file that was under old/ is now under new/ with the same name below it, old/ is
    # gone here, and nothing was known under new/ before.
    groups: dict[tuple[str, str], list[tuple[str, str]]] = defaultdict(list)
    for old, rel in pairs:
        o, n = old.split("/"), rel.split("/")
        tail = 1        # every level at which the two paths end the same: Photos/2026 -> Pictures/2026, Photos -> ...
        while tail < min(len(o), len(n)) and o[-tail] == n[-tail]:
            od, nd = "/".join(o[:-tail]), "/".join(n[:-tail])
            if od != nd:
                groups[(od, nd)].append((old, rel))
            tail += 1
    folder_moves, used = [], set()
    for (od, nd), members in sorted(groups.items(), key=lambda g: g[0][0].count("/")):
        if od in local_dirs or any(_under(od, f) or od == f for f, _ in folder_moves):
            continue
        under_old = {p for p in known if _under(p, od)}
        if under_old != {o for o, _ in members} or any(_under(p, nd) for p in known):
            continue
        folder_moves.append((od, nd))
        used |= {o for o, _ in members}
    return folder_moves, [(o, n) for o, n in pairs if o not in used]


def _deletions(missing: list[str], base: str) -> list[str]:
    """Missing files, as the highest folders gone from this computer where a whole folder went."""
    out: set[str] = set()
    for rel in missing:
        top, parent = rel, _parent(rel)
        while parent and not os.path.isdir(os.path.join(base, *parent.split("/"))):
            top, parent = parent, _parent(parent)
        out.add(top)
    return sorted(p for p in out if not any(_under(p, q) for q in out))


class Engine:
    def __init__(self, transport: Transport, state: State, *, settle: int = 30, clock=time.time,
                 workers: int = WORKERS):
        self.t, self.state, self.settle, self.clock, self.workers = transport, state, settle, clock, workers

    def run(self, root_id: int, base: str, *, label: str = "", excludes: list[str] | None = None,
            check: bool = False) -> Report:
        report = Report(label=label)
        try:
            self._run(root_id, base, excludes or [], check, report)
        except Offline:
            report.offline = True
        except SignedOut:
            report.signed_out = True
        report.finished_at = self.clock()
        return report

    def _hash(self, base: str, rel: str, size: int, mtime_ns: int, report: Report) -> str | None:
        """The file's hash, or None when it is still changing or can't be read right now."""
        full = os.path.join(base, *rel.split("/"))
        if self.clock() - mtime_ns / 1e9 < self.settle:
            report.waiting.append(rel)
            return None
        try:
            sha = sha256_file(full)
            st = os.stat(full)
        except PermissionError:
            report.skipped.append((rel, "in use by another program; tried again next pass"))
            return None
        except OSError as exc:
            report.errors.append((rel, f"can't read it: {exc.strerror or exc}"))
            return None
        if (st.st_size, st.st_mtime_ns) != (size, mtime_ns):
            report.waiting.append(rel)     # it changed while being read
            return None
        return sha

    def _run(self, root_id: int, base: str, excludes: list[str], check: bool, report: Report) -> None:
        found = scanning.walk(base, excludes)
        report.skipped += found.skipped
        known = self.state.files(root_id)
        server = None
        self._server_folders: list[str] = []
        if check or not known:
            listing = self.t.manifest(root_id)
            server, self._server_folders = listing["files"], listing["folders"]
            known = self._reconcile(root_id, known, server)

        changes: list[_Change] = []
        new: dict[str, str] = {}
        for rel, (size, mtime_ns) in sorted(found.files.items()):
            k = known.get(rel)
            if k and (k.size, k.mtime_ns) == (size, mtime_ns) and k.sha256 == k.version:
                report.unchanged += 1
                continue
            sha = self._hash(base, rel, size, mtime_ns, report)
            if sha is None:
                continue
            if k and k.sha256 == sha == k.version:
                self.state.put(root_id, rel, Known(size, mtime_ns, sha, k.version))   # touched, not changed
                report.unchanged += 1
                continue
            if k is None and server and rel in server and server[rel]["version"] == sha:
                self.state.put(root_id, rel, Known(size, mtime_ns, sha, sha))         # already on the server
                report.unchanged += 1
                continue
            if k is None:
                new[rel] = sha
            changes.append(_Change(rel, size, mtime_ns, sha, k.version if k else
                                   (server[rel]["version"] if server and rel in server else None)))

        missing = {rel: k for rel, k in known.items() if rel not in found.files}
        # A file that's still there but couldn't be read (in use, too new) isn't missing.
        held = set(report.waiting) | {p for p, _ in report.skipped}
        missing = {rel: k for rel, k in missing.items() if rel not in held and not
                   any(_under(rel, p) for p, why in found.skipped)}

        folder_moves, file_moves = find_moves(new, missing, found.dirs, known)
        self._moves(root_id, folder_moves, file_moves, changes, missing, report)
        self._uploads(root_id, base, changes, report)
        targets = _deletions(list(missing), base) if missing else []
        if server is not None:
            # Folders left on the NAS that are empty and gone from here (a file moved out of them, say).
            after = self.t.manifest(root_id) if (missing or report.moved or changes) else {"folders": None}
            listed = after["folders"] if after["folders"] is not None else self._server_folders
            files = after.get("files") or server
            unreadable = {p for p, _ in found.skipped}
            empty = [d for d in listed if d not in found.dirs and d not in unreadable
                     and not any(_under(f, d) for f in files) and not any(_under(d, u) for u in unreadable)]
            targets = sorted(set(targets) | {d for d in empty if not any(_under(d, e) for e in empty)})
        if targets:
            for result in self.t.delete(root_id, targets):
                if result["ok"]:
                    self.state.drop(root_id, result["path"])
                    report.deleted += 1 if not result.get("gone") else 0
                else:
                    report.errors.append((result["path"], result.get("error") or "couldn't delete it"))

    def _reconcile(self, root_id: int, known: dict[str, Known], server: dict[str, dict]) -> dict[str, Known]:
        """Bring the state DB in line with the server's list: files gone there are sent again as new, files
        changed there are sent again over the server's version."""
        for rel, k in list(known.items()):
            entry = server.get(rel)
            if entry is None:
                self.state.drop(root_id, rel)
                del known[rel]
            elif entry["version"] != k.version:
                known[rel] = Known(k.size, k.mtime_ns, k.sha256, entry["version"])
                self.state.put(root_id, rel, known[rel])
        return known

    def _moves(self, root_id, folder_moves, file_moves, changes, missing, report) -> None:
        moves = folder_moves + file_moves
        if not moves:
            return
        results = self.t.move(root_id, moves)
        new_paths = set()
        for (old, rel), result in zip(moves, results):
            if not result["ok"]:
                continue        # sent as a new file and deleted at its old place instead
            self.state.rename(root_id, old, rel)
            report.moved += 1
            for p in list(missing):
                if p == old or _under(p, old):
                    del missing[p]
            new_paths.add(rel)
        if new_paths:
            # A moved file needs no upload, unless it changed too.
            moved = self.state.files(root_id)
            keep = []
            for c in changes:
                k = moved.get(c.rel)
                if any(c.rel == p or _under(c.rel, p) for p in new_paths) and k and k.sha256 == c.sha:
                    self.state.put(root_id, c.rel, Known(c.size, c.mtime_ns, c.sha, k.version))
                else:
                    keep.append(c)
            changes[:] = keep

    def _uploads(self, root_id: int, base: str, changes: list[_Change], report: Report) -> None:
        def send(c: _Change) -> str:
            full = Path(base, *c.rel.split("/"))
            try:
                return self.t.put_file(root_id, c.rel, full, c.sha, c.size, version=c.version, mtime_ns=c.mtime_ns,
                                       state=self.state)
            except Precondition as exc:
                if exc.current == c.sha:
                    return c.sha                       # the server already has exactly this
                # One way: this computer's copy wins over a change made on the NAS.
                return self.t.put_file(root_id, c.rel, full, c.sha, c.size, version=exc.current,
                                       mtime_ns=c.mtime_ns, state=self.state)

        changes.sort(key=lambda c: c.size)
        with ThreadPoolExecutor(self.workers) as pool:
            futures = {pool.submit(send, c): c for c in changes}
            for future in as_completed(futures):
                c = futures[future]
                try:
                    version = future.result()
                except (Offline, SignedOut):
                    for f in futures:
                        f.cancel()
                    raise
                except (TransportError, OSError) as exc:
                    report.errors.append((c.rel, str(exc)))
                    continue
                self.state.put(root_id, c.rel, Known(c.size, c.mtime_ns, c.sha, version))
                report.uploaded += 1
                report.bytes += c.size
