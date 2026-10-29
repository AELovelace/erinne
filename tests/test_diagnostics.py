"""Development diagnostics: gated to a group an admin names (none = off), and what the diagnose tool
returns: calls with errors, a search trace, file and chunk state, jobs, the log, the config."""

import io
import json
import logging

import pytest
from fastapi.testclient import TestClient
from mcp.server.mcpserver.exceptions import ToolError

from fakes import FakeDirectory, FakeEmbedder, KeywordEmbedder
from mnemerynne import api_tokens, diagnostics, permissions, users, worker
from mnemerynne.ingest import store
from mnemerynne.mcp_server import Tools
from mnemerynne.web.app import Services, create_app
from test_mcp import ACCEPT


@pytest.fixture
def people(conn):
    out = {"alice": users.create_user(conn, "alice", "password123"),
           "dev": users.create_user(conn, "dev", "password123"),
           "root": users.create_user(conn, "root", "password123", role="admin")}
    conn.execute("UPDATE users SET ad_account = username")
    return out


@pytest.fixture
def directory(people):
    return FakeDirectory({"alice": {"Staff"}, "dev": {"Developers"}, "root": {"Domain Admins"}},
                         groups=["Staff", "Developers", "Domain Admins"])


def _caller(conn, user_id):
    return api_tokens.verify(conn, api_tokens.create(conn, user_id, "t", can_edit=True)[1])


def test_the_group_gates_who_may_diagnose(conn, settings, people, directory):
    tools = Tools(settings, lambda: FakeEmbedder(), lambda: directory)
    dev, root = _caller(conn, people["dev"]), _caller(conn, people["root"])
    with pytest.raises(ToolError, match="off"):
        tools.diagnose(conn, dev, "config")                                      # no group yet
    with TestClient(create_app(settings, Services(llm=None, embedder=FakeEmbedder(), directory=directory))) as web:
        web.post("/login", data={"username": "root", "password": "password123"})
        assert web.get("/api/admin/settings/diagnostics").json()["enabled"] is False
        assert web.put("/api/admin/settings/diagnostics", json={"group": "Nobody"}).status_code == 422
        got = web.put("/api/admin/settings/diagnostics", json={"group": "Developers"}).json()
        assert got["enabled"] and got["group"] == "Developers" and got["members"] == ["dev"]
        assert 'id="dev-settings"' in web.get("/admin/libraries").text
    assert diagnostics.enabled(conn) and diagnostics.group(conn) == "Developers"
    log = conn.execute("SELECT action, target, detail FROM access_log ORDER BY id DESC LIMIT 1").fetchone()
    assert tuple(log) == ("settings.diagnostics", "Developers", "was (off)")

    with pytest.raises(ToolError, match="development group"):
        tools.diagnose(conn, root, "config")                                     # an admin, but not in the group
    with pytest.raises(ToolError, match="what is one of"):
        tools.diagnose(conn, dev, "everything")
    cfg = tools.diagnose(conn, dev, "config")[0]
    assert cfg["what"] == "config" and cfg["schema"] > 30 and "secret_key" not in cfg["settings"]
    assert cfg["settings"]["embed_dim"] == 4 and cfg["meta"]["dev_diagnostics_group"] == "Developers" and "users" in cfg["tables"]

    with TestClient(create_app(settings, Services(llm=None, embedder=FakeEmbedder(), directory=directory))) as web:
        web.post("/login", data={"username": "alice", "password": "password123"})
        assert web.get("/api/admin/settings/diagnostics").status_code == 403
        web.post("/logout")
        web.post("/login", data={"username": "root", "password": "password123"})
        assert web.put("/api/admin/settings/diagnostics", json={"group": ""}).json()["enabled"] is False
    with pytest.raises(ToolError, match="off"):
        tools.diagnose(conn, dev, "config")


def test_calls_search_file_chunk_jobs_and_log(conn, settings, people, directory):
    diagnostics.set_group(conn, "Developers")
    embedder = KeywordEmbedder()
    tools = Tools(settings, lambda: embedder, lambda: directory)
    dev = _caller(conn, people["dev"])
    lib = permissions.create_library(conn, "Oracle")
    permissions.grant_user(conn, lib, people["dev"])
    fid = store.add_file(conn, settings, store.receive(io.BytesIO(b"# Night audit\n\nRun it after midnight.\n"),
                                                       "audit.md", settings), lib, people["dev"])[0]
    worker.run_one(conn, settings, embedder)

    conn.execute("INSERT INTO mcp_calls(user_id, token_id, tool, args, file_ids, ok, ms, error) VALUES (?, ?, ?, ?, ?, 0, 3, ?)",
                 (people["dev"], dev.token_id, "browse_folder", '{"library": "Nope"}', "[]", "[not_found] no library"))
    calls = tools.diagnose(conn, dev, "calls", failed_only=True)[0]
    assert calls["claude"][0]["tool"] == "browse_folder" and calls["claude"][0]["error"].startswith("[not_found]")
    assert calls["claude"][0]["args"] == {"library": "Nope"} and "erynne" in calls and "apis" in calls

    trace = tools.diagnose(conn, dev, "search", query="night audit")[0]
    assert trace["fts_query"] and trace["gate"]["passed"] and trace["chosen"]
    top = trace["candidates"][0]
    assert top["file"] == "audit.md" and top["chosen"] and top["fts_rank"] == 1 and "midnight" in top["text"]
    assert "distance" in top and trace["gate"]["distance_limit"] == settings.vector_max_distance

    state = tools.diagnose(conn, dev, "file", file_id=fid)[0]
    assert state["found"] and state["status"] == "ready" and state["chunks"] == 1 and state["embedded"] == 1
    assert state["jobs"][0]["status"] == "done" and state["content"]["exists"] is True and state["collection"] == "Oracle"
    assert tools.diagnose(conn, dev, "file", file_id=999)[0]["found"] is False

    chunk = tools.diagnose(conn, dev, "chunk", chunk_id=top["chunk_id"])[0]
    assert chunk["found"] and chunk["has_vector"] and chunk["display_name"] == "audit.md" and "midnight" in chunk["text"]

    queue = tools.diagnose(conn, dev, "jobs")[0]
    assert queue["counts"] == {"done": 1} and queue["jobs"][0]["display_name"] == "audit.md"

    diagnostics.install_log_capture()
    logging.getLogger("mnemerynne.test").warning("something odd with file %d", fid)
    log = tools.diagnose(conn, dev, "log", level="WARNING", tool="mnemerynne.test")[0]
    assert log["records"][-1]["message"] == f"something odd with file {fid}" and log["records"][-1]["level"] == "WARNING"


def test_the_call_log_keeps_errors(conn, settings, people):
    """Through the real wrapper: a refused tool call lands in mcp_calls with its [code] message."""
    _, token = api_tokens.create(conn, people["root"], "t")
    with TestClient(create_app(settings, Services(llm=None, embedder=FakeEmbedder()))) as c:
        headers = {"Accept": ACCEPT, "Content-Type": "application/json", "Authorization": f"Bearer {token}"}
        c.post("/mcp", headers=headers, json={"jsonrpc": "2.0", "id": 1, "method": "initialize",
                                              "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                                                         "clientInfo": {"name": "pytest", "version": "1"}}})
        bad = c.post("/mcp", headers=headers, json={"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                                                    "params": {"name": "browse_folder", "arguments": {"library": "Nope"}}})
        assert bad.json()["result"]["isError"]
    row = conn.execute("SELECT tool, ok, error FROM mcp_calls ORDER BY id DESC LIMIT 1").fetchone()
    assert row["tool"] == "browse_folder" and row["ok"] == 0 and row["error"].startswith("[not_found]")


def test_a_developer_who_is_not_an_admin_sees_only_their_own(conn, settings, people):
    """calls: only the caller's own; file and chunk: only what they may read themselves (anything else reads
    as not found, so there's nothing to enumerate). An admin in the group sees everything."""
    diagnostics.set_group(conn, "Developers")
    directory = FakeDirectory({"alice": {"Staff"}, "dev": {"Developers"}, "root": {"Developers", "Domain Admins"}},
                              groups=["Staff", "Developers", "Domain Admins"])
    embedder = KeywordEmbedder()
    tools = Tools(settings, lambda: embedder, lambda: directory)
    alice, dev, root = (_caller(conn, people[n]) for n in ("alice", "dev", "root"))
    payroll = permissions.create_library(conn, "Payroll")                      # alice may read it, dev may not
    permissions.grant_user(conn, payroll, people["alice"])
    fid = store.add_file(conn, settings, store.receive(io.BytesIO(b"# Pay\n\nBonuses go out Friday.\n"), "pay.md", settings),
                         payroll, people["alice"])[0]
    worker.run_one(conn, settings, embedder)
    chunk_id = conn.execute("SELECT id FROM chunks WHERE file_id = ?", (fid,)).fetchone()[0]
    for who in (alice, dev):
        conn.execute("INSERT INTO mcp_calls(user_id, token_id, tool, args, file_ids, ok, ms) VALUES (?, ?, 'search_documents', ?, '[]', 1, 2)",
                     (who.user_id, who.token_id, json.dumps({"query": f"a question only {who.user_id} asked"})))

    mine = tools.diagnose(conn, dev, "calls")[0]["claude"]
    assert [c["username"] for c in mine] == ["dev"]                            # never alice's questions
    assert tools.diagnose(conn, dev, "file", file_id=fid)[0]["found"] is False
    assert tools.diagnose(conn, dev, "chunk", chunk_id=chunk_id)[0]["found"] is False

    everything = tools.diagnose(conn, root, "calls")[0]["claude"]
    assert {c["username"] for c in everything} == {"alice", "dev"}
    assert tools.diagnose(conn, root, "file", file_id=fid)[0]["collection"] == "Payroll"
    assert "Friday" in tools.diagnose(conn, root, "chunk", chunk_id=chunk_id)[0]["text"]
