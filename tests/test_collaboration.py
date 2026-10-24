"""Claude working with files and with other people's agents: rename, move, delete (proposed), what changed,
and comments."""

import io
from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient
from mcp.server.mcpserver.exceptions import ToolError

from fakes import FakeEmbedder
from mnemerynne import activity, api_tokens, editing, fs, permissions, users
from mnemerynne.ingest import store
from mnemerynne.mcp_server import Tools
from mnemerynne.web.app import Services, create_app


@pytest.fixture
def world(conn, settings):
    """alice writes in IT-OPS (AI edits allowed there) and Brand (not allowed); rob writes in IT-OPS too."""
    alice = users.create_user(conn, "alice", "password123")
    rob = users.create_user(conn, "rob", "password123")
    ops = permissions.create_library(conn, "IT-OPS")
    brand = permissions.create_library(conn, "Brand")
    for lib in (ops, brand):
        permissions.grant_user(conn, lib, alice, can_write=True)
    permissions.grant_user(conn, ops, rob, can_write=True)
    conn.execute("UPDATE collections SET claude_edit = 1 WHERE id = ?", (ops,))
    return {"alice": alice, "rob": rob, "ops": ops, "brand": brand,
            "inbox": permissions.private_collection_id(conn, alice)}


@pytest.fixture
def tools(settings):
    return Tools(settings, lambda: FakeEmbedder())


def _caller(conn, user_id, can_edit=True):
    return api_tokens.verify(conn, api_tokens.create(conn, user_id, "t", can_edit=can_edit)[1])


def _add(conn, settings, collection, name, data=b"# Note\n\nhello\n", folder=None, user=None):
    return store.add_file(conn, settings, store.receive(io.BytesIO(data), name, settings), collection, user,
                          folder_id=folder)[0]


@contextmanager
def _web(settings, name):
    with TestClient(create_app(settings, Services(llm=None, embedder=FakeEmbedder()))) as c:
        c.post("/login", data={"username": name, "password": "password123"})
        yield c


# ------------------------------------------------------------ rename and move
def test_rename_where_claude_may_edit(conn, settings, world, tools):
    alice = _caller(conn, world["alice"])
    note = tools.create_file(conn, alice, "draft.md", content="hi", library="IT-OPS")[0]["file_id"]
    assert tools.rename_item(conn, alice, "Handoff for Rob.md", file_id=note)[0]["name"] == "Handoff for Rob.md"
    with pytest.raises(editing.EditError, match="Keep the extension"):
        tools.rename_item(conn, alice, "Handoff.docx", file_id=note)
    folder = tools.create_folder(conn, alice, "Agents", library="IT-OPS")[0]["folder_id"]
    assert tools.rename_item(conn, alice, "Agent notes", folder_id=folder)[0]["name"] == "Agent notes"

    agents = editing.agents_folder(conn, world["alice"])
    with pytest.raises(editing.EditForbidden, match="Agents folder; it stays"):
        tools.rename_item(conn, alice, "Mine", folder_id=agents)
    elsewhere = _add(conn, settings, world["brand"], "logo.md")
    with pytest.raises(editing.EditForbidden, match="this library"):
        tools.rename_item(conn, alice, "x.md", file_id=elsewhere)
    with pytest.raises(ToolError, match="either file_id or folder_id"):
        tools.rename_item(conn, alice, "x.md")


def test_move_between_places_claude_may_edit(conn, settings, world, tools):
    alice = _caller(conn, world["alice"])
    note = tools.create_file(conn, alice, "note.md", content="for rob")[0]["file_id"]    # Agents folder
    shared = tools.create_folder(conn, alice, "Agents", library="IT-OPS")[0]
    moved = tools.move_item(conn, alice, file_id=note, library="IT-OPS", folder="Agents")[0]
    assert moved["where"] == "IT-OPS / Agents" and moved["name"] == "note.md"
    assert fs.file_row(conn, note)["folder_id"] == shared["folder_id"]
    # Back to the Agents folder (no place given); a name already there gets " (2)".
    tools.create_file(conn, alice, "note.md", content="another")
    assert tools.move_item(conn, alice, file_id=note)[0]["name"] == "note (2).md"
    with pytest.raises(editing.EditForbidden, match="this library"):
        tools.move_item(conn, alice, file_id=note, library="Brand")                        # AI edits off there

    # A folder with something read-only for the AI inside stays put.
    folder = tools.create_folder(conn, alice, "Locked")[0]["folder_id"]
    inside = tools.create_file(conn, alice, "keep.md", content="x", library="File box", folder="Agents/Locked")
    conn.execute("UPDATE files SET ai_no_edit = 1 WHERE id = ?", (inside[0]["file_id"],))
    with pytest.raises(editing.EditForbidden, match="read-only for the AI"):
        tools.move_item(conn, alice, folder_id=folder, library="IT-OPS")


# --------------------------------------------------------------------- delete
def test_delete_is_only_proposed_and_the_person_bins_it(conn, settings, world, tools):
    alice = _caller(conn, world["alice"])
    note = tools.create_file(conn, alice, "old.md", content="stale", library="IT-OPS")[0]["file_id"]
    out, _ = tools.delete_item(conn, alice, file_id=note)
    assert out["status"] == "proposed" and out["approve"] == f"/proposals#p{out['proposal']}"
    assert fs.file_row(conn, note)["deleted_at"] is None                                   # nothing happened yet
    assert tools.delete_item(conn, alice, file_id=note)[0]["proposal"] == out["proposal"]  # no duplicates

    with _web(settings, "alice") as web:
        card = web.get("/api/agent/proposals").json()["proposals"][0]
        assert card["kind"] == "delete" and card["file"] == "old.md" and "recycle bin" in card["changes"][0]
        applied = web.post(f"/api/agent/proposals/{out['proposal']}/apply")
        assert applied.status_code == 200 and applied.json()["status"] == "applied"
    assert fs.file_row(conn, note)["deleted_at"] is not None                               # in the bin, restorable

    folder = tools.create_folder(conn, alice, "Old stuff", library="IT-OPS")[0]["folder_id"]
    tools.create_file(conn, alice, "a.md", content="a", library="IT-OPS", folder="Old stuff")
    assert "and the 1 file(s)" in conn.execute(
        "SELECT changes FROM agent_proposals WHERE id = ?",
        (tools.delete_item(conn, alice, folder_id=folder)[0]["proposal"],)).fetchone()[0]
    with pytest.raises(editing.EditForbidden):
        tools.delete_item(conn, alice, file_id=_add(conn, settings, world["brand"], "brand.md"))


# --------------------------------------------------------------- what changed
def test_what_changed_and_by_whom(conn, settings, world, tools):
    alice, rob = _caller(conn, world["alice"]), _caller(conn, world["rob"])
    start = activity.parse_since("2000-01-01")
    note = tools.create_file(conn, alice, "handoff.md", content="question for rob", library="IT-OPS")[0]["file_id"]
    tools.add_comment(conn, rob, note, "Answered below.")
    conn.execute("UPDATE files SET created_at = datetime('now', '-1 minute') WHERE id = ?", (note,))   # made earlier
    opened = tools.open_file(conn, rob, note)[0]
    tools.edit_text(conn, rob, note, opened["version"], edits=[{"old": "question", "new": "answered question"}])
    binned = _add(conn, settings, world["ops"], "gone.md", user=world["alice"])
    fs.trash(conn, fs.Access.of(conn, world["alice"]), [fs.Item("file", binned)])

    out, _ = tools.list_changes(conn, alice, "IT-OPS", since="2000-01-01")
    files = {f["file"]: f for f in out["files"]}
    assert out["since"] == start + " UTC"
    assert files["handoff.md"]["added"]["by"] == "alice"
    assert files["handoff.md"]["changed"]["by"] == "Claude for rob" and files["handoff.md"]["changed"]["ai_edits"] == 1
    assert files["handoff.md"]["comments"] == {"new": 1, "last_at": files["handoff.md"]["comments"]["last_at"],
                                               "last_by": "Claude for rob"}
    assert files["gone.md"]["binned"]["by"] == "alice"
    assert files["handoff.md"]["link"].startswith("/files/")

    # A move or rename in Files isn't credited to Claude.
    fs.rename(conn, fs.Access.of(conn, world["alice"]), fs.Item("file", note), "handoff-v2.md")
    conn.execute("UPDATE files SET updated_at = datetime('now', '+5 seconds') WHERE id = ?", (note,))
    changed = {f["file"]: f for f in tools.list_changes(conn, alice, "IT-OPS", since="2000-01-01")[0]["files"]}
    assert changed["handoff-v2.md"]["changed"]["by"].startswith("someone in Files")

    assert tools.list_changes(conn, alice, "IT-OPS", since="2999-01-01")[0]["files"] == []
    with pytest.raises(ToolError, match="UTC date or time"):
        tools.list_changes(conn, alice, "IT-OPS", since="yesterday-ish")
    assert activity.parse_since("2026-10-24T14:30:00Z") == "2026-10-24 14:30:00"
    assert activity.parse_since("2026-10-24T07:30:00-07:00") == "2026-10-24 14:30:00"


# ------------------------------------------------------------------- comments
def test_comments_between_agents_and_people(conn, settings, world, tools):
    alice, rob = _caller(conn, world["alice"]), _caller(conn, world["rob"])
    note = tools.create_file(conn, alice, "plan.md", content="plan", library="IT-OPS")[0]["file_id"]
    first = tools.add_comment(conn, alice, note, "Rob, can you check the numbers?")[0]["comment_id"]
    tools.add_comment(conn, rob, note, "Checked: they match.", reply_to=first)
    thread = tools.list_comments(conn, alice, note)[0]["comments"]
    assert [(c["by"], c.get("reply_to")) for c in thread] == [("Claude for alice", None), ("Claude for rob", first)]

    with pytest.raises(ToolError, match="no such comment"):
        tools.add_comment(conn, rob, note, "x", reply_to=first + 99)
    with pytest.raises(ToolError, match="can only search"):
        tools.add_comment(conn, _caller(conn, world["rob"], can_edit=False), note, "hi")
    private = tools.create_file(conn, alice, "mine.md", content="secret")[0]["file_id"]
    with pytest.raises(ToolError, match="No such file"):
        tools.list_comments(conn, rob, private)                                            # alice's File box

    with _web(settings, "rob") as web:
        assert [c["by"] for c in web.get(f"/api/files/{note}/comments").json()["comments"]] == [
            "Claude for alice", "Claude for rob"]
        added = web.post(f"/api/files/{note}/comments", json={"text": "Thanks both", "reply_to": first})
        assert added.status_code == 201 and added.json()["comments"][-1]["by"] == "rob"
        assert web.post(f"/api/files/{note}/comments", json={"text": "  "}).status_code == 422
        assert web.get(f"/api/files/{private}/comments").status_code == 404
