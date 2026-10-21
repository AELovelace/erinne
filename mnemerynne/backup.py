"""Backups: a consistent database snapshot per day plus incremental blob copies.

    mnemerynne backup --dest /var/backups/mnemerynne --keep 14

Layout:
    <dest>/db/mnemerynne-YYYY-MM-DD.db   one snapshot per day (SQLite backup API,
                                     safe while the app is running)
    <dest>/blobs/ab/<sha>.pdf        uploaded files; content-addressed, never
                                     modified, so only new ones are copied
Restore: stop mnemerynne, copy a snapshot to <data_dir>/mnemerynne.db and the blobs
directory to <data_dir>/blobs, start mnemerynne.
"""

from __future__ import annotations

import shutil
import sqlite3
from datetime import date
from pathlib import Path

from mnemerynne.config import Settings


def backup(settings: Settings, dest: Path, keep: int = 14, today: date | None = None) -> dict:
    today = today or date.today()
    db_dir = dest / "db"
    db_dir.mkdir(parents=True, exist_ok=True)
    snapshot = db_dir / f"mnemerynne-{today.isoformat()}.db"
    tmp = snapshot.with_suffix(".tmp")
    src = sqlite3.connect(settings.db_path)
    dst = sqlite3.connect(tmp)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    tmp.replace(snapshot)

    copied = 0
    if settings.blob_dir.exists():
        for path in settings.blob_dir.rglob("*"):
            if path.is_file():
                target = dest / "blobs" / path.relative_to(settings.blob_dir)
                if not target.exists() or target.stat().st_size != path.stat().st_size:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(path, target)
                    copied += 1

    snapshots = sorted(db_dir.glob("mnemerynne-*.db"))
    removed = []
    for old in snapshots[:-keep] if keep > 0 else []:
        old.unlink()
        removed.append(old.name)
    return {"snapshot": str(snapshot), "blobs_copied": copied, "pruned": removed}
