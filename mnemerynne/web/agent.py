"""Erynne's agent: applying or dismissing her proposed file changes (or
Claude's, in chat), admins approving or rejecting the requests that wait for
them, and the admin settings (who gets the agent, which model it uses, and who
may ask Claude in chat)."""

from __future__ import annotations

import sqlite3

import anyio
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from mnemerynne import accounts, agent as agent_mod, apis, bfgguf, claude_chat, editing, group_refs, proposals
from mnemerynne.db import transaction
from mnemerynne.web.admin import _log
from mnemerynne.web.deps import admin_user, api_user, get_db, is_admin, page_user

router = APIRouter()


def _refused(exc: Exception) -> HTTPException:
    return HTTPException(getattr(exc, "status", status.HTTP_400_BAD_REQUEST), str(exc))


# ---------------------------------------------------------------- proposals
class ApplyBody(BaseModel):
    items: list[int] | None = Field(None, max_length=100)   # a batch: the items to run (all when left out)


@router.post("/api/agent/proposals/{proposal_id}/apply")
def apply_proposal(proposal_id: int, request: Request, body: ApplyBody | None = None, user=Depends(api_user),
                   conn: sqlite3.Connection = Depends(get_db)):
    """Mail drafts: still in the agent group. File changes: the agent group anywhere they may edit,
    everyone else only in their own Agents folder. API actions check instead that the person may still
    use the operation, and deletes bin with the person's own rights (both can come from Claude's connector).
    Access requests switch AI edit on with the person's own rights; a whole library needs an admin."""
    from mnemerynne import m365

    try:
        row = proposals.get(conn, proposal_id, user["id"])
        if row["kind"] not in ("api", "batch", "delete", "access") and not accounts.is_agent_user(
                conn, user, request.app.state.directory):
            if row["kind"] == "mail" or not proposals.in_agents_folder(conn, user["id"], row):
                raise HTTPException(status.HTTP_403_FORBIDDEN, "Outside the agent group, Erynne only changes files "
                                                               "in your Agents folder.")
        out = proposals.apply(conn, request.app.state.settings, user, proposal_id, request.app.state.directory,
                              admin=row["kind"] == "access" and is_admin(request, conn, user),
                              items=body.items if body else None)
    except (editing.EditError, proposals.ProposalError, m365.M365Error) as exc:
        raise _refused(exc) from None
    _log(conn, user, "proposal.apply", out["summary"][:200], f"#{proposal_id} ({out['requested_via']})")
    return out


@router.get("/api/me/tools")
async def my_tools(request: Request, user=Depends(api_user)):
    """The tools chat's Advanced settings lists for this person (switching one on never adds access)."""
    return await request.app.state.agent.tool_list(user["id"])


@router.get("/proposals", response_class=HTMLResponse)
def proposals_page(request: Request, user=Depends(page_user), conn: sqlite3.Connection = Depends(get_db)):
    """Where Claude's connector sends people to apply what it proposed (chat shows them under answers)."""
    return request.app.state.templates.TemplateResponse(request, "proposals.html", {
        "user": user, "is_admin": is_admin(request, conn, user), "active": "proposals"})


@router.get("/api/agent/proposals")
def list_proposals(user=Depends(api_user), conn: sqlite3.Connection = Depends(get_db)):
    """The person's pending proposals, then the last few decided ones."""
    rows = conn.execute("SELECT * FROM agent_proposals WHERE user_id = ? AND (status = 'pending' OR "
                        "decided_at > datetime('now', '-7 days')) ORDER BY status <> 'pending', id DESC LIMIT 50",
                        (user["id"],)).fetchall()
    return {"proposals": [proposals.view(conn, r) for r in rows]}


class DismissBody(BaseModel):
    note: str = Field("", max_length=1000)   # why, shown to the person's Claude (get_proposal)


@router.post("/api/agent/proposals/{proposal_id}/dismiss")
def dismiss_proposal(proposal_id: int, request: Request, body: DismissBody | None = None, user=Depends(api_user),
                     conn: sqlite3.Connection = Depends(get_db)):
    # Anyone may dismiss their own proposals, even after leaving the group.
    try:
        out = proposals.dismiss(conn, request.app.state.settings, user["id"], proposal_id,
                                note=body.note if body else "")
    except editing.EditError as exc:
        raise _refused(exc) from None
    if out["status"] == "dismissed" and out.get("decided_at"):
        _log(conn, user, "proposal.dismiss", out["summary"][:200],
             f"#{proposal_id} ({out['requested_via']})" + (f": {body.note}" if body and body.note else ""))
    return out


# ------------------------------------------------------------ admin approvals
class DecisionBody(BaseModel):
    note: str = Field("", max_length=1000)   # shown to the requester and their Claude


@router.get("/api/admin/proposals")
def admin_proposals(user=Depends(admin_user), conn: sqlite3.Connection = Depends(get_db)):
    """Access → Approvals: requests waiting for an admin, then the ones decided in the last week."""
    return {"requests": proposals.for_admins(conn)}


@router.post("/api/admin/proposals/{proposal_id}/approve")
def approve_proposal(proposal_id: int, body: DecisionBody, request: Request, user=Depends(admin_user),
                     conn: sqlite3.Connection = Depends(get_db)):
    try:
        out = proposals.approve(conn, request.app.state.settings, user, proposal_id, body.note,
                                request.app.state.directory)
    except (editing.EditError, proposals.ProposalError) as exc:
        raise _refused(exc) from None
    _log(conn, user, "proposal.approve", out["file"], f"#{proposal_id}" + (f": {body.note}" if body.note else ""))
    return out


@router.post("/api/admin/proposals/{proposal_id}/reject")
def reject_proposal(proposal_id: int, body: DecisionBody, user=Depends(admin_user),
                    conn: sqlite3.Connection = Depends(get_db)):
    try:
        out = proposals.reject(conn, user, proposal_id, body.note)
    except (editing.EditError, proposals.ProposalError) as exc:
        raise _refused(exc) from None
    _log(conn, user, "proposal.reject", out["file"], f"#{proposal_id}" + (f": {body.note}" if body.note else ""))
    return out


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


# ------------------------------------------------------------ Claude in chat
async def _claude_status(request: Request, conn: sqlite3.Connection) -> dict:
    """Who may ask Claude in chat, and which of them saved their own key."""
    directory = request.app.state.directory
    group = claude_chat.claude_group(conn)
    members = None
    if group:
        rows = conn.execute("SELECT u.*, c.user_id AS has_key FROM users u LEFT JOIN user_claude c "
                            "ON c.user_id = u.id WHERE u.active = 1").fetchall()
        members = sorted(({"username": r["username"], "key": r["has_key"] is not None} for r in rows
                          if claude_chat.is_claude_user(conn, r, directory)), key=lambda m: m["username"].lower())
    ad = await anyio.to_thread.run_sync(directory.groups)
    return {"group": group_refs.label(conn, group), "domain": request.app.state.settings.ad_domain,
            "enabled": directory.enabled, "groups": group_refs.choices(conn, ad), "members": members,
            "secret_key_set": bool(request.app.state.settings.secret_key)}


@router.get("/api/admin/settings/claude")
async def get_claude(request: Request, user=Depends(admin_user), conn: sqlite3.Connection = Depends(get_db)):
    return await _claude_status(request, conn)


@router.put("/api/admin/settings/claude-group")
async def set_claude_group(body: AgentGroupBody, request: Request, user=Depends(admin_user),
                           conn: sqlite3.Connection = Depends(get_db)):
    directory = request.app.state.directory
    known = await anyio.to_thread.run_sync(directory.groups) if directory.enabled else []
    try:
        name = group_refs.resolve(conn, body.group, known)
    except group_refs.GroupRefError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from None
    with transaction(conn):
        old = group_refs.label(conn, claude_chat.claude_group(conn))
        claude_chat.set_claude_group(conn, name)
        _log(conn, user, "settings.claude_group", group_refs.label(conn, name) or "(nobody)",
             f"was {old or '(nobody)'}")
    return await _claude_status(request, conn)


# -------------------------------------------------- experimental (BFGguf)
class BfggufBody(BaseModel):
    kind: str = Field(..., pattern="^(balancer|local|openai)$")
    url: str = Field("", max_length=500)
    model: str = Field("", max_length=200)
    timeout: int = bfgguf.DEFAULT_TIMEOUT
    api_key: str | None = Field(default=None, max_length=2000)   # None: keep; '': remove


async def _experimental_status(request: Request, conn: sqlite3.Connection) -> dict:
    """Who is in the experimental group, and where BFGguf runs (never its key)."""
    directory = request.app.state.directory
    group = bfgguf.experimental_group(conn)
    members = None
    if group:
        rows = conn.execute("SELECT * FROM users WHERE active = 1").fetchall()
        members = sorted((r["username"] for r in rows if bfgguf.is_experimental_user(conn, r, directory)),
                         key=str.lower)
    ad = await anyio.to_thread.run_sync(directory.groups)
    nodes = [n for n in request.app.state.balancer.view() if n["role"] == "bfgguf"]
    return {"group": group_refs.label(conn, group), "domain": request.app.state.settings.ad_domain,
            "enabled": directory.enabled, "groups": group_refs.choices(conn, ad), "members": members,
            "bfgguf": bfgguf.view(bfgguf.load_config(conn)), "bfgguf_nodes": nodes,
            "secret_key_set": bool(request.app.state.settings.secret_key)}


@router.get("/api/admin/settings/experimental")
async def get_experimental(request: Request, user=Depends(admin_user), conn: sqlite3.Connection = Depends(get_db)):
    return await _experimental_status(request, conn)


@router.put("/api/admin/settings/experimental-group")
async def set_experimental_group(body: AgentGroupBody, request: Request, user=Depends(admin_user),
                                 conn: sqlite3.Connection = Depends(get_db)):
    directory = request.app.state.directory
    known = await anyio.to_thread.run_sync(directory.groups) if directory.enabled else []
    try:
        name = group_refs.resolve(conn, body.group, known)
    except group_refs.GroupRefError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from None
    with transaction(conn):
        old = group_refs.label(conn, bfgguf.experimental_group(conn))
        bfgguf.set_experimental_group(conn, name)
        _log(conn, user, "settings.experimental_group", group_refs.label(conn, name) or "(nobody)",
             f"was {old or '(nobody)'}")
    return await _experimental_status(request, conn)


@router.put("/api/admin/settings/bfgguf")
async def set_bfgguf(body: BfggufBody, request: Request, user=Depends(admin_user),
                     conn: sqlite3.Connection = Depends(get_db)):
    """Where BFGguf runs. It must answer with the new settings before they are saved."""
    app = request.app
    try:
        checked = bfgguf.check(body.kind, body.url, body.model, body.timeout)
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from None
    try:
        trial = bfgguf.merged(app.state.settings, bfgguf.load_config(conn), checked, body.api_key)
    except apis.ApiError as exc:   # no MNEMERYNNE_SECRET_KEY to encrypt the key with
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from None
    client = bfgguf.client(app.state.settings, trial, app.state.balancer, getattr(app.state, "bfgguf_transport", None))
    try:
        ok = await client.reachable()
    finally:
        await client.aclose()
    if not ok:
        where = "No balancer node is in the BFGguf role and ready" if checked["kind"] == "balancer" \
            else f"{checked['url']} didn't answer (/health or /v1/models)"
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"Not saved: {where}.")
    with transaction(conn):
        bfgguf.save_config(conn, trial)
        _log(conn, user, "settings.bfgguf", checked["kind"],
             " ".join(x for x in (checked["url"], checked["model"]) if x)
             + ("; key set" if body.api_key else "; key removed" if body.api_key == "" else ""))
    return await _experimental_status(request, conn)
