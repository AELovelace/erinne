"""assistant.db and the assistant service (docs/plans/services-rewrite.md, Phase 7): Ask's conversations, answers,
ratings and timings, and LangGraph's checkpoints and memories, live in a file of their own; Ask runs in one process
of its own (an answer's events live in the process writing it), sharing the web app's sign-in."""

import asyncio
import sqlite3

import pytest
from fastapi.testclient import TestClient

from mnemerynne import assistant_db, db, events, users
from mnemerynne.graph import memory_namespace
from mnemerynne.services import nginx, registry
from mnemerynne.web.app import Services, create_app, create_role_app
from fakes import KeywordEmbedder
from test_chat import FakeLLM, _ask, _final, _login, chat_client, llm  # noqa: F401


def _langgraph(path, thread: str, user_id: int) -> None:
    """A checkpoint for `thread` and a memory for `user_id`, the way LangGraph writes them (to `path`)."""
    from langgraph.checkpoint.base import empty_checkpoint
    from langgraph.checkpoint.sqlite import SqliteSaver
    from langgraph.store.sqlite import SqliteStore

    saver = SqliteSaver(sqlite3.connect(path, check_same_thread=False))
    saver.setup()
    saver.put({"configurable": {"thread_id": thread, "checkpoint_ns": ""}}, empty_checkpoint(), {}, {})
    store = SqliteStore(sqlite3.connect(path, isolation_level=None, check_same_thread=False))
    store.setup()
    store.put(memory_namespace(user_id), "k1", {"text": "Works nights."})


def _checkpoint(path, thread: str):
    from langgraph.checkpoint.sqlite import SqliteSaver

    return SqliteSaver(sqlite3.connect(path, check_same_thread=False)).get_tuple({"configurable": {"thread_id": thread}})


def _memories(path, user_id: int) -> list:
    from langgraph.store.sqlite import SqliteStore

    store = SqliteStore(sqlite3.connect(path, isolation_level=None, check_same_thread=False))
    store.setup()
    return store.search(memory_namespace(user_id))


def test_memories_read_from_the_table_match_langgraphs_own_search(tmp_path):
    """Preferences reads memories straight from the table (the slim web app builds no LangGraph store): the same
    keys, texts and order as LangGraph's search, and only that person's."""
    from langgraph.store.sqlite import SqliteStore
    from mnemerynne import memory

    path = tmp_path / "assistant.db"
    store = SqliteStore(sqlite3.connect(path, isolation_level=None, check_same_thread=False))
    store.setup()
    for key, text in (("k1", "Works nights."), ("k2", "Prefers short answers."), ("k3", "Has a cat.")):
        store.put(memory_namespace(7), key, {"text": text, "created_at": "2026-11-14T10:00:00Z"})
    store.put(memory_namespace(7), "k1", {"text": "Works days now."})        # an update: the new text
    store.put(memory_namespace(8), "other", {"text": "Someone else's."})
    expected = [{"id": i.key, "text": i.value["text"], "created_at": i.value.get("created_at")}
                for i in store.search(memory_namespace(7))]
    conn = sqlite3.connect(path)

    def by_id(items):                     # updated_at counts whole seconds: rows written together tie in order
        return sorted(items, key=lambda m: m["id"])
    assert by_id(memory.read_memories(conn, 7)) == by_id(expected) and len(expected) == 3
    assert {m["text"] for m in memory.read_memories(conn, 7)} == {"Works days now.", "Prefers short answers.",
                                                                  "Has a cat."}
    assert memory.read_memories(sqlite3.connect(tmp_path / "empty.db"), 7) == []   # no store table: nothing


def _before_phase_7(settings):
    """A box from before Phase 7: Ask's tables and LangGraph's in core, with a conversation, an answer citing a
    source, a rating and its archive copy, a trace, a checkpoint, a memory, and a proposal drafted in that answer."""
    core = db.connect(settings.db_path, features=False)
    db.migrate(core, settings.embed_dim)
    alice = users.create_user(core, "alice", "password123")
    core.execute("INSERT INTO conversations(id, user_id, title) VALUES ('c1', ?, 'pool?')", (alice,))
    core.execute("INSERT INTO messages(conversation_id, role, content) VALUES ('c1', 'user', 'pool?')")
    answer = core.execute("INSERT INTO messages(conversation_id, role, content, status) "
                          "VALUES ('c1', 'assistant', 'At 8am [1].', 'answered')").lastrowid
    core.execute("INSERT INTO message_sources(message_id, n, cited) VALUES (?, 1, 1)", (answer,))
    rated = core.execute("INSERT INTO feedback(message_id, user_id, rating) VALUES (?, ?, 1)", (answer, alice)).lastrowid
    from mnemerynne import feedback
    feedback.archive(core, rated)
    core.execute("INSERT INTO graph_runs(conversation_id, node, duration_ms) VALUES ('c1', 'route', 3)")
    inbox = core.execute("SELECT id FROM collections WHERE kind = 'private'").fetchone()[0]
    core.execute("INSERT INTO agent_proposals(user_id, conversation_id, kind, collection_id, name, message_id) "
                 "VALUES (?, 'c1', 'create', ?, 'notes.md', ?)", (alice, inbox, answer))
    core.close()
    _langgraph(settings.db_path, "c1", alice)
    return alice, answer


def test_ask_and_langgraph_move_out_of_core_with_every_row(settings):
    alice, answer = _before_phase_7(settings)
    moved = db.migrate_all(settings.db_path, settings.embed_dim)
    assert moved["core"]
    conn = db.connect(settings.db_path)
    tables = {r[0] for r in conn.execute("SELECT name FROM main.sqlite_master WHERE type = 'table'")}
    assert not set(db.ASSISTANT_TABLES) & tables                        # gone from core...
    for table, rows in (("conversations", 1), ("messages", 2), ("message_sources", 1), ("feedback", 1),
                        ("feedback_archive", 1), ("graph_runs", 1), ("checkpoints", 1), ("store", 1)):
        assert conn.execute(f"SELECT count(*) FROM assistant.{table}").fetchone()[0] == rows, table   # ...all here
    # The proposal stays in core, still pointing at its answer; the reference that can't cross files is cut.
    assert conn.execute("SELECT message_id FROM agent_proposals").fetchone()[0] == answer
    assert not [r for r in conn.execute("PRAGMA foreign_key_list(agent_proposals)") if r["table"] == "messages"]
    with pytest.raises(sqlite3.OperationalError, match="readonly"):
        conn.execute("DELETE FROM conversations")                       # writes go through assistant.db's own
    conn.close()
    # LangGraph opens assistant.db now and finds its state there; its setup has nothing left to do.
    graph_file = assistant_db.path(settings.db_path)
    assert graph_file == db.feature_path(settings.db_path, "assistant")
    assert _checkpoint(graph_file, "c1") is not None
    assert [m.value["text"] for m in _memories(graph_file, alice)] == ["Works nights."]


def test_a_conversation_takes_its_langgraph_state_and_timings_with_it(settings, conn):
    alice = users.create_user(conn, "alice", "password123")
    graph_file = assistant_db.path(settings.db_path)
    for thread in ("c1", "c2"):
        with assistant_db.on(conn) as chats:
            chats.execute("INSERT INTO conversations(id, user_id) VALUES (?, ?)", (thread, alice))
            chats.execute("INSERT INTO graph_runs(conversation_id, node) VALUES (?, 'route')", (thread,))
        _langgraph(graph_file, thread, alice)
    with assistant_db.on(conn) as chats:
        chats.execute("DELETE FROM conversations WHERE id = 'c1'")       # whichever code deletes it
    assert _checkpoint(graph_file, "c1") is None and _checkpoint(graph_file, "c2") is not None
    assert [r[0] for r in conn.execute("SELECT conversation_id FROM graph_runs")] == ["c2"]


def test_deleting_a_person_clears_their_conversations_and_memories_and_keeps_the_ratings_archive(settings, conn):
    alice = users.create_user(conn, "alice", "password123")
    with assistant_db.on(conn) as chats:
        chats.execute("INSERT INTO conversations(id, user_id) VALUES ('c1', ?)", (alice,))
        answer = chats.execute("INSERT INTO messages(conversation_id, role, content, status) "
                               "VALUES ('c1', 'assistant', 'x', 'answered')").lastrowid
        rated = chats.execute("INSERT INTO feedback(message_id, user_id, rating) VALUES (?, ?, -1)",
                              (answer, alice)).lastrowid
        from mnemerynne import feedback
        feedback.archive(chats, rated)
    _langgraph(assistant_db.path(settings.db_path), "c1", alice)

    assert users.delete_user(conn, "alice")
    graph_file = assistant_db.path(settings.db_path)
    assert conn.execute("SELECT count(*) FROM conversations").fetchone()[0] == 0
    assert conn.execute("SELECT count(*) FROM messages").fetchone()[0] == 0         # with their conversation
    assert _checkpoint(graph_file, "c1") is None and _memories(graph_file, alice) == []
    kept = conn.execute("SELECT rating, username FROM feedback_archive").fetchone()
    assert tuple(kept) == (-1, "alice")                                 # the archive keeps what was rated


def test_a_message_id_is_never_handed_out_twice(conn):
    alice = users.create_user(conn, "alice", "password123")
    with assistant_db.on(conn) as chats:
        chats.execute("INSERT INTO conversations(id, user_id) VALUES ('c1', ?)", (alice,))
        first = chats.execute("INSERT INTO messages(conversation_id, role, content) VALUES ('c1', 'user', 'a')"
                              ).lastrowid
        chats.execute("DELETE FROM messages WHERE id = ?", (first,))
        again = chats.execute("INSERT INTO messages(conversation_id, role, content) VALUES ('c1', 'user', 'b')"
                              ).lastrowid
    assert again > first                                                # core's proposals may still name `first`


def test_move_back_puts_ask_and_langgraph_back_in_core_and_the_newer_code_moves_them_out_again(settings):
    alice, answer = _before_phase_7(settings)
    db.migrate_all(settings.db_path, settings.embed_dim)
    copied = db.move_back(settings.db_path, "assistant")
    assert copied["conversations"] == 1 and copied["messages"] == 2 and copied["checkpoints"] == 1
    assert not db.feature_path(settings.db_path, "assistant").exists()   # set aside, never deleted
    assert list(settings.db_path.parent.glob("assistant.moved-back-*.db"))
    assert assistant_db.path(settings.db_path) == settings.db_path       # LangGraph opens core again (the old code)
    assert _checkpoint(settings.db_path, "c1") is not None
    core = db.connect(settings.db_path, features=False)
    assert core.execute("SELECT count(*) FROM messages").fetchone()[0] == 2
    core.execute("DELETE FROM conversations WHERE id = 'c1'")           # its trigger came back with it
    assert core.execute("SELECT count(*) FROM checkpoints").fetchone()[0] == 0
    core.close()
    db.migrate_all(settings.db_path, settings.embed_dim)                 # B again
    assert db.feature_path(settings.db_path, "assistant").exists()


def _assistant(settings, llm, embedder):
    return TestClient(create_role_app("assistant", settings, Services(llm=llm, embedder=embedder)))


def test_the_assistant_process_answers_and_keeps_the_conversation_for_the_web_app(chat_client, settings, llm):
    _login(chat_client, "alice")
    with _assistant(settings, llm, KeywordEmbedder()) as service:
        assert service.get("/healthz").json() == {"status": "ok", "role": "assistant"}
        service.cookies.update(dict(chat_client.cookies))               # signed in on the web app: here too
        final = _final(_ask(service, "When does the pool open?"))
        assert final["status"] == "answered" and "8am" in final["answer"]
        conversations = chat_client.get("/api/conversations").json()["conversations"]   # one assistant.db
        assert [c["title"] for c in conversations] == ["When does the pool open?"]
        shown = service.get(f"/api/conversations/{conversations[0]['id']}").json()
        assert [m["role"] for m in shown["messages"]] == ["user", "assistant"]
        assert service.post(f"/api/messages/{final['message_id']}/feedback", json={"rating": 1}).json() == {"status": "ok"}
        assert service.get("/api/memories").status_code == 200
        assert "tools" in service.get("/api/me/tools").json()
        assert service.get("/api/files").status_code == 404             # Files, Rooms, pages: not here
        assert service.get("/api/rooms").status_code == 404
        assert service.get("/").status_code == 404
    rated = chat_client.get(f"/api/conversations/{conversations[0]['id']}").json()   # the web app reads the rating's
    assert rated["messages"][1]["content"] == final["answer"]


def test_with_the_assistant_running_and_valkey_the_web_app_leaves_rooms_answers_to_it(settings, conn, monkeypatch):
    llm = FakeLLM()
    services = Services(llm=llm, embedder=KeywordEmbedder())
    monkeypatch.setattr(events, "shared", lambda: True)                 # Valkey: rooms:ask reaches the assistant
    with TestClient(create_app(settings.model_copy(update={"services_on": "assistant"}), services)) as client:
        assert client.app.state.rooms_erynne is None                    # Rooms hands its asks on
        assert client.app.state.automator.rooms_erynne is not None      # automations still post their reports
    monkeypatch.setattr(events, "shared", lambda: False)                # no Valkey: the web app keeps answering
    with TestClient(create_app(settings.model_copy(update={"services_on": "assistant"}), services)) as client:
        assert client.app.state.rooms_erynne is not None


def test_the_assistant_is_one_process_and_nginx_sends_asks_and_ratings_to_it():
    service = registry.SERVICES["assistant"]
    assert service.single and service.processes("ryzen") == 1
    assert service.port not in {s.port for s in registry.SERVICES.values() if s.name != "assistant"}
    conf = nginx.render("192.168.1.5:38200", running=["assistant"])

    def upstream(location):
        part = conf[conf.index(f"location {location} {{"):]
        return part[part.index("proxy_pass http://") + 18:part.index(";")]
    for location in ("/api/ask", "/api/runs/", "/api/conversations", "/api/memories", "/api/me/tools",
                     "~ ^/api/messages/[0-9]+/feedback$"):
        assert upstream(location) == "mnemerynne_assistant", location
    assert "location /api/messages {" not in conf                        # the inbox stays with the web app
    assert "mnemerynne_assistant;" not in nginx.render("192.168.1.5:38200", running=[])
