import io
import os
import sqlite3
import stat
from datetime import date, timedelta
from pathlib import Path

import pytest

from mnemerynne import apis, db, users
from mnemerynne.backup import RestoreError, backup, env_secret_key, restore
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


def _populated(conn, settings, tmp_path):
    """A user with a file and an earlier version, an API whose credential is encrypted with the
    settings' secret key, a trained router and a settings file holding that key."""
    users.create_user(conn, "alice", "password123")
    inbox = conn.execute("SELECT id FROM collections").fetchone()[0]
    store.add_file(conn, settings, store.receive(io.BytesIO(b"# Hi\n\nThere."), "a.md", settings), inbox, None)
    old = settings.data_dir / "versions" / "1" / "v1.md"
    old.parent.mkdir(parents=True)
    old.write_bytes(b"# Before")
    conn.execute("INSERT INTO apis(name, base_url, auth_type, secret_enc) VALUES ('Atera', 'https://x', 'bearer', ?)",
                 (apis.encrypt_secret(settings, "s3cret-token"),))
    (settings.data_dir / "router.json").write_text("{}", encoding="utf-8")
    env = tmp_path / "etc" / "mnemerynne.env"
    env.parent.mkdir()
    env.write_text(f"MNEMERYNNE_DATA_DIR={settings.data_dir}\nMNEMERYNNE_SECRET_KEY='{settings.secret_key}'\n",
                   encoding="utf-8")
    return settings.model_copy(update={"backup_env_file": env})


def test_backup_keeps_settings_file_and_data_files_per_snapshot(conn, settings, tmp_path):
    settings = _populated(conn, settings, tmp_path)
    dest = tmp_path / "backups"
    result = backup(settings, dest, keep=1, today=date(2026, 10, 1))
    assert result["config"] == ["mnemerynne.env", "router.json"] and result["warnings"] == []
    assert env_secret_key(dest / "config" / "2026-10-01" / "mnemerynne.env") == "test-secret"
    assert (dest / "versions" / "1" / "v1.md").read_bytes() == b"# Before"
    if os.name == "posix":  # a backup holds every document and the key to every credential
        assert stat.S_IMODE((dest / "db").stat().st_mode) == 0o700
        assert stat.S_IMODE(Path(result["snapshot"]).stat().st_mode) == 0o600
        assert stat.S_IMODE((dest / "config" / "2026-10-01" / "mnemerynne.env").stat().st_mode) == 0o600

    backup(settings, dest, keep=1, today=date(2026, 10, 2))
    assert sorted(p.name for p in (dest / "config").iterdir()) == ["2026-10-02"]  # pruned with its snapshot


def test_backup_warns_when_the_settings_file_cant_be_read(conn, settings, tmp_path):
    settings = settings.model_copy(update={"backup_env_file": tmp_path / "missing.env"})
    result = backup(settings, tmp_path / "backups", today=date(2026, 10, 1))
    assert result["config"] == []
    assert "MNEMERYNNE_SECRET_KEY" in result["warnings"][0]


def test_restore_into_an_empty_data_dir(conn, settings, tmp_path):
    settings = _populated(conn, settings, tmp_path)
    dest = tmp_path / "backups"
    backup(settings, dest, today=date(2026, 10, 1))

    fresh = settings.model_copy(update={"data_dir": tmp_path / "new-box"})
    result = restore(fresh, dest, running=lambda: [])
    assert result["date"] == "2026-10-01" and result["missing_blobs"] == 0
    assert result["files"] == ["router.json"] and result["secret_key"] == "matches"
    restored = db.connect(fresh.db_path)
    try:
        assert restored.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert restored.execute("SELECT username FROM users").fetchone()[0] == "alice"
        rel = restored.execute("SELECT rel_path FROM blobs").fetchone()[0]
        assert store.blob_path(fresh, rel).read_bytes() == b"# Hi\n\nThere."
        assert (fresh.data_dir / "versions" / "1" / "v1.md").read_bytes() == b"# Before"
        secret = restored.execute("SELECT secret_enc FROM apis").fetchone()[0]
        assert apis.decrypt_secret(fresh, secret) == "s3cret-token"
    finally:
        restored.close()

    # A box whose key was lost: the backed-up settings file has the one that decrypts.
    other = fresh.model_copy(update={"data_dir": tmp_path / "third-box", "secret_key": "a-new-key"})
    result = restore(other, dest, running=lambda: [])
    assert result["secret_key"] == "differs"
    key = env_secret_key(Path(result["env_backup"]))
    restored = db.connect(other.db_path)
    try:
        secret = restored.execute("SELECT secret_enc FROM apis").fetchone()[0]
        assert apis.decrypt_secret(other, secret) is None
        assert apis.decrypt_secret(other.model_copy(update={"secret_key": key}), secret) == "s3cret-token"
    finally:
        restored.close()


def test_restore_refuses_over_a_database_or_while_running(conn, settings, tmp_path):
    dest = tmp_path / "backups"
    backup(settings, dest, today=date(2026, 10, 1))
    with pytest.raises(RestoreError, match="Stop mnemerynne-web"):
        restore(settings, dest, running=lambda: ["mnemerynne-web"])
    with pytest.raises(RestoreError, match="already exists"):
        restore(settings, dest, running=lambda: [])
    with pytest.raises(RestoreError, match="No snapshot for 2026-09-30"):
        restore(settings, dest, "2026-09-30", running=lambda: [])

    conn.close()
    result = restore(settings, dest, force=True, running=lambda: [])
    aside = Path(result["moved_aside"])
    assert (aside / "mnemerynne.db").exists()
    assert not Path(f"{settings.db_path}-wal").exists()  # the old database's WAL never meets the snapshot


def test_restore_refuses_a_damaged_snapshot(settings, tmp_path):
    dest = tmp_path / "backups"
    (dest / "db").mkdir(parents=True)
    (dest / "db" / "mnemerynne-2026-10-01.db").write_bytes(b"SQLite format 3\x00" + b"\x00" * 200)
    with pytest.raises(RestoreError, match="damaged"):
        restore(settings, dest, running=lambda: [])
    assert not settings.db_path.exists()


def test_restore_cli_lists_and_restores(conn, settings, tmp_path, monkeypatch, capsys):
    settings = settings.model_copy(update={"backup_env_file": None})
    dest = tmp_path / "backups"
    backup(settings, dest, today=date(2026, 10, 1))
    fresh = settings.model_copy(update={"data_dir": tmp_path / "new-box"})
    monkeypatch.setattr("mnemerynne.cli.get_settings", lambda: fresh)
    monkeypatch.setattr("mnemerynne.backup.running_services", lambda: [])
    main(["restore", "--from", str(dest), "--list"])
    assert capsys.readouterr().out.strip() == "2026-10-01"
    main(["restore", "--from", str(dest)])
    out = capsys.readouterr().out
    assert "Restored the 2026-10-01 snapshot" in out and "no settings file" in out
    assert fresh.db_path.exists()


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
