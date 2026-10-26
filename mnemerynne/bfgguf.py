"""BFGguf: a much bigger model, tried by an experimental group before anyone else.

Members of the experimental group (Access → Settings → Experimental, meta
'experimental_group') get a BFGguf toggle in chat. A question asked with it
is answered entirely by the BFGguf: the follow-up rewrite, the cited answer,
small talk, API parameters and Erynne's tool loop (graph.run_turn sets
clients.llm.ACTIVE_LLM for the turn). When a balancer node is ready, the
BFGguf can also send MinErynnes (the usual model, on the balancer's cards) to
read for it. POST /api/bfgguf talks to it directly, for scripts.

Where it runs is the admin's choice (meta 'bfgguf'):
- "balancer": the balancer nodes an admin has switched to the bfgguf role
  (balancer.BfgTransport); no address or key to enter.
- "local": any OpenAI-compatible server on the network (llama-server, vLLM…).
- "openai": a hosted OpenAI-compatible API with a key. Its tokens count as
  external, and the question, passages and memories go to that service: the
  experimental group is the admin's statement that this is acceptable.
"""

from __future__ import annotations

import base64
import json
import logging
import re
import sqlite3

import httpx

from mnemerynne import apis, group_refs
from mnemerynne.balancer import BFGGUF_URL, BfgTransport
from mnemerynne.clients.llm import LLM
from mnemerynne.config import Settings

log = logging.getLogger("mnemerynne.bfgguf")

EXPERIMENTAL_GROUP_KEY = "experimental_group"
META_KEY = "bfgguf"
KINDS = ("balancer", "local", "openai")
DEFAULT_TIMEOUT = 300
TIMEOUT_LIMITS = (10, 1800)
LABEL_PREFIX = "bfgguf:"
_URL = re.compile(r"^https?://[A-Za-z0-9.\-\[\]:]+(/[A-Za-z0-9._~\-/]*)?$")


# ------------------------------------------------------------- the group
def experimental_group(conn: sqlite3.Connection) -> str:
    """The group(s) whose members get experimental features (BFGguf); '' = nobody."""
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (EXPERIMENTAL_GROUP_KEY,)).fetchone()
    return row["value"] if row else ""


def set_experimental_group(conn: sqlite3.Connection, name: str) -> str:
    name = group_refs.join(group_refs.split(name))
    if len(name) > 2000:
        raise ValueError("Pick a group.")
    conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)", (EXPERIMENTAL_GROUP_KEY, name))
    return name


def is_experimental_user(conn: sqlite3.Connection, user: sqlite3.Row, directory) -> bool:
    return group_refs.in_any(conn, user, directory, experimental_group(conn))


# ------------------------------------------------------------ the endpoint
def load_config(conn: sqlite3.Connection) -> dict:
    """{kind, url, model, timeout} and api_key_enc when a key is saved; kind "" = not set up."""
    cfg = {"kind": "", "url": "", "model": "", "timeout": DEFAULT_TIMEOUT}
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (META_KEY,)).fetchone()
    if row:
        try:
            stored = json.loads(row["value"])
            cfg.update({k: stored[k] for k in ("kind", "url", "model", "timeout", "api_key_enc") if k in stored})
        except (ValueError, TypeError):
            log.warning("The BFGguf settings are unreadable; treating it as not set up.")
    return cfg


def api_key(settings: Settings, cfg: dict) -> str:
    enc = cfg.get("api_key_enc")
    return (apis.decrypt_secret(settings, base64.b64decode(enc)) or "") if enc else ""


def check(kind: str, url: str, model: str, timeout: int) -> dict:
    """Validated settings (without the key), or ValueError saying what's wrong."""
    if kind not in KINDS:
        raise ValueError("Pick where BFGguf runs: the balancer, a local server, or a hosted API.")
    url, model = (url or "").strip().rstrip("/"), (model or "").strip()
    if url.endswith("/v1"):
        url = url[:-3]
    if kind != "balancer" and not _URL.match(url):
        raise ValueError("Give the server's address, like http://10.0.5.30:8080.")
    if kind == "openai" and not url.startswith("https://"):
        raise ValueError("A hosted API's address must start with https://.")
    if kind == "openai" and not model:
        raise ValueError("Give the model name the hosted API expects.")
    if len(model) > 200 or not re.fullmatch(r"[A-Za-z0-9._:/@\-]*", model):
        raise ValueError("The model name has characters a model name doesn't.")
    if not TIMEOUT_LIMITS[0] <= int(timeout) <= TIMEOUT_LIMITS[1]:
        raise ValueError(f"The timeout must be between {TIMEOUT_LIMITS[0]} and {TIMEOUT_LIMITS[1]} seconds.")
    return {"kind": kind, "url": "" if kind == "balancer" else url, "model": model, "timeout": int(timeout)}


def merged(settings: Settings, current: dict, checked: dict, key: str | None) -> dict:
    """The settings with checked (from check()) and key applied: None keeps the key, '' removes it.
    Only local and hosted servers use one; on the balancer its own key is used. Raises apis.ApiError
    when there's no MNEMERYNNE_SECRET_KEY to encrypt a new key with."""
    cfg = {**current, **checked}
    if checked["kind"] == "balancer":
        cfg.pop("api_key_enc", None)
    elif key is not None:
        if key.strip():
            cfg["api_key_enc"] = base64.b64encode(apis.encrypt_secret(settings, key.strip())).decode()
        else:
            cfg.pop("api_key_enc", None)
    return cfg


def save_config(conn: sqlite3.Connection, cfg: dict) -> dict:
    """Store settings from merged()."""
    conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)", (META_KEY, json.dumps(cfg)))
    return cfg


def view(cfg: dict) -> dict:
    """What the admin page shows: never the key."""
    return {"kind": cfg["kind"], "url": cfg["url"], "model": cfg["model"], "timeout": cfg["timeout"],
            "api_key_set": bool(cfg.get("api_key_enc"))}


def configured(cfg: dict) -> bool:
    return cfg["kind"] in KINDS


def label(cfg: dict, balancer=None) -> str:
    """What answers are saved as (messages.model): the BFGguf and its model."""
    model = cfg["model"]
    if cfg["kind"] == "balancer" and balancer is not None:
        ready = balancer.candidates("bfgguf")
        model = model or (ready[0].models[0] if ready and ready[0].models else "")
    return LABEL_PREFIX + (model or cfg["kind"] or "unknown")


def is_label(model: str | None) -> bool:
    return bool(model) and str(model).startswith(LABEL_PREFIX)


def client(settings: Settings, cfg: dict, balancer=None, transport: httpx.AsyncBaseTransport | None = None) -> LLM:
    """A client for one question (the caller closes it). transport: the HTTP transport underneath (tests).
    Never balancer.BalancedTransport: that one rewrites requests to the answer model's nodes and key."""
    timeout = float(cfg["timeout"])
    reasoning = settings.llm_reasoning_tokens
    if cfg["kind"] == "balancer":
        http = httpx.AsyncClient(timeout=timeout, transport=BfgTransport(balancer, transport))
        return LLM(BFGGUF_URL, model=cfg["model"] or "bfgguf", timeout=timeout, client=http, max_retries=1,
                   reasoning_tokens=reasoning)
    key = api_key(settings, cfg)
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    http = httpx.AsyncClient(timeout=timeout, transport=transport, headers=headers)
    hosted = cfg["kind"] == "openai"
    return LLM(cfg["url"], model=cfg["model"] or "local", timeout=timeout, client=http, max_retries=1,
               flavor="openai" if hosted else "llamacpp", reasoning_tokens=0 if hosted else reasoning,
               external=hosted)


def available(conn: sqlite3.Connection, user: sqlite3.Row, directory) -> str:
    """"ok", "off" (not in the experimental group) or "unset" (no BFGguf set up)."""
    if not is_experimental_user(conn, user, directory):
        return "off"
    return "ok" if configured(load_config(conn)) else "unset"


FALLBACKS = {"off": "BFGguf isn't turned on for you", "unset": "BFGguf isn't set up yet",
             "down": "BFGguf isn't answering"}
