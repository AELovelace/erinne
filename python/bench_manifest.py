# Benchmark: the backup app's manifest (sync.manifest), page by page, for a backed-up folder like the box's root 5
# (4,198 folders). Times every page of one full manifest and the slowest page. Run from the repo root:
#   .venv\Scripts\python python\bench_manifest.py [files_in_root] [other_files]
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from mnemerynne import db, permissions, sync, users      # noqa: E402

IN_ROOT = int(sys.argv[1]) if len(sys.argv) > 1 else 60_000      # files under the backed-up folder
OTHERS = int(sys.argv[2]) if len(sys.argv) > 2 else 100_000      # files elsewhere (other people, other roots)
FOLDERS = 4_198

conn = db.connect(Path(tempfile.mkdtemp()) / "bench.db")
db.migrate(conn, 4)
uid = users.create_user(conn, "jane", "password123")
cid = permissions.private_collection_id(conn, uid)


def required(table):
    """The table's NOT NULL columns without a default: what an INSERT must give."""
    return [r[1] for r in conn.execute(f"PRAGMA table_info({table})") if r[3] and r[4] is None and not r[5]]


print("blobs needs:", required("blobs"), "| files needs:", required("files"))
with db.transaction(conn):
    top = conn.execute("INSERT INTO folders(collection_id, parent_id, name) VALUES (?, NULL, 'Backup')",
                       (cid,)).lastrowid
    other = conn.execute("INSERT INTO folders(collection_id, parent_id, name) VALUES (?, NULL, 'Elsewhere')",
                         (cid,)).lastrowid
    level = [top]
    made = [top]
    while len(made) < FOLDERS:                            # a bushy tree under the backed-up folder
        nxt = []
        for parent in level:
            for i in range(8):
                if len(made) >= FOLDERS:
                    break
                fid = conn.execute("INSERT INTO folders(collection_id, parent_id, name) VALUES (?, ?, ?)",
                                   (cid, parent, f"d{len(made)}")).lastrowid
                made.append(fid)
                nxt.append(fid)
        level = nxt
    total = IN_ROOT + OTHERS
    for n in range(total):                                # interleaved: root files and others, as ids are handed out
        sha = f"{n:064x}"
        conn.execute("INSERT INTO blobs(sha256, kind, size, rel_path) VALUES (?, 'md', ?, ?)", (sha, 100 + n, f"b/{n}"))
        folder = made[n % len(made)] if n % total < IN_ROOT and n % 5 < 5 * IN_ROOT / total else other
        conn.execute("INSERT INTO files(collection_id, folder_id, blob_sha256, display_name) VALUES (?, ?, ?, ?)",
                     (cid, folder, sha, f"file{n}.md"))
in_root = conn.execute(f"WITH RECURSIVE {sync._TREE} SELECT count(*) FROM files WHERE folder_id IN (SELECT id FROM t)",
                       (top,)).fetchone()[0]
print(f"{FOLDERS:,} folders under the root, {in_root:,} files in it, {total:,} files in all")

root = sync.Root(1, uid, "PC", "Backup", cid, top)
after, pages, slowest, began = 0, 0, 0.0, time.perf_counter()
while True:
    t = time.perf_counter()
    page = sync.manifest(conn, root, after)
    slowest = max(slowest, time.perf_counter() - t)
    pages += 1
    if page["next"] is None:
        break
    after = page["next"]
print(f"whole manifest: {pages} pages in {time.perf_counter() - began:.1f} s, slowest page {slowest * 1000:.0f} ms")
