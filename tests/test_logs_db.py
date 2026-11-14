"""logs.db and the mcp service (docs/plans/services-rewrite.md, Phase 8): the busy call logs (Claude's tool calls,
internal API calls, Erynne's tool calls) live in a file of their own, and Claude's MCP endpoint runs in processes of
its own (stateless, so any of them answers)."""

import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

from mnemerynne import api_tokens, call_logs, db, users
from mnemerynne.services import nginx, registry
from mnemerynne.web.app import Services, create_role_app
from test_mcp import _initialize, _rpc, embedder, world  # noqa: F401


def test_the_call_logs_move_out_of_core_and_an_old_answers_api_source_keeps_its_id(settings):
    core = db.connect(settings.db_path, features=False)                 # a box from before Phase 7 and 8
    db.migrate(core, settings.embed_dim)
    alice = users.create_user(core, "alice", "password123")
    core.execute("INSERT INTO mcp_calls(user_id, tool, args) VALUES (?, 'search_documents', '{}')", (alice,))
    call = core.execute("INSERT INTO api_calls(user_id, via, api_name, operation_name, ok) "
                        "VALUES (?, 'chat', 'Atera', 'Tickets', 1)", (alice,)).lastrowid
    core.execute("INSERT INTO agent_calls(user_id, tool, ok) VALUES (?, 'search', 1)", (alice,))
    core.execute("INSERT INTO conversations(id, user_id) VALUES ('c1', ?)", (alice,))
    answer = core.execute("INSERT INTO messages(conversation_id, role, content) VALUES ('c1', 'assistant', 'x')"
                          ).lastrowid
    core.execute("INSERT INTO message_sources(message_id, n, api_call_id) VALUES (?, 1, ?)", (answer, call))
    core.close()

    db.migrate_all(settings.db_path, settings.embed_dim)
    conn = db.connect(settings.db_path)
    tables = {r[0] for r in conn.execute("SELECT name FROM main.sqlite_master WHERE type = 'table'")}
    assert not {"mcp_calls", "api_calls", "agent_calls"} & tables and "access_log" in tables   # the audit stays
    for table in ("mcp_calls", "api_calls", "agent_calls"):
        assert conn.execute(f"SELECT count(*) FROM logs.{table}").fetchone()[0] == 1, table
    assert conn.execute("SELECT api_call_id FROM message_sources").fetchone()[0] == call   # the answer's source
    with pytest.raises(sqlite3.OperationalError, match="readonly"):
        conn.execute("DELETE FROM mcp_calls")                           # writes go through logs.db's own
    conn.close()


def test_deleting_a_person_deletes_their_agent_calls_and_keeps_the_others_without_the_name(conn):
    alice = users.create_user(conn, "alice", "password123")
    with call_logs.on(conn) as logs:
        logs.execute("INSERT INTO mcp_calls(user_id, tool, args) VALUES (?, 'about', '{}')", (alice,))
        logs.execute("INSERT INTO api_calls(user_id, via, api_name, operation_name, ok) "
                     "VALUES (?, 'claude', 'Atera', 'Tickets', 1)", (alice,))
        logs.execute("INSERT INTO agent_calls(user_id, tool, args, ok) VALUES (?, 'search', '{\"q\": 1}', 1)", (alice,))
    assert users.delete_user(conn, "alice")
    assert conn.execute("SELECT count(*) FROM agent_calls").fetchone()[0] == 0     # their tool arguments go
    assert [tuple(r) for r in conn.execute("SELECT tool, user_id FROM mcp_calls")] == [("about", None)]
    assert conn.execute("SELECT user_id FROM api_calls").fetchone()[0] is None


def test_an_api_call_id_is_never_handed_out_twice(conn):
    with call_logs.on(conn) as logs:
        first = logs.execute("INSERT INTO api_calls(via, api_name, operation_name, ok) VALUES ('test', 'A', 'B', 1)"
                             ).lastrowid
        logs.execute("DELETE FROM api_calls")                           # retention purged it
        again = logs.execute("INSERT INTO api_calls(via, api_name, operation_name, ok) VALUES ('test', 'A', 'B', 1)"
                             ).lastrowid
    assert again > first                                                # an old answer's source names `first`


def test_a_call_logged_inside_a_rolled_back_transaction_stays_logged(conn):
    with pytest.raises(RuntimeError):
        with db.transaction(conn):
            with call_logs.on(conn) as logs:
                logs.execute("INSERT INTO mcp_calls(tool, args) VALUES ('about', '{}')")
            raise RuntimeError("the change failed")
    assert conn.execute("SELECT count(*) FROM mcp_calls").fetchone()[0] == 1   # the call happened all the same


def test_the_mcp_process_answers_claude_and_logs_each_call_in_logs_db(settings, conn, world, embedder):
    db.migrate_all(settings.db_path, settings.embed_dim)               # as the web app's start leaves it
    _, token = api_tokens.create(conn, world["alice"], "test")
    service = create_role_app("mcp", settings, Services(llm=None, embedder=embedder))
    with TestClient(service) as client:
        assert client.get("/healthz").json() == {"status": "ok", "role": "mcp"}
        assert _initialize(client, None).status_code == 401
        assert _initialize(client, token).json()["result"]["serverInfo"]["name"] == "mnemerynne"
        called = _rpc(client, token, "tools/call", {"name": "search_documents", "arguments": {"query": "night audit"}})
        result = called.json()["result"]
        payload = result.get("structuredContent") or json.loads(result["content"][0]["text"])
        assert payload["found"] and payload["results"][0]["file"] == "OPERA.pdf"
        assert client.get("/api/files").status_code == 404 and client.get("/").status_code == 404   # only Claude's
    logged = conn.execute("SELECT tool, ok FROM logs.mcp_calls").fetchall()
    assert [tuple(r) for r in logged] == [("search_documents", 1)]


def test_mcp_runs_as_several_processes_and_nginx_sends_claude_to_it():
    service = registry.SERVICES["mcp"]
    assert not service.single and service.processes("nas") == 2 and service.processes("ryzen") == 4
    assert service.port not in {s.port for s in registry.SERVICES.values() if s.name != "mcp"}
    conf = nginx.render("192.168.1.5:38200", running=["mcp"])
    for location in ("/mcp", "/c/"):
        part = conf[conf.index(f"location {location} {{"):]
        assert part[part.index("proxy_pass http://") + 18:part.index(";")] == "mnemerynne_mcp", location
    assert "mnemerynne_mcp;" not in nginx.render("192.168.1.5:38200", running=[])
