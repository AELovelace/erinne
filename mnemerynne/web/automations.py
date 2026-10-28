"""Automator's page and API (mnemerynne/automations.py), and its admin settings: on/off, who may use it,
the webhooks results may go to and the security feeds Erynne may read."""

from __future__ import annotations

import json
import sqlite3

import anyio
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from mnemerynne import apis, automations as auto, group_refs, rooms
from mnemerynne.automations_run import footnotes
from mnemerynne.db import transaction
from mnemerynne.ingest import markdown
from mnemerynne.web.admin import _log
from mnemerynne.web.deps import admin_user, api_user, get_db, is_admin, page_user

TEMPLATES = (
    {"key": "briefing", "name": "Morning briefing", "runner": "erynne",
     "schedule": {"every": "day", "at": "07:30", "days": ["mon", "tue", "wed", "thu", "fri"]},
     "deliver": {"inbox": True},
     "prompt": "Brief me for the day: what's waiting for me (unread notes, announcements, room mentions), today's "
               "meetings and anything urgent in my mail, and the figures from the internal systems I can query "
               "that I'd want first thing. Short sections, most important first."},
    {"key": "security", "name": "Security digest", "runner": "erynne",
     "schedule": {"every": "day", "at": "08:00", "days": ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]},
     "deliver": {"inbox": True},
     "prompt": "Write today's security digest: Mnemerynne updates waiting (and any flagged as touching services "
               "or packages), open Microsoft 365 security alerts and risky users, and new advisories in the "
               "security feeds for the products we run. Say what needs doing first. If nothing is new, answer "
               f"with exactly {auto.QUIET}."},
    {"key": "health", "name": "API health check", "runner": "erynne",
     "schedule": {"every": "hours", "n": 4}, "deliver": {"inbox": True},
     "prompt": "Check the internal systems I can query for anything wrong right now (failures, errors, stuck "
               f"jobs, figures far off normal). If everything looks fine, answer with exactly {auto.QUIET}; "
               "otherwise say what's wrong and where."},
)


def _dir(request: Request):
    return request.app.state.directory


def _on(request: Request, user=Depends(api_user), conn: sqlite3.Connection = Depends(get_db)) -> sqlite3.Row:
    """The person, if Automator is on and theirs to use; otherwise it isn't there."""
    if not auto.may_use(conn, request.app.state.settings, user, _dir(request)):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not Found")
    return user


def _page_on(request: Request, user=Depends(page_user), conn: sqlite3.Connection = Depends(get_db)) -> sqlite3.Row:
    if not auto.may_use(conn, request.app.state.settings, user, _dir(request)):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not Found")
    return user


def _scheduler(request: Request):
    scheduler = getattr(request.app.state, "automator", None)
    if scheduler is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Automator isn't running yet; try again shortly.")
    return scheduler


def _refuse(exc: auto.AutomationError):
    return HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc))


router = APIRouter()
admin_router = APIRouter(prefix="/api/admin/automations")


@router.get("/automations", response_class=HTMLResponse)
def automations_page(request: Request, user=Depends(_page_on), conn: sqlite3.Connection = Depends(get_db)):
    return request.app.state.templates.TemplateResponse(request, "automations.html", {
        "user": user, "is_admin": is_admin(request, conn, user), "active": "automations"})


@router.get("/api/automations")
async def list_automations(request: Request, user=Depends(_on), conn: sqlite3.Connection = Depends(get_db)):
    """The person's automations, and what the editor offers: tools, rooms, webhooks, templates."""
    tools = (await request.app.state.agent.tool_list(user["id"], automation=True))["tools"]
    directory = _dir(request)
    room_list = []
    if rooms.enabled(conn):
        room_list = [{"id": r["id"], "name": r["name"], "kind": r["kind"]}
                     for r in rooms.rooms_for(conn, user, directory) if r.get("erynne", True)]
    return {"automations": [auto.view(conn, r) for r in auto.for_user(conn, user["id"])],
            "tools": tools, "rooms": room_list,
            "destinations": [{"id": d["id"], "name": d["name"], "format": d["format"]}
                             for d in auto.destinations_for(conn, user, directory)],
            "templates": TEMPLATES, "timezone": request.app.state.settings.timezone,
            "claude": (await request.app.state.agent.ready(user["id"], claude=True)) != "off"}


@router.post("/api/automations", status_code=status.HTTP_201_CREATED)
async def create_automation(request: Request, user=Depends(_on), conn: sqlite3.Connection = Depends(get_db)):
    body = await request.json()
    try:
        with transaction(conn):
            new_id = auto.create(conn, request.app.state.settings, user, _dir(request), body if isinstance(body, dict) else {})
    except (auto.AutomationError, apis.ApiError) as exc:
        raise _refuse(auto.AutomationError(str(exc))) from None
    return auto.view(conn, auto.get(conn, new_id))


@router.patch("/api/automations/{automation_id}")
async def update_automation(automation_id: int, request: Request, user=Depends(_on),
                            conn: sqlite3.Connection = Depends(get_db)):
    body = await request.json()
    try:
        with transaction(conn):
            auto.update(conn, request.app.state.settings, user, _dir(request), automation_id,
                        body if isinstance(body, dict) else {})
    except (auto.AutomationError, apis.ApiError) as exc:
        raise _refuse(auto.AutomationError(str(exc))) from None
    return auto.view(conn, auto.get(conn, automation_id))


@router.delete("/api/automations/{automation_id}")
def delete_automation(automation_id: int, user=Depends(_on), conn: sqlite3.Connection = Depends(get_db)):
    try:
        with transaction(conn):
            auto.delete(conn, user["id"], automation_id)
    except auto.AutomationError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from None
    return {"ok": True}


@router.post("/api/automations/{automation_id}/run")
def run_automation(automation_id: int, request: Request, user=Depends(_on), conn: sqlite3.Connection = Depends(get_db)):
    scheduler = _scheduler(request)
    try:
        with transaction(conn):
            row = auto.owned(conn, user["id"], automation_id)
            run_id = scheduler.run_now(conn, row, "manual")
    except auto.AutomationError as exc:
        raise _refuse(exc) from None
    return {"run_id": run_id}


@router.get("/api/automations/{automation_id}/runs")
def list_runs(automation_id: int, user=Depends(_on), conn: sqlite3.Connection = Depends(get_db)):
    try:
        auto.owned(conn, user["id"], automation_id)
    except auto.AutomationError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from None
    return {"runs": [auto.run_view(r, full=False) for r in auto.runs(conn, automation_id)]}


@router.get("/api/automations/runs/{run_id}")
def get_run(run_id: int, user=Depends(_on), conn: sqlite3.Connection = Depends(get_db)):
    run = auto.get_run(conn, run_id)
    if run is None or run["owner_id"] != user["id"]:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such run")
    out = auto.run_view(run)
    body = out["output"] + footnotes(out["output"], out["sources"], "")
    return {**out, "name": run["name"], "html": markdown.render_html(body) if body.strip() else ""}


# -------------------------------------------------------------------- admin
class SettingsBody(BaseModel):
    enabled: bool | None = None
    group: str | None = Field(None, max_length=2000)


def _admin_status(conn: sqlite3.Connection) -> dict:
    return {"enabled": auto.enabled(conn), "group": group_refs.label(conn, auto.group(conn)),
            "automations": [{"id": r["id"], "owner": r["username"], "name": r["name"], "runner": r["runner"],
                             "when": auto.describe(json.loads(r["schedule"]), r["tz"]), "enabled": bool(r["enabled"]),
                             "paused_reason": r["paused_reason"]} for r in auto.all_rows(conn)],
            "destinations": [{"id": d["id"], "name": d["name"], "url": d["url"], "format": d["format"],
                              "secret": bool(d["secret_enc"]), "group": group_refs.label(conn, d["group_ref"])}
                             for d in conn.execute("SELECT * FROM automation_destinations ORDER BY name").fetchall()],
            "feeds": [{"id": f["id"], "name": f["name"], "url": f["url"], "kind": f["kind"], "keywords": f["keywords"],
                       "enabled": bool(f["enabled"]), "fetched_at": f["fetched_at"]}
                      for f in conn.execute("SELECT * FROM automation_feeds ORDER BY name").fetchall()]}


async def _group(request: Request, conn: sqlite3.Connection, text: str) -> str:
    if not text.strip():
        return ""
    directory = _dir(request)
    known = await anyio.to_thread.run_sync(directory.groups) if directory.enabled else []
    try:
        return group_refs.resolve(conn, text, known)
    except group_refs.GroupRefError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from None


@admin_router.get("")
def admin_status(user=Depends(admin_user), conn: sqlite3.Connection = Depends(get_db)):
    return _admin_status(conn)


@admin_router.put("")
async def admin_settings(body: SettingsBody, request: Request, user=Depends(admin_user),
                         conn: sqlite3.Connection = Depends(get_db)):
    """Automator on or off, and the group that may use it (admins always may)."""
    name = await _group(request, conn, body.group) if body.group is not None else None
    with transaction(conn):
        if body.enabled is not None and body.enabled != auto.enabled(conn):
            auto.set_enabled(conn, body.enabled)
            _log(conn, user, "settings.automations", "on" if body.enabled else "off")
        if name is not None:
            auto.set_group(conn, name)
            _log(conn, user, "settings.automations_group", group_refs.label(conn, name) or "(admins only)")
    return _admin_status(conn)


@admin_router.post("/{automation_id}/pause")
def admin_pause(automation_id: int, user=Depends(admin_user), conn: sqlite3.Connection = Depends(get_db)):
    row = auto.get(conn, automation_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such automation")
    with transaction(conn):
        auto.pause(conn, automation_id, f"an admin ({user['username']}) paused it.")
        _log(conn, user, "automations.pause", str(automation_id), row["name"])
    return _admin_status(conn)


class DestinationBody(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    url: str = Field(max_length=1000)
    format: str = "json"
    secret: str | None = Field(None, max_length=500)
    group: str = Field("", max_length=2000)


@admin_router.post("/destinations", status_code=status.HTTP_201_CREATED)
async def add_destination(body: DestinationBody, request: Request, user=Depends(admin_user),
                          conn: sqlite3.Connection = Depends(get_db)):
    url = body.url.strip()
    if not url.startswith("https://") or " " in url:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "A webhook's address must start with https://.")
    if body.format not in ("json", "teams", "slack"):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "The format is json, teams or slack.")
    group_ref = await _group(request, conn, body.group)
    try:
        secret = apis.encrypt_secret(request.app.state.settings, body.secret.strip()) if body.secret and body.secret.strip() else None
    except apis.ApiError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from None
    try:
        with transaction(conn):
            conn.execute("INSERT INTO automation_destinations(name, url, format, secret_enc, group_ref) "
                         "VALUES (?, ?, ?, ?, ?)", (body.name.strip(), url, body.format, secret, group_ref))
            _log(conn, user, "automations.destination", body.name.strip(), url[:200])
    except sqlite3.IntegrityError:
        raise HTTPException(status.HTTP_409_CONFLICT, "There's already a webhook with that name.") from None
    return _admin_status(conn)


@admin_router.delete("/destinations/{destination_id}")
def delete_destination(destination_id: int, user=Depends(admin_user), conn: sqlite3.Connection = Depends(get_db)):
    with transaction(conn):
        row = conn.execute("SELECT name FROM automation_destinations WHERE id = ?", (destination_id,)).fetchone()
        if row is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "No such webhook")
        conn.execute("DELETE FROM automation_destinations WHERE id = ?", (destination_id,))
        # Automations that sent there stop doing so.
        for a in conn.execute("SELECT id, deliver FROM automations").fetchall():
            deliver = json.loads(a["deliver"])
            if destination_id in deliver.get("destinations", []):
                deliver["destinations"] = [d for d in deliver["destinations"] if d != destination_id]
                conn.execute("UPDATE automations SET deliver = ? WHERE id = ?", (json.dumps(deliver), a["id"]))
        _log(conn, user, "automations.destination_delete", row["name"], "")
    return _admin_status(conn)


class FeedBody(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    url: str = Field(max_length=1000)
    kind: str
    keywords: str = Field("", max_length=1000)
    enabled: bool = True


@admin_router.post("/feeds", status_code=status.HTTP_201_CREATED)
def add_feed(body: FeedBody, user=Depends(admin_user), conn: sqlite3.Connection = Depends(get_db)):
    url = body.url.strip()
    if not url.startswith("https://") or " " in url:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "A feed's address must start with https://.")
    if body.kind not in ("rss", "atom", "json_kev", "nvd"):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "The kind is rss, atom, json_kev or nvd.")
    try:
        with transaction(conn):
            conn.execute("INSERT INTO automation_feeds(name, url, kind, keywords, enabled) VALUES (?, ?, ?, ?, ?)",
                         (body.name.strip(), url, body.kind, body.keywords.strip(), int(body.enabled)))
            _log(conn, user, "automations.feed", body.name.strip(), url[:200])
    except sqlite3.IntegrityError:
        raise HTTPException(status.HTTP_409_CONFLICT, "There's already a feed with that name.") from None
    return _admin_status(conn)


@admin_router.delete("/feeds/{feed_id}")
def delete_feed(feed_id: int, user=Depends(admin_user), conn: sqlite3.Connection = Depends(get_db)):
    with transaction(conn):
        row = conn.execute("SELECT name FROM automation_feeds WHERE id = ?", (feed_id,)).fetchone()
        if row is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "No such feed")
        conn.execute("DELETE FROM automation_feeds WHERE id = ?", (feed_id,))
        _log(conn, user, "automations.feed_delete", row["name"], "")
    return _admin_status(conn)
