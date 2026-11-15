"""Files' big uploads in parts, so a dropped connection or a restart loses one part, never the whole file.

A browser upload of a big file used to be one request that ran for as long as the file took to send (often most of an
hour from outside the house). Any blip on the way (the laptop sleeping, Wi-Fi changing, the server restarting for an
update) failed it, and the upload started again from nothing. Here the browser sends PART_BYTES at a time:

1. start(): where it will land (creating a folder upload's folders), and which parts are already here. The upload's
   id comes from who is uploading, where to, and the file's name, size and modified time, so choosing the same file
   again (after a reload, say) finds the upload and carries on.
2. put_part(): one part, written straight into its place in the file. A part that arrives again simply overwrites
   itself, so retrying is always safe.
3. finish(): every part is here. The file is hashed and checked like any upload (ingest/store.py) and added with
   store.add_file, which gives it its real name.

Where the bytes go: on the share, a hidden file in the folder it will land in (share.STAGE_PREFIX), so it is created
with that folder's permissions and finishing is only a rename, however big it is. Elsewhere, data/tmp.

Which parts arrived is kept as one empty file per part beside the upload's meta.json (data/tmp/file-uploads/<id>/),
not in a table: Files runs as several processes, and a new file per part needs no lock and no migration. Uploads
nobody finished are cleared after UPLOAD_DAYS (sweep(), the worker's hourly round; the share's scan clears its hidden
files after a day untouched too).
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import time
from pathlib import Path

from mnemerynne import share
from mnemerynne.config import Settings
from mnemerynne.ingest import store

PART_BYTES = 16 * 1024 * 1024     # a lost part costs at most this much sending again
UPLOAD_DAYS = 1                   # an upload untouched this long is dropped (sweep)
FINISH_HOLD = 2 * 3600            # a finish this old that never ended was cut off (a restart): another may take over
_ID_LEN = 32                      # hex characters in an upload id


class PartsError(Exception):
    """A refusal with the HTTP status to answer and a message safe to show."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def _base(settings: Settings) -> Path:
    return settings.tmp_dir / "file-uploads"            # every upload's meta.json and part markers


def upload_id(user_id: int, collection_id: int, folder_id: int | None, relative_path: str | None, name: str,
              size: int, modified: int) -> str:
    """The same file to the same place by the same person always gets the same id: that's how it resumes."""
    key = json.dumps([user_id, collection_id, folder_id, relative_path or "", name, size, modified])
    return hashlib.sha256(key.encode()).hexdigest()[:_ID_LEN]


def _dir(settings: Settings, uid: str) -> Path:
    if len(uid) != _ID_LEN or any(c not in "0123456789abcdef" for c in uid):
        raise PartsError("That isn't an upload.", 404)  # never a path: only hex reaches the disk
    return _base(settings) / uid


def record(settings: Settings, uid: str, user_id: int) -> dict:
    """The upload's record, refusing anyone but the person who started it."""
    try:
        meta = json.loads((_dir(settings, uid) / "meta.json").read_text())
    except (OSError, ValueError):
        raise PartsError("That upload isn't here any more; start it again.", 404) from None
    if meta.get("user_id") != user_id:
        raise PartsError("That upload isn't here any more; start it again.", 404)
    return meta


def _write_meta(folder: Path, meta: dict) -> None:
    tmp = folder / f"meta.json.{os.getpid()}"            # written whole, then swapped in: never half a record
    tmp.write_text(json.dumps(meta))
    os.replace(tmp, folder / "meta.json")


def _count(size: int) -> int:
    return -(-size // PART_BYTES)                        # how many parts a file of `size` bytes has


def part_length(size: int, n: int) -> int:
    """How long part n is (0: no such part)."""
    return max(0, min(PART_BYTES, size - n * PART_BYTES)) if n >= 0 else 0


def _parts(folder: Path) -> list[int]:
    marks = folder / "parts"
    return sorted(int(p.name) for p in marks.iterdir() if p.name.isdigit()) if marks.is_dir() else []


def data_path(conn: sqlite3.Connection, settings: Settings, meta: dict) -> Path:
    """Where the upload's bytes are now. On the share it follows its folder, which may be renamed meanwhile."""
    if meta.get("stage"):
        try:
            return share.folder_dir(conn, meta["collection_id"], meta["folder_id"]) / meta["stage"]
        except Exception:                                 # the folder went (deleted, or moved off the share)
            raise PartsError("The folder this was going into isn't there any more.", 410) from None
    return _dir(settings, meta["id"]) / "data"


def _status(settings: Settings, meta: dict) -> dict:
    folder = _dir(settings, meta["id"])
    return {"id": meta["id"], "size": meta["size"], "part_size": PART_BYTES, "parts": _parts(folder),
            "file_id": meta.get("file_id")}


def start(conn: sqlite3.Connection, settings: Settings, user_id: int, collection_id: int, folder_id: int | None,
          relative_path: str | None, name: str, size: int, modified: int, landed: int | None,
          into: Path | None) -> dict:
    """Start the upload, or find it again. landed/into: store.landing()'s answer (its folder; the share folder or
    None). Answers its id, the part size and the parts already here (file_id once it has been finished)."""
    if size <= 0:
        raise PartsError("The file is empty.")
    if size > settings.max_file_bytes:
        raise PartsError(f"File is larger than the {settings.max_file_bytes // (1024 * 1024)} MB upload limit.", 413)
    uid = upload_id(user_id, collection_id, folder_id, relative_path, name, size, modified)
    folder = _dir(settings, uid)
    try:
        meta = record(settings, uid, user_id)
    except PartsError:
        meta = None
    if meta is not None:
        if meta.get("file_id"):
            return _status(settings, meta)                # already finished: the browser shows the file
        try:
            if data_path(conn, settings, meta).exists():
                return _status(settings, meta)            # carry on from the parts already here
        except PartsError:
            pass                                          # its folder went: start over below
        shutil.rmtree(folder, ignore_errors=True)         # its bytes were cleared (a day untouched): start over
    if into is not None:                                  # room for it, before a single byte is sent
        free = shutil.disk_usage(into if into.exists() else into.parent).free
    else:
        settings.tmp_dir.mkdir(parents=True, exist_ok=True)
        free = shutil.disk_usage(settings.tmp_dir).free
    if free < size:
        raise PartsError(f"There isn't room for this file: {free // 1024 ** 3} GB free, it needs "
                         f"{-(-size // 1024 ** 3)} GB.", 507)
    (folder / "parts").mkdir(parents=True, exist_ok=True)
    meta = {"id": uid, "user_id": user_id, "collection_id": collection_id, "folder_id": landed, "name": name,
            "size": size, "stage": f"{share.STAGE_PREFIX}{uid[:16]}" if into is not None else None,
            "started": time.time()}
    data = data_path(conn, settings, meta)
    try:
        data.parent.mkdir(parents=True, exist_ok=True)
        with open(data, "ab") as fh:                      # created in its folder: the folder's permissions and label
            fh.truncate(size)                             # sparse: parts land at their place in any order
    except OSError as exc:
        shutil.rmtree(folder, ignore_errors=True)
        raise PartsError(f"Couldn't start saving the file: {exc.strerror or exc}", 503) from exc
    _write_meta(folder, meta)
    return _status(settings, meta)


def put_part(conn: sqlite3.Connection, settings: Settings, user_id: int, uid: str, n: int, data: bytes) -> dict:
    """Write part n in its place, then mark it arrived (after it is on the disk, so a crash never marks it early)."""
    meta = record(settings, uid, user_id)
    if meta.get("file_id"):
        return _status(settings, meta)                    # finished already: a late retry changes nothing
    length = part_length(meta["size"], n)
    if length == 0:
        raise PartsError(f"Part {n} isn't part of this file.")
    if len(data) != length:
        raise PartsError(f"Part {n} should be {length} bytes, not {len(data)}.")
    path = data_path(conn, settings, meta)
    try:
        with open(path, "r+b") as out:                    # r+: the other parts stay as they are
            out.seek(n * PART_BYTES)
            out.write(data)
            out.flush()
            os.fsync(out.fileno())
    except FileNotFoundError:
        raise PartsError("That upload's file isn't here any more; start it again.", 404) from None
    except OSError as exc:
        raise PartsError(f"Couldn't save that part: {exc.strerror or exc}", 503) from exc
    (_dir(settings, uid) / "parts" / str(n)).touch()      # arrived: a file per part, no lock between processes
    return _status(settings, meta)


def finish(conn: sqlite3.Connection, settings: Settings, user_id: int, uid: str) -> tuple[int, bool]:
    """Every part is here: check the file as any upload is checked and add it. Returns (file_id, created).
    Asked again after it worked (the answer was lost), it answers the same file."""
    meta = record(settings, uid, user_id)
    folder = _dir(settings, uid)
    if meta.get("file_id"):
        return meta["file_id"], False
    missing = sorted(set(range(_count(meta["size"]))) - set(_parts(folder)))
    if missing:
        raise PartsError(f"Parts {', '.join(map(str, missing[:10]))} haven't arrived yet.")
    hold = folder / "finishing"
    try:
        fd = os.open(hold, os.O_CREAT | os.O_EXCL | os.O_WRONLY)   # one finish at a time, across processes
        os.close(fd)
    except FileExistsError:
        if time.time() - hold.stat().st_mtime < FINISH_HOLD:
            raise PartsError("It's still being put together; ask again in a moment.", 409) from None
        hold.touch()                                       # an old finish cut off by a restart: take it over
    path = data_path(conn, settings, meta)
    try:
        received = store.received_from(path, meta["name"], settings, staged=bool(meta.get("stage")))
        file_id, created = store.add_file(conn, settings, received, meta["collection_id"], user_id,
                                          folder_id=meta["folder_id"])
    except BaseException:
        path.unlink(missing_ok=True)                       # refused: nothing is left half-way; it starts again
        shutil.rmtree(folder, ignore_errors=True)
        raise
    meta["file_id"] = file_id                              # kept a day, so a lost answer can be asked again
    _write_meta(folder, meta)
    shutil.rmtree(folder / "parts", ignore_errors=True)
    hold.unlink(missing_ok=True)
    return file_id, created


def drop(conn: sqlite3.Connection, settings: Settings, user_id: int, uid: str) -> None:
    """Cancel: the bytes and the record go."""
    meta = record(settings, uid, user_id)
    if not meta.get("file_id"):
        try:
            data_path(conn, settings, meta).unlink(missing_ok=True)
        except PartsError:
            pass                                           # its folder went, and the bytes with it
    shutil.rmtree(_dir(settings, uid), ignore_errors=True)


def sweep(conn: sqlite3.Connection, settings: Settings) -> int:
    """Uploads untouched for UPLOAD_DAYS (the worker, hourly): their bytes and records go. Returns how many."""
    base = _base(settings)
    if not base.is_dir():
        return 0
    cutoff = time.time() - UPLOAD_DAYS * 86400
    dropped = 0
    for folder in base.iterdir():
        try:
            touched = max(p.stat().st_mtime for p in [folder, *folder.rglob("*")])   # the newest part counts
        except (OSError, ValueError):
            continue
        if touched > cutoff:
            continue
        try:
            meta = json.loads((folder / "meta.json").read_text())
            if not meta.get("file_id"):
                data_path(conn, settings, meta).unlink(missing_ok=True)
        except Exception:                                  # no record, or its folder went: just the record
            pass
        shutil.rmtree(folder, ignore_errors=True)
        dropped += 1
    return dropped
