"""Admins: Settings → Balancer nodes (the boxes Erynne's answer model runs on), and swapping a
node between its usual role and BFGguf (bfgguf.py)."""

from __future__ import annotations

import asyncio
import sqlite3

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from mnemerynne import apis, balancer as balancer_mod
from mnemerynne.db import transaction
from mnemerynne.web.admin import _log
from mnemerynne.web.deps import admin_user, get_db

router = APIRouter()


class BalancerBody(BaseModel):
    nodes: str | None = Field(default=None, max_length=4000)      # comma-separated; '' = none (use LLM_URL)
    api_key: str | None = Field(default=None, max_length=2000)    # None: keep; '': remove


class RoleBody(BaseModel):
    url: str = Field(..., max_length=300)
    role: str = Field(..., pattern="^(balancer|bfgguf)$")
    anyway: bool = False   # start the BFGguf even if the node thinks it won't fit


def _status(request: Request, conn: sqlite3.Connection) -> dict:
    cfg = balancer_mod.load_config(conn)
    return {"nodes": cfg["nodes"], "api_key_set": bool(cfg.get("api_key_enc")),
            "fallback": request.app.state.settings.llm_url, "status": request.app.state.balancer.view()}


@router.get("/api/admin/settings/balancer")
def get_balancer(request: Request, user=Depends(admin_user), conn: sqlite3.Connection = Depends(get_db)):
    return _status(request, conn)


@router.put("/api/admin/settings/balancer")
async def set_balancer(body: BalancerBody, request: Request, user=Depends(admin_user),
                       conn: sqlite3.Connection = Depends(get_db)):
    """Every node must answer /v1/balancer/load with the key before the list is saved."""
    settings, balancer = request.app.state.settings, request.app.state.balancer
    cfg = balancer_mod.load_config(conn)
    try:
        nodes = balancer_mod.parse_nodes(body.nodes) if body.nodes is not None else cfg["nodes"]
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from None
    key = body.api_key.strip() if body.api_key is not None else balancer_mod.api_key(settings, cfg)
    if nodes and not key:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT,
                            "Enter the balancers' API key (BALANCER_API_KEY on the balancer).")
    problems = await asyncio.gather(*(balancer.check(n, key) for n in nodes))
    bad = [f"{n} {why}" for n, why in zip(nodes, problems) if why]
    if bad:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Not saved: " + "; ".join(bad) + ".")
    try:
        with transaction(conn):
            balancer_mod.save_config(conn, settings, nodes, body.api_key)
            what = []
            if body.nodes is not None and nodes != cfg["nodes"]:
                what.append(f"was {', '.join(cfg['nodes']) or '(none)'}")
            if body.api_key is not None:
                what.append("key set" if body.api_key.strip() else "key removed")
            _log(conn, user, "settings.balancer", ", ".join(nodes) or "(none)", "; ".join(what))
    except apis.ApiError as exc:   # no MNEMERYNNE_SECRET_KEY to encrypt the key with
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from None
    balancer.configure(nodes, key if nodes else "")
    await balancer.poll_once()
    return _status(request, conn)


@router.post("/api/admin/settings/balancer/role")
async def set_role(body: RoleBody, request: Request, user=Depends(admin_user),
                   conn: sqlite3.Connection = Depends(get_db)):
    """Swap a node between "balancer" (the usual small models) and "bfgguf" (the big model on all its
    cards). The node stops one set and loads the other, which can take a few minutes."""
    balancer = request.app.state.balancer
    try:
        await balancer.set_role(body.url, body.role, body.anyway)
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from None
    with transaction(conn):
        _log(conn, user, "settings.balancer_role", body.url, body.role + (" (anyway)" if body.anyway else ""))
    return _status(request, conn)
