import hashlib
import os
import time

import pytest
from fastapi.testclient import TestClient

from fakes import FakeDirectory, FakeEmbedder, FakeShareChecker
from mnemerynne import accounts, api_tokens, db, fs, share, sync, users, worker
from mnemerynne.config import Settings
from mnemerynne.web.app import Services, create_app


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
def directory():
    return FakeDirectory({"jsmith": {"Domain Users", "Backup Users"}, "rdoe": {"Domain Users"}})


@pytest.fixture
def conn(settings, directory, root):
    share.configure(settings, directory, FakeShareChecker(root))
    connection = db.connect(settings.db_path)
    db.migrate(connection, settings.embed_dim)
    yield connection
    connection.close()


@pytest.fixture
def client(settings, conn, directory, root):
    with TestClient(create_app(settings, Services(llm=None, embedder=FakeEmbedder(), directory=directory,
                                                  share_checker=FakeShareChecker(root)))) as c:
        yield c


def _person(client, conn, directory, name="jane", account="jsmith"):
    """A signed-in person with a File box on the share; returns their id."""
    uid = users.create_user(conn, name, "password123")
    accounts.link(conn, uid, account, directory)
    assert client.post("/login", data={"username": name, "password": "password123"},
                       follow_redirects=False).status_code == 303
    return uid


def _open_backups(conn, group="Backup Users"):
    sync.save_settings(conn, {"enabled": True, "group": group})


def _token(client, computer="DESKTOP-1"):
    r = client.post("/api/sync/tokens", json={"computer": computer})
    assert r.status_code == 201, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


@pytest.fixture
def app_auth(client, conn, directory):
    """jane, in the Backups group, with the backup app signed in and one backed-up folder."""
    _open_backups(conn)
    _person(client, conn, directory)
    auth = _token(client)
    client.cookies.clear()       # from here on, only the app's token
    r = client.post("/api/sync/roots", json={"computer": "DESKTOP-1", "label": "Documents"}, headers=auth)
    assert r.status_code == 200, r.text
    return auth, r.json()["id"]


def _put(client, auth, root_id, path, data: bytes, *, match=None, new=True, mtime=None):
    headers = dict(auth, **{"X-Sha256": hashlib.sha256(data).hexdigest()})
    if match is not None:
        headers["If-Match"] = f'"{match}"'
    elif new:
        headers["If-None-Match"] = "*"
    if mtime is not None:
        headers["X-Mtime"] = str(mtime)
    return client.put(f"/api/sync/roots/{root_id}/files", params={"path": path}, content=data, headers=headers)


def _backup_dir(root):
    return root / "Users" / "jane" / "Backups" / "DESKTOP-1" / "Documents"


# ------------------------------------------------------------------ who may
def test_sync_tokens_and_mcp_tokens_only_work_where_they_belong(client, conn, app_auth):
    auth, _ = app_auth
    sync_token = auth["Authorization"][7:]
    assert api_tokens.verify(conn, sync_token) is None                     # not an MCP token
    assert api_tokens.verify(conn, sync_token, scope="sync") is not None
    uid = conn.execute("SELECT id FROM users WHERE username = 'jane'").fetchone()["id"]
    _, mcp_token = api_tokens.create(conn, uid, "Claude Desktop")
    assert client.get("/api/sync/roots", headers={"Authorization": f"Bearer {mcp_token}"}).status_code == 401
    assert client.get("/api/sync/roots", headers=auth).status_code == 200
    # The Claude page lists only MCP tokens.
    assert [t["name"] for t in api_tokens.list_for_user(conn, uid)] == ["Claude Desktop"]
    assert not api_tokens.set_can_edit(conn, api_tokens.verify(conn, sync_token, scope="sync").token_id, uid, True)


def test_only_the_backups_group_may_back_up(client, conn, directory):
    _person(client, conn, directory, "rob", "rdoe")
    assert client.post("/api/sync/tokens", json={"computer": "PC"}).status_code == 403       # Backups off
    _open_backups(conn)
    assert client.post("/api/sync/tokens", json={"computer": "PC"}).status_code == 403       # not in the group
    _open_backups(conn, "Domain Users")
    auth = _token(client, "PC")
    client.cookies.clear()
    assert client.get("/api/sync/roots", headers=auth).status_code == 200
    # Out of the group: the token already issued stops working at once.
    directory.members["rdoe"] = set()
    assert client.get("/api/sync/roots", headers=auth).status_code == 403
    directory.members["rdoe"] = {"domain users"}
    sync.save_settings(conn, {"enabled": False})
    assert client.get("/api/sync/roots", headers=auth).status_code == 403


def test_admin_settings_need_an_admin(client, conn, directory):
    _person(client, conn, directory)
    assert client.get("/api/admin/settings/backups").status_code == 403
    users.create_user(conn, "boss", "password123", role="admin")
    client.post("/logout")
    client.post("/login", data={"username": "boss", "password": "password123"})
    r = client.put("/api/admin/settings/backups", json={"enabled": True, "group": "Backup Users"})
    assert r.status_code == 200 and r.json()["settings"]["enabled"]
    assert sync.load_settings(conn)["group"].lower() == "backup users"


# ------------------------------------------------------------------- roots
def test_a_root_is_a_folder_under_backups_not_searchable_and_hidden_from_claude(client, conn, root, app_auth):
    auth, root_id = app_auth
    assert _backup_dir(root).is_dir()
    backups = conn.execute("SELECT * FROM folders WHERE name = 'Backups'").fetchone()
    assert backups["no_index"] == 1 and backups["claude_hidden"] == 1
    again = client.post("/api/sync/roots", json={"computer": "desktop-1", "label": "documents"}, headers=auth)
    assert again.json()["id"] == root_id                                     # the same root, any case
    listed = client.get("/api/sync/roots", headers=auth).json()["roots"]
    assert [(r["computer"], r["label"], r["searchable"], r["present"]) for r in listed] == \
        [("DESKTOP-1", "Documents", False, True)]
    for bad in ("CON", ".hidden", "a:b", "x/y"):
        assert client.post("/api/sync/roots", json={"computer": "PC", "label": bad},
                           headers=auth).status_code == 400


def test_another_persons_root_is_not_found(client, conn, directory, app_auth):
    _, jane_root = app_auth
    _open_backups(conn, "Domain Users")
    _person(client, conn, directory, "rob", "rdoe")
    rob = _token(client, "PC")
    client.cookies.clear()
    assert client.get(f"/api/sync/roots/{jane_root}/manifest", headers=rob).status_code == 404
    assert _put(client, rob, jane_root, "x.txt", b"hi").status_code == 404


# ------------------------------------------------------------------- writes
def test_new_files_land_on_the_share_and_in_the_manifest(client, conn, root, settings, app_auth):
    auth, root_id = app_auth
    then = time.time() - 3600
    r = _put(client, auth, root_id, "Reports/2026/q1.txt", b"first quarter", mtime=then)
    assert r.status_code == 200, r.text
    version = r.json()["version"]
    assert r.headers["etag"] == f'"{version}"' and version == hashlib.sha256(b"first quarter").hexdigest()
    on_disk = _backup_dir(root) / "Reports" / "2026" / "q1.txt"
    assert on_disk.read_bytes() == b"first quarter"
    assert abs(on_disk.stat().st_mtime - then) < 1
    row = conn.execute("SELECT * FROM files WHERE display_name = 'q1.txt'").fetchone()
    assert row["status"] == "stored" and "not searchable" in row["notes"]

    m = client.get(f"/api/sync/roots/{root_id}/manifest", headers=auth).json()
    assert m["folders"] == ["Reports", "Reports/2026"] and m["next"] is None
    assert [(f["path"], f["size"], f["version"]) for f in m["files"]] == [("Reports/2026/q1.txt", 13, version)]
    # The share scan finds nothing to do.
    assert not share.sync(conn, settings)
    # Nothing to read for search, even after the worker runs.
    while worker.run_one(conn, settings, FakeEmbedder()):
        pass
    assert conn.execute("SELECT count(*) FROM chunks").fetchone()[0] == 0


def test_writes_need_the_right_preconditions_and_keep_the_old_version(client, conn, root, settings, app_auth):
    auth, root_id = app_auth
    v1 = _put(client, auth, root_id, "notes.txt", b"version one").json()["version"]
    assert _put(client, auth, root_id, "notes.txt", b"other", new=False).status_code == 428
    clash = _put(client, auth, root_id, "notes.txt", b"other")               # If-None-Match: * but it exists
    assert clash.status_code == 412 and clash.headers["etag"] == f'"{v1}"'
    assert _put(client, auth, root_id, "notes.txt", b"other", match="0" * 64).status_code == 412
    assert _put(client, auth, root_id, "gone.txt", b"other", match=v1).status_code == 412

    r = _put(client, auth, root_id, "notes.txt", b"version two", match=v1)
    assert r.status_code == 200 and r.json()["changed"]
    assert (_backup_dir(root) / "notes.txt").read_bytes() == b"version two"
    kept = conn.execute("SELECT * FROM file_versions").fetchall()
    assert [(k["via"], k["sha256"]) for k in kept] == [("sync", v1)]
    from mnemerynne import editing
    assert editing.version_path(settings, kept[0]["rel_path"]).read_bytes() == b"version one"
    # The same content again changes nothing.
    same = _put(client, auth, root_id, "notes.txt", b"version two", match=r.json()["version"])
    assert same.status_code == 200 and not same.json()["changed"]
    assert not share.sync(conn, settings)


def test_bad_paths_and_bodies_are_refused(client, app_auth, root):
    auth, root_id = app_auth
    for bad in ("../escape.txt", "/abs.txt", "a/../b.txt", "a//b.txt", ".git/config", "a\\b.txt", "x:y.txt",
                "folder/CON", "trailing. ", "~$lock.docx"):
        assert _put(client, auth, root_id, bad, b"x").status_code == 400, bad
    headers = dict(auth, **{"X-Sha256": "0" * 64, "If-None-Match": "*"})
    r = client.put(f"/api/sync/roots/{root_id}/files", params={"path": "a.txt"}, content=b"x", headers=headers)
    assert r.status_code == 400                                              # doesn't match its hash
    assert not (root / "escape.txt").exists() and not any(_backup_dir(root).iterdir())


def test_a_file_and_a_folder_cant_share_a_name(client, app_auth):
    auth, root_id = app_auth
    assert _put(client, auth, root_id, "thing/inside.txt", b"x").status_code == 200
    assert _put(client, auth, root_id, "thing", b"y").status_code == 409
    assert _put(client, auth, root_id, "plain.txt", b"y").status_code == 200
    assert _put(client, auth, root_id, "plain.txt/inside.txt", b"z").status_code == 409


# ------------------------------------------------------------ delete, move
def test_deletes_go_to_the_recycle_bin(client, conn, root, app_auth):
    auth, root_id = app_auth
    _put(client, auth, root_id, "a.txt", b"a")
    _put(client, auth, root_id, "Old/b.txt", b"b")
    r = client.post(f"/api/sync/roots/{root_id}/delete", json={"paths": ["a.txt", "Old", "never.txt"]},
                    headers=auth)
    assert [(x["path"], x["ok"], x.get("gone", False)) for x in r.json()["results"]] == \
        [("a.txt", True, False), ("Old", True, False), ("never.txt", True, True)]
    assert not (_backup_dir(root) / "a.txt").exists() and not (_backup_dir(root) / "Old").exists()
    assert conn.execute("SELECT deleted_at FROM files WHERE display_name = 'a.txt'").fetchone()[0]
    m = client.get(f"/api/sync/roots/{root_id}/manifest", headers=auth).json()
    assert m == {"folders": [], "files": [], "next": None}


def test_moves_keep_the_file(client, conn, root, settings, app_auth):
    auth, root_id = app_auth
    v = _put(client, auth, root_id, "draft.txt", b"text").json()["version"]
    _put(client, auth, root_id, "Photos/cat.jpg", b"meow")
    _put(client, auth, root_id, "taken.txt", b"t")
    file_id = conn.execute("SELECT id FROM files WHERE display_name = 'draft.txt'").fetchone()["id"]
    r = client.post(f"/api/sync/roots/{root_id}/move", headers=auth, json={"moves": [
        {"from": "draft.txt", "to": "Final/report.txt"},
        {"from": "Photos", "to": "Pictures"},
        {"from": "taken.txt", "to": "Pictures/cat.jpg"},
        {"from": "missing.txt", "to": "x.txt"},
    ]})
    assert [(x["ok"], x.get("status")) for x in r.json()["results"]] == [(True, None), (True, None), (False, 409),
                                                                        (False, 404)]
    assert conn.execute("SELECT display_name FROM files WHERE id = ?", (file_id,)).fetchone()[0] == "report.txt"
    assert (_backup_dir(root) / "Final" / "report.txt").read_bytes() == b"text"
    assert (_backup_dir(root) / "Pictures" / "cat.jpg").exists() and not (_backup_dir(root) / "Photos").exists()
    # Only the case changes.
    r = client.post(f"/api/sync/roots/{root_id}/move", headers=auth,
                    json={"moves": [{"from": "Final/report.txt", "to": "Final/Report.txt"}]})
    assert r.json()["results"][0]["ok"]
    paths = {f["path"]: f["version"] for f in client.get(f"/api/sync/roots/{root_id}/manifest",
                                                         headers=auth).json()["files"]}
    assert paths == {"Final/Report.txt": v, "Pictures/cat.jpg": hashlib.sha256(b"meow").hexdigest(),
                     "taken.txt": hashlib.sha256(b"t").hexdigest()}
    assert not share.sync(conn, settings)


# ------------------------------------------------------------- searchable
def test_not_indexed_follows_the_nearest_folder_with_a_setting(conn, client, settings, app_auth):
    auth, root_id = app_auth
    _put(client, auth, root_id, "Notes/hello.md", b"# Hello\n\nThe night audit runs at 2am.")
    folder = conn.execute("SELECT folder_id FROM sync_roots WHERE id = ?", (root_id,)).fetchone()[0]
    notes = conn.execute("SELECT id FROM folders WHERE name = 'Notes'").fetchone()["id"]
    assert fs.not_indexed(conn, notes)
    conn.execute("UPDATE folders SET no_index = 0 WHERE id = ?", (folder,))   # the root made Searchable
    assert not fs.not_indexed(conn, notes)
    file_id = conn.execute("SELECT id FROM files WHERE display_name = 'hello.md'").fetchone()["id"]
    conn.execute("UPDATE files SET status = 'queued' WHERE id = ?", (file_id,))
    from mnemerynne import jobs
    jobs.enqueue(conn, "ingest", file_id)
    while worker.run_one(conn, settings, FakeEmbedder()):
        pass
    assert conn.execute("SELECT status FROM files WHERE id = ?", (file_id,)).fetchone()[0] == "ready"
    assert not fs.not_indexed(conn, None)


def test_a_deleted_root_folder_must_be_registered_again(client, conn, app_auth):
    auth, root_id = app_auth
    folder = conn.execute("SELECT folder_id FROM sync_roots WHERE id = ?", (root_id,)).fetchone()[0]
    uid = conn.execute("SELECT id FROM users WHERE username = 'jane'").fetchone()["id"]
    assert fs.trash(conn, fs.Access.of(conn, uid), [fs.Item("folder", folder)])[0].ok
    assert client.get(f"/api/sync/roots/{root_id}/manifest", headers=auth).status_code == 409
    again = client.post("/api/sync/roots", json={"computer": "DESKTOP-1", "label": "Documents"}, headers=auth)
    assert again.json()["id"] == root_id and again.json()["present"]
    assert client.get(f"/api/sync/roots/{root_id}/manifest", headers=auth).status_code == 200


def test_big_bodies_are_refused(client, app_auth, monkeypatch):
    auth, root_id = app_auth
    monkeypatch.setattr(sync, "MAX_PUT_BYTES", 10)
    assert _put(client, auth, root_id, "big.bin", os.urandom(11)).status_code == 413


# ------------------------------------------------------------- backup host
def test_the_backup_host_answers_only_the_backup_api(tmp_path, root, directory):
    settings = Settings(_env_file=None, data_dir=tmp_path / "d2", secret_key="test-secret-key-for-the-test-suite-only",
                        embed_dim=4, share_root=root, public_url="https://beepsquad.wtf",
                        backup_url="https://bkp.beepsquad.wtf")
    with TestClient(create_app(settings, Services(llm=None, embedder=FakeEmbedder(), directory=directory,
                                                  share_checker=FakeShareChecker(root))),
                    base_url="https://bkp.beepsquad.wtf") as c:
        assert c.get("/api/sync/client").json() == {"api_url": "https://bkp.beepsquad.wtf",
                                                    "server": "https://beepsquad.wtf"}
        assert c.get("/api/sync/roots").status_code == 401           # the app's API: there, but needs a token
        for path in ("/", "/login", "/files", "/api/files", "/api/admin/settings/backups", "/mcp", "/oauth/authorize"):
            assert c.get(path).status_code == 404, path
        assert c.post("/oauth/token", data={}).status_code != 404
    for bad in ({"backup_url": "https://beepsquad.wtf"}, {"backup_url": "https://bkp.beepsquad.wtf/path"}):
        with pytest.raises(Exception, match="MNEMERYNNE_BACKUP_URL"):
            create_app(Settings(_env_file=None, data_dir=tmp_path / "d3", embed_dim=4,
                                secret_key="test-secret-key-for-the-test-suite-only",
                                public_url="https://beepsquad.wtf", **bad))


# ---------------------------------------------------------- uploads in parts
def test_big_files_arrive_in_parts(client, conn, root, settings, app_auth, monkeypatch):
    auth, root_id = app_auth
    monkeypatch.setattr(sync, "PART_BYTES", 10)
    data = os.urandom(25)
    sha = hashlib.sha256(data).hexdigest()
    start = client.post("/api/sync/uploads", json={"root_id": root_id, "path": "Big/disk.img", "size": 25,
                                                   "sha256": sha}, headers=auth)
    assert start.status_code == 200, start.text
    up = start.json()
    assert up["part_size"] == 10 and up["parts"] == []

    def part(n, body):
        return client.put(f"/api/sync/uploads/{up['id']}/parts/{n}", content=body,
                          headers=dict(auth, **{"X-Sha256": hashlib.sha256(body).hexdigest()}))

    assert part(2, data[20:]).status_code == 200                       # any order
    assert part(0, data[:9]).status_code == 400                        # wrong length
    assert part(3, b"x").status_code == 400                            # no such part
    assert part(0, data[:10]).status_code == 200
    finish = client.post(f"/api/sync/uploads/{up['id']}/finish", headers=dict(auth, **{"If-None-Match": "*"}))
    assert finish.status_code == 409 and "1" in finish.json()["detail"]        # part 1 hasn't arrived
    # Asking again for the same file resumes it.
    again = client.post("/api/sync/uploads", json={"root_id": root_id, "path": "Big/disk.img", "size": 25,
                                                   "sha256": sha}, headers=auth).json()
    assert again["id"] == up["id"] and again["parts"] == [0, 2]
    assert part(1, data[10:20]).status_code == 200
    finish = client.post(f"/api/sync/uploads/{up['id']}/finish", headers=dict(auth, **{"If-None-Match": "*"}))
    assert finish.status_code == 200 and finish.json()["version"] == sha
    assert (_backup_dir(root) / "Big" / "disk.img").read_bytes() == data
    assert conn.execute("SELECT count(*) FROM sync_uploads").fetchone()[0] == 0
    assert not list((settings.tmp_dir / "sync-uploads").glob("*.part"))


def test_parts_that_dont_add_up_are_dropped_and_old_uploads_are_swept(client, conn, settings, app_auth, monkeypatch):
    auth, root_id = app_auth
    monkeypatch.setattr(sync, "PART_BYTES", 10)
    up = client.post("/api/sync/uploads", json={"root_id": root_id, "path": "x.bin", "size": 5, "sha256": "a" * 64},
                     headers=auth).json()
    client.put(f"/api/sync/uploads/{up['id']}/parts/0", content=b"12345",
               headers=dict(auth, **{"X-Sha256": hashlib.sha256(b"12345").hexdigest()}))
    r = client.post(f"/api/sync/uploads/{up['id']}/finish", headers=dict(auth, **{"If-None-Match": "*"}))
    assert r.status_code == 400 and client.get(f"/api/sync/uploads/{up['id']}", headers=auth).status_code == 404
    up = client.post("/api/sync/uploads", json={"root_id": root_id, "path": "y.bin", "size": 5, "sha256": "b" * 64},
                     headers=auth).json()
    conn.execute("UPDATE sync_uploads SET created_at = datetime('now', '-2 days')")
    (settings.tmp_dir / "sync-uploads" / "stray.part").write_bytes(b"")
    assert sync.sweep_uploads(conn, settings) == 1
    assert not list((settings.tmp_dir / "sync-uploads").glob("*.part"))
    too_big = client.post("/api/sync/uploads", json={"root_id": root_id, "path": "z.bin", "size": 10 ** 15,
                                                     "sha256": "c" * 64}, headers=auth)
    assert too_big.status_code == 413
