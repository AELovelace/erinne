# Benchmark: the Files page's own requests against a share library as big as the box's (28,789 folders).
# Times each request once the permission walk is done, so what's left is the request's own work, then profiles
# the slowest. Run from the repo root:   .venv\Scripts\python python\bench_files.py [folders]
import cProfile
import pstats
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "tests")]          # the app, and the tests' fakes

from fastapi.testclient import TestClient               # noqa: E402

from fakes import FakeDirectory, FakeEmbedder           # noqa: E402
from mnemerynne import accounts, db, share, users       # noqa: E402
from mnemerynne.config import Settings                   # noqa: E402
from mnemerynne.web.app import Services, create_app      # noqa: E402

FOLDERS = int(sys.argv[1]) if len(sys.argv) > 1 else 28_789


class OpenShare:
    """Every folder open to everyone: the share's own speed isn't what's measured here."""
    def rights(self, path, uid, gids):
        return True, True


tmp = Path(tempfile.mkdtemp())
root = tmp / "share"
(root / "Docs").mkdir(parents=True)
(root / share.MARKER).write_text("")
(root / "Docs" / "a.md").write_text("hello")
settings = Settings(_env_file=None, data_dir=tmp / "data", secret_key="bench-secret-key-for-local-runs-only",
                    embed_dim=4, ad_domain="ETERNALHOTELS", share_root=root, share_settle_seconds=0)
directory = FakeDirectory({"jsmith": {"Domain Users"}})
share.configure(settings, directory, OpenShare())
conn = db.connect(settings.db_path)
db.migrate(conn, settings.embed_dim)
share.sync(conn, settings)
cid = conn.execute("SELECT id FROM collections WHERE share_path = 'Docs'").fetchone()[0]
with db.transaction(conn):                                # 100 top folders, 3 levels, the rest spread below
    tops = [conn.execute("INSERT INTO folders(collection_id, parent_id, name) VALUES (?, NULL, ?)",
                         (cid, f"top{i}")).lastrowid for i in range(100)]
    mids = [conn.execute("INSERT INTO folders(collection_id, parent_id, name) VALUES (?, ?, ?)",
                         (cid, tops[i % 100], f"mid{i}")).lastrowid for i in range(2000)]
    for n in range(FOLDERS - 2100):
        conn.execute("INSERT INTO folders(collection_id, parent_id, name) VALUES (?, ?, ?)", (cid, mids[n % 2000], f"f{n}"))
print(f"library {cid}: {FOLDERS:,} folders")

with TestClient(create_app(settings, Services(llm=None, embedder=FakeEmbedder(), directory=directory,
                                              share_checker=OpenShare()))) as client:
    uid = users.create_user(conn, "jane", "password123")
    accounts.link(conn, uid, "jsmith", directory)
    assert client.post("/login", data={"username": "jane", "password": "password123"},
                       follow_redirects=False).status_code == 303
    client.get("/api/locations")                         # the first look: starts the walk
    for t in [t for t in threading.enumerate() if t.name == "share-rights-walk"]:
        t.join(120)                                      # wait it out: measure the requests, not the walk

    requests = {
        "GET /api/locations": "/api/locations",
        "GET folder tree": f"/api/collections/{cid}/folders",
        "GET browse (library top)": f"/api/browse?collection_id={cid}",
        "GET browse (a top folder)": f"/api/browse?collection_id={cid}&folder_id={tops[0]}",
        "GET /api/sync/roots": "/api/sync/roots",
        "GET /files (the page)": "/files",
    }
    took = {}
    for label, url in requests.items():
        best = None
        for _ in range(3):
            began = time.perf_counter()
            status = client.get(url).status_code
            spent = time.perf_counter() - began
            best = spent if best is None else min(best, spent)
        took[label] = best
        print(f"{label:<30} {status}  {best * 1000:8.0f} ms (best of 3)")

    import gzip                                           # how much each answer weighs on the wire
    for label in ("GET folder tree", "GET browse (library top)", "GET /api/locations"):
        reply = client.get(requests[label], headers={"Accept-Encoding": "gzip"})
        size = len(reply.content)
        print(f"{label:<30} {size / 1048576:6.2f} MB, {len(gzip.compress(reply.content)) / 1048576:5.2f} MB gzipped"
              f"  (sent compressed: {reply.headers.get('content-encoding') or 'no'})")

    slowest = max(took, key=took.get)
    print(f"\n--- where '{slowest}' spends its time")
    profile = cProfile.Profile()
    profile.enable()
    client.get(requests[slowest])
    profile.disable()
    stats = pstats.Stats(profile)
    stats.sort_stats("cumulative").print_stats(r"mnemerynne", 18)
