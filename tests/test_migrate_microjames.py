import importlib.util
import json
import sqlite3
import sys
from pathlib import Path

import pytest
from passlib.hash import pbkdf2_sha256

from mnemerynne import permissions, users

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "migrate_microjames.py"
_spec = importlib.util.spec_from_file_location("migrate_microjames", _SCRIPT)
migrate_microjames = importlib.util.module_from_spec(_spec)
sys.modules["migrate_microjames"] = migrate_microjames  # dataclasses need the module registered
_spec.loader.exec_module(migrate_microjames)


@pytest.fixture
def microjames(tmp_path):
    """A minimal MicroJames install laid out like the real one."""
    root = tmp_path / "MicroJames"
    (root / "data").mkdir(parents=True)
    src = sqlite3.connect(root / "data" / "users.db")
    src.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, username TEXT, password TEXT, role TEXT)")
    for name, pw, role in [("admin", "admin-pass", "user"), ("rob", "rob-pass", "admin"),
                           ("james", "james-pass", "user"), ("bad name", "x", "user")]:
        src.execute("INSERT INTO users(username, password, role) VALUES (?, ?, ?)",
                    (name, pbkdf2_sha256.hash(pw), role))
    src.commit()
    src.close()
    (root / "data" / "access_control.json").write_text(json.dumps({
        "rob": ["brandresbw", "oracle"],
        "james": ["BrandResBW", "rob"],   # second grant points at a private folder
        "ghost": ["oracle"],
    }), encoding="utf-8")
    for rel in ["docs/BrandResBW/standards.pdf", "docs/Oracle/manual.pdf", "docs/Oracle/start.md",
                "docs/Oracle/logo.png", "docs/users/rob/mine.pdf"]:
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x")
    return root


def test_plan(microjames):
    plan = migrate_microjames.build_plan(microjames, admin_users={"admin"})
    assert [(u, r) for u, _, r in plan.users] == [("admin", "admin"), ("james", "user"), ("rob", "admin")]
    assert plan.libraries == ["BrandResBW", "Oracle"]
    assert sorted(plan.grants) == [("james", "BrandResBW"), ("rob", "BrandResBW"), ("rob", "Oracle")]
    assert plan.file_counts["library:Oracle"] == (2, 1)  # .pdf + .md supported, .png not
    assert plan.file_counts["inbox:rob"] == (1, 0)
    warnings = " ".join(plan.warnings)
    assert "bad name" in warnings and "ghost" in warnings and "private inbox" in warnings


def test_import_files_queues_supported_documents(microjames, conn, settings, monkeypatch):
    sys.path.insert(0, str(Path(__file__).parent))
    from pdfgen import make_pdf

    for rel in ["docs/BrandResBW/standards.pdf", "docs/Oracle/manual.pdf", "docs/users/rob/mine.pdf"]:
        (microjames / rel).write_bytes(make_pdf([[f"Text of {rel}"]]))
    (microjames / "docs/Oracle/start.md").write_text("# Start\n\nHello.", encoding="utf-8")
    plan = migrate_microjames.build_plan(microjames, admin_users={"admin"})
    migrate_microjames.apply_plan(conn, plan)
    migrate_microjames.import_files(conn, settings, plan)
    migrate_microjames.import_files(conn, settings, plan)  # repeat: nothing duplicated

    rows = conn.execute(
        "SELECT c.name, f.display_name FROM files f JOIN collections c ON c.id = f.collection_id "
        "ORDER BY c.name, f.display_name").fetchall()
    assert [tuple(r) for r in rows] == [
        ("BrandResBW", "standards.pdf"), ("Oracle", "logo.png"), ("Oracle", "manual.pdf"),
        ("Oracle", "start.md"), ("rob's inbox", "mine.pdf")]
    # Searchable documents are queued; the .png is kept but not indexed.
    assert conn.execute("SELECT count(*) FROM jobs WHERE status = 'queued'").fetchone()[0] == 4
    assert conn.execute("SELECT status FROM files WHERE display_name = 'logo.png'").fetchone()[0] == "stored"


def test_apply_keeps_passwords_and_is_idempotent(microjames, conn):
    plan = migrate_microjames.build_plan(microjames, admin_users={"admin"})
    migrate_microjames.apply_plan(conn, plan)
    second = migrate_microjames.apply_plan(conn, plan)
    assert any("already exists" in line for line in second)

    rob = users.authenticate(conn, "rob", "rob-pass")
    assert rob is not None and rob["role"] == "admin"
    assert users.authenticate(conn, "admin", "admin-pass")["role"] == "admin"

    james = users.get_user(conn, "james")
    names = {c["name"] for c in permissions.readable_collections(conn, james["id"])}
    assert names == {"james's inbox", "BrandResBW"}
    assert permissions.private_collection_id(conn, rob["id"]) not in permissions.readable_collection_ids(conn, james["id"])
