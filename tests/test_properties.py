import io

import pytest
from fastapi.testclient import TestClient

from fakes import KeywordEmbedder
from mnemerynne import api_tokens, fs, permissions, users, worker
from mnemerynne.editing import EditError
from mnemerynne.ingest import store
from mnemerynne.mcp_server import Tools
from mnemerynne.web.app import Services, create_app


@pytest.fixture
def embedder():
    return KeywordEmbedder()


@pytest.fixture
def world(conn, settings, embedder):
    """alice: notes.md at the top of her inbox and plan.md in Plans/Deep; Oracle: alice writes, boss is admin."""
    alice = users.create_user(conn, "alice", "password123")
    boss = users.create_user(conn, "boss", "password123", role="admin")
    inbox = permissions.private_collection_id(conn, alice)
    oracle = permissions.create_library(conn, "Oracle")
    permissions.grant_user(conn, oracle, alice, can_write=True)
    permissions.grant_user(conn, oracle, boss)
    access = fs.Access.of(conn, alice)
    plans = fs.create_folder(conn, access, inbox, None, "Plans")
    deep = fs.create_folder(conn, fs.Access.of(conn, alice), inbox, plans, "Deep")

    def add(data, name, collection, folder_id=None):
        return store.add_file(conn, settings, store.receive(io.BytesIO(data), name, settings), collection, alice,
                              folder_id=folder_id)[0]

    ids = {"notes": add(b"# Notes\n\nThe pool code is 4411.\n", "notes.md", inbox),
           "plan": add(b"# Plan\n\nThe budget is 777.\n", "plan.md", inbox, deep),
           "opera": add(b"# Opera\n\nRun the night audit after midnight.\n", "opera.md", oracle)}
    while worker.run_one(conn, settings, embedder):
        pass
    return {"alice": alice, "boss": boss, "inbox": inbox, "oracle": oracle, "plans": plans, "deep": deep, **ids}


@pytest.fixture
def client(settings, conn, embedder, world):
    with TestClient(create_app(settings, Services(llm=None, embedder=embedder))) as c:
        yield c


def _login(client, name):
    client.post("/logout")
    client.post("/login", data={"username": name, "password": "password123"})


def _status(conn, file_id):
    return conn.execute("SELECT status FROM files WHERE id = ?", (file_id,)).fetchone()[0]


# ------------------------------------------------------------------ properties
def test_file_and_folder_properties(client, conn, world):
    _login(client, "alice")
    p = client.get(f"/api/properties?type=file&id={world['plan']}").json()
    assert p["name"] == "plan.md" and p["where"] == "My files / Plans / Deep"
    assert p["search"]["status"] == "ready" and p["search"]["chunks"] >= 1
    assert p["search"]["embedded"] == p["search"]["chunks"]
    assert p["can_reembed"] is True and p["claude_connected"] is False
    assert p["claude_see"]["on"] is True
    assert p["ai_edit"] == {"on": False, "can_change": True, "shared": True, "why": None}   # her inbox: she may allow it
    f = client.get(f"/api/properties?type=folder&id={world['plans']}").json()
    assert (f["files"], f["folders"], f["where"]) == (1, 1, "My files")
    assert f["search"]["by_status"] == {"ready": 1}
    assert client.get("/api/properties?type=file&id=99999").status_code == 404


# ------------------------------------------------------------------ re-embedding
def test_reembed_in_your_own_inbox(client, conn, world):
    _login(client, "alice")
    assert client.get(f"/api/browse?collection_id={world['inbox']}").json()["can_reembed"] is True
    r = client.post("/api/reembed", json={"collection_id": world["inbox"],
                                          "items": [{"type": "folder", "id": world["plans"]}]})
    assert r.json() == {"queued": 1} and _status(conn, world["plan"]) == "queued" and _status(conn, world["notes"]) == "ready"
    # Already queued: nothing more to do. The whole inbox: the rest.
    assert client.post("/api/reembed", json={"collection_id": world["inbox"]}).json() == {"queued": 1}
    assert _status(conn, world["notes"]) == "queued"
    assert conn.execute("SELECT count(*) FROM jobs WHERE kind = 'ingest' AND status = 'queued'").fetchone()[0] == 2


def test_only_admins_reembed_libraries(client, conn, world, settings, embedder):
    _login(client, "alice")                            # writes Oracle, but isn't an admin
    assert client.get(f"/api/browse?collection_id={world['oracle']}").json()["can_reembed"] is False
    assert client.post("/api/reembed", json={"collection_id": world["oracle"]}).status_code == 403
    _login(client, "boss")
    assert client.get(f"/api/browse?collection_id={world['oracle']}").json()["can_reembed"] is True
    assert client.post("/api/reembed", json={"collection_id": world["oracle"]}).json() == {"queued": 1}
    assert client.post("/api/reembed", json={"collection_id": world["inbox"]}).status_code == 404   # not his
    while worker.run_one(conn, settings, embedder):
        pass
    assert _status(conn, world["opera"]) == "ready"     # embedded again


# ------------------------------------------------------------------ AI edit
def _edit(tools, conn, caller, file_id, text):
    opened, _ = tools.open_file(conn, caller, file_id)
    return tools.edit_text(conn, caller, file_id, opened["version"], content=text)


def test_owners_allow_ai_edits_in_their_inbox(client, conn, settings, embedder, world):
    claude = Tools(settings, lambda: embedder)
    caller = api_tokens.verify(conn, api_tokens.create(conn, world["alice"], "laptop", can_edit=True)[1])
    with pytest.raises(EditError, match="outside the Agents folder"):
        _edit(claude, conn, caller, world["plan"], "# Plan\n\nThe budget is 800.\n")
    _login(client, "alice")
    rows = client.get(f"/api/browse?collection_id={world['inbox']}").json()
    assert rows["ai"] is True                                              # Claude is connected
    plans = next(f for f in rows["folders"] if f["name"] == "Plans")
    assert plans["ai_edit"]["on"] is False and plans["ai_edit"]["can_change"] is True
    assert client.put(f"/api/folders/{world['plans']}/ai-edit", json={"allowed": True}).status_code == 200
    _edit(claude, conn, caller, world["plan"], "# Plan\n\nThe budget is 800.\n")   # Plans/Deep: allowed now
    inner = client.get(f"/api/browse?collection_id={world['inbox']}&folder_id={world['deep']}").json()
    assert inner["files"][0]["ai_edit"]["on"] is True
    # A file can be kept read-only inside an allowed folder.
    assert client.put(f"/api/files/{world['plan']}/ai-edit", json={"allowed": False}).status_code == 200
    with pytest.raises(EditError, match="read-only for the AI"):
        _edit(claude, conn, caller, world["plan"], "# Plan\n\nThe budget is 900.\n")
    # Making a folder read-only fixes everything inside it.
    client.put(f"/api/files/{world['plan']}/ai-edit", json={"allowed": True})
    client.put(f"/api/folders/{world['plans']}/ai-edit", json={"allowed": False})
    inner = client.get(f"/api/browse?collection_id={world['inbox']}&folder_id={world['deep']}").json()
    assert inner["folders"] == [] and inner["files"][0]["ai_edit"] == {
        "on": False, "can_change": False, "shared": False, "why": "“Plans” is read-only for the AI (AI edit is off for it in Files)."}
    with pytest.raises(EditError, match="read-only for the AI"):
        _edit(claude, conn, caller, world["plan"], "# Plan\n\nThe budget is 900.\n")


def test_libraries_only_get_read_only_switches(client, conn, settings, embedder, world):
    """In a library the switch can't allow what an admin hasn't; where an admin has, it can make things read-only."""
    api_tokens.create(conn, world["alice"], "laptop")
    _login(client, "alice")
    p = client.get(f"/api/properties?type=file&id={world['opera']}").json()
    assert p["ai_edit"]["shared"] is False and p["ai_edit"]["can_change"] is False and "admin" in p["ai_edit"]["why"]
    client.put(f"/api/files/{world['opera']}/ai-edit", json={"allowed": True})     # stored, but changes nothing
    claude = Tools(settings, lambda: embedder)
    caller = api_tokens.verify(conn, api_tokens.create(conn, world["alice"], "desk", can_edit=True)[1])
    with pytest.raises(EditError, match="this library"):
        _edit(claude, conn, caller, world["opera"], "# Opera\n\nChanged.\n")
    conn.execute("UPDATE collections SET claude_edit = 1 WHERE id = ?", (world["oracle"],))
    _edit(claude, conn, caller, world["opera"], "# Opera\n\nChanged.\n")
    assert client.put(f"/api/files/{world['opera']}/ai-edit", json={"allowed": False}).status_code == 200
    with pytest.raises(EditError, match="read-only for the AI"):
        _edit(claude, conn, caller, world["opera"], "# Opera\n\nAgain.\n")
    _login(client, "boss")                                                             # reads only
    assert client.put(f"/api/files/{world['opera']}/ai-edit", json={"allowed": True}).status_code == 403
