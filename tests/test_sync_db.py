"""sync.db (docs/plans/services-rewrite.md, Phase 10): the backup app's roots and part-by-part uploads live in a file
of their own, so each part no longer waits for core's lock."""

import hashlib
import sqlite3

import pytest

from mnemerynne import db, sync, users
from test_sync import _backup_dir, app_auth, client, conn, directory, root, settings  # noqa: F401


def test_the_backups_tables_move_out_of_core_and_go_with_their_person(tmp_path):
    from mnemerynne.config import Settings
    settings = Settings(_env_file=None, data_dir=tmp_path / "data", secret_key="x" * 40, embed_dim=4)
    core = db.connect(settings.db_path, features=False)                 # a box from before Phase 10
    db.migrate(core, settings.embed_dim)
    jane = users.create_user(core, "jane", "password123")
    inbox = core.execute("SELECT id FROM collections WHERE kind = 'private'").fetchone()[0]
    folder = core.execute("INSERT INTO folders(collection_id, name) VALUES (?, 'Backups')", (inbox,)).lastrowid
    core.execute("INSERT INTO sync_roots(user_id, computer, label, folder_id) VALUES (?, 'PC', 'Docs', ?)", (jane, folder))
    core.execute("INSERT INTO sync_uploads(id, user_id, root_id, path, size, sha256) VALUES ('u1', ?, 1, 'a', 1, 'x')",
                 (jane,))
    core.close()

    db.migrate_all(settings.db_path, settings.embed_dim)
    conn = db.connect(settings.db_path)
    tables = {r[0] for r in conn.execute("SELECT name FROM main.sqlite_master WHERE type = 'table'")}
    assert not {"sync_roots", "sync_uploads"} & tables
    assert conn.execute("SELECT folder_id FROM sync.sync_roots").fetchone()[0] == folder
    with pytest.raises(sqlite3.OperationalError, match="readonly"):
        conn.execute("DELETE FROM sync_uploads")                        # writes go through sync.db's own
    # The folder goes (deleted for good): the root stays, without it, and asks to be registered again.
    conn.close()
    core = db.connect(settings.db_path, features=False)
    core.execute("DELETE FROM folders WHERE id = ?", (folder,))
    core.close()
    assert db.sweep_orphans(settings.db_path) == {"sync.sync_roots.folder_id": 1}
    conn = db.connect(settings.db_path)
    assert conn.execute("SELECT folder_id FROM sync_roots").fetchone()[0] is None
    assert users.delete_user(conn, "jane")                              # the person goes: their roots and uploads too
    assert conn.execute("SELECT count(*) FROM sync_roots").fetchone()[0] == 0
    assert conn.execute("SELECT count(*) FROM sync_uploads").fetchone()[0] == 0
    conn.close()


def test_a_big_backup_arrives_in_parts_written_to_sync_db(client, conn, root, settings, app_auth, monkeypatch):
    auth, root_id = app_auth
    monkeypatch.setattr(sync, "PART_BYTES", 10)
    data = b"0123456789abcdefghij"
    sha = hashlib.sha256(data).hexdigest()
    up = client.post("/api/sync/uploads", json={"root_id": root_id, "path": "disk.img", "size": 20, "sha256": sha},
                     headers=auth).json()
    for n in (0, 1):
        body = data[n * 10:(n + 1) * 10]
        assert client.put(f"/api/sync/uploads/{up['id']}/parts/{n}", content=body,
                          headers=dict(auth, **{"X-Sha256": hashlib.sha256(body).hexdigest()})).status_code == 200
    there = sqlite3.connect(db.feature_path(settings.db_path, "sync"))
    assert there.execute("SELECT parts_received FROM sync_uploads").fetchone()[0] == "[0, 1]"   # sync.db's rows
    there.close()
    done = client.post(f"/api/sync/uploads/{up['id']}/finish", headers=dict(auth, **{"If-None-Match": "*"}))
    assert done.status_code == 200 and (_backup_dir(root) / "disk.img").read_bytes() == data
    assert conn.execute("SELECT count(*) FROM sync_uploads").fetchone()[0] == 0
    renamed = client.patch(f"/api/sync/roots/{root_id}", json={"label": "Work"}, headers=auth)
    assert renamed.status_code == 200, renamed.text
    assert conn.execute("SELECT label FROM sync_roots WHERE id = ?", (root_id,)).fetchone()[0] == "Work"
