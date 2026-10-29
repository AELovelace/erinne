"""The desktop backup app (clients/desktop-sync) against the real server: whole passes through TestClient."""

import os
import sys
import time
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "clients" / "desktop-sync"))

from fakes import FakeDirectory, FakeEmbedder, FakeShareChecker  # noqa: E402
from mnemerynne import accounts, db, share, sync, users  # noqa: E402
from mnemerynne.config import Settings  # noqa: E402
from mnemerynne.web.app import Services, create_app  # noqa: E402
from mnemerynne_backup import names, scan  # noqa: E402
from mnemerynne_backup.backup import Backup, connect  # noqa: E402
from mnemerynne_backup.config import Config, Folder  # noqa: E402
from mnemerynne_backup.engine import Engine, find_moves  # noqa: E402
from mnemerynne_backup.state import Known, State  # noqa: E402
from mnemerynne_backup.transport import Transport  # noqa: E402

OLD = time.time() - 3600


# ------------------------------------------------------------------ the rules
@pytest.mark.parametrize("name", ["report.pdf", "a:b.txt", "what?", "trailing.", "trailing ", "CON", "lpt1.txt",
                                  "x|y", "tab\there", "two  spaces", "café.txt", "café.txt", "ok name"])
def test_the_app_and_the_server_agree_on_names(name):
    try:
        sync.clean_part(name)
        server_ok = True
    except sync.SyncError:
        server_ok = False
    assert (names.problem(name) is None and not names.is_junk(name)) == server_ok, name


def test_the_app_and_the_server_agree_on_junk():
    assert names.BAD_CHARS == share._BAD_CHARS and names.RESERVED == share._RESERVED
    for name in (".hidden", "~$doc.docx", "Thumbs.db", "desktop.ini", "x.tmp", "y.part", "z.crdownload", "plain.txt"):
        assert names.is_junk(name) == share.is_junk(name)


def _write(path: Path, data: bytes = b"x", mtime: float = OLD) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    os.utime(path, (mtime, mtime))
    return path


def test_scanning_skips_what_the_share_cant_hold(tmp_path):
    _write(tmp_path / "Docs" / "a.txt")
    _write(tmp_path / "Docs" / "~$a.docx")                 # never wanted: not even listed
    _write(tmp_path / "node_modules" / "pkg" / "x.js")
    _write(tmp_path / "bad:name.txt")
    _write(tmp_path / "Notes.txt")
    _write(tmp_path / "notes.txt")
    os.symlink(tmp_path / "Docs", tmp_path / "link")
    found = scan.walk(str(tmp_path), ["node_modules"])
    assert set(found.files) == {"Docs/a.txt", "Notes.txt"} and found.dirs == {"Docs"}
    why = dict(found.skipped)
    assert "bad:name.txt" in why and "notes.txt" in why and "link" in why


def test_a_renamed_folder_is_one_move():
    known = {f"Photos/2026/{i}.jpg": Known(1, 1, f"h{i}", f"h{i}") for i in range(5)}
    known["Photos/keep.txt"] = Known(1, 1, "k", "k")
    new = {f"Pictures/2026/{i}.jpg": f"h{i}" for i in range(5)}
    missing = {p: k for p, k in known.items() if p.startswith("Photos/2026/")}
    folders, files = find_moves(new, missing, {"Photos", "Pictures", "Pictures/2026"}, known)
    assert folders == [("Photos/2026", "Pictures/2026")] and files == []
    # The same file under another name is a file move.
    folders, files = find_moves({"b.txt": "k"}, {"Photos/keep.txt": known["Photos/keep.txt"]}, set(), known)
    assert folders == [] and files == [("Photos/keep.txt", "b.txt")]


# --------------------------------------------------------------- end to end
@pytest.fixture
def root(tmp_path):
    path = tmp_path / "storage" / "share"
    path.mkdir(parents=True)
    (path / share.MARKER).write_text("")
    return path


@pytest.fixture
def settings(tmp_path, root):
    return Settings(_env_file=None, data_dir=tmp_path / "data", secret_key="test-secret-key-for-the-test-suite-only",
                    embed_dim=4, ad_domain="ETERNALHOTELS", share_root=root, share_settle_seconds=0)


@pytest.fixture
def server(settings, root, tmp_path):
    directory = FakeDirectory({"jsmith": {"Backup Users"}})
    share.configure(settings, directory, FakeShareChecker(root))
    conn = db.connect(settings.db_path)
    db.migrate(conn, settings.embed_dim)
    with TestClient(create_app(settings, Services(llm=None, embedder=FakeEmbedder(), directory=directory,
                                                  share_checker=FakeShareChecker(root)))) as client:
        sync.save_settings(conn, {"enabled": True, "group": "Backup Users"})
        uid = users.create_user(conn, "jane", "password123")
        accounts.link(conn, uid, "jsmith", directory)
        client.post("/login", data={"username": "jane", "password": "password123"})
        token = client.post("/api/sync/tokens", json={"computer": "LAPTOP"}).json()["token"]
        client.cookies.clear()
        yield client, conn, token
    conn.close()


@pytest.fixture
def app(server, tmp_path):
    client, conn, token = server
    local = tmp_path / "home" / "Documents"
    local.mkdir(parents=True)
    config = Config(server="http://testserver", computer="LAPTOP", settle_seconds=0, pause_on_metered=False)
    transport = Transport("http://testserver", lambda refresh: token, client=client)
    backup = Backup(config, State(tmp_path / "state.db"), transport, config_path=tmp_path / "config.json")
    backup.add(str(local), "Documents")
    return backup, local, conn


def _on_nas(root: Path) -> Path:
    return root / "Users" / "jane" / "Backups" / "LAPTOP" / "Documents"


def _once(backup, check=None):
    (report,) = backup.run(check=check)
    assert not report.errors and not report.offline and not report.signed_out, report
    return report


def test_connect_says_where_backups_go(server, settings):
    client, _, _ = server
    assert connect("http://testserver", client)["api_url"] == "http://testserver"


def test_a_folder_is_backed_up_and_then_left_alone(app, root):
    backup, local, conn = app
    for i in range(12):
        _write(local / "Reports" / f"r{i}.txt", f"report {i}".encode())
    _write(local / "big.bin", os.urandom(200_000))
    _write(local / "bad:name.txt")
    r = _once(backup)
    assert r.uploaded == 13 and r.bytes > 200_000 and [p for p, _ in r.skipped] == ["bad:name.txt"]
    assert (_on_nas(root) / "Reports" / "r3.txt").read_bytes() == b"report 3"
    assert abs((_on_nas(root) / "big.bin").stat().st_mtime - OLD) < 1
    r = _once(backup)
    assert (r.uploaded, r.moved, r.deleted, r.unchanged) == (0, 0, 0, 13)


def test_changes_moves_and_deletes(app, root):
    backup, local, conn = app
    for i in range(20):
        _write(local / "Photos" / "2026" / f"{i}.jpg", f"photo {i}".encode())
    _write(local / "draft.txt", b"one")
    _write(local / "Old" / "gone.txt", b"bye")
    _once(backup)

    _write(local / "draft.txt", b"two", time.time() - 120)            # changed
    os.rename(local / "Photos", local / "Pictures")                    # a whole folder renamed
    os.rename(local / "Old" / "gone.txt", local / "kept.txt")         # a file moved
    os.rmdir(local / "Old")
    _write(local / "Notes" / "n.txt", b"n")
    (local / "Notes" / "n.txt").unlink()
    r = _once(backup)
    assert (r.uploaded, r.moved) == (1, 2), r
    nas = _on_nas(root)
    assert (nas / "draft.txt").read_bytes() == b"two"
    assert (nas / "Pictures" / "2026" / "7.jpg").read_bytes() == b"photo 7" and not (nas / "Photos").exists()
    assert (nas / "kept.txt").read_bytes() == b"bye"
    assert conn.execute("SELECT count(*) FROM file_versions WHERE via = 'sync'").fetchone()[0] == 1

    import shutil
    shutil.rmtree(local / "Pictures")
    r = _once(backup)
    assert r.deleted == 1 and not (nas / "Pictures").exists()          # one folder to the recycle bin
    assert conn.execute("SELECT count(*) FROM folders WHERE name = 'Pictures' AND deleted_at IS NOT NULL"
                        ).fetchone()[0] == 1
    # Old/ was left empty on the NAS when its file moved out; the next check tidies it away.
    assert (nas / "Old").is_dir()
    _once(backup, check=True)
    assert not (nas / "Old").exists()


def test_files_still_changing_wait(app):
    backup, local, _ = app
    backup.config.settle_seconds = 30
    _write(local / "new.txt", b"x", time.time())
    r = _once(backup)
    assert r.uploaded == 0 and r.waiting == ["new.txt"]


def test_a_lost_state_db_doesnt_upload_again(app, tmp_path, root):
    backup, local, _ = app
    for i in range(5):
        _write(local / f"{i}.txt", str(i).encode())
    _once(backup)
    backup.state = State(tmp_path / "fresh.db")
    r = _once(backup)
    assert (r.uploaded, r.unchanged) == (0, 5)


def test_changes_made_on_the_nas_are_put_right_on_the_next_check(app, root):
    backup, local, conn = app
    _write(local / "a.txt", b"mine")
    _write(local / "b.txt", b"also mine")
    _once(backup)
    root_id = backup.config.folders[0].root_id
    t = backup.transport
    v = t.manifest(root_id)["files"]["a.txt"]["version"]
    tmp = local.parent / "theirs.txt"
    _write(tmp, b"someone else's")
    import hashlib
    t.put_file(root_id, "a.txt", tmp, hashlib.sha256(b"someone else's").hexdigest(), 14, version=v)
    t.delete(root_id, ["b.txt"])
    assert _once(backup).uploaded == 0                       # not checked yet: nothing known to be wrong
    r = _once(backup, check=True)
    assert r.uploaded == 2
    assert (_on_nas(root) / "a.txt").read_bytes() == b"mine" and (_on_nas(root) / "b.txt").exists()


def test_a_backup_deleted_on_the_nas_is_made_again(app, root):
    backup, local, conn = app
    _write(local / "a.txt", b"a")
    _once(backup)
    from mnemerynne import fs
    folder = conn.execute("SELECT folder_id FROM sync_roots").fetchone()[0]
    uid = conn.execute("SELECT id FROM users WHERE username = 'jane'").fetchone()[0]
    assert fs.trash(conn, fs.Access.of(conn, uid), [fs.Item("folder", folder)])[0].ok
    r = _once(backup)
    assert r.uploaded == 1 and (_on_nas(root) / "a.txt").read_bytes() == b"a"


def test_offline_and_signed_out_are_reported_not_raised(app, tmp_path):
    backup, local, _ = app
    _write(local / "a.txt")

    def refuse(request):
        raise httpx.ConnectError("no network")

    offline = Transport("http://testserver", lambda refresh: "x", client=httpx.Client(transport=httpx.MockTransport(refuse)))
    import mnemerynne_backup.transport as tr
    tr_sleep, tr.time.sleep = tr.time.sleep, lambda s: None
    try:
        report = Engine(offline, State(tmp_path / "s.db"), settle=0).run(1, str(local))
    finally:
        tr.time.sleep = tr_sleep
    assert report.offline and not report.ok
    _, _, _ = backup, local, None
    backup._transport = Transport("http://testserver", lambda refresh: "mnemerynne_wrong",
                                  client=backup.transport.http)
    (report,) = backup.run()
    assert report.signed_out


def test_restore_puts_files_back_without_overwriting(app, tmp_path):
    backup, local, _ = app
    _write(local / "Docs" / "a.txt", b"a1")
    _write(local / "b.txt", b"b")
    _once(backup)
    _write(local / "Docs" / "a.txt", b"a2", time.time() - 100)
    _once(backup)
    out = tmp_path / "restored"
    _write(out / "b.txt", b"local copy")
    assert backup.restore("Documents", str(out)) == 1
    assert (out / "Docs" / "a.txt").read_bytes() == b"a2" and (out / "b.txt").read_bytes() == b"local copy"
    root_id = backup.config.folders[0].root_id
    (old,) = backup.transport.versions(root_id, "Docs/a.txt")
    assert backup.restore("Documents", str(tmp_path / "v"), "Docs/a.txt", version_id=old["id"]) == 1
    assert (tmp_path / "v" / "a.txt").read_bytes() == b"a1"


def test_big_files_go_up_in_parts_and_resume(app, root, monkeypatch):
    backup, local, conn = app
    import mnemerynne_backup.transport as tr
    monkeypatch.setattr(tr, "SMALL", 1000)
    monkeypatch.setattr(sync, "PART_BYTES", 1000)
    monkeypatch.setattr(tr.time, "sleep", lambda s: None)
    data = os.urandom(4500)
    _write(local / "video.mp4", data)
    http = backup.transport.http
    real = http.request
    sent = []

    def flaky(method, url, **kw):
        if "/parts/" in url:
            sent.append(url.rsplit("/", 1)[1])
            if len(sent) == 3:
                raise httpx.ConnectError("Wi-Fi dropped")
        return real(method, url, **kw)

    monkeypatch.setattr(http, "request", flaky)
    monkeypatch.setattr(tr, "RETRIES", 0)
    (r,) = backup.run()
    assert r.offline and r.uploaded == 0
    (r,) = backup.run()
    assert r.uploaded == 1 and (_on_nas(root) / "video.mp4").read_bytes() == data
    # Parts 0 and 1 weren't sent again.
    assert sent == ["0", "1", "2", "2", "3", "4"]
