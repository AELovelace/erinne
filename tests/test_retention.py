import re
import sqlite3
from pathlib import Path

from mnemerynne import retention, users
from mnemerynne.config import Settings

DEPLOY = Path(__file__).resolve().parent.parent / "deploy" / "linux"


def _count(conn, table):
    return conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]


def test_purge_deletes_log_rows_past_their_keep_date(conn, settings):
    uid = users.create_user(conn, "alice", "password123")
    for age in ("-100 days", "-1 day"):
        conn.execute("INSERT INTO mcp_calls(at, user_id, tool, args) VALUES (datetime('now', ?), ?, 'search', '{}')",
                     (age, uid))
        conn.execute("INSERT INTO api_calls(at, user_id, via, api_name, operation_name, ok) "
                     "VALUES (datetime('now', ?), ?, 'chat', 'Atera', 'Tickets', 1)", (age, uid))
        conn.execute("INSERT INTO agent_calls(at, user_id, tool, ok) VALUES (datetime('now', ?), ?, 'search', 1)",
                     (age, uid))
        conn.execute("INSERT INTO graph_runs(started_at, node) VALUES (datetime('now', ?), 'route')", (age,))

    out = retention.purge(conn, settings)
    assert out == {"mcp_calls": 1, "api_calls": 1, "agent_calls": 1, "graph_runs": 1}
    for table in ("mcp_calls", "api_calls", "agent_calls", "graph_runs"):
        assert _count(conn, table) == 1  # the recent row stays
    assert retention.purge(conn, settings) == {}

    keep_all = settings.model_copy(update={"mcp_calls_days": 0, "graph_runs_days": 0})
    conn.execute("INSERT INTO mcp_calls(at, tool, args) VALUES (datetime('now', '-5000 days'), 'x', '{}')")
    conn.execute("INSERT INTO graph_runs(started_at, node) VALUES (datetime('now', '-5000 days'), 'x')")
    assert retention.purge(conn, keep_all) == {}


def _client(conn, name, created="-0 days", last_used=None):
    conn.execute("INSERT INTO oauth_clients(client_id, name, redirect_uris, created_at, last_used_at) "
                 "VALUES (?, ?, '[]', datetime('now', ?), CASE WHEN ? IS NULL THEN NULL ELSE datetime('now', ?) END)",
                 (name, name, created, last_used, last_used))
    return conn.execute("SELECT id FROM oauth_clients WHERE client_id = ?", (name,)).fetchone()[0]


def _grant(conn, uid, client, revoked=None):
    conn.execute("INSERT INTO oauth_grants(user_id, client_row, revoked_at) "
                 "VALUES (?, ?, CASE WHEN ? IS NULL THEN NULL ELSE datetime('now', ?) END)",
                 (uid, client, revoked, revoked))
    return conn.execute("SELECT max(id) FROM oauth_grants").fetchone()[0]


def test_purge_removes_unused_oauth_clients_and_dead_tokens(conn, settings):
    uid = users.create_user(conn, "alice", "password123")
    _client(conn, "fresh-registration")                            # consent may still be under way
    _client(conn, "abandoned", created="-2 days")                  # registered, never connected
    live = _client(conn, "claude", created="-200 days", last_used="-200 days")
    _grant(conn, uid, live)                                        # a live connection: always kept
    gone = _client(conn, "old", created="-100 days", last_used="-60 days")
    _grant(conn, uid, gone, revoked="-60 days")
    recent = _client(conn, "recently-revoked", created="-10 days", last_used="-5 days")
    _grant(conn, uid, recent, revoked="-5 days")
    grant = _grant(conn, uid, live)
    conn.execute("INSERT INTO oauth_tokens(token_hash, grant_id, kind, expires_at) "
                 "VALUES ('old', ?, 'access', datetime('now', '-3 days')), "
                 "       ('new', ?, 'refresh', datetime('now', '+80 days'))", (grant, grant))

    out = retention.purge(conn, settings)
    assert out == {"oauth_tokens": 1, "oauth_grants": 1, "oauth_clients": 2}
    left = {r[0] for r in conn.execute("SELECT client_id FROM oauth_clients")}
    assert left == {"fresh-registration", "claude", "recently-revoked"}
    assert [r[0] for r in conn.execute("SELECT token_hash FROM oauth_tokens")] == ["new"]

    assert retention.purge(conn, settings.model_copy(update={"oauth_client_days": 0})) == {}


def test_delete_user_removes_conversation_history_and_memories(conn, settings):
    from langgraph.checkpoint.base import empty_checkpoint
    from langgraph.checkpoint.sqlite import SqliteSaver
    from langgraph.store.sqlite import SqliteStore

    from mnemerynne.graph import memory_namespace

    alice = users.create_user(conn, "alice", "password123")
    bob = users.create_user(conn, "bob", "password123")
    saver = SqliteSaver(sqlite3.connect(settings.db_path, check_same_thread=False))
    saver.setup()
    for uid, thread in ((alice, "conv-a"), (bob, "conv-b")):
        conn.execute("INSERT INTO conversations(id, user_id) VALUES (?, ?)", (thread, uid))
        saver.put({"configurable": {"thread_id": thread, "checkpoint_ns": ""}}, empty_checkpoint(), {}, {})
        conn.execute("INSERT INTO graph_runs(conversation_id, node) VALUES (?, 'route')", (thread,))
        conn.execute("INSERT INTO agent_calls(user_id, conversation_id, tool, ok) VALUES (?, ?, 'search', 1)",
                     (uid, thread))
    memories = SqliteStore(sqlite3.connect(settings.db_path, isolation_level=None, check_same_thread=False))
    memories.setup()
    memories.put(memory_namespace(alice), "k1", {"text": "Works nights."})

    assert users.delete_user(conn, "alice")
    assert saver.get_tuple({"configurable": {"thread_id": "conv-a"}}) is None
    assert saver.get_tuple({"configurable": {"thread_id": "conv-b"}}) is not None
    assert [r[0] for r in conn.execute("SELECT conversation_id FROM graph_runs")] == ["conv-b"]
    assert [r[0] for r in conn.execute("SELECT conversation_id FROM agent_calls")] == ["conv-b"]
    assert memories.search(memory_namespace(alice)) == []
    assert not users.delete_user(conn, "alice")


def test_proxy_body_limits_match_the_app_upload_limit():
    limit = Settings(_env_file=None).max_file_bytes
    caddy = re.search(r"max_size\s+(\d+)MiB", (DEPLOY / "Caddyfile-mnemerynne").read_text(encoding="utf-8"))
    nginx = re.search(r"client_max_body_size\s+(\d+)m;", (DEPLOY / "nginx-mnemerynne.conf").read_text(encoding="utf-8"))
    for found in (caddy, nginx):
        proxy = int(found.group(1)) * 1024 * 1024
        # Room for the multipart overhead, but not so much that the proxy waves through what the app refuses.
        assert limit < proxy <= limit + 32 * 1024 * 1024
