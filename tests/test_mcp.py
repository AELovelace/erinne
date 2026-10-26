import base64
import io
import json

import PIL.Image
import pytest
from fastapi.testclient import TestClient
from mcp.server.mcpserver.exceptions import ToolError

from mnemerynne import api_tokens, mcp_server, permissions, users, worker
from mnemerynne.ingest import store
from mnemerynne.mcp_server import Tools
from mnemerynne.web.app import Services, create_app
from fakes import KeywordEmbedder
from pdfgen import make_pdf

ACCEPT = "application/json, text/event-stream"


@pytest.fixture
def embedder():
    return KeywordEmbedder()


@pytest.fixture
def world(conn, settings, embedder):
    """alice and bob with private notes; an Oracle library granted to alice only."""
    alice = users.create_user(conn, "alice", "password123")
    bob = users.create_user(conn, "bob", "password123")
    oracle = permissions.create_library(conn, "Oracle")
    permissions.grant_user(conn, oracle, alice)

    def add(data, name, collection):
        store.add_file(conn, settings, store.receive(io.BytesIO(data), name, settings), collection, None)

    add(b"# Secret\n\nAlice's secret pool code is 4411.\n", "alice.md", permissions.private_collection_id(conn, alice))
    add(b"# Secret\n\nBob's secret pool code is 9922.\n", "bob.md", permissions.private_collection_id(conn, bob))
    add(make_pdf([["Night Audit", "Run the night audit after midnight to roll the date."],
                  ["Pool Hours", "The pool is open from 8am to 10pm."],
                  ["Parking", "Parking is in lot B for registered guests."]],
                 [("Night Audit", 0, 0), ("Pool Hours", 1, 0), ("Parking", 2, 0)]),
        "OPERA.pdf", oracle)
    while worker.run_one(conn, settings, embedder):
        pass
    return {"alice": alice, "bob": bob, "oracle": oracle}


def _caller(conn, user_id):
    return api_tokens.verify(conn, api_tokens.create(conn, user_id, "test")[1])


def _texts(out):
    return " ".join(r["text"] for r in out["results"])


# ----------------------------------------------------------------- tokens
def test_tokens_from_before_the_rename_still_work(conn, world):
    token_id, token = api_tokens.create(conn, world["alice"], "laptop")
    old = "erinne_" + token.removeprefix(api_tokens.PREFIX)
    conn.execute("UPDATE api_tokens SET token_hash = ? WHERE id = ?", (api_tokens._hash(old), token_id))
    assert api_tokens.verify(conn, old).user_id == world["alice"]


def test_tokens_expire_revoke_and_follow_the_user(conn, world):
    token_id, token = api_tokens.create(conn, world["alice"], "laptop", days=30)
    assert token.startswith("mnemerynne_") and len(token) > 40
    assert conn.execute("SELECT count(*) FROM api_tokens WHERE token_hash = ?", (token,)).fetchone()[0] == 0
    assert api_tokens.verify(conn, token).user_id == world["alice"]
    assert api_tokens.verify(conn, "mnemerynne_nope") is None and api_tokens.verify(conn, None) is None

    users.set_active(conn, "alice", False)
    assert api_tokens.verify(conn, token) is None
    users.set_active(conn, "alice", True)

    conn.execute("UPDATE api_tokens SET expires_at = datetime('now', '-1 minute') WHERE id = ?", (token_id,))
    assert api_tokens.verify(conn, token) is None
    _, other = api_tokens.create(conn, world["alice"], "desktop")
    assert not api_tokens.revoke(conn, token_id + 1, world["bob"])  # not bob's
    assert api_tokens.revoke_all(conn, world["alice"]) == 2           # expired ones are marked revoked too
    assert api_tokens.verify(conn, other) is None


# ------------------------------------------------------------------ tools
def test_search_is_limited_to_what_is_shared_with_claude(conn, settings, embedder, world):
    tools = Tools(settings, lambda: embedder)
    alice = _caller(conn, world["alice"])
    out, files = tools.search_documents(conn, alice, "night audit")
    assert out["found"] and out["results"][0]["file"] == "OPERA.pdf"
    top = out["results"][0]
    assert top["citation"].startswith("OPERA.pdf · p. 1") and top["link"].startswith("/files/")
    assert top["library"] == "Oracle" and files

    # Alice's own inbox is searchable, Bob's never is.
    assert "4411" in _texts(tools.search_documents(conn, alice, "secret pool code")[0])
    assert "9922" not in _texts(tools.search_documents(conn, alice, "secret pool code")[0])

    # Library switch off: the library disappears from Claude but not from the web app.
    permissions.set_claude_enabled(conn, world["oracle"], False, world["alice"])
    out, _ = tools.search_documents(conn, alice, "night audit")
    assert all(r["file"] != "OPERA.pdf" for r in out["results"])
    with pytest.raises(ToolError):
        tools.search_documents(conn, alice, "night audit", library="Oracle")
    assert world["oracle"] in permissions.readable_collection_ids(conn, world["alice"])

    # Inbox switch off too: nothing left.
    permissions.set_claude_enabled(conn, permissions.private_collection_id(conn, world["alice"]), False, world["alice"])
    out, files = tools.search_documents(conn, alice, "secret pool code")
    assert out["found"] is False and files == []


def test_scope_by_library_name_and_inbox(conn, settings, embedder, world):
    tools = Tools(settings, lambda: embedder)
    alice = _caller(conn, world["alice"])
    out, _ = tools.search_documents(conn, alice, "pool", library="oracle")
    assert {r["file"] for r in out["results"]} == {"OPERA.pdf"}
    out, _ = tools.search_documents(conn, alice, "pool", library="inbox")
    assert {r["file"] for r in out["results"]} == {"alice.md"}
    with pytest.raises(ToolError):
        tools.search_documents(conn, alice, "pool", folder="Somewhere")


def test_read_passage_and_browsing_respect_access(conn, settings, embedder, world):
    tools = Tools(settings, lambda: embedder)
    alice, bob = _caller(conn, world["alice"]), _caller(conn, world["bob"])
    hit = tools.search_documents(conn, alice, "pool hours")[0]["results"][0]
    out, files = tools.read_passage(conn, alice, hit["chunk_id"], around=1)
    assert hit["chunk_id"] in [p["chunk_id"] for p in out["passages"]] and files == [hit["file_id"]]
    with pytest.raises(ToolError):
        tools.read_passage(conn, bob, hit["chunk_id"])

    libs = tools.list_libraries(conn, alice)[0]["libraries"]
    assert [(l["library"], l["kind"]) for l in libs] == [("File box", "file box"), ("Oracle", "library")]
    assert [l["library"] for l in tools.list_libraries(conn, bob)[0]["libraries"]] == ["File box"]

    listing = tools.browse_folder(conn, alice, "Oracle")[0]
    assert [f["name"] for f in listing["files"]] == ["OPERA.pdf"] and listing["files"][0]["searchable"]
    with pytest.raises(ToolError):
        tools.browse_folder(conn, bob, "Oracle")
    assert [f["name"] for f in tools.find_files(conn, alice, "opera")[0]["files"]] == ["OPERA.pdf"]
    assert tools.find_files(conn, bob, "opera")[0]["files"] == []


# --------------------------------------------------------------- over HTTP
@pytest.fixture
def mcp_client(settings, conn, world, embedder):
    with TestClient(create_app(settings, Services(llm=None, embedder=embedder))) as client:
        yield client


def _rpc(client, token, method, params=None, id_=1):
    headers = {"Accept": ACCEPT, "Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return client.post("/mcp", headers=headers,
                       json={"jsonrpc": "2.0", "id": id_, "method": method, "params": params or {}})


def _initialize(client, token):
    return _rpc(client, token, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                              "clientInfo": {"name": "pytest", "version": "1"}})


def test_mcp_endpoint_end_to_end(mcp_client, conn, world):
    assert _initialize(mcp_client, None).status_code == 401
    assert _initialize(mcp_client, "mnemerynne_wrong").status_code == 401

    _, token = api_tokens.create(conn, world["alice"], "test")
    init = _initialize(mcp_client, token)
    assert init.status_code == 200, init.text
    assert init.json()["result"]["serverInfo"]["name"] == "mnemerynne"
    assert init.json()["result"]["serverInfo"]["version"] == mcp_server.MCP_VERSION == "1.3.0"

    listed = _rpc(mcp_client, token, "tools/list").json()["result"]["tools"]
    edits = {"edit_text", "edit_document", "edit_spreadsheet", "create_file", "create_folder", "rename_item",
             "move_item", "delete_item", "add_comment", "propose_api_action", "propose_batch",
             "request_edit_access", "withdraw_proposal", "send_message", "post_to_room", "react_in_room"}
    assert {t["name"] for t in listed} == {"search_documents", "read_passage", "list_libraries", "browse_folder",
                                           "find_files", "view_page", "open_file", "list_versions",
                                           "list_changes", "list_comments", "list_apis", "query_api",
                                           "list_proposals", "get_proposal", "list_messages", "diagnose",
                                           "list_rooms", "read_room"} | edits
    assert all(t["annotations"]["readOnlyHint"] is (t["name"] not in edits) for t in listed)
    assert not any(t["annotations"].get("destructiveHint") for t in listed)   # every change can be undone

    called = _rpc(mcp_client, token, "tools/call", {"name": "search_documents", "arguments": {"query": "night audit"}})
    result = called.json()["result"]
    assert not result.get("isError"), result
    payload = result.get("structuredContent") or json.loads(result["content"][0]["text"])
    assert payload["found"] and payload["results"][0]["file"] == "OPERA.pdf"

    bad = _rpc(mcp_client, token, "tools/call", {"name": "browse_folder", "arguments": {"library": "Nope"}})
    assert bad.json()["result"]["isError"]
    assert "[not_found] There is no library called “Nope”" in bad.json()["result"]["content"][0]["text"]

    logged = conn.execute("SELECT tool, file_ids, ok FROM mcp_calls ORDER BY id").fetchall()
    assert [(r["tool"], r["ok"]) for r in logged] == [("search_documents", 1), ("browse_folder", 0)]
    assert json.loads(logged[0]["file_ids"])

    api_tokens.revoke_all(conn, world["alice"])
    assert _rpc(mcp_client, token, "tools/list").status_code == 401


def test_view_page_returns_the_page_as_an_image(mcp_client, conn, settings, embedder, world):
    alice, bob = _caller(conn, world["alice"]), _caller(conn, world["bob"])
    hit = Tools(settings, lambda: embedder).search_documents(conn, alice, "pool hours")[0]["results"][0]
    assert hit["file"] == "OPERA.pdf" and hit["page"] == 2

    _, token = api_tokens.create(conn, world["alice"], "test")
    called = _rpc(mcp_client, token, "tools/call",
                  {"name": "view_page", "arguments": {"file_id": hit["file_id"], "page": hit["page"]}})
    result = called.json()["result"]
    assert not result.get("isError"), result
    kinds = [c["type"] for c in result["content"]]
    assert kinds == ["text", "image"]
    assert "OPERA.pdf · p. 2 of 3" in result["content"][0]["text"]
    image = result["content"][1]
    assert image["mimeType"] == "image/jpeg"
    jpeg = base64.b64decode(image["data"])
    assert jpeg[:3] == b"\xff\xd8\xff" and len(jpeg) < 1_000_000
    assert PIL.Image.open(io.BytesIO(jpeg)).width == 1200
    logged = conn.execute("SELECT file_ids FROM mcp_calls WHERE tool = 'view_page'").fetchone()
    assert json.loads(logged["file_ids"]) == [hit["file_id"]]

    tools = Tools(settings, lambda: embedder)
    with pytest.raises(ToolError, match="has 3 page"):
        tools.view_page(conn, alice, hit["file_id"], 9)
    with pytest.raises(ToolError, match="isn't shared"):
        tools.view_page(conn, bob, hit["file_id"], 1)        # bob can't read the Oracle library
    md_file = conn.execute("SELECT id FROM files WHERE display_name = 'alice.md'").fetchone()["id"]
    with pytest.raises(ToolError, match="isn't a PDF"):
        tools.view_page(conn, alice, md_file, 1)
    permissions.set_claude_enabled(conn, world["oracle"], False, world["alice"])
    with pytest.raises(ToolError, match="isn't shared"):
        tools.view_page(conn, alice, hit["file_id"], 1)


def test_mcp_rate_limit(settings, conn, world, embedder):
    settings.mcp_calls_per_minute = 2
    with TestClient(create_app(settings, Services(llm=None, embedder=embedder))) as client:
        _, token = api_tokens.create(conn, world["alice"], "test")
        assert _initialize(client, token).status_code == 200
        assert _rpc(client, token, "tools/list").status_code == 200
        limited = _rpc(client, token, "tools/list")
        assert limited.status_code == 429 and limited.headers["retry-after"] == "60"


# ------------------------------------------------------------ web pages/API
def test_connect_page_and_token_api(mcp_client, conn, world):
    mcp_client.post("/login", data={"username": "alice", "password": "password123"})
    moved = mcp_client.get("/connect", follow_redirects=False)   # the Claude half of Settings now
    assert moved.status_code == 308 and moved.headers["location"] == "/preferences#claude"
    page = mcp_client.get("/preferences").text
    assert 'id="claude-title"' in page and 'id="new-token"' in page
    assert "claude_chat.js" not in page   # alice isn't in the Claude group
    made = mcp_client.post("/api/tokens", json={"name": "Laptop", "days": 30})
    assert made.status_code == 201 and made.json()["token"].startswith("mnemerynne_")
    assert mcp_client.post("/api/tokens", json={"name": "x", "days": 7}).status_code == 422

    listed = mcp_client.get("/api/tokens").json()
    assert [t["name"] for t in listed["tokens"]] == ["Laptop"] and "token" not in listed["tokens"][0]
    assert {c["name"] for c in listed["shared"]} == {"File box", "Oracle"}
    assert listed["mcp_url"].endswith("/mcp")
    assert mcp_client.get("/connect/mnemerynne.mcpb").status_code == 404

    token_id = made.json()["id"]
    assert mcp_client.delete(f"/api/tokens/{token_id}").status_code == 200
    assert api_tokens.verify(conn, made.json()["token"]) is None
    mcp_client.post("/logout")

    # Bob can't revoke Alice's tokens.
    _, token = api_tokens.create(conn, world["alice"], "desktop")
    mcp_client.post("/login", data={"username": "bob", "password": "password123"})
    other = conn.execute("SELECT id FROM api_tokens WHERE revoked_at IS NULL").fetchone()["id"]
    assert mcp_client.delete(f"/api/tokens/{other}").status_code == 404
    assert api_tokens.verify(conn, token) is not None


def test_admin_sees_activity_and_can_cut_off(mcp_client, conn, world):
    users.create_user(conn, "boss", "password123", role="admin")
    _, token = api_tokens.create(conn, world["alice"], "test")
    _rpc(mcp_client, token, "tools/call", {"name": "search_documents", "arguments": {"query": "night audit"}})

    mcp_client.post("/login", data={"username": "boss", "password": "password123"})
    calls = mcp_client.get("/api/admin/claude-activity").json()["calls"]
    assert calls[0]["user"] == "alice" and "OPERA.pdf" in calls[0]["files"]
    people = {p["name"]: p for p in mcp_client.get("/api/admin/access").json()["users"]}
    assert people["alice"]["tokens"] == 1

    assert mcp_client.delete(f"/api/admin/users/{world['alice']}/tokens").json()["revoked"] == 1
    assert api_tokens.verify(conn, token) is None
    entries = mcp_client.get("/api/admin/log").json()["entries"]
    assert entries[0]["action"] == "tokens.revoke" and entries[0]["target"] == "alice"
