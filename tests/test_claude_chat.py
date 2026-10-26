"""Claude in chat: the Claude group, each person's own key, Erynne's full reach under that key, and Erynne
subagents."""

import asyncio
import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import mnemerynne.web.settings as settings_pages
from fakes import FakeDirectory
from mnemerynne import claude_chat, permissions
from mnemerynne.clients import claude as claude_client
from mnemerynne.clients.llm import ToolCall, ToolTurn
from mnemerynne.web.app import Services, create_app
from test_agent import GROUP, AgentLLM, call, last_tool, world  # noqa: F401 (world is a fixture)
from test_chat import FakeLLM, _final, _login

CLAUDE_GROUP = "Claude Users"
KEY = "sk-ant-api03-" + "x" * 40


@pytest.fixture
def directory():
    # alice may ask Claude but isn't in the agent group; bob may do neither.
    return FakeDirectory({"alice": {CLAUDE_GROUP, "Staff"}, "bob": {"Staff"}, "boss": {"Domain Admins"}},
                         groups=[GROUP, CLAUDE_GROUP, "Staff", "Domain Admins"])


class FakeClaude(AgentLLM):
    model = "claude-opus-5-5"


class LocalErynne(AgentLLM):
    """The local model, as Erynne subagents: searches once, then reports what it found.
    Counts how many run at the same time."""

    def __init__(self):
        super().__init__()
        self.running = self.most = 0

    async def chat_tools(self, messages, tools, **kwargs):
        self.running += 1
        self.most = max(self.most, self.running)
        try:
            await asyncio.sleep(0.05)
            self.calls.append((json.loads(json.dumps(messages)), [t["function"]["name"] for t in tools], tools))
            if messages[-1]["role"] == "user":
                return ToolTurn("", [ToolCall("c_search", "search_documents", {"query": "night audit"})])
            found = last_tool(messages).get("results") or []
            return ToolTurn(f"After midnight {found[0]['cite']}." if found else "Nothing about that.")
        finally:
            self.running -= 1


@pytest.fixture
def claude():
    return FakeClaude()


@pytest.fixture
def local():
    return LocalErynne()


@pytest.fixture
def client(settings, conn, world, directory, claude, local):  # noqa: F811
    claude_chat.set_claude_group(conn, CLAUDE_GROUP)
    llm = FakeLLM()
    with TestClient(create_app(settings, Services(llm=llm, embedder=world["embedder"], directory=directory,
                                                  agent_llm=local, claude_llm=claude))) as c:
        c.llm = llm
        yield c


def _ask(client, question, claude=True, conversation_id=None):
    events = []
    body = {"question": question, "conversation_id": conversation_id, "claude": claude}
    with client.stream("POST", "/api/ask", json=body) as r:
        assert r.status_code == 200, r.read()
        for line in r.iter_lines():
            if line.startswith("data:"):
                events.append(json.loads(line[5:]))
    return events


def _with_key(conn, settings, world, name="alice", model="claude-opus-5-5"):
    claude_chat.save_key(conn, settings, world[name], KEY, model)


def _inbox_for_claude(conn, world, on):
    inbox = permissions.private_collection_id(conn, world["alice"])
    permissions.set_claude_enabled(conn, inbox, on, world["alice"])


# --------------------------------------------------------------------- gate
def test_only_the_claude_group_with_a_key_gets_claude(client, claude, conn, settings, world):
    _login(client, "bob")
    assert 'id="use-claude"' not in client.get("/").text and "claude_chat.js" not in client.get("/preferences").text
    assert client.put("/api/me/claude", json={"key": KEY}).status_code == 403
    events = _ask(client, "How do I run the night audit?")
    route = next(e for e in events if e["type"] == "route")
    assert route["claude"] and route["fallback"] == "the external API isn't turned on for you"
    assert claude.calls == []                                                   # Erynne answered instead

    client.post("/logout")
    _login(client, "alice")
    page = client.get("/").text
    assert 'id="use-claude"' not in page and 'href="/preferences#claude-key"' in page   # no key yet
    events = _ask(client, "How do I run the night audit?")
    assert next(e for e in events if e["type"] == "route")["fallback"].startswith("add your API key")
    assert claude.calls == []

    _with_key(conn, settings, world)
    assert 'id="use-claude"' in client.get("/").text
    claude.turns(ToolTurn("Hello from Claude."))
    final = _final(_ask(client, "How do I run the night audit?"))
    assert final["route"] == "agent" and final["answer"] == "Hello from Claude."
    saved = conn.execute("SELECT model FROM messages WHERE role = 'assistant' ORDER BY id DESC").fetchone()
    assert saved["model"] == "claude-opus-5-5"


def test_keys_are_each_persons_own_and_never_shown(client, conn, settings, world, monkeypatch):
    checked = []

    async def check(key, model):
        checked.append((key, model))
        if key.endswith("bad"):
            raise claude_client.ClaudeKeyError("Anthropic didn't accept that key.")
    monkeypatch.setattr(settings_pages.claude_client, "check_key", check)
    _login(client, "alice")
    assert client.get("/api/me/claude").json()["key"] == "missing"
    assert client.put("/api/me/claude", json={"model": "claude-sonnet-5-5"}).json()["detail"] == \
        "Paste your API key first."
    assert client.put("/api/me/claude", json={"key": "hunter2"}).status_code == 422          # not a key's shape
    refused = client.put("/api/me/claude", json={"key": KEY[:-3] + "bad"})
    assert refused.status_code == 422 and refused.json()["detail"] == "Anthropic didn't accept that key."
    assert client.put("/api/me/claude", json={"key": KEY, "model": "gpt-9"}).status_code == 422

    saved = client.put("/api/me/claude", json={"key": KEY, "model": "claude-sonnet-5-5"}).json()
    assert saved["key"] == "set" and saved["model"] == "claude-sonnet-5-5"
    assert checked[-1] == (KEY, "claude-sonnet-5-5")
    blob = conn.execute("SELECT key_enc FROM user_claude WHERE user_id = ?", (world["alice"],)).fetchone()[0]
    assert KEY.encode() not in bytes(blob)                                                   # encrypted
    assert KEY not in client.get("/api/me/claude").text + client.get("/preferences").text      # write-only
    assert claude_chat.credentials(conn, settings, world["alice"]) == (KEY, "claude-sonnet-5-5")
    assert claude_chat.credentials(conn, settings, world["bob"]) is None                    # not shared
    assert client.put("/api/me/claude", json={"model": "claude-haiku-5-5"}).json()["model"] == "claude-haiku-5-5"
    log = conn.execute("SELECT detail FROM access_log WHERE action = 'claude.key'").fetchall()
    assert [r[0] for r in log] == ["saved their own, claude-sonnet-5-5"]

    assert client.delete("/api/me/claude").json()["key"] == "missing"
    assert claude_chat.credentials(conn, settings, world["alice"]) is None


# ------------------------------------------------------------ Erynne's reach
def test_claude_gets_erynnes_full_reach(client, claude, conn, settings, world, directory, monkeypatch):
    """The Claude switches don't apply to the external API: people in the group send Erynne's whole reach
    under their own key, mail included once they're in the agent group."""
    from mnemerynne import agent as agent_mod

    _with_key(conn, settings, world)
    _login(client, "alice")
    _inbox_for_claude(conn, world, False)                                        # not shared with the connector
    claude.turns(call("search_documents", query="night audit"),
                 lambda m: ToolTurn(f"After midnight {last_tool(m)['results'][0]['cite']}."))
    final = _final(_ask(client, "How do I run the night audit?"))
    assert final["answer"] == "After midnight [1]." and final["citations"] == [1]   # found anyway
    messages, offered, _ = claude.calls[0]
    assert "create_file" in offered and "ask_minerynne" in offered and "view_page" not in offered
    assert not {"search_mail", "calendar", "recent_mail"} & set(offered)           # not in the agent group
    assert messages[0]["content"].startswith("You are Erynne") and "Claude" not in messages[0]["content"]
    assert "can't be reached this way" not in messages[0]["content"]

    # In the agent group, mail and calendar come along like they do for Erynne.
    directory.members["alice"].add(GROUP.lower())
    mail_spec = {"type": "function", "function": {"name": "search_mail", "description": "Search mail",
                                                  "parameters": {"type": "object", "properties": {}}}}
    monkeypatch.setattr(agent_mod.Agent, "_mail_tools", lambda self, uid: ([mail_spec], None))
    claude.turns(ToolTurn("Nothing in your mail about that."))
    _final(_ask(client, "Did anyone email me about the audit?"))
    assert "search_mail" in claude.calls[-1][1]


def test_tool_errors_dont_name_claude(client, claude, conn, settings, world):
    _with_key(conn, settings, world)
    _login(client, "alice")
    _inbox_for_claude(conn, world, False)
    claude.turns(call("read_passage", chunk_id=999999), ToolTurn("Not found."))
    events = _ask(client, "Read it")
    failed = [e for e in events if e["type"] == "tool" and e["state"] == "failed"]
    assert failed and "No such passage" in failed[0]["summary"]
    assert "Claude" not in json.dumps(events) and "Claude" not in claude.calls[1][0][-1]["content"]


def test_the_whole_conversation_goes_along(client, claude, conn, settings, world):
    _with_key(conn, settings, world)
    _login(client, "alice")
    events = _ask(client, "How do I run the night audit?", claude=False)          # Erynne answers this one
    conversation = next(e for e in events if e["type"] == "conversation")["conversation_id"]
    claude.turns(ToolTurn("Claude's answer."))
    _final(_ask(client, "And after that?", conversation_id=conversation))
    messages = claude.calls[-1][0]
    assert [m["role"] for m in messages] == ["system", "user", "assistant", "user"]   # Erynne's turn too
    assert messages[1]["content"] == "How do I run the night audit?" and messages[-1]["content"] == "And after that?"
    claude.turns(ToolTurn("Second."))
    _final(_ask(client, "And then?", conversation_id=conversation))
    assert [m["content"] for m in claude.calls[-1][0][-3:]] == ["And after that?", "Claude's answer.", "And then?"]


def test_claude_hands_reading_to_two_erynne_subagents_at_a_time(client, claude, local, conn, settings, world):
    _with_key(conn, settings, world)
    _login(client, "alice")
    tasks = [ToolCall(f"t{n}", "ask_minerynne", {"task": f"Find part {n} of the night audit"}) for n in range(3)]
    claude.turns(ToolTurn("", tasks), lambda m: ToolTurn("Done: " + " / ".join(
        json.loads(x["content"])["report"] for x in m if x["role"] == "tool")))
    events = _ask(client, "Summarise the night audit")
    final = _final(events)
    assert local.most == 2                                       # two at a time, never three
    assert len([c for c in local.calls if c[0][-1]["role"] == "user"]) == 3
    assert final["answer"] == "Done: After midnight [1]. / After midnight [1]. / After midnight [1]."
    assert final["citations"] == [1] and final["sources"][0]["file"] == "OPERA.pdf"
    # Subagents: read-only tools, Erynne's reach, their steps shown under the answer.
    offered = local.calls[0][1]
    assert "search_documents" in offered and not {"create_file", "edit_text", "ask_minerynne", "view_page"} & set(offered)
    sub_steps = [e for e in events if e["type"] == "tool" and "/" in e["id"]]
    assert {e["label"] for e in sub_steps if e["state"] == "running"} == {
        f"MinErynne {n}: Searching your documents" for n in (1, 2, 3)}
    assert {e["summary"].split(":")[0] for e in sub_steps if e["state"] == "done"} == {
        "MinErynne 1", "MinErynne 2", "MinErynne 3"}
    sent = [e for e in events if e["type"] == "tool" and e["name"] == "ask_minerynne"]
    assert [e["label"] for e in sent if e["state"] == "running"] == [f"Sending MinErynne {n} to read" for n in (1, 2, 3)]
    assert {e["summary"] for e in sent if e["state"] == "done"} == {f"MinErynne {n}: Reported back" for n in (1, 2, 3)}
    assert "Claude" not in json.dumps(events)                     # the person never sees Claude's name
    # MinErynnes run on the local model, never on the person's Claude key: every MinErynne prompt went to
    # the local fake, and none reached the Claude fake.
    assert len(local.calls) >= 3 and all(m[0]["content"].startswith("You are a MinErynne") for m, _, _ in local.calls)
    assert not any(m[0]["content"].startswith("You are a MinErynne") for m, _, _ in claude.calls)

    _inbox_for_claude(conn, world, False)                       # the connector switch doesn't limit MinErynnes
    claude.turns(ToolTurn("", tasks[:1]), lambda m: ToolTurn(json.loads(m[-1]["content"])["report"]))
    assert _final(_ask(client, "Summarise the night audit"))["answer"] == "After midnight [1]."


def test_no_subagents_when_erynnes_model_is_down(client, claude, local, conn, settings, world):
    _with_key(conn, settings, world)
    _login(client, "alice")
    local.up = False
    claude.turns(ToolTurn("Fine."))
    _final(_ask(client, "Hello"))
    messages, offered, _ = claude.calls[0]
    assert "ask_minerynne" not in offered and "do the reading yourself" in messages[0]["content"]


def test_claudes_edits_are_proposals_the_person_applies(client, claude, conn, settings, world):
    _with_key(conn, settings, world)
    _login(client, "alice")                                       # not in the agent group
    claude.turns(call("create_file", name="notes.md", content="# Notes\n"), ToolTurn("Drafted notes.md."))
    events = _ask(client, "Make me a notes file")
    proposal = next(e for e in events if e["type"] == "proposal")
    assert conn.execute("SELECT count(*) FROM files WHERE display_name = 'notes.md'").fetchone()[0] == 0
    applied = client.post(f"/api/agent/proposals/{proposal['id']}/apply")
    assert applied.status_code == 200
    assert conn.execute("SELECT count(*) FROM files WHERE display_name = 'notes.md'").fetchone()[0] == 1


def test_admin_picks_the_claude_group(client, conn, settings, world):
    _with_key(conn, settings, world)
    _login(client, "boss")
    got = client.get("/api/admin/settings/claude").json()
    assert got["group"] == CLAUDE_GROUP and got["members"] == [{"username": "alice", "key": True}]
    assert client.put("/api/admin/settings/claude-group", json={"group": ""}).json()["group"] == ""
    assert claude_chat.claude_group(conn) == ""


# ------------------------------------------------------- the Messages API side
class FakeMessages:
    def __init__(self, replies):
        self.replies, self.sent = list(replies), []

    async def create(self, **kwargs):
        self.sent.append(kwargs)
        return self.replies.pop(0)


def _reply(*blocks, stop="end_turn"):
    return SimpleNamespace(content=list(blocks), stop_reason=stop)


def _client(replies):
    messages = FakeMessages(replies)
    return SimpleNamespace(beta=SimpleNamespace(messages=messages)), messages


def test_conversation_is_translated_and_claudes_blocks_go_back_unchanged():
    thinking = SimpleNamespace(type="thinking", thinking="", signature="sig")
    use = SimpleNamespace(type="tool_use", id="toolu_1", name="search_documents", input={"query": "audit"})
    sdk, sent = _client([_reply(thinking, use, stop="tool_use"),
                         _reply(SimpleNamespace(type="text", text="Done [1]."))])
    llm = claude_client.ClaudeLLM("k", "claude-opus-5-5", client=sdk)
    tools = [{"type": "function", "function": {"name": "search_documents", "description": "Search",
                                               "parameters": {"type": "object", "properties": {}}}}]
    messages = [{"role": "system", "content": "Be brief."}, {"role": "user", "content": "Audit?"}]
    turn = asyncio.run(llm.chat_tools(messages, tools))
    assert [c.name for c in turn.tool_calls] == ["search_documents"]
    first = sent.sent[0]
    assert first["system"] == "Be brief." and first["fallbacks"] == "default"
    assert first["tools"][0]["input_schema"] == {"type": "object", "properties": {}}
    assert "temperature" not in first and "thinking" not in first

    messages += [{"role": "assistant", "content": "", "tool_calls": [
                     {"id": "toolu_1", "type": "function",
                      "function": {"name": "search_documents", "arguments": '{"query": "audit"}'}}]},
                 {"role": "tool", "tool_call_id": "toolu_1", "content": '{"results": []}'},
                 {"role": "user", "content": "(You've used all your tool steps.)"}]
    turn = asyncio.run(llm.chat_tools(messages, []))                # out of steps: no tools offered
    second = sent.sent[1]
    assert turn.content == "Done [1]."
    assert second["messages"][1]["content"] == [thinking, use]      # exactly as Claude wrote it
    assert second["messages"][2]["content"] == [
        {"type": "tool_result", "tool_use_id": "toolu_1", "content": '{"results": []}'},
        {"type": "text", "text": "(You've used all your tool steps.)"}]
    assert second["tool_choice"] == {"type": "none"} and second["tools"] == first["tools"]


def test_refusals_and_models_without_a_fallback():
    sdk, sent = _client([_reply(stop="refusal")])
    turn = asyncio.run(claude_client.ClaudeLLM("k", "claude-haiku-5-5", client=sdk).chat_tools(
        [{"role": "user", "content": "x"}], []))
    assert turn.content == claude_client.REFUSED_TEXT and not turn.tool_calls
    assert "fallbacks" not in sent.sent[0] and "tools" not in sent.sent[0]


# ------------------------------------------------------------- token counts
def test_tokens_are_counted_per_answer_and_kept(client, claude, conn, settings, world, monkeypatch):
    from mnemerynne.clients import llm as llm_mod

    async def counted(self, messages, tools, **kwargs):
        llm_mod.count_usage(1200, 80, external=True)
        return await AgentLLM.chat_tools(self, messages, tools, **kwargs)
    monkeypatch.setattr(FakeClaude, "chat_tools", counted)
    _with_key(conn, settings, world)
    _login(client, "alice")
    claude.turns(ToolTurn("Hi."))
    final = _final(_ask(client, "Hello"))
    assert final["tokens"] == {"in": 1200, "out": 80, "external": 1280, "estimated": False}
    row = conn.execute("SELECT tokens_in, tokens_out, tokens_external, tokens_estimated FROM messages "
                       "WHERE role = 'assistant'").fetchone()
    assert tuple(row) == (1200, 80, 1280, 0)
    conversation = conn.execute("SELECT id FROM conversations").fetchone()[0]
    shown = client.get(f"/api/conversations/{conversation}").json()["messages"][-1]
    assert (shown["tokens_in"], shown["tokens_out"], shown["tokens_external"]) == (1200, 80, 1280)


def test_claude_and_streamed_answers_report_what_they_used():
    import httpx

    from mnemerynne.clients import llm as llm_mod

    usage = llm_mod.new_usage()
    token = llm_mod.USAGE.set(usage)
    try:
        used = SimpleNamespace(input_tokens=500, output_tokens=40, cache_read_input_tokens=1000,
                               cache_creation_input_tokens=0)
        reply = SimpleNamespace(content=[SimpleNamespace(type="text", text="Hi.")], stop_reason="end_turn",
                                usage=used)
        sdk, _ = _client([reply])
        asyncio.run(claude_client.ClaudeLLM("k", "claude-opus-5-5", client=sdk).chat_tools(
            [{"role": "user", "content": "x"}], []))
        assert usage == {"in": 1500, "out": 40, "external": 1540, "estimated": False}   # cached input counts

        def stream(with_usage):
            chunks = [{"choices": [{"delta": {"content": "Hello there"}}]}]
            if with_usage:
                chunks.append({"choices": [], "usage": {"prompt_tokens": 300, "completion_tokens": 5}})
            body = "".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n"
            return httpx.MockTransport(lambda r: httpx.Response(200, text=body))

        async def answer(with_usage):
            llm = llm_mod.LLM("http://model.test", client=httpx.AsyncClient(transport=stream(with_usage)))
            return "".join([d async for d in llm.stream([{"role": "user", "content": "x" * 400}])])
        asyncio.run(answer(True))
        assert (usage["in"], usage["out"], usage["estimated"]) == (1800, 45, False)
        asyncio.run(answer(False))                                     # no usage reported: about 4 chars a token
        assert (usage["in"], usage["out"], usage["estimated"]) == (1900, 47, True)
    finally:
        llm_mod.USAGE.reset(token)
