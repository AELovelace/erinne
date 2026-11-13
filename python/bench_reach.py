# Benchmark: how long the permission check behind every Files request takes with a big share library.
# Builds a share library with FOLDERS folders (the box's biggest has 28,789), then times permissions.reach
# (run on every Files/Media request) and prints the slowest functions. Run from the repo root:
#   .venv\Scripts\python python\bench_reach.py [folders]
import cProfile
import pstats
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "tests")]          # the app, and the tests' fake share and directory

from fakes import FakeDirectory, FakeShareChecker       # noqa: E402
from mnemerynne import accounts, db, permissions, share, users   # noqa: E402
from mnemerynne.config import Settings                   # noqa: E402

FOLDERS = int(sys.argv[1]) if len(sys.argv) > 1 else 28_789

tmp = Path(tempfile.mkdtemp())
root = tmp / "share"
(root / "Docs").mkdir(parents=True)
(root / share.MARKER).write_text("")
(root / "Docs" / "a.md").write_text("hello")              # one file, so the scan makes the library
settings = Settings(_env_file=None, data_dir=tmp / "data", secret_key="bench-secret-key-for-local-runs-only",
                    embed_dim=4, ad_domain="ETERNALHOTELS", share_root=root, share_settle_seconds=0)
rt = share.configure(settings, FakeDirectory({"jsmith": {"Domain Users"}}), FakeShareChecker(root))
conn = db.connect(settings.db_path)
db.migrate(conn, settings.embed_dim)
uid = users.create_user(conn, "jane", "password123")
accounts.link(conn, uid, "jsmith", rt.directory)
share.sync(conn, settings)                               # makes the "Docs" library from the share

cid = conn.execute("SELECT id FROM collections WHERE share_path IS NOT NULL").fetchone()[0]
with db.transaction(conn):                               # a deep-ish tree: 100 top folders, the rest spread below
    tops = [conn.execute("INSERT INTO folders(collection_id, parent_id, name) VALUES (?, NULL, ?)",
                         (cid, f"top{i}")).lastrowid for i in range(100)]
    for n in range(FOLDERS - 100):
        conn.execute("INSERT INTO folders(collection_id, parent_id, name) VALUES (?, ?, ?)",
                     (cid, tops[n % 100], f"f{n}"))
print(f"library {cid}: {conn.execute('SELECT count(*) FROM folders WHERE collection_id = ?', (cid,)).fetchone()[0]:,} folders")


def timed(label, fn, runs=3):
    best = None
    for _ in range(runs):
        began = time.perf_counter()
        fn()
        took = time.perf_counter() - began
        best = took if best is None else min(best, took)
    print(f"{label:<40} {best * 1000:8.0f} ms (best of {runs})")


timed("reach, first (walks the share)", lambda: permissions.reach(conn, uid), runs=1)
timed("reach, warm (share rights cached)", lambda: permissions.reach(conn, uid))

profile = cProfile.Profile()
profile.enable()
permissions.reach(conn, uid)
profile.disable()
print("\n--- where a warm reach spends its time")
pstats.Stats(profile).sort_stats("cumulative").print_stats(14)
