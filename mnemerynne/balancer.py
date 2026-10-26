"""Balancer nodes: Erynne's answer model on several mnemerynne-balancer boxes.

Settings → Balancer nodes holds a list of balancer addresses and one API key
(meta 'balancer', the key encrypted like API credentials). Each node is asked
every 2 s how busy it is (GET /v1/balancer/load: requests in flight and
parallel slots of the models it's running). Every request to the answer model
goes to the ready node with the lowest in-flight per slot. A node that refuses,
times out, rejects the key or answers 5xx sits out for 30 s and the request
tries the next one; once a node has answered, its answer streams as is. With no
nodes set, or none answering, requests go to MNEMERYNNE_LLM_URL as before.

The balancers don't know about each other: each one balances only its own
cards, and this module spreads requests between boxes.

Each node also reports its role: "balancer" (small models, one per card) or
"bfgguf" (one big model over all its cards, bfgguf.py). Only balancer nodes
take the answer model's requests; BFGguf nodes take BFGguf's (BfgTransport),
and an admin can swap a node's role from Settings (set_role).
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import re
import sqlite3
import time
from collections import deque
from dataclasses import dataclass, field

import httpx

from mnemerynne import apis
from mnemerynne.config import Settings

log = logging.getLogger("mnemerynne.balancer")

META_KEY = "balancer"
POLL_SECONDS = 2.0
POLL_TIMEOUT = 1.0
CHECK_TIMEOUT = 5.0      # when an admin saves the list
DOWN_SECONDS = 30.0
MAX_NODES = 16
ROLES = ("balancer", "bfgguf")
ROLE_TIMEOUT = 300.0     # a role swap stops one set of models and loads the other
_NODE = re.compile(r"^https?://[A-Za-z0-9.\-\[\]:]+$")


# ------------------------------------------------------------------ settings
def parse_nodes(text: str) -> list[str]:
    """'http://a:9180, http://b:9180/v1/' -> ['http://a:9180', 'http://b:9180'] (order kept, no repeats)."""
    out: list[str] = []
    for raw in re.split(r"[,\s]+", text or ""):
        url = raw.strip().rstrip("/")
        if not url:
            continue
        if url.endswith("/v1"):
            url = url[:-3]
        if not _NODE.match(url):
            raise ValueError(f"'{raw}' isn't a balancer address like http://10.0.5.20:9180.")
        if url.lower() not in (u.lower() for u in out):
            out.append(url)
    if len(out) > MAX_NODES:
        raise ValueError(f"At most {MAX_NODES} balancer nodes.")
    return out


def load_config(conn: sqlite3.Connection) -> dict:
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (META_KEY,)).fetchone()
    cfg = {"nodes": []}
    if row:
        try:
            stored = json.loads(row["value"])
            cfg = {"nodes": [str(n) for n in stored.get("nodes", [])], **({"api_key_enc": stored["api_key_enc"]}
                                                                           if stored.get("api_key_enc") else {})}
        except (ValueError, AttributeError):
            log.warning("The balancer settings are unreadable; using none.")
    return cfg


def api_key(settings: Settings, cfg: dict) -> str:
    enc = cfg.get("api_key_enc")
    return (apis.decrypt_secret(settings, base64.b64decode(enc)) or "") if enc else ""


def save_config(conn: sqlite3.Connection, settings: Settings, nodes: list[str] | None, key: str | None) -> dict:
    """nodes: None keeps the list. key: None keeps it, '' removes it."""
    cfg = load_config(conn)
    if nodes is not None:
        cfg["nodes"] = nodes
    if key is not None:
        if key.strip():
            cfg["api_key_enc"] = base64.b64encode(apis.encrypt_secret(settings, key.strip())).decode()
        else:
            cfg.pop("api_key_enc", None)
    conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)", (META_KEY, json.dumps(cfg)))
    return cfg


def _why(exc: Exception) -> str:
    if isinstance(exc, httpx.TimeoutException):
        return "didn't answer in time"
    if isinstance(exc, httpx.ConnectError):
        return "refused the connection (not running, or a firewall)"
    return f"{type(exc).__name__}: {exc}"[:200]


def _status_error(code: int) -> str:
    return {401: "rejected the API key", 403: "rejected the API key", 404: "isn't a mnemerynne-balancer "
            "(no /v1/balancer/load)"}.get(code, f"answered HTTP {code}")


# ------------------------------------------------------------------ nodes
@dataclass
class Node:
    url: str
    reachable: bool | None = None       # None: not asked yet
    error: str = ""
    ready: bool = False                 # at least one model ready
    models: list[str] = field(default_factory=list)
    in_flight: int = 0                  # as the node last reported
    slots: int = 0
    pending: int = 0                    # sent since that report (counted at once, so bursts spread)
    down_until: float = 0.0
    polled_at: float | None = None      # wall clock
    sent: deque = field(default_factory=deque)   # wall-clock times of answers sent here
    role: str = "balancer"              # "balancer" or "bfgguf", as the node last reported
    switching: bool = False             # mid-way through a role swap: takes nothing

    def busy(self) -> float:
        return (self.in_flight + self.pending) / max(1, self.slots)


class Balancer:
    def __init__(self, transport: httpx.AsyncBaseTransport | None = None, clock=time.monotonic, wall=time.time):
        self.nodes: list[Node] = []
        self.key = ""
        self.clock, self.wall = clock, wall
        self._client = httpx.AsyncClient(transport=transport, timeout=POLL_TIMEOUT)

    def configure(self, urls: list[str], key: str) -> None:
        """Replace the list; nodes that stay keep their numbers."""
        old = {n.url: n for n in self.nodes}
        self.nodes = [old.get(u) or Node(u) for u in urls]
        if key != self.key:
            for n in self.nodes:
                n.reachable, n.ready, n.error = None, False, ""
        self.key = key

    def _headers(self, key: str | None = None) -> dict:
        k = self.key if key is None else key
        return {"Authorization": f"Bearer {k}"} if k else {}

    async def check(self, url: str, key: str) -> str | None:
        """Why the node can't be used with this key, or None if it answers (for saving the list)."""
        try:
            r = await self._client.get(url + "/v1/balancer/load", headers=self._headers(key), timeout=CHECK_TIMEOUT)
        except httpx.HTTPError as exc:
            return _why(exc)
        if r.status_code != 200:
            return _status_error(r.status_code)
        try:
            if not isinstance(r.json().get("models"), list):
                raise ValueError
        except (ValueError, AttributeError):
            return "isn't a mnemerynne-balancer (unexpected answer)"
        return None

    async def poll_once(self) -> None:
        await asyncio.gather(*(self._poll(n) for n in list(self.nodes)))

    async def _poll(self, node: Node) -> None:
        try:
            r = await self._client.get(node.url + "/v1/balancer/load", headers=self._headers())
            if r.status_code != 200:
                raise _Refused(_status_error(r.status_code))
            ready = [m for m in r.json().get("models", []) if isinstance(m, dict) and m.get("ready")]
        except (httpx.HTTPError, _Refused, ValueError, AttributeError) as exc:
            if node.reachable is not False:
                log.warning("Balancer %s: %s", node.url, exc if isinstance(exc, _Refused) else _why(exc))
            node.reachable, node.ready = False, False
            node.error = str(exc) if isinstance(exc, _Refused) else _why(exc)
            node.polled_at = self.wall()
            return
        if node.reachable is False:
            log.info("Balancer %s answers again.", node.url)
        node.reachable, node.error, node.polled_at = True, "", self.wall()
        report = r.json()
        # Balancers from before roles don't report one: they are balancer nodes.
        node.role = report.get("role") if report.get("role") in ROLES else "balancer"
        node.switching = bool(report.get("switching"))
        node.models = [str(m.get("id", "")) for m in ready]
        node.in_flight = sum(int(m.get("in_flight", 0)) for m in ready)
        node.slots = sum(int(m.get("slots", 0)) for m in ready)
        node.ready = bool(ready) and node.slots > 0
        node.pending = 0   # The report already counts what we sent.

    async def run(self) -> None:
        while True:
            try:
                if self.nodes:
                    await self.poll_once()
            except Exception:   # the poller must never die
                log.exception("Polling the balancer nodes failed")
            await asyncio.sleep(POLL_SECONDS)

    def candidates(self, role: str = "balancer") -> list[Node]:
        """Ready nodes in this role that aren't sitting out or swapping roles, least busy first (ties:
        earlier in the list). The answer model uses balancer nodes; BFGguf uses bfgguf nodes."""
        now = self.clock()
        order = {id(n): i for i, n in enumerate(self.nodes)}
        return sorted((n for n in self.nodes if n.ready and n.role == role and not n.switching
                       and n.down_until <= now),
                      key=lambda n: (n.busy(), order[id(n)]))

    def ready_nodes(self) -> list[Node]:
        """Balancer nodes that can take a request now (MinErynnes for BFGguf need one)."""
        return self.candidates("balancer")

    async def set_role(self, url: str, role: str, anyway: bool = False) -> None:
        """Ask a node to swap role (POST /v1/balancer/role). Raises ValueError with the node's reason."""
        if role not in ROLES:
            raise ValueError(f"The role is one of: {', '.join(ROLES)}.")
        node = next((n for n in self.nodes if n.url == url), None)
        if node is None:
            raise ValueError("That isn't one of the balancer nodes.")
        try:
            r = await self._client.post(url + "/v1/balancer/role", json={"role": role, "anyway": anyway},
                                        headers=self._headers(), timeout=ROLE_TIMEOUT)
        except httpx.HTTPError as exc:
            raise ValueError(f"The node {_why(exc)}.") from None
        if r.status_code == 404:
            raise ValueError("That node's mnemerynne-balancer is too old to swap roles; update it.")
        if r.status_code != 200:
            try:
                detail = str(r.json().get("detail") or "")
            except (ValueError, AttributeError):
                detail = ""
            raise ValueError(detail or f"The node {_status_error(r.status_code)}.")
        node.switching, node.role = False, role
        await self._poll(node)

    def sent_to(self, node: Node) -> None:
        node.pending += 1
        now = self.wall()
        node.sent.append(now)
        while node.sent and now - node.sent[0] > 3600:
            node.sent.popleft()

    def failed(self, node: Node, why: str) -> None:
        node.down_until = self.clock() + DOWN_SECONDS
        node.error = why
        log.warning("Balancer %s %s; skipping it for %d s.", node.url, why, DOWN_SECONDS)

    def view(self) -> list[dict]:
        now, wall = self.clock(), self.wall()
        out = []
        for n in self.nodes:
            while n.sent and wall - n.sent[0] > 3600:
                n.sent.popleft()
            out.append({"url": n.url, "role": n.role, "switching": n.switching,
                        "reachable": n.reachable, "ready": n.ready, "models": n.models,
                        "in_flight": n.in_flight + n.pending, "slots": n.slots, "error": n.error,
                        "sitting_out_s": max(0, round(n.down_until - now)),
                        "polled_s_ago": round(wall - n.polled_at) if n.polled_at else None,
                        "sent_last_hour": len(n.sent)})
        return out

    async def aclose(self) -> None:
        await self._client.aclose()


class _Refused(Exception):
    pass


# ------------------------------------------------------------------ requests
class BalancedTransport(httpx.AsyncBaseTransport):
    """The answer model's HTTP transport: requests for MNEMERYNNE_LLM_URL go to
    the least busy balancer node instead, then the next, then LLM_URL itself."""

    def __init__(self, balancer: Balancer, fallback_url: str, inner: httpx.AsyncBaseTransport | None = None):
        self.balancer = balancer
        self.inner = inner or httpx.AsyncHTTPTransport()
        base = httpx.URL(fallback_url.rstrip("/"))
        self._origin = (base.scheme, base.host, base.port)
        self._prefix = base.path.rstrip("/")

    def _ours(self, request: httpx.Request) -> bool:
        u = request.url
        return (u.scheme, u.host, u.port) == self._origin and u.path.startswith(self._prefix)

    def _to(self, request: httpx.Request, node: Node, body: bytes) -> httpx.Request:
        path = request.url.raw_path.decode()[len(self._prefix):]
        headers = [(k, v) for k, v in request.headers.multi_items() if k.lower() not in ("host", "authorization")]
        headers += list(self.balancer._headers().items())
        return httpx.Request(request.method, node.url + path, headers=headers, content=body,
                             extensions=request.extensions)

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if not self.balancer.nodes or not self._ours(request):
            return await self.inner.handle_async_request(request)
        body = await request.aread()
        for node in self.balancer.candidates():
            self.balancer.sent_to(node)
            try:
                response = await self.inner.handle_async_request(self._to(request, node, body))
            except httpx.TransportError as exc:
                self.balancer.failed(node, _why(exc))
                continue
            if response.status_code >= 500 or response.status_code in (401, 403):
                await response.aclose()
                self.balancer.failed(node, _status_error(response.status_code))
                continue
            return response   # Streams from here on: no retries once a node answered.
        return await self.inner.handle_async_request(request)

    async def aclose(self) -> None:
        await self.inner.aclose()


# BFGguf on the balancer: requests go to this placeholder address, and BfgTransport sends them to the
# least busy bfgguf node instead, with the balancer key and that node's model name.
BFGGUF_URL = "http://bfgguf.balancer"


class NoBfggufNode(httpx.TransportError):
    pass


class BfgTransport(httpx.AsyncBaseTransport):
    """BFGguf's HTTP transport when it runs on the balancer: each request goes to the least busy
    bfgguf node, then the next; there is no fallback address. The JSON body's "model" becomes the
    node's model, so the node's router matches it."""

    def __init__(self, balancer: Balancer, inner: httpx.AsyncBaseTransport | None = None):
        self.balancer = balancer
        self.inner = inner or httpx.AsyncHTTPTransport()

    def _to(self, request: httpx.Request, node: Node, body: bytes) -> httpx.Request:
        if body and node.models:
            try:
                payload = json.loads(body)
                if isinstance(payload, dict) and "model" in payload:
                    payload["model"] = node.models[0]
                    body = json.dumps(payload).encode()
            except ValueError:
                pass
        headers = [(k, v) for k, v in request.headers.multi_items()
                   if k.lower() not in ("host", "authorization", "content-length")]
        headers += list(self.balancer._headers().items())
        return httpx.Request(request.method, node.url + request.url.raw_path.decode(), headers=headers,
                             content=body, extensions=request.extensions)

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        body = await request.aread()
        for node in self.balancer.candidates("bfgguf"):
            self.balancer.sent_to(node)
            try:
                response = await self.inner.handle_async_request(self._to(request, node, body))
            except httpx.TransportError as exc:
                self.balancer.failed(node, _why(exc))
                continue
            if response.status_code >= 500 or response.status_code in (401, 403):
                await response.aclose()
                self.balancer.failed(node, _status_error(response.status_code))
                continue
            return response
        raise NoBfggufNode("No BFGguf node is ready.")

    async def aclose(self) -> None:
        await self.inner.aclose()
