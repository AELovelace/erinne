import io

import pytest
from fastapi.testclient import TestClient
from mcp.server.mcpserver.exceptions import ToolError

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
    """alice: a note at the top of her inbox and a plan in a folder; Oracle: alice reads, bob writes."""
    alice = users.create_user(conn, "alice", "password123")
    bob = users.create_user(conn, "bob", "password123")
    inbox = permissions.private_collection_id(conn, alice)
    oracle = permissions.create_library(conn, "Oracle")
    permissions.grant_user(conn, oracle, alice)
    permissions.grant_user(conn, oracle, bob, can_write=True)
    folder = fs.create_folder(conn, fs.Access.of(conn, alice), inbox, None, "Plans")

    def add(data, name, collection, folder_id=None):
        return store.add_file(conn, settings, store.receive(io.BytesIO(data), name, settings), collection, None,
                              folder_id=folder_id)[0]

    note = add(b"# Secret\n\nAlice's secret pool code is 4411.\n", "alice.md", inbox)
    plan = add(b"# Budget\n\nThe budget plan number is 777.\n", "plan.md", inbox, folder)
    opera = add(b"# Night audit\n\nRun the night audit after midnight.\n", "opera.md", oracle)
    while worker.run_one(conn, settings, embedder):
        pass
    return {"alice": alice, "bob": bob, "inbox": inbox, "oracle": oracle, "folder": folder,
            "note": note, "plan": plan, "opera": opera}


def _caller(conn, user_id):
    return api_tokens.verify(conn, api_tokens.create(conn, user_id, "test")[1])


def _texts(out):
    return " ".join(r["text"] for r in out["results"])


@pytest.fixture
def client(settings, conn, embedder, world):
    with TestClient(create_app(settings, Services(llm=None, embedder=embedder))) as c:
        yield c


def _login(client, name):
    client.post("/logout")
    client.post("/login", data={"username": name, "password": "password123"})


def _rows(client, cid, fid=None):
    data = client.get(f"/api/browse?collection_id={cid}" + (f"&folder_id={fid}" if fid else "")).json()
    return data, {r["name"]: r for r in data["folders"] + data["files"]}


def test_column_only_with_claude_connected(client, conn, world):
    _login(client, "alice")
    data, rows = _rows(client, world["inbox"])
    assert data["claude"] is False and "claude" not in rows["alice.md"]
    api_tokens.create(conn, world["alice"], "laptop")
    data, rows = _rows(client, world["inbox"])
    assert data["claude"] is True
    assert rows["alice.md"]["claude"] == {"on": True, "can_change": True, "shared": True}
    assert rows["Plans"]["claude"] == {"on": True, "can_change": True, "shared": True}


def test_a_hidden_file_is_gone_for_claude_only(client, conn, settings, embedder, world):
    alice = _caller(conn, world["alice"])
    claude, erynne = Tools(settings, lambda: embedder), Tools(settings, lambda: embedder, lambda: None, mode="erynne")
    out, _ = claude.search_documents(conn, alice, "secret pool code")
    chunk_id = out["results"][0]["chunk_id"]
    _login(client, "alice")
    assert client.put(f"/api/files/{world['note']}/claude", json={"visible": False}).status_code == 200

    assert "4411" not in _texts(claude.search_documents(conn, alice, "secret pool code")[0])
    with pytest.raises(ToolError):
        claude.read_passage(conn, alice, chunk_id)
    with pytest.raises((ToolError, EditError)):
        claude.open_file(conn, alice, world["note"])
    assert "alice.md" not in [f["name"] for f in claude.browse_folder(conn, alice, "My inbox")[0]["files"]]
    assert claude.find_files(conn, alice, "alice")[0]["files"] == []
    # Erynne in chat, and the person in Files, still see it.
    assert "4411" in _texts(erynne.search_documents(conn, alice, "secret pool code")[0])
    assert _rows(client, world["inbox"])[1]["alice.md"]["claude"]["on"] is False
    # Chunks and vectors carry the flag, so the vector search filters it too.
    assert {r[0] for r in conn.execute("SELECT claude_hidden FROM chunks WHERE file_id = ?", (world["note"],))} == {1}
    assert {r[0] for r in conn.execute("SELECT v.claude_hidden FROM chunk_vec v JOIN chunks c ON c.id = v.rowid "
                                       "WHERE c.file_id = ?", (world["note"],))} == {1}

    assert client.put(f"/api/files/{world['note']}/claude", json={"visible": True}).status_code == 200
    assert "4411" in _texts(claude.search_documents(conn, alice, "secret pool code")[0])


def test_a_hidden_folder_hides_everything_under_it(client, conn, settings, embedder, world):
    alice = _caller(conn, world["alice"])
    claude = Tools(settings, lambda: embedder)
    assert "777" in _texts(claude.search_documents(conn, alice, "budget plan number")[0])
    _login(client, "alice")
    assert client.put(f"/api/folders/{world['folder']}/claude", json={"visible": False}).status_code == 200
    assert "777" not in _texts(claude.search_documents(conn, alice, "budget plan number")[0])
    with pytest.raises(ToolError):
        claude.browse_folder(conn, alice, "My inbox", "Plans")
    assert _rows(client, world["inbox"])[1]["Plans"]["claude"] == {"on": False, "can_change": True, "shared": True}
    # Inside it, the boxes are unticked and fixed: the folder decides.
    assert _rows(client, world["inbox"], world["folder"])[1]["plan.md"]["claude"] == {
        "on": False, "can_change": False, "shared": False}
    with pytest.raises((ToolError, EditError)):
        claude.open_file(conn, alice, world["plan"])
    # A file in a folder that's hidden can't be shown on its own.
    client.put(f"/api/files/{world['plan']}/claude", json={"visible": True})
    assert "777" not in _texts(claude.search_documents(conn, alice, "budget plan number")[0])


def test_only_people_who_can_change_the_place_can_toggle(client, conn, settings, embedder, world):
    api_tokens.create(conn, world["alice"], "laptop")
    _login(client, "alice")                       # reads Oracle, can't change it
    assert _rows(client, world["oracle"])[1]["opera.md"]["claude"] == {"on": True, "can_change": False, "shared": True}
    assert client.put(f"/api/files/{world['opera']}/claude", json={"visible": False}).status_code == 403
    _login(client, "bob")                         # writes Oracle
    assert client.put(f"/api/files/{world['opera']}/claude", json={"visible": False}).status_code == 200
    out, _ = Tools(settings, lambda: embedder).search_documents(conn, _caller(conn, world["alice"]), "night audit")
    assert "midnight" not in _texts(out)          # It's a property of the file: hidden for everyone's Claude.
    _login(client, "alice")
    assert client.put(f"/api/files/{world['note'] + 999}/claude", json={"visible": False}).status_code == 404


def test_nothing_beyond_what_admins_share(client, conn, world):
    """The box only takes things away: with the inbox not shared with Claude, it's unticked and fixed."""
    permissions.set_claude_enabled(conn, world["inbox"], False, world["bob"])
    api_tokens.create(conn, world["alice"], "laptop")
    _login(client, "alice")
    assert _rows(client, world["inbox"])[1]["alice.md"]["claude"] == {"on": False, "can_change": False, "shared": False}
