"""Erynne with tools in chat: only the agent group, proposals applied with a click, fallbacks."""

import io
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from fakes import FakeDirectory, KeywordEmbedder
from mnemerynne import accounts, agent as agent_mod, editing, permissions, users, worker
from mnemerynne.clients.llm import ToolCall, ToolTurn, parse_inline_calls
from mnemerynne.ingest import store
from mnemerynne.web.app import Services, create_app
from pdfgen import make_pdf
from test_chat import FakeLLM, _ask, _final, _login

GROUP = "Erynne Agents"


class AgentLLM:
    """Plays the agent model from a script: each entry is a ToolTurn, or a
    function of the messages so far that returns one."""

    def __init__(self):
        self.script = []
        self.calls = []        # (messages, tool names offered)
        self.up = True
        self.fail = False

    def turns(self, *entries):
        self.script = list(entries)

    async def reachable(self):
        return self.up

    async def chat_tools(self, messages, tools, **kwargs):
        if self.fail:
            raise httpx.ConnectError("agent model down")
        self.calls.append((json.loads(json.dumps(messages)), [t["function"]["name"] for t in tools]))
        entry = self.script.pop(0) if self.script else ToolTurn("All done.")
        return entry(messages) if callable(entry) else entry

    async def aclose(self):
        pass


def call(tool, **args):
    return ToolTurn("", [ToolCall(f"c_{tool}", tool, args)])


def last_tool(messages) -> dict:
    return json.loads(next(m for m in reversed(messages) if m["role"] == "tool")["content"])


@pytest.fixture
def directory():
    return FakeDirectory({"alice": {GROUP, "Staff"}, "bob": {"Staff"}, "boss": {"Domain Admins"}},
                         groups=[GROUP, "Staff", "Domain Admins"])


@pytest.fixture
def agent_llm():
    return AgentLLM()


@pytest.fixture
def world(settings, conn, directory):
    embedder = KeywordEmbedder()
    ids = {name: users.create_user(conn, name, "password123") for name in ("alice", "bob", "boss")}
    for name, uid in ids.items():
        accounts.link(conn, uid, name, directory)
    pdf = make_pdf([["Night Audit", "Run the night audit after midnight to roll the date."]], [("Night Audit", 0, 0)])
    for name in ("alice", "bob"):
        store.add_file(conn, settings, store.receive(io.BytesIO(pdf), "OPERA.pdf", settings),
                       permissions.private_collection_id(conn, ids[name]), ids[name])
    while worker.run_one(conn, settings, embedder):
        pass
    settings.vector_max_distance, settings.vector_max_distance_no_keywords = 99.0, 0.2
    accounts.set_agent_group(conn, GROUP)
    return {**ids, "embedder": embedder}


@pytest.fixture
def client(settings, world, directory, agent_llm):
    llm = FakeLLM()
    with TestClient(create_app(settings, Services(llm=llm, embedder=world["embedder"], directory=directory,
                                                  agent_llm=agent_llm))) as c:
        c.llm = llm
        yield c


def _proposal(events):
    return next(e for e in events if e["type"] == "proposal")


# --------------------------------------------------------------------- gate
def test_only_the_agent_group_gets_tools(client, agent_llm, conn):
    _login(client, "bob")
    assert client.get("/api/me").json()["agent"] is False
    assert 'id="use-tools"' not in client.get("/").text
    final = _final(_ask(client, "How do I run the night audit?"))
    assert final["route"] == "search" and agent_llm.calls == []

    client.post("/logout")
    _login(client, "alice")
    answered = len(client.llm.answer_prompts)
    assert client.get("/api/me").json()["agent"] is True
    assert 'id="use-tools"' in client.get("/").text
    agent_llm.turns(call("search_documents", query="night audit"),
                    lambda m: ToolTurn(f"Run it after midnight {last_tool(m)['results'][0]['cite']}."))
    events = _ask(client, "How do I run the night audit?")
    final = _final(events)
    assert final["route"] == "agent" and final["answer"] == "Run it after midnight [1]."
    assert final["citations"] == [1] and final["sources"][0]["cited"]
    assert final["sources"][0]["url"].startswith("/files/") and final["sources"][0]["file"] == "OPERA.pdf"
    steps = [e for e in events if e["type"] == "tool"]
    assert [s["state"] for s in steps] == ["running", "done"]
    assert steps[1]["summary"] == "Searched “night audit”: 1 passage(s)"
    # The model saw the same tools Claude gets, and no links to paste.
    messages, offered = agent_llm.calls[0]
    assert "create_file" in offered and "query_api" in offered and len(offered) == 14
    assert "link" not in agent_llm.calls[1][0][-1]["content"]
    assert len(client.llm.answer_prompts) == answered               # the answer model wasn't used
    row = conn.execute("SELECT tool, ok, u.username FROM agent_calls JOIN users u ON u.id = user_id").fetchone()
    assert tuple(row) == ("search_documents", 1, "alice")
    saved = conn.execute("SELECT route, model FROM messages WHERE role = 'assistant' ORDER BY id DESC").fetchone()
    assert tuple(saved) == ("agent", agent_mod.AGENT_MODEL_NAME)


def test_no_group_means_nobody(client, agent_llm, conn, settings):
    _login(client, "alice")
    accounts.set_agent_group(conn, "")
    assert _final(_ask(client, "night audit"))["route"] == "search"
    assert client.get("/api/me").json()["agent"] is False and agent_llm.calls == []


def test_tools_can_be_switched_off_for_a_question(client, agent_llm):
    _login(client, "alice")
    events = []
    with client.stream("POST", "/api/ask", json={"question": "night audit", "tools": False}) as r:
        for line in r.iter_lines():
            if line.startswith("data:"):
                events.append(json.loads(line[5:]))
    assert _final(events)["route"] == "search" and agent_llm.calls == []


# ---------------------------------------------------------------- proposals
def test_edits_are_proposals_until_applied(client, agent_llm, conn, settings, world):
    _login(client, "alice")
    agent_llm.turns(call("create_file", name="notes.md", content="# Notes\nhello\n"),
                    ToolTurn("I drafted notes.md in your Agents folder."))
    events = _ask(client, "start a notes file")
    made = _proposal(events)
    assert made["kind"] == "create" and made["status"] == "pending" and made["where"] == "My inbox / Agents"
    assert "+hello" in made["diff"]
    assert conn.execute("SELECT count(*) FROM files WHERE display_name = 'notes.md'").fetchone()[0] == 0
    # The conversation remembers it under the answer.
    cid = events[0]["conversation_id"]
    shown = client.get(f"/api/conversations/{cid}").json()["messages"][-1]["proposals"]
    assert [p["id"] for p in shown] == [made["id"]]

    applied = client.post(f"/api/agent/proposals/{made['id']}/apply").json()
    assert applied["status"] == "applied"
    file_id = applied["file_id"]
    assert store.content_path(conn, settings, file_id).read_bytes() == b"# Notes\nhello\n"
    assert conn.execute("SELECT edited_via FROM files WHERE id = ?", (file_id,)).fetchone()[0] == "erynne:alice"
    assert client.post(f"/api/agent/proposals/{made['id']}/apply").status_code == 400   # only once

    agent_llm.turns(call("open_file", file_id=file_id),
                    lambda m: call("edit_text", file_id=file_id, version=last_tool(m)["version"],
                                   edits=[{"old": "hello", "new": "hello world"}]),
                    ToolTurn("I drafted the change."))
    change = _proposal(_ask(client, "say hello world in my notes", cid))
    assert change["kind"] == "edit" and "-hello" in change["diff"] and "+hello world" in change["diff"]
    assert store.content_path(conn, settings, file_id).read_bytes() == b"# Notes\nhello\n"   # untouched
    assert client.post(f"/api/agent/proposals/{change['id']}/apply").json()["status"] == "applied"
    assert store.content_path(conn, settings, file_id).read_bytes() == b"# Notes\nhello world\n"
    versions = editing.versions(conn, file_id)
    assert versions[0]["replaced_by"] == "Erynne for alice" and versions[0]["change"]


def test_a_stale_proposal_is_refused(client, agent_llm, conn, settings, world):
    _login(client, "alice")
    agents = editing.agents_folder(conn, world["alice"])
    inbox = permissions.private_collection_id(conn, world["alice"])
    received = store.receive(io.BytesIO(b"one\n"), "list.txt", settings)
    file_id = store.add_file(conn, settings, received, inbox, world["alice"], folder_id=agents)[0]
    agent_llm.turns(call("open_file", file_id=file_id),
                    lambda m: call("edit_text", file_id=file_id, version=last_tool(m)["version"], content="two\n"),
                    ToolTurn("Drafted."))
    change = _proposal(_ask(client, "change my list"))
    editing.replace_content(conn, settings, file_id, b"someone else\n", expected=None, actor_id=world["alice"],
                            via="restore", summary="test")
    refused = client.post(f"/api/agent/proposals/{change['id']}/apply")
    assert refused.status_code == 409 and "changed since" in refused.json()["detail"]
    assert conn.execute("SELECT status FROM agent_proposals WHERE id = ?", (change["id"],)).fetchone()[0] == "stale"
    assert store.content_path(conn, settings, file_id).read_bytes() == b"someone else\n"


def test_apply_and_tools_follow_the_rules(client, agent_llm, conn, directory, world):
    _login(client, "alice")
    brand = permissions.create_library(conn, "Brand")
    permissions.grant_user(conn, brand, world["alice"])                    # read-only for alice
    agent_llm.turns(call("create_file", name="x.md", content="x", library="Brand"),
                    lambda m: ToolTurn("Couldn't: " + last_tool(m)["error"]))
    final = _final(_ask(client, "make x in Brand"))
    assert "can't change files here yourself" in final["answer"]
    assert conn.execute("SELECT ok FROM agent_calls ORDER BY id DESC").fetchone()[0] == 0

    agent_llm.turns(call("create_file", name="y.md", content="y"), ToolTurn("Drafted."))
    made = _proposal(_ask(client, "make y"))
    directory.members["alice"] = {"staff"}                                  # left the group
    assert client.post(f"/api/agent/proposals/{made['id']}/apply").status_code == 403
    assert client.post(f"/api/agent/proposals/{made['id']}/dismiss").json()["status"] == "dismissed"
    client.post("/logout")
    _login(client, "bob")
    assert client.post(f"/api/agent/proposals/{made['id']}/dismiss").status_code == 404    # not his


# ---------------------------------------------------------------- fallbacks
def test_model_down_falls_back_to_the_normal_answer(client, agent_llm):
    _login(client, "alice")
    agent_llm.up = False
    events = _ask(client, "How do I run the night audit?")
    assert any(e["type"] == "route" and e.get("fallback") for e in events)
    final = _final(events)
    assert final["route"] == "search" and final["citations"] == [1]

    # Reachable, but the first call fails: same.
    agent_llm.up, agent_llm.fail = True, True
    client.app.state.agent._health.clear()
    final = _final(_ask(client, "How do I run the night audit?"))
    assert final["route"] == "search" and final["citations"] == [1]


def test_steps_are_capped(client, agent_llm, conn, settings):
    agent_mod.save_model_config(conn, settings, {"max_steps": 2})
    _login(client, "alice")
    agent_llm.turns(call("find_files", name="a"), call("find_files", name="b"), ToolTurn("Here's what I found."))
    final = _final(_ask(client, "look around"))
    assert final["answer"] == "Here's what I found."
    assert [len(offered) for _, offered in agent_llm.calls] == [14, 14, 0]      # the last turn gets no tools


# -------------------------------------------------------------------- admin
def test_admin_settings(client, conn, settings):
    _login(client, "bob")
    assert client.get("/api/admin/settings/agent").status_code == 403
    client.post("/logout")
    _login(client, "boss")
    got = client.get("/api/admin/settings/agent").json()
    assert got["group"] == GROUP and got["members"] == ["alice"] and got["reachable"] is True
    assert got["fallback"] == settings.llm_url and got["nodes"] == 0 and set(got["model"]) == set(agent_mod.MODEL_DEFAULTS)
    assert client.put("/api/admin/settings/agent-group", json={"group": "Nope"}).status_code == 422
    assert client.put("/api/admin/settings/agent-model", json={"max_steps": 99}).status_code == 422
    saved = client.put("/api/admin/settings/agent-model", json={"max_steps": 5, "url": "http://ignored"}).json()
    assert saved["model"]["max_steps"] == 5 and "url" not in saved["model"]
    off = client.put("/api/admin/settings/agent-group", json={"group": ""}).json()
    assert off["group"] == "" and accounts.agent_group(conn) == ""
    assert client.get("/api/admin/log").json()["entries"][0]["action"] == "settings.agent_group"


# --------------------------------------------------------------------- unit
def test_inline_tool_calls_are_parsed():
    text, calls = parse_inline_calls('Let me look. <|tool_call_start|>[search_documents(query="pool hours", '
                                     'limit=3)]<|tool_call_end|>')
    assert text.strip() == "Let me look." and calls[0].name == "search_documents"
    assert calls[0].arguments == {"query": "pool hours", "limit": 3}
    _, calls = parse_inline_calls('<tool_call>{"name": "find_files", "arguments": {"name": "rates"}}</tool_call>')
    assert (calls[0].name, calls[0].arguments) == ("find_files", {"name": "rates"})
    _, calls = parse_inline_calls("<|tool_call_start|>[oops(]<|tool_call_end|>")
    assert calls[0].error


def test_erynne_tools_forgive_guessed_places(settings, conn, world):
    from mnemerynne.mcp_server import Tools

    tools = Tools(settings, lambda: world["embedder"], mode="erynne")
    alice = agent_mod.AgentCaller(world["alice"], "alice", None)
    editing.agents_folder(conn, world["alice"])
    out, _ = tools.search_documents(conn, alice, "night audit", library="Front Office", folder="Night Audit")
    assert out["found"] and "Searched everywhere" in out["note"]
    out, _ = tools.search_documents(conn, alice, "night audit", library="My inbox / Agents")   # empty folder
    assert out["found"] and "these are from everywhere" in out["note"]
    made, _ = tools.create_file(conn, alice, "a.md", content="a", library="My inbox / Agents")
    assert made["status"] == "proposed" and made["where"] == "My inbox / Agents"
    # The "Search in…" place picked in chat narrows her searches.
    brand = permissions.create_library(conn, "Brand")
    permissions.grant_user(conn, brand, world["alice"])
    scoped = agent_mod.AgentCaller(world["alice"], "alice", None, {"collection_id": brand, "folders": None})
    assert not tools.search_documents(conn, scoped, "night audit")[0]["found"]
    # Claude still gets told off for a wrong place.
    from mcp.server.mcpserver.exceptions import ToolError
    from mnemerynne import api_tokens
    with pytest.raises(ToolError):
        Tools(settings, lambda: world["embedder"]).search_documents(
            conn, api_tokens.Caller(world["alice"], "alice", 1), "night audit", library="Front Office")


def test_old_own_address_settings_are_ignored(conn, settings):
    conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES ('agent_model', ?)",
                 (json.dumps({"url": "http://old:8083", "model": "qwen3-8b", "api_key_enc": "eA==", "max_steps": 3}),))
    assert agent_mod.model_config(conn) == {**agent_mod.MODEL_DEFAULTS, "max_steps": 3}


def test_agent_requests_take_the_balancer_path(settings):
    """No llm_factory: the agent's client goes to the least busy balancer node, then MNEMERYNNE_LLM_URL."""
    import asyncio

    import httpx

    from mnemerynne import balancer as bal

    node = "http://10.0.5.20:9180"
    seen = []

    def handler(request):
        seen.append(request)
        if request.url.path == "/v1/balancer/load":
            return httpx.Response(200, json={"models": [{"id": "m", "ready": True, "in_flight": 0, "slots": 4}]})
        return httpx.Response(200, json={"choices": [{"message": {"content": "hi", "tool_calls": []}}]})

    b = bal.Balancer(httpx.MockTransport(handler))
    b.configure([node], "k" * 24)
    agent = agent_mod.Agent(settings, lambda: None, lambda: None, balancer=b)
    client = agent._client_for(agent_mod.MODEL_DEFAULTS)
    client._client._transport.inner = httpx.MockTransport(handler)   # the network, faked

    async def go():
        await b.poll_once()
        turn = await client.chat_tools([{"role": "user", "content": "hello"}], [])
        await agent.aclose()
        await b.aclose()
        return turn
    assert asyncio.run(go()).content == "hi"
    assert str(seen[-1].url) == node + "/v1/chat/completions"
    assert seen[-1].headers["authorization"] == "Bearer " + "k" * 24


def test_a_reply_the_server_cant_parse_is_asked_again_without_tools(client, agent_llm):
    _login(client, "alice")
    tried = []

    def parse_error(messages):
        tried.append("parse")
        request = httpx.Request("POST", "http://agent.test/v1/chat/completions")
        raise httpx.HTTPStatusError("500", request=request, response=httpx.Response(
            500, request=request, json={"error": {"code": 500, "message": "Failed to parse input at pos 1599: 1][3]."}}))

    agent_llm.turns(call("search_documents", query="night audit"), parse_error,
                    lambda m: ToolTurn(f"Run it after midnight {last_tool(m)['results'][0]['cite']}."))
    final = _final(_ask(client, "How do I run the night audit?"))
    assert final["answer"] == "Run it after midnight [1]." and tried == ["parse"]
    assert agent_llm.calls[-1][1] == []                       # the retry offered no tools


def test_long_conversations_are_shortened_to_fit():
    big = json.dumps({"results": [{"cite": f"[{n}]", "file": "OPERA.pdf", "section": "Night Audit",
                                   "text": "x" * 3000} for n in range(1, 9)]})
    messages = [{"role": "system", "content": "s" * 500},
                {"role": "user", "content": "earlier"}, {"role": "assistant", "content": "e" * 2000},
                {"role": "user", "content": "now"},
                {"role": "assistant", "content": "", "tool_calls": [{"id": "1"}]},
                {"role": "tool", "content": big}, {"role": "assistant", "content": "", "tool_calls": [{"id": "2"}]},
                {"role": "tool", "content": big}]
    assert agent_mod.fit(messages, 8000)
    assert agent_mod._size(messages) <= 8000
    assert "[8] OPERA.pdf" in messages[-3]["content"]           # older result: cite markers kept
    assert any(m["content"] == "now" for m in messages)          # the question stays
