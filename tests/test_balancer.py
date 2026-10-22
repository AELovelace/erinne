import asyncio
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from fakes import FakeDirectory, FakeEmbedder
from mnemerynne import accounts, balancer as bal, users
from mnemerynne.clients.llm import LLM
from mnemerynne.web.app import Services, create_app

A, B = "http://10.0.5.20:9180", "http://10.0.5.21:9180"
KEY = "k" * 24
FALLBACK = "http://127.0.0.1:8086"


class Nodes:
    """Fake balancers (and the fallback llama-server): load per node, which nodes are down, what each received."""

    def __init__(self, load=None, down=(), status=None):
        self.load = load or {A: (0, 4), B: (0, 4)}      # url -> (in_flight, slots)
        self.down, self.status = set(down), status or {}
        self.seen: list[httpx.Request] = []

    def handler(self, request: httpx.Request):
        self.seen.append(request)
        origin = f"{request.url.scheme}://{request.url.host}:{request.url.port}"
        if origin in self.down:
            raise httpx.ConnectError("refused", request=request)
        if origin != FALLBACK and request.headers.get("authorization") != f"Bearer {KEY}":
            return httpx.Response(401)
        if origin in self.status:
            return httpx.Response(self.status[origin])
        if request.url.path == "/v1/balancer/load":
            in_flight, slots = self.load.get(origin, (0, 0))
            return httpx.Response(200, json={"node": origin, "models": [
                {"id": "lfm2.5-2.6b-q8", "ready": slots > 0, "in_flight": in_flight, "slots": slots, "cards": [0]}]})
        return httpx.Response(200, json={"choices": [{"message": {"content": f"from {origin}"}}]})

    def transport(self):
        return httpx.MockTransport(self.handler)


class Clock:
    t = 100.0

    def __call__(self):
        return self.t


def run(coro):
    return asyncio.run(coro)


def test_node_list_parsing():
    assert bal.parse_nodes(f" {A}/v1/, {B}\n{A.upper().replace('HTTP', 'http')}") == [A, B]
    assert bal.parse_nodes("") == []
    for bad in ("10.0.5.20:9180", "ftp://x", "http://a/b c", "http://a:9180/path"):
        with pytest.raises(ValueError):
            bal.parse_nodes(bad)


def test_least_busy_node_and_burst_spreading():
    nodes = Nodes(load={A: (3, 4), B: (1, 4)})
    b = bal.Balancer(nodes.transport())
    b.configure([A, B], KEY)

    async def go():
        await b.poll_once()
        assert [n.url for n in b.candidates()] == [B, A]
        b.sent_to(b.nodes[1])
        b.sent_to(b.nodes[1])           # B: (1 + 2) / 4 = 0.75 = A's 3/4; tie goes to the earlier node.
        assert b.candidates()[0].url == A
        await b.poll_once()             # A fresh report counts them: pending is reset.
        assert b.nodes[1].pending == 0 and b.candidates()[0].url == B
        await b.aclose()
    run(go())


def test_poll_reports_problems():
    nodes = Nodes(down={B})
    b = bal.Balancer(nodes.transport())
    b.configure([A, B], "wrong key")

    async def go():
        await b.poll_once()
        view = {v["url"]: v for v in b.view()}
        assert view[A]["reachable"] is False and "API key" in view[A]["error"]
        assert view[B]["reachable"] is False and "refused" in view[B]["error"]
        assert b.candidates() == []
        assert await b.check(A, KEY) is None
        assert "API key" in await b.check(A, "nope")
        await b.aclose()
    run(go())


def _llm(nodes, b):
    return LLM(FALLBACK, client=httpx.AsyncClient(transport=bal.BalancedTransport(b, FALLBACK, inner=nodes.transport())),
               max_retries=0)


def test_requests_go_to_the_least_busy_node_with_the_key():
    nodes = Nodes(load={A: (4, 4), B: (0, 4)})
    b = bal.Balancer(nodes.transport())
    b.configure([A, B], KEY)

    async def go():
        await b.poll_once()
        assert await _llm(nodes, b).chat([{"role": "user", "content": "hi"}]) == f"from {B}"
        sent = nodes.seen[-1]
        assert str(sent.url) == B + "/v1/chat/completions" and sent.headers["host"] == "10.0.5.21:9180"
        assert json.loads(sent.content)["messages"][0]["content"] == "hi"
        assert b.nodes[1].pending == 1 and len(b.nodes[1].sent) == 1
        await b.aclose()
    run(go())


def test_failed_node_sits_out_and_the_next_one_answers():
    nodes = Nodes()
    clock = Clock()
    b = bal.Balancer(nodes.transport(), clock=clock)
    b.configure([A, B], KEY)

    async def go():
        await b.poll_once()
        nodes.down.add(A)
        llm = _llm(nodes, b)
        assert await llm.chat([{"role": "user", "content": "hi"}]) == f"from {B}"
        assert b.view()[0]["sitting_out_s"] == 30
        nodes.down.clear()
        await b.poll_once()
        assert [n.url for n in b.candidates()] == [B]          # Still sitting out...
        clock.t += 31
        assert [n.url for n in b.candidates()][0] == A          # ...for 30 s.
        nodes.status[A] = 503                                  # A 5xx before any output also moves on.
        assert await llm.chat([{"role": "user", "content": "hi"}]) == f"from {B}"
        await b.aclose()
    run(go())


def test_nothing_answering_falls_back_to_llm_url():
    nodes = Nodes(down={A, B})
    b = bal.Balancer(nodes.transport())

    async def go():
        llm = _llm(nodes, b)
        assert await llm.chat([{"role": "user", "content": "hi"}]) == f"from {FALLBACK}"   # No nodes set.
        b.configure([A, B], KEY)
        await b.poll_once()
        assert await llm.chat([{"role": "user", "content": "hi"}]) == f"from {FALLBACK}"   # None answering.
        assert "authorization" not in nodes.seen[-1].headers                               # The key stays put.
        await b.aclose()
    run(go())


def test_streaming_comes_from_the_chosen_node():
    nodes = Nodes(load={A: (0, 4), B: (2, 4)})

    def handler(request):
        if request.url.path == "/v1/chat/completions":
            return httpx.Response(200, content=b'data: {"choices":[{"delta":{"content":"hel"}}]}\n\n'
                                               b'data: {"choices":[{"delta":{"content":"lo"}}]}\n\ndata: [DONE]\n\n')
        return nodes.handler(request)
    b = bal.Balancer(nodes.transport())
    b.configure([A, B], KEY)

    async def go():
        await b.poll_once()
        llm = LLM(FALLBACK, client=httpx.AsyncClient(transport=bal.BalancedTransport(
            b, FALLBACK, inner=httpx.MockTransport(handler))))
        assert "".join([d async for d in llm.stream([{"role": "user", "content": "hi"}])]) == "hello"
        await b.aclose()
    run(go())


# ------------------------------------------------------------------ settings page
@pytest.fixture
def admin(settings, conn):
    nodes = Nodes()
    directory = FakeDirectory({"boss": {"Domain Admins"}}, groups=["Domain Admins"])
    services = Services(llm=None, embedder=FakeEmbedder(), directory=directory,
                        balancer=bal.Balancer(nodes.transport()))
    with TestClient(create_app(settings, services)) as c:
        uid = users.create_user(conn, "boss", "password123")
        accounts.link(conn, uid, "boss", directory)
        c.post("/login", data={"username": "boss", "password": "password123"})
        yield c, nodes


def test_settings_check_nodes_before_saving(admin, conn):
    c, nodes = admin
    assert c.get("/api/admin/settings/balancer").json()["nodes"] == []
    r = c.put("/api/admin/settings/balancer", json={"nodes": f"{A}, {B}"})
    assert r.status_code == 422 and "API key" in r.json()["detail"]
    nodes.down.add(B)
    r = c.put("/api/admin/settings/balancer", json={"nodes": f"{A}, {B}", "api_key": KEY})
    assert r.status_code == 422 and r.json()["detail"].startswith(f"Not saved: {B} refused")   # Only B is named.
    assert c.get("/api/admin/settings/balancer").json()["nodes"] == []          # Nothing was saved.
    nodes.down.clear()
    r = c.put("/api/admin/settings/balancer", json={"nodes": f"{A}, {B}/v1", "api_key": KEY})
    assert r.status_code == 200
    data = r.json()
    assert data["nodes"] == [A, B] and data["api_key_set"] is True and KEY not in r.text
    assert [s["ready"] for s in data["status"]] == [True, True]                 # Polled straight away.
    row = conn.execute("SELECT value FROM meta WHERE key = 'balancer'").fetchone()
    assert KEY not in row["value"]                                              # Stored encrypted.
    assert c.app.state.balancer.key == KEY
    # The key is kept when only the list changes; an empty list turns balancing off.
    assert c.put("/api/admin/settings/balancer", json={"nodes": A}).json()["nodes"] == [A]
    off = c.put("/api/admin/settings/balancer", json={"nodes": ""}).json()
    assert off["nodes"] == [] and c.app.state.balancer.nodes == []


def test_settings_are_admin_only(settings, conn):
    with TestClient(create_app(settings, Services(llm=None, embedder=FakeEmbedder()))) as c:
        users.create_user(conn, "sam", "password123")
        c.post("/login", data={"username": "sam", "password": "password123"})
        assert c.get("/api/admin/settings/balancer").status_code in (401, 403)
        assert c.put("/api/admin/settings/balancer", json={"nodes": A}).status_code in (401, 403)


def test_saved_nodes_are_used_after_a_restart(settings, conn):
    bal.save_config(conn, settings, [A], KEY)
    conn.commit()
    b = bal.Balancer(Nodes().transport())
    with TestClient(create_app(settings, Services(llm=None, embedder=FakeEmbedder(), balancer=b))):
        assert [n.url for n in b.nodes] == [A] and b.key == KEY
