import io
import sqlite3
from datetime import date, timedelta

import pytest

from mnemerynne import users
from mnemerynne.backup import backup
from mnemerynne.cli import main
from mnemerynne.ingest import store


def test_backup_snapshots_db_copies_blobs_and_prunes(conn, settings, tmp_path):
    users.create_user(conn, "alice", "password123")
    inbox = conn.execute("SELECT id FROM collections").fetchone()[0]
    store.add_file(conn, settings, store.receive(io.BytesIO(b"# Hi\n\nThere."), "a.md", settings), inbox, None)
    dest = tmp_path / "backups"

    first = backup(settings, dest, keep=2, today=date(2026, 10, 1))
    assert first["blobs_copied"] == 1
    snap = sqlite3.connect(first["snapshot"])
    assert snap.execute("SELECT username FROM users").fetchone()[0] == "alice"
    snap.close()

    again = backup(settings, dest, keep=2, today=date(2026, 10, 2))
    assert again["blobs_copied"] == 0  # unchanged files are not copied twice
    third = backup(settings, dest, keep=2, today=date(2026, 10, 1) + timedelta(days=2))
    assert third["pruned"] == ["mnemerynne-2026-10-01.db"]
    assert sorted(p.name for p in (dest / "db").iterdir()) == ["mnemerynne-2026-10-02.db", "mnemerynne-2026-10-03.db"]


def test_forget_memories_cli(conn, settings, monkeypatch, capsys):
    from langgraph.store.sqlite import SqliteStore

    from mnemerynne.graph import memory_namespace

    uid = users.create_user(conn, "alice", "password123")
    s = SqliteStore(sqlite3.connect(settings.db_path, isolation_level=None, check_same_thread=False))
    s.setup()
    s.put(memory_namespace(uid), "k1", {"text": "Works nights."})
    monkeypatch.setattr("mnemerynne.cli.get_settings", lambda: settings)
    main(["forget-memories", "alice"])
    assert "Forgot 1 memory item(s)" in capsys.readouterr().out
    assert s.search(memory_namespace(uid)) == []


def test_delete_user_cli_wipes_memories_and_inbox(conn, settings, monkeypatch, capsys):
    from langgraph.store.sqlite import SqliteStore

    from mnemerynne.graph import memory_namespace

    uid = users.create_user(conn, "alice", "password123")
    s = SqliteStore(sqlite3.connect(settings.db_path, isolation_level=None, check_same_thread=False))
    s.setup()
    s.put(memory_namespace(uid), "k1", {"text": "Works nights."})
    monkeypatch.setattr("mnemerynne.cli.get_settings", lambda: settings)
    monkeypatch.setattr("builtins.input", lambda prompt: "nope")
    with pytest.raises(SystemExit, match="Not deleted"):
        main(["delete-user", "alice"])
    main(["delete-user", "alice", "--yes"])
    assert "Deleted 'alice'" in capsys.readouterr().out
    assert users.get_user(conn, "alice") is None
    assert conn.execute("SELECT count(*) FROM collections WHERE owner_user_id = ?", (uid,)).fetchone()[0] == 0
    assert s.search(memory_namespace(uid)) == []
