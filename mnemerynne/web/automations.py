"""Automator's page and API (mnemerynne/automations.py), and its admin settings: on/off, who may use it,
the webhooks results may go to and the security feeds Erynne may read.

The routes that change automations, runs, webhooks or feeds hold automations.db's connection
(get_automations_db, Phase 9; core's tables still read by their plain names through it). Automator's on/off
and group are core's settings (meta), so those keep core's. The page itself stays with the web app; the API
is the automations service's while it runs."""

from __future__ import annotations

import asyncio
import json
import sqlite3
import time
from datetime import timedelta

import anyio
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel, Field

from mnemerynne import apis, automations as auto, automations_run, events, group_refs, rooms, security_feeds
from mnemerynne.automations_run import footnotes
from mnemerynne.db import transaction
from mnemerynne.ingest import markdown
from mnemerynne.rooms_bus import sse
from mnemerynne.web.admin import _log
from mnemerynne.web.deps import admin_user, api_user, get_automations_db, get_db, is_admin, page_user, release_db

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
               "or packages), open Microsoft 365 security alerts and risky users, and from security_feeds: known "
               "exploited vulnerabilities (kev_only) and CVSS 7+ items that affect our machines (affects_us_only, "
               "min_cvss 7), KEV that affects us first, with the machines. Name any feed whose feed_status has an "
               "error. Say what needs doing first. If nothing is new and every feed works, answer "
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
    with rooms.on(conn) as rooms_conn:
        if rooms_conn is not None and rooms.enabled(rooms_conn):
            room_list = [{"id": r["id"], "name": r["name"], "kind": r["kind"]}
                         for r in rooms.rooms_for(rooms_conn, user, directory) if r.get("erynne", True)]
    return {"automations": [auto.view(conn, r) for r in auto.for_user(conn, user["id"])],
            "tools": tools, "rooms": room_list,
            "destinations": [{"id": d["id"], "name": d["name"], "format": d["format"]}
                             for d in auto.destinations_for(conn, user, directory)],
            "templates": TEMPLATES, "timezone": request.app.state.settings.timezone,
            "claude": (await request.app.state.agent.ready(user["id"], claude=True)) != "off"}


@router.get("/api/automations/prompt-files")
def prompt_files(q: str = "", user=Depends(_on), conn: sqlite3.Connection = Depends(get_db)):
    """The editor's prompt file search: .md and .txt files this person can read whose name holds `q`."""
    from mnemerynne import editing, fs, permissions

    found = fs.search_names(conn, fs.Access.of(conn, user["id"]), q[:200], limit=60, forgiving=True)["files"]
    libraries = {r["id"]: permissions.PRIVATE_LABEL if r["kind"] == "private" else r["name"]   # "My files" for one's own
                 for r in conn.execute("SELECT id, kind, name FROM collections")}
    return {"files": [{"id": f["id"], "name": f["display_name"], "library": libraries.get(f["collection_id"], "")}
                      for f in found if editing.format_of(f["display_name"]) == "text"][:20]}   # text files only


@router.post("/api/automations", status_code=status.HTTP_201_CREATED)
async def create_automation(request: Request, user=Depends(_on), conn: sqlite3.Connection = Depends(get_automations_db)):
    body = await request.json()
    try:
        with transaction(conn):
            new_id = auto.create(conn, request.app.state.settings, user, _dir(request), body if isinstance(body, dict) else {})
    except (auto.AutomationError, apis.ApiError) as exc:
        raise _refuse(auto.AutomationError(str(exc))) from None
    return auto.view(conn, auto.get(conn, new_id))


@router.patch("/api/automations/{automation_id}")
async def update_automation(automation_id: int, request: Request, user=Depends(_on),
                            conn: sqlite3.Connection = Depends(get_automations_db)):
    body = await request.json()
    try:
        with transaction(conn):
            auto.update(conn, request.app.state.settings, user, _dir(request), automation_id,
                        body if isinstance(body, dict) else {})
    except (auto.AutomationError, apis.ApiError) as exc:
        raise _refuse(auto.AutomationError(str(exc))) from None
    return auto.view(conn, auto.get(conn, automation_id))


@router.delete("/api/automations/{automation_id}")
def delete_automation(automation_id: int, user=Depends(_on), conn: sqlite3.Connection = Depends(get_automations_db)):
    try:
        with transaction(conn):
            auto.delete(conn, user["id"], automation_id)
    except auto.AutomationError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from None
    return {"ok": True}


@router.post("/api/automations/{automation_id}/run")
def run_automation(automation_id: int, request: Request, user=Depends(_on),
                   conn: sqlite3.Connection = Depends(get_automations_db)):
    try:
        with transaction(conn):            # here, or queued for the scheduler's process (automations_run.start)
            row = auto.owned(conn, user["id"], automation_id)
            run_id = automations_run.start(conn, row, "manual", request.app.state.settings, _dir(request))
    except auto.AutomationError as exc:
        raise _refuse(exc) from None
    return {"run_id": run_id}


@router.post("/api/automations/runs/{run_id}/cancel")
def cancel_run(run_id: int, user=Depends(_on), conn: sqlite3.Connection = Depends(get_automations_db)):
    """Stop one of the person's runs that's queued, running, waiting for Claude or about to be delivered: it ends
    as 'cancelled' and nothing is delivered (automations_run.cancel). 409 when it has already ended."""
    run = auto.get_run(conn, run_id)
    if run is None or run["owner_id"] != user["id"]:   # theirs only, as starting it is
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such run")
    with transaction(conn):
        cancelled = automations_run.cancel(conn, run_id, user["username"])
    if not cancelled:
        raise HTTPException(status.HTTP_409_CONFLICT, f"It has already ended ({auto.get_run(conn, run_id)['status']}).")
    return _run_body(auto.get_run(conn, run_id))


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
    return _run_body(run)


def _run_body(run: sqlite3.Row) -> dict:
    """A run for the page: its fields, its trace, and its answer rendered from Markdown (raw HTML escaped)."""
    out = auto.run_view(run, trace=True)                # the page shows the raw output too
    body = out["output"] + footnotes(out["output"], out["sources"], "")
    return {**out, "name": run["name"], "html": markdown.render_html(body) if body.strip() else ""}


APPROVAL_DAYS = 14         # the Approvals tab shows proposals from runs this recent (pending ones always)


@router.get("/api/automations/approvals")
def approvals(user=Depends(_on), conn: sqlite3.Connection = Depends(get_db)):
    """The Approvals tab: what the person's automations proposed, by run. Pending ones first (from any run still
    on record), then those decided in the last APPROVAL_DAYS days, with who decided (auto-approved or by hand)."""
    from mnemerynne import proposals

    try:
        runs = conn.execute(
            "SELECT r.id, r.automation_id, r.started_at, r.proposals, a.name, a.auto_approve FROM automation_runs r "
            "JOIN automations a ON a.id = r.automation_id WHERE a.owner_id = ? AND r.proposals <> '[]' "
            "ORDER BY r.id DESC LIMIT 200", (user["id"],)).fetchall()
    except sqlite3.OperationalError:                   # the runs are still core's: no proposals column yet
        runs = []
    items = []
    for run in runs:
        for pid in json.loads(run["proposals"] or "[]"):
            row = conn.execute("SELECT * FROM agent_proposals WHERE id = ? AND user_id = ?", (pid, user["id"])).fetchone()
            if row is None:
                continue                               # purged, or not theirs
            if row["status"] != "pending" and (row["decided_at"] or "") < auto.stamp(
                    auto.now() - timedelta(days=APPROVAL_DAYS)):
                continue                               # decided long ago
            items.append({"run_id": run["id"], "automation_id": run["automation_id"], "automation": run["name"],
                          "run_started_at": run["started_at"] + " UTC", "auto_approve": bool(run["auto_approve"]),
                          "proposal": proposals.view(conn, row)})
    items.sort(key=lambda i: (i["proposal"]["status"] != "pending", -i["run_id"]))   # waiting first, newest first
    return {"approvals": items, "pending": sum(1 for i in items if i["proposal"]["status"] == "pending")}


FINAL = auto.FINAL         # a run in one of these won't change any more (cancelled too)
STREAM_POLL = 2.0          # seconds between looks when no wake comes (Valkey down, another process's run)
STREAM_BEAT = 15.0         # a comment this often keeps proxies from closing a quiet stream
STREAM_GRACE = 10.0        # after the run is final, how long to wait for its last steps (a "finish" step)


def _run_state(settings, run_id: int) -> tuple[sqlite3.Row | None, list[dict]]:
    """The run and its trace, read afresh with a connection of the stream's own."""
    conn = automations_run.connect(settings)
    try:
        run = auto.get_run(conn, run_id)
        if run is None:
            return None, []
        return run, json.loads((run["trace"] if "trace" in run.keys() else "") or "[]")
    finally:
        conn.close()


async def _run_stream(settings, run_id: int, after: int, disconnected):
    """Server-sent events for one run: "step" for each new step of its trace (from step `after` on), "status" when
    its status changes, and "end" with the whole run (its rendered answer too) once it's over."""
    woken = asyncio.Event()

    async def listen() -> None:                       # the runner saved new steps: look at once
        try:
            async for _ in events.subscribe(automations_run.trace_channel(run_id)):
                woken.set()
        except asyncio.CancelledError:
            raise
        except Exception:
            pass                                       # no bus: polling still works

    listener = asyncio.create_task(listen())
    seen, final_at, beat = None, None, time.monotonic()
    try:
        while True:
            run, trace = await asyncio.to_thread(_run_state, settings, run_id)
            if run is None:
                yield sse("end", {"gone": True})
                return
            for step in trace[after:]:
                yield sse("step", step)
            after = max(after, len(trace))
            if run["status"] != seen:
                seen = run["status"]
                yield sse("status", {"status": seen})
            if seen in FINAL:
                final_at = final_at or time.monotonic()
                ended = not trace or trace[-1]["kind"] in ("finish", "error", "cut", "cancelled")
                if ended or time.monotonic() - final_at > STREAM_GRACE:   # older runs have no "finish" step
                    yield sse("end", _run_body(run))
                    return
            if await disconnected():
                return
            woken.clear()
            try:
                await asyncio.wait_for(woken.wait(), 0.5 if final_at else STREAM_POLL)
            except asyncio.TimeoutError:
                pass
            if time.monotonic() - beat >= STREAM_BEAT:
                beat = time.monotonic()
                yield b": ping\n\n"
    finally:
        listener.cancel()
        await asyncio.gather(listener, return_exceptions=True)


@router.get("/api/automations/runs/{run_id}/stream")
async def stream_run(run_id: int, request: Request, after: int = 0, user=Depends(_on),
                     conn: sqlite3.Connection = Depends(get_db)):
    """A run's raw output, live: its trace step by step, then the finished run."""
    run = auto.get_run(conn, run_id)
    if run is None or run["owner_id"] != user["id"]:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such run")
    release_db(request)     # the stream reads with connections of its own; don't hold this one while the tab is open
    return StreamingResponse(_run_stream(request.app.state.settings, run_id, max(0, after), request.is_disconnected),
                             media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# -------------------------------------------------------------------- admin
class SettingsBody(BaseModel):
    enabled: bool | None = None
    group: str | None = Field(None, max_length=2000)


def _admin_status(conn: sqlite3.Connection) -> dict:
    return {"enabled": auto.enabled(conn), "group": group_refs.label(conn, auto.group(conn)),
            "automations": [{"id": r["id"], "owner": r["username"], "name": r["name"], "runner": r["runner"],
                             "when": auto.describe(json.loads(r["schedule"]), r["tz"]), "enabled": bool(r["enabled"]),
                             "paused_reason": r["paused_reason"],
                             "running_run_id": _running(conn, r["id"])}   # the admin page offers Cancel run
                            for r in auto.all_rows(conn)],
            "destinations": [{"id": d["id"], "name": d["name"], "url": d["url"], "format": d["format"],
                              "secret": bool(d["secret_enc"]), "group": group_refs.label(conn, d["group_ref"])}
                             for d in conn.execute("SELECT * FROM automation_destinations ORDER BY name").fetchall()],
            "feeds": _feeds(conn)}


def _running(conn: sqlite3.Connection, automation_id: int) -> int | None:
    """The id of an automation's run that's still going, or None."""
    found = auto.active_run(conn, automation_id)
    return found["id"] if found is not None else None


def _feeds(conn: sqlite3.Connection) -> list[dict]:
    """Every security feed with its health (T-21): the defaults Mnemerynne ships (put in on first look) and the
    admins' own. Never the API key itself: `has_key` says whether one is saved."""
    with security_feeds._db(conn) as fdb:
        if fdb is None:
            return []
        security_feeds.ensure_defaults(fdb)
        return [{"id": f["id"], "key": f["key"] or "", "default": f["key"] is not None, "name": f["name"],
                 "url": f["url"], "kind": f["kind"], "keywords": f["keywords"],
                 "products": json.loads(f["products"] or "[]"), "note": f["note"], "enabled": bool(f["enabled"]),
                 "interval_hours": round(f["interval_seconds"] / 3600, 2), "has_key": f["secret_enc"] is not None,
                 "last_attempt": f["last_attempt"] or "", "last_success": f["last_success"] or "",
                 "error": f["error"] or "", "items_last_fetch": f["items_last_fetch"],
                 "next_poll": security_feeds.iso(security_feeds.next_due(f)) if f["enabled"] else ""}
                for f in fdb.execute("SELECT * FROM automation_feeds ORDER BY id").fetchall()]


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
def admin_pause(automation_id: int, user=Depends(admin_user), conn: sqlite3.Connection = Depends(get_automations_db)):
    row = auto.get(conn, automation_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such automation")
    with transaction(conn):
        auto.pause(conn, automation_id, f"an admin ({user['username']}) paused it.")
        _log(conn, user, "automations.pause", str(automation_id), row["name"])
    return _admin_status(conn)


@admin_router.post("/{automation_id}/cancel")
def admin_cancel(automation_id: int, user=Depends(admin_user), conn: sqlite3.Connection = Depends(get_automations_db)):
    """An admin stops any automation's run that's going (as they may pause any automation); its owner is told."""
    row = auto.get(conn, automation_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such automation")
    with transaction(conn):
        run = auto.active_run(conn, automation_id)
        if run is None or not automations_run.cancel(conn, run["id"], f"an admin ({user['username']})"):
            raise HTTPException(status.HTTP_409_CONFLICT, "It has no run going.")
        auto.tell(conn, row["owner_id"], f"An admin ({user['username']}) cancelled the run of your automation "
                  f"“{row['name']}”.", f"/automations#run={run['id']}")   # the owner hears why it stopped
        _log(conn, user, "automations.cancel", str(automation_id), f"{row['name']} (run {run['id']})")
    return _admin_status(conn)


class DestinationBody(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    url: str = Field(max_length=1000)
    format: str = "json"
    secret: str | None = Field(None, max_length=500)
    group: str = Field("", max_length=2000)


@admin_router.post("/destinations", status_code=status.HTTP_201_CREATED)
async def add_destination(body: DestinationBody, request: Request, user=Depends(admin_user),
                          conn: sqlite3.Connection = Depends(get_automations_db)):
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
def delete_destination(destination_id: int, user=Depends(admin_user), conn: sqlite3.Connection = Depends(get_automations_db)):
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
def add_feed(body: FeedBody, user=Depends(admin_user), conn: sqlite3.Connection = Depends(get_automations_db)):
    url = body.url.strip()
    if not url.startswith("https://") or " " in url:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "A feed's address must start with https://.")
    if body.kind not in security_feeds.KINDS:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT,
                            f"The kind is one of {', '.join(security_feeds.KINDS)}.")
    try:
        with transaction(conn):
            conn.execute("INSERT INTO automation_feeds(name, url, kind, keywords, enabled) VALUES (?, ?, ?, ?, ?)",
                         (body.name.strip(), url, body.kind, body.keywords.strip(), int(body.enabled)))
            _log(conn, user, "automations.feed", body.name.strip(), url[:200])
    except sqlite3.IntegrityError:
        raise HTTPException(status.HTTP_409_CONFLICT, "There's already a feed with that name.") from None
    return _admin_status(conn)


@admin_router.delete("/feeds/{feed_id}")
def delete_feed(feed_id: int, user=Depends(admin_user), conn: sqlite3.Connection = Depends(get_automations_db)):
    with transaction(conn):
        row = conn.execute("SELECT name FROM automation_feeds WHERE id = ?", (feed_id,)).fetchone()
        if row is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "No such feed")
        conn.execute("DELETE FROM automation_feeds WHERE id = ?", (feed_id,))
        _log(conn, user, "automations.feed_delete", row["name"], "")
    return _admin_status(conn)


# ----------------------------------------------------------- security feeds (T-21)
# The defaults ship with Mnemerynne (security_feeds.DEFAULT_FEEDS/DEFAULT_PRODUCTS); here admins switch them on and
# off, enter the NVD API key, poll one now, and adjust the products' CPEs, keywords and Wazuh package names (e.g. the
# Windows Server versions we still run).
class FeedPatch(BaseModel):
    enabled: bool | None = None
    url: str | None = Field(None, max_length=1000)
    keywords: str | None = Field(None, max_length=1000)
    interval_hours: float | None = Field(None, ge=0.5, le=168)
    api_key: str | None = Field(None, max_length=500)      # "" clears it


class ProductBody(BaseModel):
    key: str | None = Field(None, max_length=40, pattern=r"^[a-z0-9][a-z0-9-]*$")
    name: str | None = Field(None, min_length=1, max_length=120)
    vendor: str | None = Field(None, max_length=120)
    cpes: list[str] | None = Field(None, max_length=50)
    keywords: list[str] | None = Field(None, max_length=50)
    packages: list[str] | None = Field(None, max_length=50)
    enabled: bool | None = None


def _security_status(request: Request, conn: sqlite3.Connection) -> dict:
    """What the admin page's Security feeds section shows: feeds with health, products, and the Wazuh log."""
    settings = request.app.state.settings
    feeds = _feeds(conn)                                     # puts the defaults in first
    with security_feeds._db(conn) as fdb:
        prods = security_feeds.products(fdb, enabled_only=False) if fdb is not None else []
    return {"feeds": feeds, "products": prods, "kinds": list(security_feeds.KINDS),
            "log": str(security_feeds.log_path(settings)), "localfile": security_feeds.wazuh_localfile(settings)}


def _clean(values: list[str] | None) -> str | None:
    """A list from the page as stored JSON (blanks dropped); None when it wasn't sent."""
    return None if values is None else json.dumps([v.strip() for v in values if v and v.strip()][:50])


@admin_router.get("/security-feeds")
def security_status(request: Request, user=Depends(admin_user),
                    conn: sqlite3.Connection = Depends(get_automations_db)):
    """The feeds with their health, the products we watch, and where the Wazuh log goes."""
    return _security_status(request, conn)


@admin_router.patch("/feeds/{feed_id}")
def patch_feed(feed_id: int, body: FeedPatch, request: Request, user=Depends(admin_user),
               conn: sqlite3.Connection = Depends(get_automations_db)):
    """Switch a feed on or off, change its address, keywords or interval, or save (or clear) its API key."""
    with transaction(conn):
        row = conn.execute("SELECT * FROM automation_feeds WHERE id = ?", (feed_id,)).fetchone()
        if row is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "No such feed")
        changes: dict = {}
        if body.enabled is not None:
            changes["enabled"] = int(body.enabled)
        if body.url is not None:
            url = body.url.strip()
            if not url.startswith("https://") or " " in url:
                raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "A feed's address must start with https://.")
            changes["url"] = url
        if body.keywords is not None:
            changes["keywords"] = body.keywords.strip()
        if body.interval_hours is not None:
            changes["interval_seconds"] = int(body.interval_hours * 3600)
        if body.api_key is not None:                        # encrypted with MNEMERYNNE_SECRET_KEY, never shown again
            key = body.api_key.strip()
            changes["secret_enc"] = apis.encrypt_secret(request.app.state.settings, key) if key else None
        if changes:
            what = ", ".join(("API key " + ("saved" if changes["secret_enc"] else "cleared")) if k == "secret_enc"
                             else k for k in changes)
            changes["backoff_until"] = None                 # a change is a reason to try again now
            conn.execute("UPDATE automation_feeds SET " + ", ".join(f"{k} = ?" for k in changes) + " WHERE id = ?",
                         (*changes.values(), feed_id))
            _log(conn, user, "automations.feed_change", row["name"], what)
    return _security_status(request, conn)


@admin_router.post("/feeds/{feed_id}/poll")
async def poll_feed(feed_id: int, request: Request, user=Depends(admin_user),
                    conn: sqlite3.Connection = Depends(get_automations_db)):
    """Poll one (enabled) feed now, and push anything urgent to Wazuh as a scheduled poll would."""
    if conn.execute("SELECT 1 FROM automation_feeds WHERE id = ?", (feed_id,)).fetchone() is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such feed")
    security_feeds.clear_cache()
    settings = request.app.state.settings
    done = await anyio.to_thread.run_sync(lambda: security_feeds.poll_due(conn, settings, only=feed_id))
    out = _security_status(request, conn)
    out["polled"] = done
    if not done:
        out["note"] = "Nothing was polled: the feed is off, or another poll is running (try again in a minute)."
    return out


@admin_router.post("/products", status_code=status.HTTP_201_CREATED)
def add_product(body: ProductBody, request: Request, user=Depends(admin_user),
                conn: sqlite3.Connection = Depends(get_automations_db)):
    """A product of our own to watch (key, name, vendor, CPEs, keywords, Wazuh package names)."""
    if not body.key or not body.name:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "A product needs a key and a name.")
    try:
        with transaction(conn):
            conn.execute("INSERT INTO security_products(key, name, vendor, cpes, keywords, packages, enabled) "
                         "VALUES (?, ?, ?, ?, ?, ?, ?)",
                         (body.key, body.name.strip(), (body.vendor or "").strip(), _clean(body.cpes or []),
                          _clean(body.keywords or []), _clean(body.packages or []), int(body.enabled is not False)))
            _log(conn, user, "automations.security_product", body.name.strip(), body.key)
    except sqlite3.IntegrityError:
        raise HTTPException(status.HTTP_409_CONFLICT, "There's already a product with that key.") from None
    return _security_status(request, conn)


@admin_router.patch("/products/{product_id}")
def patch_product(product_id: int, body: ProductBody, request: Request, user=Depends(admin_user),
                  conn: sqlite3.Connection = Depends(get_automations_db)):
    """A product's name, vendor, CPEs, keywords, Wazuh package names, or on/off."""
    with transaction(conn):
        row = conn.execute("SELECT name FROM security_products WHERE id = ?", (product_id,)).fetchone()
        if row is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "No such product")
        given = {"name": body.name.strip() if body.name else None,
                 "vendor": body.vendor.strip() if body.vendor is not None else None,
                 "cpes": _clean(body.cpes), "keywords": _clean(body.keywords), "packages": _clean(body.packages),
                 "enabled": None if body.enabled is None else int(body.enabled)}
        changes = {k: v for k, v in given.items() if v is not None}
        if changes:
            conn.execute("UPDATE security_products SET " + ", ".join(f"{k} = ?" for k in changes) +
                         ", updated_at = datetime('now') WHERE id = ?", (*changes.values(), product_id))
            _log(conn, user, "automations.security_product_change", row["name"], ", ".join(changes))
    security_feeds.clear_cache()                            # new package names: read the inventory again
    return _security_status(request, conn)


@admin_router.delete("/products/{product_id}")
def delete_product(product_id: int, request: Request, user=Depends(admin_user),
                   conn: sqlite3.Connection = Depends(get_automations_db)):
    """Stop watching a product (a default one stays deleted: it's not put back)."""
    with transaction(conn):
        row = conn.execute("SELECT name FROM security_products WHERE id = ?", (product_id,)).fetchone()
        if row is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "No such product")
        conn.execute("DELETE FROM security_products WHERE id = ?", (product_id,))
        _log(conn, user, "automations.security_product_delete", row["name"], "")
    return _security_status(request, conn)
