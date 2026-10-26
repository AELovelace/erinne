"""POST /api/bfgguf: the experimental group talks to BFGguf directly (scripts, comparisons).

A plain chat completion: no documents, tools or memories. For Erynne's whole answer on BFGguf, ask
/api/ask with "bfgguf": true. Signed in, or with a personal access key (Authorization: Bearer
mnemerynne_…); the experimental group only; 20 requests a minute per person.
"""

from __future__ import annotations

import logging
import sqlite3
import time
from collections import defaultdict, deque
from typing import Literal

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from mnemerynne import bfgguf
from mnemerynne.clients.llm import USAGE, new_usage
from mnemerynne.web.deps import api_user_or_token, get_db

log = logging.getLogger("mnemerynne.bfgguf")
router = APIRouter()
PER_MINUTE = 20
_recent: dict[int, deque] = defaultdict(deque)


class Message(BaseModel):
    role: Literal["system", "user", "assistant"]
    content: str = Field(..., max_length=100_000)


class CompletionBody(BaseModel):
    messages: list[Message] = Field(..., min_length=1, max_length=50)
    max_tokens: int = Field(1024, ge=1, le=4096)
    temperature: float = Field(0.2, ge=0.0, le=2.0)


def _over_limit(user_id: int) -> bool:
    now, window = time.monotonic(), _recent[user_id]
    while window and now - window[0] > 60:
        window.popleft()
    if len(window) >= PER_MINUTE:
        return True
    window.append(now)
    return False


@router.post("/api/bfgguf")
async def complete(body: CompletionBody, request: Request, user=Depends(api_user_or_token),
                   conn: sqlite3.Connection = Depends(get_db)):
    app = request.app
    state = bfgguf.available(conn, user, app.state.directory)
    if state == "off":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "BFGguf is for the experimental group.")
    if state == "unset":
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "BFGguf isn't set up yet.")
    if _over_limit(user["id"]):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, f"At most {PER_MINUTE} requests a minute.",
                            headers={"Retry-After": "60"})
    cfg = bfgguf.load_config(conn)
    client = bfgguf.client(app.state.settings, cfg, app.state.balancer, getattr(app.state, "bfgguf_transport", None))
    usage = new_usage()
    token = USAGE.set(usage)
    started = time.monotonic()
    try:
        content = await client.chat([m.model_dump() for m in body.messages], temperature=body.temperature,
                                    max_tokens=body.max_tokens)
    except (httpx.HTTPError, OSError) as exc:
        log.warning("BFGguf didn't answer %s: %s", user["username"], exc)
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "BFGguf isn't answering.") from None
    finally:
        USAGE.reset(token)
        await client.aclose()
    model = bfgguf.label(cfg, app.state.balancer)[len(bfgguf.LABEL_PREFIX):]
    log.info("BFGguf for %s: %s, %d in / %d out tokens, %d ms.", user["username"], model, usage["in"],
             usage["out"], round((time.monotonic() - started) * 1000))
    return {"model": model, "content": content, "usage": {"tokens_in": usage["in"], "tokens_out": usage["out"]}}
