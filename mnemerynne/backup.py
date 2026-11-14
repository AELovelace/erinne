"""Backups: a consistent database snapshot per day plus incremental blob copies.

    mnemerynne backup --dest /var/backups/mnemerynne --keep 14
    mnemerynne restore --from /var/backups/mnemerynne [--date YYYY-MM-DD] [--force]

Layout:
    <dest>/db/mnemerynne-YYYY-MM-DD.db   one snapshot per day (SQLite backup API,
                                     safe while the app is running)
    <dest>/db/<feature>-YYYY-MM-DD.db    the same day's snapshot of each feature's own
                                     database file (games.db, rooms.db, ...: db.FEATURE_DBS),
                                     taken right after core's. No transaction spans two files,
                                     so each snapshot is whole on its own.
    <dest>/config/YYYY-MM-DD/        that day's settings file (it holds the secret
                                     key that API credentials and Microsoft 365
                                     tokens are encrypted with), router.json,
                                     synonyms.txt and mnemerynne.mcpb
    <dest>/blobs/ab/<sha>.pdf        uploaded files; content-addressed, never
                                     modified, so only new ones are copied
    <dest>/versions/<file>/...       earlier versions of files Claude changed;
                                     also never modified, copied the same way

Everything is written owner-only (0700 folders, 0600 files): a backup holds every
document and the key to every stored credential. Keep a copy of the secret key
off the box as well: a snapshot whose key is lost restores without them.

Files on the SMB share are not in here; they are backed up with the NAS.
"""

from __future__ import annotations

import logging
import os
import shutil
import sqlite3
import subprocess
from datetime import date, datetime
from pathlib import Path

from mnemerynne import db as db_mod
from mnemerynne.config import Settings

log = logging.getLogger("mnemerynne.backup")

# Kept beside each snapshot when present in data_dir.
DATA_FILES = ("router.json", "synonyms.txt", "mnemerynne.mcpb")
ENV_NAME = "mnemerynne.env"
SERVICES = ("mnemerynne-web", "mnemerynne-worker")   # plus each service's unit (running_services)


def _snapshot(source: Path, target: Path) -> None:
    """A consistent copy of a live database (the SQLite backup API), owner-only, written whole or not at all."""
    tmp = target.with_suffix(".tmp")
    src = sqlite3.connect(source)
    dst = sqlite3.connect(tmp)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    _private_file(tmp)
    tmp.replace(target)


def _feature_names() -> list[str]:
    return list(db_mod.FEATURE_DBS)


class RestoreError(Exception):
    """A restore that was refused or failed; the message says what to do."""


def _private_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    if os.name == "posix":
        path.chmod(0o700)


def _private_file(path: Path) -> None:
    if os.name == "posix":
        path.chmod(0o600)


def _copy_tree(source: Path, target: Path, *, private: bool) -> int:
    """Copy files that are missing or differ in size; nothing is deleted (blobs never change)."""
    copied = 0
    if not source.exists():
        return 0
    for path in source.rglob("*"):
        if path.is_file():
            out = target / path.relative_to(source)
            if not out.exists() or out.stat().st_size != path.stat().st_size:
                if private:
                    for parent in reversed(out.relative_to(target).parents):
                        _private_dir(target / parent)
                else:
                    out.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, out)
                if private:
                    _private_file(out)
                copied += 1
    return copied


class BackupError(Exception):
    """A backup that was refused; the message says what to change."""


def _inside(path: Path, folder: Path) -> bool:
    return path.resolve().is_relative_to(folder.resolve())


def backup(settings: Settings, dest: Path, keep: int = 14, today: date | None = None) -> dict:
    today = today or date.today()
    if settings.share_root is not None and _inside(dest, settings.share_root):
        raise BackupError(f"{dest} is inside the SMB share ({settings.share_root}), where people could read "
                          f"the backed-up secret key. Set MNEMERYNNE_BACKUP_DIR to a folder outside it.")
    _private_dir(dest)
    db_dir = dest / "db"
    _private_dir(db_dir)
    snapshot = db_dir / f"mnemerynne-{today.isoformat()}.db"
    _snapshot(settings.db_path, snapshot)
    features = []
    for name in _feature_names():                       # each feature's own file, right after core
        path = db_mod.feature_path(settings.db_path, name)
        if path.exists():
            _snapshot(path, db_dir / f"{name}-{today.isoformat()}.db")
            features.append(name)

    config_dir = dest / "config" / today.isoformat()
    _private_dir(dest / "config")
    _private_dir(config_dir)
    kept = []
    warnings = []
    env = settings.backup_env_file
    if env is not None and str(env) not in ("", "."):
        try:
            shutil.copyfile(env, config_dir / ENV_NAME)
            _private_file(config_dir / ENV_NAME)
            kept.append(ENV_NAME)
        except OSError as exc:
            message = (f"Settings file {env} not backed up ({exc.strerror or exc}). Without its "
                       f"MNEMERYNNE_SECRET_KEY, stored API credentials and Microsoft 365 links can't be restored.")
            log.warning(message)
            warnings.append(message)
    for name in DATA_FILES:
        path = settings.data_dir / name
        if path.is_file():
            shutil.copy2(path, config_dir / name)
            _private_file(config_dir / name)
            kept.append(name)

    copied = 0
    for source, name in ((settings.blob_dir, "blobs"), (settings.data_dir / "versions", "versions")):
        _private_dir(dest / name)
        copied += _copy_tree(source, dest / name, private=True)

    snapshots = sorted(db_dir.glob("mnemerynne-*.db"))
    removed = []
    for old in snapshots[:-keep] if keep > 0 else []:
        day = old.stem.removeprefix("mnemerynne-")
        old.unlink()
        for name in _feature_names():                   # that day's feature snapshots go with it
            (db_dir / f"{name}-{day}.db").unlink(missing_ok=True)
        shutil.rmtree(dest / "config" / day, ignore_errors=True)
        removed.append(old.name)
    return {"snapshot": str(snapshot), "features": features, "blobs_copied": copied, "pruned": removed,
            "config": kept, "warnings": warnings}


# ------------------------------------------------------------------- restore
def snapshots(source: Path) -> list[str]:
    """Dates (YYYY-MM-DD) with a database snapshot, oldest first."""
    return sorted(p.stem.removeprefix("mnemerynne-") for p in (source / "db").glob("mnemerynne-*.db"))


def running_services() -> list[str]:
    """The Mnemerynne services systemd reports as running (none where there's no systemctl)."""
    if shutil.which("systemctl") is None:
        return []
    from mnemerynne.services import registry          # the services' own units (mnemerynne-sync, ...)

    running = []
    for unit in (*SERVICES, *(f"mnemerynne-{name}" for name in registry.SERVICES)):
        try:
            out = subprocess.run(["systemctl", "is-active", unit], capture_output=True, text=True, timeout=10)
        except (OSError, subprocess.TimeoutExpired):
            continue
        if out.stdout.strip() in ("active", "activating", "reloading"):
            running.append(unit)
    try:                                              # and the template's instances (mnemerynne@rooms, ...)
        out = subprocess.run(["systemctl", "list-units", "--type=service", "--state=active", "--no-legend", "--plain",
                              "mnemerynne@*.service"], capture_output=True, text=True, timeout=10)
        running += [line.split()[0] for line in out.stdout.splitlines() if line.strip()]
    except (OSError, subprocess.TimeoutExpired):
        pass
    return running


def _integrity(path: Path) -> str:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        return conn.execute("PRAGMA integrity_check").fetchone()[0]
    except sqlite3.DatabaseError as exc:  # not a database at all
        return str(exc)
    finally:
        conn.close()


def env_secret_key(path: Path) -> str | None:
    """MNEMERYNNE_SECRET_KEY from a settings file, or None."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    for line in lines:
        line = line.strip()
        if line.startswith("export "):
            line = line[7:].lstrip()
        if line.startswith("MNEMERYNNE_SECRET_KEY="):
            value = line.split("=", 1)[1].strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
                value = value[1:-1]
            return value
    return None


def restore(settings: Settings, source: Path, when: str | None = None, *, force: bool = False,
            running=None) -> dict:
    """Put a backup back into settings.data_dir. Never touches the settings file in /etc: the
    result says where the backed-up copy is and whether its secret key matches the current one."""
    dates = snapshots(source)
    if not dates:
        raise RestoreError(f"No database snapshots in {source / 'db'}.")
    when = when or dates[-1]
    if when not in dates:
        raise RestoreError(f"No snapshot for {when}. Available: {', '.join(dates)}.")
    up = (running or running_services)()
    if up:
        raise RestoreError(f"Stop {' and '.join(up)} first: sudo systemctl stop {' '.join(up)}")
    snapshot = source / "db" / f"mnemerynne-{when}.db"
    # Every snapshot of that day is checked before anything here is touched: core's and each feature's.
    feature_snaps = {name: source / "db" / f"{name}-{when}.db" for name in _feature_names()
                     if (source / "db" / f"{name}-{when}.db").is_file()}
    for label, path in (("core", snapshot), *feature_snaps.items()):
        check = _integrity(path)
        if check != "ok":
            raise RestoreError(f"The {label} snapshot for {when} is damaged ({check}); pick another date.")

    data = settings.data_dir
    db_path = settings.db_path
    moved_aside = None
    targets = [Path(db_path)] + [db_mod.feature_path(db_path, name) for name in _feature_names()]
    existing = [p for target in targets
                for p in (target, Path(f"{target}-wal"), Path(f"{target}-shm")) if p.exists()]
    if existing:
        if not force:
            raise RestoreError(f"{db_path} already exists. Add --force to replace it (the current "
                               f"database is moved aside, not deleted).")
        moved_aside = data / f"pre-restore-{datetime.now():%Y%m%d-%H%M%S}"
        moved_aside.mkdir(parents=True)
        # -wal/-shm belong to the old database: never pair them with the snapshot. A feature file with no
        # snapshot that day goes aside too: it would be newer than the core put back (the web app's
        # migrate_all then takes its tables out of that core again).
        for path in existing:
            path.replace(moved_aside / path.name)
    data.mkdir(parents=True, exist_ok=True)
    tmp = data / "mnemerynne.db.restoring"
    shutil.copyfile(snapshot, tmp)
    tmp.replace(db_path)
    for name, path in feature_snaps.items():
        target = db_mod.feature_path(db_path, name)
        tmp = data / f"{name}.db.restoring"
        shutil.copyfile(path, tmp)
        tmp.replace(target)

    copied = 0
    for name, target in (("blobs", settings.blob_dir), ("versions", data / "versions")):
        copied += _copy_tree(source / name, target, private=False)
    config_dir = source / "config" / when
    restored_files = []
    for name in DATA_FILES:
        if (config_dir / name).is_file():
            shutil.copy2(config_dir / name, data / name)
            restored_files.append(name)

    check = _integrity(db_path)
    if check != "ok":
        raise RestoreError(f"The restored database fails its integrity check ({check}).")
    conn = sqlite3.connect(db_path)
    try:
        # Files on the share keep their bytes there, not in blobs/.
        rows = conn.execute("SELECT DISTINCT b.rel_path FROM blobs b JOIN files f ON f.blob_sha256 = b.sha256 "
                            "JOIN collections c ON c.id = f.collection_id WHERE c.share_path IS NULL")
        missing = sum(1 for (rel,) in rows if rel and not (settings.blob_dir / rel).exists())
    finally:
        conn.close()

    env_copy = config_dir / ENV_NAME
    backed_key = env_secret_key(env_copy) if env_copy.is_file() else None
    if backed_key is None:
        key = "unknown"  # no settings file in this backup
    elif backed_key == settings.secret_key:
        key = "matches"
    else:
        key = "differs"
    return {"date": when, "database": str(db_path), "features": sorted(feature_snaps), "blobs_copied": copied,
            "missing_blobs": missing,
            "files": restored_files, "moved_aside": str(moved_aside) if moved_aside else None,
            "env_backup": str(env_copy) if env_copy.is_file() else None, "secret_key": key}
