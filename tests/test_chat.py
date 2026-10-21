import io
import json
import os
import socket
import time

import pytest
from fastapi.testclient import TestClient

from erinne import graph as graph_mod
from erinne import memory, permissions, users, worker
from erinne.ingest import store
from erinne.web.app import Services, create_app
from fakes import KeywordEmbedder
from pdfgen import make_pdf


class FakeLLM:
    """Plays the model: condenses follow-ups, answers from source [1], extracts memories."""

    def __init__(self):
        self.condense_calls = []
        self.answer_prompts = []
        self.general_prompts = []
        self.memory_calls = 0
        self.fail = False

    async def chat_json(self, messages, schema, **kwargs):
        content = messages[-1]["content"]
        if schema is graph_mod.CONDENSE_SCHEMA:
            self.condense_calls.append(content)
            latest = content.rsplit("Latest message:", 1)[1].strip()
            return {"question": f"{latest} (night audit)" if "what about" in latest.lower() else latest}
        if schema is memory.MEMORY_SCHEMA:
            self.memory_calls += 1
            facts = []
            if "portland" in content.lower():
                facts.append("Works at the Portland property.")
            if "password" in content.lower():
                facts.append("Their PMS password is hunter2.")
            return {"facts": facts}
        raise AssertionError("unexpected schema")

    async def stream(self, messages, **kwargs):
        if self.fail:
            raise ConnectionError("model server down")
        if kwargs.get("json_schema") is None:  # general / small talk: plain text
            self.general_prompts.append(messages)
            for word in ["Hi! ", "I can ", "help with ", "your documents."]:
                yield word
            return
        self.answer_prompts.append(messages)
        user = messages[-1]["content"]
        # Quote source 1's text (everything after its header line, up to the next source).
        first = user.split("[1] ", 1)[1].split("\n", 1)[1].split("\n\n")[0].replace("\n", " ")
        payload = json.dumps({"status": "answered", "answer": f"{first} [1]", "citations": [1]})
        for i in range(0, len(payload), 7):
            yield payload[i:i + 7]

    async def aclose(self):
        pass


@pytest.fixture
def llm():
    return FakeLLM()


@pytest.fixture
def chat_client(settings, conn, llm):
    embedder = KeywordEmbedder()
    alice = users.create_user(conn, "alice", "password123")
    users.create_user(conn, "bob", "password123")
    pdf = make_pdf([["Night Audit", "Run the night audit after midnight to roll the date."],
                    ["Pool Hours", "The pool is open from 8am to 10pm."]],
                   [("Night Audit", 0, 0), ("Pool Hours", 1, 0)])
    store.add_file(conn, settings, store.receive(io.BytesIO(pdf), "OPERA.pdf", settings),
                   permissions.private_collection_id(conn, alice), alice)
    while worker.run_one(conn, settings, embedder):
        pass
    settings.vector_max_distance, settings.vector_max_distance_no_keywords = 99.0, 0.2
    with TestClient(create_app(settings, Services(llm=llm, embedder=embedder))) as client:
        yield client


def _login(client, name):
    client.post("/login", data={"username": name, "password": "password123"})


def _ask(client, question, conversation_id=None):
    events = []
    with client.stream("POST", "/api/ask", json={"question": question, "conversation_id": conversation_id}) as r:
        assert r.status_code == 200, r.read()
        for block in r.iter_text():
            for chunk in block.split("\n\n"):
                for line in chunk.split("\n"):
                    if line.startswith("data:"):
                        events.append(json.loads(line[5:]))
    return events


def _final(events):
    return next(e for e in events if e["type"] == "final")


def _wait_for(predicate, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return False


def test_answer_streams_with_citation_links(chat_client, llm):
    _login(chat_client, "alice")
    events = _ask(chat_client, "How do I run the night audit?")
    kinds = [e["type"] for e in events]
    assert kinds[0] == "conversation" and kinds[1] == "sources" and "delta" in kinds and kinds[-1] == "final"
    final = _final(events)
    assert final["status"] == "answered" and final["citations"] == [1]
    assert "Run the night audit after midnight" in final["answer"]
    streamed = "".join(e["text"] for e in events if e["type"] == "delta")
    assert streamed == final["answer"]
    cited = [s for s in final["sources"] if s["cited"]]
    assert cited[0]["url"].endswith("/pdf#page=1") and cited[0]["file"] == "OPERA.pdf"
    assert not llm.condense_calls  # first turn: no condense call

    conv = chat_client.get(f"/api/conversations/{events[0]['conversation_id']}").json()
    assert [m["role"] for m in conv["messages"]] == ["user", "assistant"]
    assert conv["messages"][1]["sources"][0]["url"].endswith("#page=1")


def test_nothing_relevant_skips_the_model(chat_client, llm):
    _login(chat_client, "alice")
    final = _final(_ask(chat_client, "wifi code for the lobby"))
    assert final["status"] == "not_found" and "couldn't find" in final["answer"]
    assert llm.answer_prompts == []


def test_follow_up_is_condensed_using_history(chat_client, llm):
    _login(chat_client, "alice")
    first = _ask(chat_client, "How do I run the night audit?")
    cid = first[0]["conversation_id"]
    final = _final(_ask(chat_client, "and what about after midnight?", cid))
    assert len(llm.condense_calls) == 1 and "How do I run the night audit?" in llm.condense_calls[0]
    assert final["standalone"].endswith("(night audit)")
    assert len(chat_client.get(f"/api/conversations/{cid}").json()["messages"]) == 4


def test_conversations_are_private(chat_client):
    _login(chat_client, "alice")
    final = _final(_ask(chat_client, "night audit"))
    cid = _ask(chat_client, "pool")[0]["conversation_id"]
    chat_client.post("/logout")
    _login(chat_client, "bob")
    assert chat_client.get("/api/conversations").json()["conversations"] == []
    assert chat_client.get(f"/api/conversations/{cid}").status_code == 404
    assert chat_client.post(f"/api/messages/{final['message_id']}/feedback", json={"rating": 1}).status_code == 404
    r = chat_client.post("/api/ask", json={"question": "night audit", "conversation_id": cid})
    assert r.status_code == 404
    # Bob has no documents: nothing to answer from.
    assert _final(_ask(chat_client, "night audit"))["status"] == "not_found"


def test_feedback_and_delete(chat_client, conn):
    _login(chat_client, "alice")
    events = _ask(chat_client, "night audit")
    message_id, cid = _final(events)["message_id"], events[0]["conversation_id"]
    assert chat_client.post(f"/api/messages/{message_id}/feedback", json={"rating": 5}).status_code == 422
    assert chat_client.post(f"/api/messages/{message_id}/feedback", json={"rating": -1, "comment": "meh"}).json()
    assert conn.execute("SELECT rating FROM feedback").fetchone()[0] == -1
    assert chat_client.delete(f"/api/conversations/{cid}").json()["status"] == "deleted"
    assert chat_client.get(f"/api/conversations/{cid}").status_code == 404
    assert conn.execute("SELECT count(*) FROM checkpoints WHERE thread_id = ?", (cid,)).fetchone()[0] == 0


def test_model_failure_is_reported_and_saved(chat_client, llm, conn):
    _login(chat_client, "alice")
    llm.fail = True
    final = _final(_ask(chat_client, "night audit"))
    assert final["status"] == "error" and "model server down" in final["error"]
    assert conn.execute("SELECT status FROM messages WHERE role='assistant'").fetchone()[0] == "error"


def test_memories_are_extracted_filtered_used_and_private(chat_client, llm):
    _login(chat_client, "alice")
    _ask(chat_client, "I work at the Portland property and my PMS password is hunter2. How do I run the night audit?")
    assert _wait_for(lambda: chat_client.get("/api/memories").json()["memories"])
    mems = chat_client.get("/api/memories").json()["memories"]
    assert [m["text"] for m in mems] == ["Works at the Portland property."]  # password fact filtered out

    _ask(chat_client, "night audit again")
    system = llm.answer_prompts[-1][0]["content"]
    assert "Works at the Portland property." in system and "hunter2" not in system

    page = chat_client.get("/memories")
    assert page.status_code == 200 and "Works at the Portland property." in page.text

    chat_client.post("/logout")
    _login(chat_client, "bob")
    assert chat_client.get("/api/memories").json()["memories"] == []
    assert chat_client.delete(f"/api/memories/{mems[0]['id']}").status_code == 404
    chat_client.post("/logout")
    _login(chat_client, "alice")
    assert chat_client.delete(f"/api/memories/{mems[0]['id']}").json()["status"] == "deleted"
    assert chat_client.get("/api/memories").json()["memories"] == []


def test_explicit_memory_mode(settings, chat_client, llm):
    _login(chat_client, "alice")
    settings.memory_mode = "explicit"  # read when the writer runs? no: captured at startup
    # The writer captured "auto" at startup; build a fresh writer in explicit mode.
    import asyncio
    writer = memory.make_writer(settings, llm, chat_client.app.state.store, set())
    calls = llm.memory_calls
    asyncio.run(writer(1, "c", "I work at the Portland property"))
    assert llm.memory_calls == calls  # no "remember": nothing extracted
    asyncio.run(writer(1, "c", "Remember that I work at the Portland property"))
    assert llm.memory_calls == calls + 1


@pytest.mark.parametrize("fact, ok", [
    ("Works at the Portland property.", True),
    ("Works in guest services on the night shift.", True),
    ("Card number 4111 1111 1111 1111.", False),
    ("Their PIN is 4412.", False),
    ("Can be reached at jo@example.com.", False),
    ("Is handling the reservation for room 412.", False),
    ("Badge code 883421.", False),
])
def test_memory_safety_filter(fact, ok):
    assert memory.is_safe(fact) is ok


def test_langsmith_forced_off_and_no_outbound_connections(chat_client, monkeypatch):
    assert os.environ["LANGSMITH_TRACING"] == "false" and os.environ["LANGCHAIN_TRACING_V2"] == "false"
    attempts = []
    real_connect = socket.socket.connect

    def guard(self, address):
        host = address[0] if isinstance(address, tuple) else address
        if host not in ("127.0.0.1", "::1", "localhost"):
            attempts.append(host)
            raise OSError("outbound connection blocked in test")
        return real_connect(self, address)

    monkeypatch.setattr(socket.socket, "connect", guard)
    _login(chat_client, "alice")
    assert _final(_ask(chat_client, "night audit"))["status"] == "answered"
    assert attempts == []


class KeywordRouter:
    """Routes by keywords; stands in for the trained embedding router."""
    labels = ["general", "search", "smalltalk"]

    def decide(self, vector):
        from erinne.router import Decision
        return Decision(self.next, {"search": 0.1, "smalltalk": 0.1, "general": 0.1, self.next: 0.9})


@pytest.fixture
def routed_client(settings, conn, llm, chat_client):
    router = KeywordRouter()
    router.next = "search"
    with TestClient(create_app(settings, Services(llm=llm, embedder=KeywordEmbedder(), router=router))) as client:
        yield client, router


def test_smalltalk_and_general_skip_documents(routed_client, llm, conn):
    client, router = routed_client
    _login(client, "alice")
    router.next = "smalltalk"
    events = _ask(client, "hi there!")
    final = _final(events)
    assert final["route"] == "smalltalk" and final["status"] == "answered"
    assert final["answer"] == "Hi! I can help with your documents." and final["sources"] == []
    assert any(e["type"] == "route" and e["route"] == "smalltalk" for e in events)
    assert llm.answer_prompts == [] and len(llm.general_prompts) == 1
    assert "not a question about the user's documents" in llm.general_prompts[0][0]["content"]

    router.next = "general"
    cid = events[0]["conversation_id"]
    final = _final(_ask(client, "write a short welcome email for a guest", cid))
    assert final["route"] == "general"
    # General help sees the earlier turn for continuity.
    assert any(m["content"] == "hi there!" for m in llm.general_prompts[-1])
    saved = conn.execute("SELECT route FROM messages WHERE role='assistant' ORDER BY id").fetchall()
    assert [r[0] for r in saved] == ["smalltalk", "general"]
    msgs = client.get(f"/api/conversations/{cid}").json()["messages"]
    assert msgs[-1]["route"] == "general"


def test_search_route_still_answers_from_documents(routed_client, llm):
    client, router = routed_client
    _login(client, "alice")
    router.next = "search"
    final = _final(_ask(client, "How do I run the night audit?"))
    assert final["route"] == "search" and final["citations"] == [1] and llm.general_prompts == []


def test_unregistered_route_falls_back_to_search(routed_client, llm):
    client, router = routed_client
    _login(client, "alice")
    router.next = "translate"  # a future flow nobody registered yet
    final = _final(_ask(client, "How do I run the night audit?"))
    assert final["citations"] == [1] and llm.general_prompts == []


def test_chat_pages_render(chat_client):
    _login(chat_client, "alice")
    assert "Ask Erinne" in chat_client.get("/").text
    assert "Upload files" in chat_client.get("/files").text
