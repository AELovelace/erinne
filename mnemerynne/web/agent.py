"""Erynne's agent: applying or dismissing her proposed file changes, and the
admin settings (who gets the agent, which model it uses)."""

from __future__ import annotations

import sqlite3

import anyio
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from mnemerynne import accounts, agent as agent_mod, editing, group_refs, proposals
from mnemerynne.db import transaction
from mnemerynne.web.admin import _log
from mnemerynne.web.deps import admin_user, api_user, get_db

router = APIRouter()


def _agent_user(request: Request, user=Depends(api_user), conn: sqlite3.Connection = Depends(get_db)):
    """Still in the agent group (checked again for every Apply)."""
    if not accounts.is_agent_user(conn, user, request.app.state.directory):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Erynne's tools aren't turned on for you.")
    return user


def _refused(exc: Exception) -> HTTPException:
    return HTTPException(getattr(exc, "status", status.HTTP_400_BAD_REQUEST), str(exc))


# ---------------------------------------------------------------- proposals
@router.post("/api/agent/proposals/{proposal_id}/apply")
def apply_proposal(proposal_id: int, request: Request, user=Depends(_agent_user),
                   conn: sqlite3.Connection = Depends(get_db)):
    from mnemerynne import m365

    try:
        return proposals.apply(conn, request.app.state.settings, user, proposal_id)
    except (editing.EditError, proposals.ProposalError, m365.M365Error) as exc:
        raise _refused(exc) from None


@router.post("/api/agent/proposals/{proposal_id}/dismiss")
def dismiss_proposal(proposal_id: int, request: Request, user=Depends(api_user),
                     conn: sqlite3.Connection = Depends(get_db)):
    # Anyone may dismiss their own proposals, even after leaving the group.
    try:
        return proposals.dismiss(conn, request.app.state.settings, user["id"], proposal_id)
    except editing.EditError as exc:
        raise _refused(exc) from None


# ------------------------------------------------------------------- admin
class AgentGroupBody(BaseModel):
    group: str = Field(default="", max_length=2000)   # ';'-separated, AD or "Tenant / Group"; '' turns it off


class AgentModelBody(BaseModel):
    timeout: int | None = None
    max_steps: int | None = None
    max_tokens: int | None = None
    reasoning_tokens: int | None = None
    context_tokens: int | None = None
    thinking: bool | None = None


async def _status(request: Request, conn: sqlite3.Connection) -> dict:
    """The agent settings plus whether its model (balancer nodes, then MNEMERYNNE_LLM_URL) answers now."""
    directory = request.app.state.directory
    cfg = agent_mod.model_config(conn)
    agent = getattr(request.app.state, "agent", None)
    reachable = await agent.reachable(cfg) if agent is not None else None
    balancer = getattr(request.app.state, "balancer", None)
    members = None
    group = accounts.agent_group(conn)
    if group:
        rows = conn.execute("SELECT * FROM users WHERE active = 1").fetchall()
        members = sorted(r["username"] for r in rows if accounts.is_agent_user(conn, r, directory))
    ad = await anyio.to_thread.run_sync(directory.groups)
    return {"group": group_refs.label(conn, group), "domain": request.app.state.settings.ad_domain,
            "enabled": directory.enabled, "groups": group_refs.choices(conn, ad), "members": members,
            "model": cfg, "reachable": reachable, "fallback": request.app.state.settings.llm_url,
            "nodes": len(balancer.nodes) if balancer is not None else 0}


@router.get("/api/admin/settings/agent")
async def get_agent(request: Request, user=Depends(admin_user), conn: sqlite3.Connection = Depends(get_db)):
    return await _status(request, conn)


@router.put("/api/admin/settings/agent-group")
async def set_agent_group(body: AgentGroupBody, request: Request, user=Depends(admin_user),
                          conn: sqlite3.Connection = Depends(get_db)):
    directory = request.app.state.directory
    known = await anyio.to_thread.run_sync(directory.groups) if directory.enabled else []
    try:
        name = group_refs.resolve(conn, body.group, known)
    except group_refs.GroupRefError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from None
    with transaction(conn):
        old = group_refs.label(conn, accounts.agent_group(conn))
        accounts.set_agent_group(conn, name)
        _log(conn, user, "settings.agent_group", group_refs.label(conn, name) or "(nobody)",
             f"was {old or '(nobody)'}")
    return await _status(request, conn)


@router.put("/api/admin/settings/agent-model")
async def set_agent_model(body: AgentModelBody, request: Request, user=Depends(admin_user),
                          conn: sqlite3.Connection = Depends(get_db)):
    changes = body.model_dump()
    try:
        with transaction(conn):
            cfg = agent_mod.save_model_config(conn, request.app.state.settings, changes)
            what = ", ".join(f"{k} {v}" for k, v in changes.items() if v is not None)
            _log(conn, user, "settings.agent_model", "limits", what)
    except (ValueError, TypeError) as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from None
    return await _status(request, conn)
