"""Asking questions (streamed), conversation history and feedback.

A question runs as a background job on the server (a Run), not inside the
HTTP request: the browser watches its events, and if the page is closed or
the connection drops (a long agent turn), the answer is still finished and
saved, and the page picks the events up again from where it was
(/api/runs/{id}/events?after=n). Runs live in memory; a restart ends the
ones in progress, like it always has.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import time
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from mnemerynne import answer as answer_mod
from mnemerynne import db, fs, graph as graph_mod, proposals
from mnemerynne.web.deps import api_user, get_db

log = logging.getLogger("mnemerynne.chat")
router = APIRouter()


class Scope(BaseModel):
    collection_id: int
    folder_id: int | None = None


class AskBody(BaseModel):
    question: str
    conversation_id: str | None = None
    scope: Scope | None = None  # "search in" one collection or folder
    tools: bool = True          # agent group members: let Erynne use tools for this question
    claude: bool = False        # Claude group members: have Claude answer, with their own key
    disabled_tools: list[str] = Field(default_factory=list, max_length=300)   # switched off in Advanced settings


class FeedbackBody(BaseModel):
    rating: int = Field(..., description="1 = helpful, -1 = not helpful")
    comment: str | None = None


RUN_KEEP_SECONDS = 15 * 60   # finished runs stay readable this long (a page catching up)


class Run:
    """One question being answered: its events so far, in order."""

    def __init__(self, user_id: int, conversation_id: str, question: str):
        self.id = uuid.uuid4().hex
        self.user_id, self.conversation_id, self.question = user_id, conversation_id, question
        self.events: list[dict] = []
        self.done = False
        self.finished_at: float | None = None
        self._changed = asyncio.Event()

    def add(self, event: dict) -> None:
        self.events.append(event)
        self._changed.set()

    def finish(self) -> None:
        self.done, self.finished_at = True, time.monotonic()
        self._changed.set()

    async def follow(self, after: int = 0):
        """Events from number `after` on, waiting for new ones until the run ends."""
        i = max(0, after)
        while True:
            while i < len(self.events):
                yield self.events[i]
                i += 1
            if self.done:
                return
            self._changed.clear()
            if i == len(self.events) and not self.done:
                await self._changed.wait()


def _runs(app) -> dict[str, Run]:
    runs = getattr(app.state, "runs", None)
    if runs is None:
        runs = app.state.runs = {}
    now = time.monotonic()
    for run_id in [k for k, r in runs.items() if r.done and now - r.finished_at > RUN_KEEP_SECONDS]:
        del runs[run_id]
    return runs


def running_in(app, conversation_id: str) -> Run | None:
    return next((r for r in _runs(app).values() if r.conversation_id == conversation_id and not r.done), None)


def _stream(run: Run, after: int = 0) -> StreamingResponse:
    async def events():
        async for event in run.follow(after):
            yield _sse(event)
    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


def _sse(event: dict) -> bytes:
    return f"event: {event['type']}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n".encode()


def _own_conversation(conn: sqlite3.Connection, user_id: int, conversation_id: str) -> None:
    if not graph_mod.user_can_read_conversation(conn, user_id, conversation_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Conversation not found")


@router.post("/api/ask")
async def ask(body: AskBody, request: Request, user=Depends(api_user),
              conn: sqlite3.Connection = Depends(get_db)):
    settings = request.app.state.settings
    question = body.question.strip()
    if not question:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Question must not be empty.")
    if len(question) > settings.max_question_chars:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT,
                            f"Questions are limited to {settings.max_question_chars} characters.")

    scope = scope_json = None
    if body.scope:
        try:
            folders = fs.scope_folders(conn, fs.Access.of(conn, user["id"]), body.scope.collection_id,
                                       body.scope.folder_id)
        except fs.FsError as exc:
            raise HTTPException(exc.status, str(exc)) from exc
        scope = {"collection_id": body.scope.collection_id, "folders": sorted(folders) if folders else None}
        scope_json = json.dumps(body.scope.model_dump())

    conversation_id = body.conversation_id
    if conversation_id:
        _own_conversation(conn, user["id"], conversation_id)
        if running_in(request.app, conversation_id):
            raise HTTPException(status.HTTP_409_CONFLICT, "Erynne is still answering the last question here.")
    else:
        conversation_id = uuid.uuid4().hex
        conn.execute("INSERT INTO conversations(id, user_id, title) VALUES (?, ?, ?)",
                     (conversation_id, user["id"], question[:80]))
    conn.execute("INSERT INTO messages(conversation_id, role, content, scope) VALUES (?, 'user', ?, ?)",
                 (conversation_id, question, scope_json))
    user_id = user["id"]
    app = request.app
    run = Run(user_id, conversation_id, question)
    _runs(app)[run.id] = run
    run.add({"type": "conversation", "conversation_id": conversation_id, "run_id": run.id})

    async def work():
        final = None
        try:
            async for event in graph_mod.run_turn(app.state.graph, user_id, conversation_id, question, scope,
                                                  body.tools, body.claude, [t[:100] for t in body.disabled_tools]):
                if event["type"] == "final":
                    final = event
                else:
                    if event["type"] == "sources":
                        await asyncio.to_thread(_add_web_urls, app.state.settings, event["sources"])
                    run.add(event)
        except Exception as exc:  # model server down, etc.
            log.exception("Answering failed")
            final = {"type": "final", "status": "error", "answer": "", "citations": [], "sources": [],
                     "latency_ms": 0, "trace": [], "error": f"{type(exc).__name__}: {exc}"}
        try:
            final["message_id"] = await asyncio.to_thread(_save_answer, app.state.settings, conversation_id, final)
        except Exception:
            log.exception("Couldn't save the answer")
            final["message_id"] = None
        final.pop("trace", None)
        run.add(final)
        run.finish()
        writer = getattr(app.state, "memory_writer", None)
        if writer and final["status"] != "error":
            _background(app, writer(user_id, conversation_id, question))

    _background(app, work())
    return _stream(run)


def _add_web_urls(settings, sources: list[dict]) -> None:
    """Sources from SharePoint get their "Open in SharePoint" address (in place,
    so the final event, which shares these dicts, has it too)."""
    from mnemerynne import sharepoint

    ids = [s.get("file_id") for s in sources if s.get("file_id")]
    if not ids:
        return
    conn = db.connect(settings.db_path)
    try:
        urls = sharepoint.web_urls(conn, ids)
    finally:
        conn.close()
    for s in sources:
        if s.get("file_id") in urls:
            s["web_url"] = urls[s["file_id"]]


def _background(app, coro) -> None:
    task = asyncio.create_task(coro)
    app.state.background.add(task)
    task.add_done_callback(app.state.background.discard)


@router.get("/api/runs/{run_id}/events")
async def run_events(run_id: str, request: Request, after: int = 0, user=Depends(api_user)):
    """A question's events from number `after` on: to pick up an answer again
    after the page was closed or the connection dropped."""
    run = _runs(request.app).get(run_id)
    if run is None or run.user_id != user["id"]:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "That answer is finished or gone; reload the conversation.")
    return _stream(run, after)


def _external(source: dict) -> tuple:
    """An email, event or live SharePoint source: kept as shown (there's no file to look it up by)."""
    if source.get("kind") in ("mail", "event", "sharepoint"):
        return source["kind"], source.get("file"), source.get("location"), source.get("url")
    return None, None, None, None


def _save_answer(settings, conversation_id: str, final: dict) -> int:
    conn = db.connect(settings.db_path)
    try:
        with db.transaction(conn):
            message_id = conn.execute(
                "INSERT INTO messages(conversation_id, role, content, status, model, prompt_version, latency_ms, "
                "route) VALUES (?, 'assistant', ?, ?, ?, ?, ?, ?)",
                (conversation_id, final["answer"] or final.get("error", ""), final["status"],
                 final.get("model") or settings.llm_model, answer_mod.PROMPT_VERSION, final["latency_ms"],
                 final.get("route")),
            ).lastrowid
            conn.executemany(
                "INSERT INTO message_sources(message_id, n, chunk_id, file_id, page_start, anchor, cited, api_call_id, "
                "ext_kind, ext_title, ext_location, ext_url) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [(message_id, s["n"], s["chunk_id"], s["file_id"], s["page_start"], s["anchor"], int(s["cited"]),
                  s.get("api_call_id"), *_external(s)) for s in final["sources"]],
            )
            conn.execute("UPDATE conversations SET updated_at = datetime('now') WHERE id = ?", (conversation_id,))
            graph_mod.log_trace(conn, conversation_id, final.get("trace") or [])
            proposals.attach(conn, final.get("proposals") or [], message_id)
        return message_id
    finally:
        conn.close()


@router.get("/api/conversations")
def list_conversations(user=Depends(api_user), conn: sqlite3.Connection = Depends(get_db)):
    rows = conn.execute("SELECT id, title, updated_at FROM conversations WHERE user_id = ? "
                        "ORDER BY updated_at DESC, rowid DESC LIMIT 100", (user["id"],)).fetchall()
    return {"conversations": [dict(r) for r in rows]}


@router.get("/api/conversations/{conversation_id}")
def get_conversation(conversation_id: str, request: Request, user=Depends(api_user),
                     conn: sqlite3.Connection = Depends(get_db)):
    _own_conversation(conn, user["id"], conversation_id)
    messages = []
    for m in conn.execute("SELECT id, role, content, status, route, model, created_at FROM messages "
                          "WHERE conversation_id = ? ORDER BY id", (conversation_id,)).fetchall():
        sources = []
        if m["role"] == "assistant":
            sources = [dict(s) for s in conn.execute(
                "SELECT ms.n, ms.cited, ms.page_start, ms.anchor, ms.file_id, ms.api_call_id, "
                "COALESCE(f.display_name, ac.api_name) AS file, CASE WHEN ms.api_call_id IS NOT NULL "
                "THEN 'api' ELSE b.kind END AS kind, COALESCE(c.section_path, ac.operation_name) AS section, "
                "ac.at AS api_at, f.web_url, ms.ext_kind, ms.ext_title, ms.ext_location, ms.ext_url "
                "FROM message_sources ms LEFT JOIN files f ON f.id = ms.file_id "
                "LEFT JOIN blobs b ON b.sha256 = f.blob_sha256 LEFT JOIN chunks c ON c.id = ms.chunk_id "
                "LEFT JOIN api_calls ac ON ac.id = ms.api_call_id "
                "WHERE ms.message_id = ? ORDER BY ms.n", (m["id"],))]
            for s in sources:
                kind, title, where, url = (s.pop(k) for k in ("ext_kind", "ext_title", "ext_location", "ext_url"))
                if kind:
                    s.update({"kind": kind, "file": title, "location": where, "url": url})
                    s.pop("api_at", None)
                    continue
                s["url"] = _source_url(s)
                if s["kind"] == "api":
                    s["location"] = f"live, {(s.pop('api_at') or '')[11:16]} UTC"
                else:
                    s.pop("api_at")
        messages.append({**dict(m), "sources": sources})
    drafted = proposals.for_messages(conn, [m["id"] for m in messages if m["role"] == "assistant"])
    for m in messages:
        m["proposals"] = drafted.get(m["id"], [])
    run = running_in(request.app, conversation_id)
    return {"id": conversation_id, "messages": messages, "running": run.id if run else None}


def _source_url(s: dict) -> str | None:
    if s.get("api_call_id"):
        return f"/apis/calls/{s['api_call_id']}"
    if s["file_id"] is None or s["file"] is None:
        return None  # the file was deleted since
    if s["kind"] == "pdf":
        return f"/files/{s['file_id']}/pdf#page={s['page_start'] or 1}"
    return f"/files/{s['file_id']}/view" + (f"#{s['anchor']}" if s["anchor"] else "")


@router.delete("/api/conversations/{conversation_id}")
async def delete_conversation(conversation_id: str, request: Request, user=Depends(api_user),
                              conn: sqlite3.Connection = Depends(get_db)):
    _own_conversation(conn, user["id"], conversation_id)
    conn.execute("DELETE FROM conversations WHERE id = ?", (conversation_id,))
    await request.app.state.checkpointer.adelete_thread(conversation_id)
    return {"status": "deleted"}


@router.post("/api/messages/{message_id}/feedback")
def feedback(message_id: int, body: FeedbackBody, user=Depends(api_user),
             conn: sqlite3.Connection = Depends(get_db)):
    if body.rating not in (1, -1):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Rating must be 1 or -1.")
    row = conn.execute("SELECT c.user_id FROM messages m JOIN conversations c ON c.id = m.conversation_id "
                       "WHERE m.id = ? AND m.role = 'assistant'", (message_id,)).fetchone()
    if row is None or row[0] != user["id"]:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Message not found")
    conn.execute("DELETE FROM feedback WHERE message_id = ? AND user_id = ?", (message_id, user["id"]))
    conn.execute("INSERT INTO feedback(message_id, user_id, rating, comment) VALUES (?, ?, ?, ?)",
                 (message_id, user["id"], body.rating, (body.comment or "")[:2000] or None))
    return {"status": "ok"}
