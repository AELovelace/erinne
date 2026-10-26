"""BFGguf: the experimental group, a bigger model for the whole answer, MinErynnes while a balancer node is
ready, POST /api/bfgguf, and the balancer nodes' roles."""

import json

import httpx
import pytest
from fastapi.testclient import TestClient

from fakes import FakeDirectory
from mnemerynne import api_tokens, balancer as balancer_mod, bfgguf
from mnemerynne.web.app import Services, create_app
from test_agent import GROUP, AgentLLM, world  # noqa: F401 (world is a fixture)
from test_chat import FakeLLM, _final, _login
from test_claude_chat import LocalErynne

EXPERIMENTAL = "Experimental"
BFG_URL = "http://bfg.test:8080"
NODE = "http://node1.test:9180"


@pytest.fixture
def directory():
    # alice is in the experimental group, bob isn't; boss is the admin.
    return FakeDirectory({"alice": {EXPERIMENTAL, "Staff"}, "bob": {"Staff"}, "boss": {"Domain Admins"}},
                         groups=[GROUP, EXPERIMENTAL, "Staff", "Domain Admins"])


class BigModel:
    """Plays BFGguf behind an OpenAI-compatible HTTP API: health, chat (JSON or streamed), tool calls."""

    def __init__(self):
        self.requests: list[dict] = []
        self.headers: list[httpx.Headers] = []
        self.script: list[dict] = []
        self.up = True

    def handler(self, request: httpx.Request) -> httpx.Response:
        if request.url.path in ("/health", "/v1/models"):
            return httpx.Response(200 if self.up else 503, json={"status": "ok"})
        if not self.up:
            return httpx.Response(503)
        body = json.loads(request.content)
        self.requests.append(body)
        self.headers.append(request.headers)
        turn = self.script.pop(0) if self.script else {"content": "Big answer."}
        usage = {"prompt_tokens": 100, "completion_tokens": 20}
        if body.get("stream"):
            chunks = [{"choices": [{"delta": {"content": turn["content"]}}]}, {"choices": [], "usage": usage}]
            text = "".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n"
            return httpx.Response(200, text=text, headers={"content-type": "text/event-stream"})
        message = {"role": "assistant", "content": turn.get("content", "")}
        if turn.get("tool_calls"):
            message["tool_calls"] = [{"id": f"t{n}", "type": "function",
                                      "function": {"name": name, "arguments": json.dumps(args)}}
                                     for n, (name, args) in enumerate(turn["tool_calls"])]
        return httpx.Response(200, json={"choices": [{"message": message, "finish_reason": "stop"}], "usage": usage})


def _load(role="balancer", model="qwen3-8b", ready=True):
    return {"node": "n", "role": role, "switching": False,
            "models": [{"id": model, "ready": ready, "in_flight": 0, "slots": 4, "cards": [0, 1]}]}


class Nodes:
    """Balancer nodes' /v1/balancer/load and /v1/balancer/role, plus BFGguf chat on a bfgguf node."""

    def __init__(self, big: BigModel):
        self.big = big
        self.roles = {NODE: "balancer"}
        self.role_calls: list[tuple[str, dict, str]] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        base = f"{request.url.scheme}://{request.url.host}:{request.url.port}"
        if request.url.path == "/v1/balancer/load":
            role = self.roles.get(base)
            return httpx.Response(200, json=_load(role, "qwen3.6-35b-a3b" if role == "bfgguf" else "qwen3-8b"))
        if request.url.path == "/v1/balancer/role":
            data = json.loads(request.content)
            self.role_calls.append((base, data, request.headers.get("authorization", "")))
            self.roles[base] = data["role"]
            return httpx.Response(200, json=_load(data["role"]))
        return self.big.handler(request)


@pytest.fixture
def big():
    return BigModel()


@pytest.fixture
def nodes(big):
    return Nodes(big)


@pytest.fixture
def local():
    return LocalErynne()


def _app(settings, conn, world, directory, big, local, nodes=None, node_list=()):  # noqa: F811
    if node_list:
        balancer_mod.save_config(conn, settings, list(node_list), "node-key")
    balancer = balancer_mod.Balancer(transport=httpx.MockTransport(nodes.handler)) if nodes else None
    return create_app(settings, Services(llm=FakeLLM(), embedder=world["embedder"], directory=directory,
                                         agent_llm=local, balancer=balancer,
                                         bfgguf_transport=httpx.MockTransport((nodes or big).handler)))


@pytest.fixture
def client(settings, conn, world, directory, big, local):  # noqa: F811
    bfgguf.set_experimental_group(conn, EXPERIMENTAL)
    bfgguf.save_config(conn, bfgguf.merged(settings, bfgguf.load_config(conn),
                                           bfgguf.check("local", BFG_URL, "big-35b", 300), None))
    with TestClient(_app(settings, conn, world, directory, big, local)) as c:
        yield c


def _ask(client, question, bfg=True, tools=True):
    events = []
    with client.stream("POST", "/api/ask", json={"question": question, "bfgguf": bfg, "tools": tools}) as r:
        assert r.status_code == 200, r.read()
        for line in r.iter_lines():
            if line.startswith("data:"):
                events.append(json.loads(line[5:]))
    return events


def _saved_model(conn):
    return conn.execute("SELECT model FROM messages WHERE role = 'assistant' ORDER BY id DESC").fetchone()["model"]


# --------------------------------------------------------------------- gate
def test_only_the_experimental_group_gets_bfgguf(client, big, local, conn):
    _login(client, "bob")
    assert 'id="use-bfgguf"' not in client.get("/").text and client.get("/api/me").json()["bfgguf"] is False
    events = _ask(client, "How do I run the night audit?")
    route = next(e for e in events if e["type"] == "route" and e.get("bfgguf"))
    assert route["fallback"] == "BFGguf isn't turned on for you"
    assert big.requests == [] and _saved_model(conn) != "bfgguf:big-35b"
    local.calls.clear()                                      # bob's answer came from the usual model

    client.post("/logout")
    _login(client, "alice")
    assert 'id="use-bfgguf"' in client.get("/").text and client.get("/api/me").json()["bfgguf"] is True
    final = _final(_ask(client, "How do I run the night audit?"))
    assert final["answer"] == "Big answer." and final["model"] == "bfgguf:big-35b"
    assert _saved_model(conn) == "bfgguf:big-35b"
    assert big.requests and all(r["model"] == "big-35b" for r in big.requests)
    assert local.calls == []                                 # the usual model wasn't asked at all


def test_the_whole_answer_uses_bfgguf_even_without_tools(client, big, local):
    _login(client, "alice")
    final = _final(_ask(client, "How do I run the night audit?", tools=False))   # the plain cited-answer path
    assert final["route"] != "agent" and final["model"] == "bfgguf:big-35b"
    assert big.requests and big.requests[-1]["stream"] is True
    assert client.app.state.graph is not None and local.calls == []


def test_bfgguf_down_falls_back_without_marking_the_usual_model_down(client, big, local):
    big.up = False
    _login(client, "alice")
    events = _ask(client, "How do I run the night audit?")
    route = next(e for e in events if e["type"] == "route" and e.get("bfgguf"))
    assert route["fallback"] == "BFGguf isn't answering"
    assert _final(events)["model"] != "bfgguf:big-35b"
    assert local.calls                                       # the usual model answered, and is still up
    assert all(ok for _, ok in client.app.state.agent._health.values())


def test_a_hosted_bfgguf_sends_its_key_and_counts_as_external(client, big, conn, settings):
    bfgguf.save_config(conn, bfgguf.merged(settings, bfgguf.load_config(conn),
                                           bfgguf.check("openai", "https://api.example.com", "big-hosted", 300),
                                           "sk-test-123"))
    _login(client, "alice")
    final = _final(_ask(client, "How do I run the night audit?"))
    assert final["model"] == "bfgguf:big-hosted"
    assert big.headers[-1]["authorization"] == "Bearer sk-test-123"
    assert final["tokens"]["external"] > 0
    assert "max_completion_tokens" in big.requests[-1]          # the hosted (openai) payload


# --------------------------------------------------------------- MinErynnes
def _minerynne_turns(big):
    big.script = [{"tool_calls": [("ask_minerynne", {"task": "Find how the night audit is run."})]},
                  {"content": "After midnight."}]


def test_bfgguf_sends_minerynnes_while_a_balancer_node_is_ready(settings, conn, world, directory, big, local, nodes):  # noqa: F811
    bfgguf.set_experimental_group(conn, EXPERIMENTAL)
    bfgguf.save_config(conn, bfgguf.merged(settings, bfgguf.load_config(conn),
                                           bfgguf.check("local", BFG_URL, "big-35b", 300), None))
    with TestClient(_app(settings, conn, world, directory, big, local, nodes, [NODE])) as c:
        c.portal.call(c.app.state.balancer.poll_once)
        assert [n.url for n in c.app.state.balancer.ready_nodes()] == [NODE]
        _login(c, "alice")
        _minerynne_turns(big)
        final = _final(_ask(c, "How do I run the night audit?"))
        offered = [t["function"]["name"] for t in big.requests[0]["tools"]]
        assert "ask_minerynne" in offered and final["answer"] == "After midnight."
        assert local.calls and "MinErynne" in local.calls[0][0][0]["content"]     # she ran on the usual model
        assert "ask_minerynne" in big.requests[0]["messages"][0]["content"]       # and BFGguf was told about her

        # Switched off in chat's tool panel: not offered.
        big.requests.clear()
        with c.stream("POST", "/api/ask", json={"question": "Again?", "bfgguf": True,
                                                "disabled_tools": ["ask_minerynne"]}) as r:
            r.read()
        assert "ask_minerynne" not in [t["function"]["name"] for t in big.requests[0]["tools"]]


def test_no_minerynnes_without_a_ready_balancer_node(settings, conn, world, directory, big, local, nodes):  # noqa: F811
    bfgguf.set_experimental_group(conn, EXPERIMENTAL)
    bfgguf.save_config(conn, bfgguf.merged(settings, bfgguf.load_config(conn),
                                           bfgguf.check("local", BFG_URL, "big-35b", 300), None))
    nodes.roles[NODE] = "bfgguf"                   # the only node runs BFGguf: no balancer node for MinErynnes
    with TestClient(_app(settings, conn, world, directory, big, local, nodes, [NODE])) as c:
        c.portal.call(c.app.state.balancer.poll_once)
        assert c.app.state.balancer.ready_nodes() == []
        _login(c, "alice")
        _ask(c, "How do I run the night audit?")
        assert "ask_minerynne" not in [t["function"]["name"] for t in big.requests[0]["tools"]]


# ------------------------------------------------------- BFGguf on the balancer
def test_bfgguf_on_balancer_nodes_and_swapping_roles(settings, conn, world, directory, big, local, nodes):  # noqa: F811
    bfgguf.set_experimental_group(conn, EXPERIMENTAL)
    conn.execute("UPDATE users SET role = 'admin' WHERE username = 'boss'")
    with TestClient(_app(settings, conn, world, directory, big, local, nodes, [NODE])) as c:
        c.portal.call(c.app.state.balancer.poll_once)
        _login(c, "boss")
        # No BFGguf node yet: the balancer kind can't be saved.
        refused = c.put("/api/admin/settings/bfgguf", json={"kind": "balancer"})
        assert refused.status_code == 422 and "BFGguf role" in refused.json()["detail"]
        swapped = c.post("/api/admin/settings/balancer/role", json={"url": NODE, "role": "bfgguf"})
        assert swapped.status_code == 200, swapped.text
        assert nodes.role_calls == [(NODE, {"role": "bfgguf", "anyway": False}, "Bearer node-key")]
        assert [(n["role"], n["models"]) for n in swapped.json()["status"]] == [("bfgguf", ["qwen3.6-35b-a3b"])]
        assert c.app.state.balancer.ready_nodes() == []          # normal answers skip a BFGguf node
        saved = c.put("/api/admin/settings/bfgguf", json={"kind": "balancer"}).json()
        assert saved["bfgguf"]["kind"] == "balancer" and [n["url"] for n in saved["bfgguf_nodes"]] == [NODE]
        assert c.put("/api/admin/settings/experimental-group", json={"group": EXPERIMENTAL}).status_code == 200
        c.post("/logout")

        _login(c, "alice")
        final = _final(_ask(c, "How do I run the night audit?"))
        assert final["model"] == "bfgguf:qwen3.6-35b-a3b" and final["answer"] == "Big answer."
        assert big.requests[-1]["model"] == "qwen3.6-35b-a3b"    # the node's model name
        assert big.headers[-1]["authorization"] == "Bearer node-key"


# ------------------------------------------------------------------- admin
def test_admin_settings(client, conn, big):
    conn.execute("UPDATE users SET role = 'admin' WHERE username = 'boss'")
    _login(client, "alice")
    assert client.get("/api/admin/settings/experimental").status_code == 403
    client.post("/logout")
    _login(client, "boss")
    status = client.get("/api/admin/settings/experimental").json()
    assert status["members"] == ["alice"] and status["bfgguf"]["kind"] == "local"
    assert client.put("/api/admin/settings/experimental-group", json={"group": "Nope"}).status_code == 422
    assert client.put("/api/admin/settings/bfgguf", json={"kind": "openai", "url": "http://x.test",
                                                          "model": "m"}).status_code == 422   # hosted needs https
    saved = client.put("/api/admin/settings/bfgguf", json={"kind": "local", "url": BFG_URL + "/v1/", "model": "big",
                                                           "api_key": "secret-1"}).json()
    assert saved["bfgguf"] == {"kind": "local", "url": BFG_URL, "model": "big", "timeout": 300, "api_key_set": True}
    assert "secret-1" not in json.dumps(saved)
    big.up = False
    down = client.put("/api/admin/settings/bfgguf", json={"kind": "local", "url": BFG_URL, "model": "other"})
    assert down.status_code == 422 and down.json()["detail"].startswith("Not saved")
    assert bfgguf.load_config(conn)["model"] == "big"                                 # unchanged
    log = [r[0] for r in conn.execute("SELECT action FROM access_log ORDER BY id DESC LIMIT 1")]
    assert log == ["settings.bfgguf"]


# --------------------------------------------------------------- /api/bfgguf
def test_the_api_route(client, conn, world, big):  # noqa: F811
    body = {"messages": [{"role": "user", "content": "Say hi."}], "max_tokens": 50}
    assert client.post("/api/bfgguf", json=body).status_code == 401
    _, bob_key = api_tokens.create(conn, world["bob"], "script")
    assert client.post("/api/bfgguf", json=body, headers={"Authorization": f"Bearer {bob_key}"}).status_code == 403
    _, key = api_tokens.create(conn, world["alice"], "script")
    auth = {"Authorization": f"Bearer {key}"}
    out = client.post("/api/bfgguf", json=body, headers=auth)
    assert out.status_code == 200, out.text
    assert out.json() == {"model": "big-35b", "content": "Big answer.", "usage": {"tokens_in": 100, "tokens_out": 20}}
    assert big.requests[-1]["messages"] == [{"role": "user", "content": "Say hi."}]
    assert client.post("/api/bfgguf", json={**body, "max_tokens": 99999}, headers=auth).status_code == 422
    for _ in range(25):
        last = client.post("/api/bfgguf", json=body, headers=auth)
    assert last.status_code == 429
    big.up = False
    _login(client, "alice")                                       # signed in works too; the model is down
    from mnemerynne.web import bfgguf as route
    route._recent.clear()
    assert client.post("/api/bfgguf", json=body).status_code == 503
