"""The connector's 1.0 rules: every item says whether Claude may edit it, Claude can ask for access where
it may not, names match forgivingly, ids work wherever places are named, refusals carry a code, and
list_changes reports folders, restores and Claude's own renames and moves."""

import io
import json
from contextlib import contextmanager
from pathlib import Path

import anyio
import pytest
from fastapi.testclient import TestClient

from fakes import FakeEmbedder, KeywordEmbedder
from mnemerynne import api_tokens, editing, fs, permissions, users, worker
from mnemerynne.ingest import store
from mnemerynne.mcp_server import Refused, Tools, build_server, error_code
from mnemerynne.web.app import Services, create_app


@pytest.fixture
def world(conn, settings):
    """alice writes in IT-OPS (AI edits allowed there) and Brand (not allowed); rob only reads Docs."""
    alice = users.create_user(conn, "alice", "password123")
    rob = users.create_user(conn, "rob", "password123")
    ops = permissions.create_library(conn, "IT-OPS")
    brand = permissions.create_library(conn, "Brand")
    docs = permissions.create_library(conn, "Docs")
    for lib in (ops, brand, docs):
        permissions.grant_user(conn, lib, alice, can_write=True)
    permissions.grant_user(conn, docs, rob)
    conn.execute("UPDATE collections SET claude_edit = 1 WHERE id = ?", (ops,))
    return {"alice": alice, "rob": rob, "ops": ops, "brand": brand, "docs": docs,
            "inbox": permissions.private_collection_id(conn, alice)}


@pytest.fixture
def tools(settings):
    return Tools(settings, lambda: FakeEmbedder())


def _caller(conn, user_id, can_edit=True):
    return api_tokens.verify(conn, api_tokens.create(conn, user_id, "t", can_edit=can_edit)[1])


def _add(conn, settings, collection, name, data=b"# Note\n\nhello\n", folder=None, user=None):
    return store.add_file(conn, settings, store.receive(io.BytesIO(data), name, settings), collection, user,
                          folder_id=folder)[0]


def _folder(conn, user_id, collection, name, parent=None):
    return fs.create_folder(conn, fs.Access.of(conn, user_id), collection, parent, name)


@contextmanager
def _web(settings, name):
    with TestClient(create_app(settings, Services(llm=None, embedder=FakeEmbedder()))) as c:
        c.post("/login", data={"username": name, "password": "password123"})
        yield c


# ----------------------------------------------------------------- editable
def test_every_listed_item_says_whether_claude_may_edit_it(conn, settings, world, tools):
    alice = _caller(conn, world["alice"])
    editing.agents_folder(conn, world["alice"])                    # the server makes it on the first call
    tools.create_file(conn, alice, "runbook.md", content="x", library="IT-OPS")
    _add(conn, settings, world["brand"], "logo.md")
    _folder(conn, world["alice"], world["brand"], "Fonts")

    ops = tools.browse_folder(conn, alice, "IT-OPS")[0]
    assert ops["editable"] and ops["library_id"] == world["ops"]
    assert [f["editable"] for f in ops["files"]] == [True]
    brand = tools.browse_folder(conn, alice, "Brand")[0]
    assert not brand["editable"] and not brand["files"][0]["editable"] and not brand["folders"][0]["editable"]
    inbox = tools.browse_folder(conn, alice, "File box")[0]
    agents = next(f for f in inbox["folders"] if f["name"] == "Agents")
    assert agents["editable"] and agents["folder_id"] == agents["id"]
    assert not inbox["editable"]                                    # the File box root is outside Agents

    found = tools.find_files(conn, alice, "logo")[0]["files"][0]
    assert found["editable"] is False
    assert tools.find_files(conn, alice, "runbook")[0]["files"][0]["editable"] is True

    # A search-only token edits nothing, so nothing is editable.
    reader = _caller(conn, world["alice"], can_edit=False)
    assert not any(f["editable"] for f in tools.browse_folder(conn, reader, "IT-OPS")[0]["files"])


def test_search_results_carry_editable(conn, settings, world):
    embedder = KeywordEmbedder()
    _add(conn, settings, world["ops"], "audit.md", b"# Night audit\n\nRun the audit after midnight.\n")
    _add(conn, settings, world["brand"], "brand-audit.md", b"# Brand audit\n\nThe yearly brand audit.\n")
    while worker.run_one(conn, settings, embedder):
        pass
    tools = Tools(settings, lambda: embedder)
    results = tools.search_documents(conn, _caller(conn, world["alice"]), "audit")[0]["results"]
    assert {r["file"]: r["editable"] for r in results} == {"audit.md": True, "brand-audit.md": False}


# ------------------------------------------------------------ access requests
def test_request_edit_access_in_the_file_box_is_applied_by_the_person(conn, settings, world, tools):
    alice = _caller(conn, world["alice"])
    notes = _folder(conn, world["alice"], world["inbox"], "Notes")
    assert not tools.browse_folder(conn, alice, "File box", "Notes")[0]["editable"]
    with pytest.raises(editing.EditForbidden):
        tools.create_file(conn, alice, "a.md", content="x", library="File box", folder="Notes")

    out = tools.request_edit_access(conn, alice, folder_id=notes)[0]
    assert out["status"] == "proposed" and out["approve"] == f"/proposals#p{out['proposal']}"
    assert tools.request_edit_access(conn, alice, library="File box", folder="Notes")[0]["proposal"] == out["proposal"]
    assert not tools.browse_folder(conn, alice, "File box", "Notes")[0]["editable"]     # nothing changed yet

    with _web(settings, "alice") as web:
        card = next(p for p in web.get("/api/agent/proposals").json()["proposals"] if p["id"] == out["proposal"])
        assert card["kind"] == "access" and card["file"] == "Notes"
        applied = web.post(f"/api/agent/proposals/{out['proposal']}/apply")
        assert applied.status_code == 200 and applied.json()["status"] == "applied"
    assert tools.browse_folder(conn, alice, "File box", "Notes")[0]["editable"]
    assert tools.create_file(conn, alice, "a.md", content="x", folder=notes)[0]["where"] == "File box / Notes"
    assert tools.request_edit_access(conn, alice, folder_id=notes)[0] == {
        "editable": True, "where": "File box / Notes", "note": "You may already edit here; go ahead."}

    with pytest.raises(Refused, match="Give one of"):
        tools.request_edit_access(conn, alice)


def test_request_edit_access_clears_a_read_only_switch(conn, settings, world, tools):
    alice = _caller(conn, world["alice"])
    runbooks = _folder(conn, world["alice"], world["ops"], "Runbooks")
    note = tools.create_file(conn, alice, "dr.md", content="x", folder=runbooks)[0]["file_id"]
    fs.set_ai_edit(conn, fs.Access.of(conn, world["alice"]), fs.Item("folder", runbooks), False)
    assert not tools.find_files(conn, alice, "dr.md")[0]["files"][0]["editable"]

    out = tools.request_edit_access(conn, alice, file_id=note)[0]
    assert "“Runbooks”" in out["changes"][0]                       # the switch is on the folder above it
    with _web(settings, "alice") as web:
        assert web.post(f"/api/agent/proposals/{out['proposal']}/apply").status_code == 200
    assert tools.find_files(conn, alice, "dr.md")[0]["files"][0]["editable"]


def test_a_library_needs_an_admin(conn, settings, world, tools):
    alice = _caller(conn, world["alice"])
    out = tools.request_edit_access(conn, alice, library="Brand")[0]
    assert out["status"] == "awaiting_admin" and "approve" not in out and "Approvals" in out["note"]
    assert tools.request_edit_access(conn, alice, library="Brand")[0]["proposal"] == out["proposal"]   # not twice
    with _web(settings, "alice") as web:                            # hers to withdraw, not to apply
        assert web.post(f"/api/agent/proposals/{out['proposal']}/apply").status_code == 400
    assert conn.execute("SELECT claude_edit FROM collections WHERE id = ?", (world["brand"],)).fetchone()[0] == 0

    conn.execute("UPDATE users SET role = 'admin' WHERE id = ?", (world["alice"],))
    out = tools.request_edit_access(conn, alice, library="Brand")[0]
    assert out["status"] == "proposed"
    conn.execute("UPDATE users SET role = 'user' WHERE id = ?", (world["alice"],))
    with _web(settings, "alice") as web:                            # no longer an admin: refused
        assert web.post(f"/api/agent/proposals/{out['proposal']}/apply").status_code == 400
    conn.execute("UPDATE users SET role = 'admin' WHERE id = ?", (world["alice"],))
    with _web(settings, "alice") as web:
        assert web.post(f"/api/agent/proposals/{out['proposal']}/apply").status_code == 200
    assert conn.execute("SELECT claude_edit FROM collections WHERE id = ?", (world["brand"],)).fetchone()[0] == 1
    assert conn.execute("SELECT action FROM access_log ORDER BY id DESC").fetchone()[0] == "claude.edit.on"


def test_what_the_person_cant_give(conn, settings, world, tools):
    reader = _caller(conn, world["alice"], can_edit=False)
    out = tools.request_edit_access(conn, reader, library="IT-OPS")[0]
    assert out["status"] == "needs_person" and out["link"].endswith("/settings#claude")
    rob = _caller(conn, world["rob"])
    assert tools.request_edit_access(conn, rob, library="Docs")[0]["status"] == "not_possible"
    with pytest.raises(Refused) as refused:
        tools.request_edit_access(conn, rob, library="Brand")      # not his to see at all
    assert refused.value.code == "not_found"


# -------------------------------------------------------- names, ids and codes
def test_names_match_forgivingly(conn, settings, world, tools):
    alice = _caller(conn, world["alice"])
    night = _folder(conn, world["alice"], world["ops"], "Night-Audit")
    _add(conn, settings, world["ops"], "Night_Audit notes.md", folder=night)
    _add(conn, settings, world["ops"], "été.md")

    assert tools.browse_folder(conn, alice, "it ops")[0]["library"] == "IT-OPS"
    assert tools.browse_folder(conn, alice, "IT_OPS", "night audit")[0]["path"] == "Night-Audit"
    found = tools.find_files(conn, alice, "IT Ops")[0]
    assert found["libraries"] == [{"library": "IT-OPS", "id": world["ops"]}]
    found = tools.find_files(conn, alice, "night audit")[0]
    assert found["folders"][0]["folder_id"] == night
    assert found["files"][0]["name"] == "Night_Audit notes.md" and found["files"][0]["folder_id"] == night
    assert tools.find_files(conn, alice, "ÉTÉ")[0]["files"][0]["name"] == "été.md"    # not just ASCII case

    permissions.create_library(conn, "Front Office")
    permissions.create_library(conn, "FrontOffice")
    for name in ("Front Office", "FrontOffice"):
        permissions.grant_user(conn, permissions.get_library(conn, name)["id"], world["alice"])
    assert tools.browse_folder(conn, alice, "frontoffice")[0]["library"] == "FrontOffice"    # exact wins
    with pytest.raises(Refused, match="could be any of: Front Office, FrontOffice") as refused:
        tools.browse_folder(conn, alice, "front_office")
    assert refused.value.code == "invalid"


def test_folder_ids_work_wherever_places_are_named(conn, settings, world, tools):
    alice = _caller(conn, world["alice"])
    shared = _folder(conn, world["alice"], world["ops"], "Handoffs")
    note = tools.create_file(conn, alice, "n.md", content="x", folder=shared)[0]
    assert note["where"] == "IT-OPS / Handoffs"
    assert tools.browse_folder(conn, alice, world["ops"], shared)[0]["path"] == "Handoffs"
    assert tools.create_folder(conn, alice, "Inner", folder=shared)[0]["folder"] == "Handoffs/Inner"
    with pytest.raises(Refused) as refused:
        tools.browse_folder(conn, alice, "IT-OPS", 999_999)
    assert refused.value.code == "not_found"

    # A folder named with digits is that folder, even when another folder has that number as its id.
    numbered = _folder(conn, world["alice"], world["ops"], str(shared))
    assert tools.browse_folder(conn, alice, "IT-OPS", str(shared))[0]["folder_id"] == numbered
    assert tools.browse_folder(conn, alice, "IT-OPS", shared)[0]["folder_id"] == shared
    # Digits that name no folder still work as an id (older clients sent ids as text).
    assert tools.browse_folder(conn, alice, "IT-OPS", str(numbered))[0]["folder_id"] == numbered


def test_move_item_names_the_destination(conn, settings, world, tools):
    alice = _caller(conn, world["alice"])
    target = _folder(conn, world["alice"], world["ops"], "Archive")
    note = tools.create_file(conn, alice, "old.md", content="x")[0]["file_id"]      # Agents folder
    with pytest.raises(Refused, match="folder_id is a folder to move"):
        tools.move_item(conn, alice, file_id=note, folder_id=target)
    with pytest.raises(Refused, match="destination once"):
        tools.move_item(conn, alice, file_id=note, to_folder=target, library="IT-OPS")
    assert tools.move_item(conn, alice, file_id=note, to_folder=target)[0]["where"] == "IT-OPS / Archive"
    assert tools.move_item(conn, alice, file_id=note, to_library="File box", to_folder="Agents")[0]["where"] \
        == "File box / Agents"


def test_the_desktop_extension_lists_every_tool(settings):
    manifest = json.loads((Path(__file__).parents[1] / "clients/claude-desktop/manifest.json").read_text("utf-8"))
    server = build_server(settings, lambda: FakeEmbedder())
    assert {t["name"] for t in manifest["tools"]} == {t.name for t in anyio.run(server.list_tools)}
    relay = (Path(__file__).parents[1] / "clients/claude-desktop/server/index.js").read_text("utf-8")
    assert f'const VERSION = "{manifest["version"]}";' in relay


def test_refusals_carry_a_code():
    assert error_code(editing.EditNotFound("No such file.")) == "not_found"
    assert error_code(editing.EditForbidden("no")) == "not_allowed"
    assert error_code(editing.EditConflict("changed")) == "conflict"
    assert error_code(fs.Unavailable("the share is away")) == "unavailable"
    assert error_code(fs.Conflict("taken")) == "conflict"
    assert error_code(Refused("busy", "rate_limited")) == "rate_limited"
    assert error_code(editing.EditError("Keep the extension")) == "invalid"


# ------------------------------------------------------------------- changes
def test_list_changes_reports_folders_restores_and_claudes_moves(conn, settings, world, tools):
    alice = _caller(conn, world["alice"])
    person = fs.Access.of(conn, world["alice"])
    note = tools.create_file(conn, alice, "plan.md", content="x", library="IT-OPS")[0]["file_id"]
    conn.execute("UPDATE files SET created_at = datetime('now', '-1 minute'), "
                 "updated_at = datetime('now', '-1 minute') WHERE id = ?", (note,))
    tools.rename_item(conn, alice, "plan-v2.md", file_id=note)
    old = _folder(conn, world["alice"], world["ops"], "Old")
    _add(conn, settings, world["ops"], "inside.md", folder=old)
    fs.trash(conn, person, [fs.Item("folder", old)])
    gone = _add(conn, settings, world["ops"], "back.md", b"back", user=world["alice"])
    fs.trash(conn, person, [fs.Item("file", gone)])
    fs.restore(conn, person, [fs.Item("file", gone)])
    binned = _add(conn, settings, world["ops"], "binned.md", b"binned", user=world["alice"])
    fs.trash(conn, person, [fs.Item("file", binned)])

    out = tools.list_changes(conn, alice, "IT-OPS", since="2000-01-01")[0]
    files = {f["file"]: f for f in out["files"]}
    assert files["plan-v2.md"]["changed"]["by"] == "Claude for alice (a rename or move)"
    assert files["back.md"]["restored"]["by"] == "alice" and "link" in files["back.md"]
    assert files["binned.md"]["in_recycle_bin"] and "link" not in files["binned.md"]
    assert "inside.md" not in files                                # the folder is reported instead
    folders = {f["name"]: f for f in out["folders"]}
    assert folders["Old"]["binned"]["by"] == "alice" and folders["Old"]["in_recycle_bin"]

    fs.restore(conn, person, [fs.Item("folder", old)])
    folders = {f["name"]: f for f in tools.list_changes(conn, alice, "IT-OPS", since="2000-01-01")[0]["folders"]}
    assert folders["Old"]["restored"]["by"] == "alice" and folders["Old"]["link"].endswith(f"&f={old}")

    # Someone renaming it in Files later isn't Claude's doing.
    fs.rename(conn, person, fs.Item("file", note), "plan-v3.md")
    conn.execute("UPDATE files SET updated_at = datetime('now', '+5 seconds') WHERE id = ?", (note,))
    files = {f["file"]: f for f in tools.list_changes(conn, alice, "IT-OPS", since="2000-01-01")[0]["files"]}
    assert files["plan-v3.md"]["changed"]["by"].startswith("someone in Files")
