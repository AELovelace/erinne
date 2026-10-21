"""Asking questions (streamed), conversation history and feedback."""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from erinne import answer as answer_mod
from erinne import db, graph as graph_mod
from erinne.web.deps import api_user, get_db

log = logging.getLogger("erinne.chat")
router = APIRouter()


class AskBody(BaseModel):
    question: str
    conversation_id: str | None = None


class FeedbackBody(BaseModel):
    rating: int = Field(..., description="1 = helpful, -1 = not helpful")
    comment: str | None = None


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

    conversation_id = body.conversation_id
    if conversation_id:
        _own_conversation(conn, user["id"], conversation_id)
    else:
        conversation_id = uuid.uuid4().hex
        conn.execute("INSERT INTO conversations(id, user_id, title) VALUES (?, ?, ?)",
                     (conversation_id, user["id"], question[:80]))
    conn.execute("INSERT INTO messages(conversation_id, role, content) VALUES (?, 'user', ?)",
                 (conversation_id, question))
    user_id = user["id"]
    app = request.app

    async def events():
        yield _sse({"type": "conversation", "conversation_id": conversation_id})
        final = None
        try:
            async for event in graph_mod.run_turn(app.state.graph, user_id, conversation_id, question):
                if event["type"] == "final":
                    final = event
                else:
                    yield _sse(event)
        except Exception as exc:  # model server down, etc.
            log.exception("Answering failed")
            final = {"type": "final", "status": "error", "answer": "", "citations": [], "sources": [],
                     "latency_ms": 0, "trace": [], "error": f"{type(exc).__name__}: {exc}"}
        message_id = _save_answer(app.state.settings, conversation_id, final)
        final["message_id"] = message_id
        final.pop("trace", None)
        yield _sse(final)
        writer = getattr(app.state, "memory_writer", None)
        if writer and final["status"] != "error":
            app.state.background.add(asyncio.create_task(writer(user_id, conversation_id, question)))

    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


def _save_answer(settings, conversation_id: str, final: dict) -> int:
    conn = db.connect(settings.db_path)
    try:
        with db.transaction(conn):
            message_id = conn.execute(
                "INSERT INTO messages(conversation_id, role, content, status, model, prompt_version, latency_ms, "
                "route) VALUES (?, 'assistant', ?, ?, ?, ?, ?, ?)",
                (conversation_id, final["answer"] or final.get("error", ""), final["status"],
                 settings.llm_model, answer_mod.PROMPT_VERSION, final["latency_ms"], final.get("route")),
            ).lastrowid
            conn.executemany(
                "INSERT INTO message_sources(message_id, n, chunk_id, file_id, page_start, anchor, cited) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                [(message_id, s["n"], s["chunk_id"], s["file_id"], s["page_start"], s["anchor"], int(s["cited"]))
                 for s in final["sources"]],
            )
            conn.execute("UPDATE conversations SET updated_at = datetime('now') WHERE id = ?", (conversation_id,))
            graph_mod.log_trace(conn, conversation_id, final.get("trace") or [])
        return message_id
    finally:
        conn.close()


@router.get("/api/conversations")
def list_conversations(user=Depends(api_user), conn: sqlite3.Connection = Depends(get_db)):
    rows = conn.execute("SELECT id, title, updated_at FROM conversations WHERE user_id = ? "
                        "ORDER BY updated_at DESC, rowid DESC LIMIT 100", (user["id"],)).fetchall()
    return {"conversations": [dict(r) for r in rows]}


@router.get("/api/conversations/{conversation_id}")
def get_conversation(conversation_id: str, user=Depends(api_user), conn: sqlite3.Connection = Depends(get_db)):
    _own_conversation(conn, user["id"], conversation_id)
    messages = []
    for m in conn.execute("SELECT id, role, content, status, route, created_at FROM messages "
                          "WHERE conversation_id = ? ORDER BY id", (conversation_id,)).fetchall():
        sources = []
        if m["role"] == "assistant":
            sources = [dict(s) for s in conn.execute(
                "SELECT ms.n, ms.cited, ms.page_start, ms.anchor, ms.file_id, f.display_name AS file, "
                "b.kind, c.section_path AS section "
                "FROM message_sources ms LEFT JOIN files f ON f.id = ms.file_id "
                "LEFT JOIN blobs b ON b.sha256 = f.blob_sha256 LEFT JOIN chunks c ON c.id = ms.chunk_id "
                "WHERE ms.message_id = ? ORDER BY ms.n", (m["id"],))]
            for s in sources:
                s["url"] = _source_url(s)
        messages.append({**dict(m), "sources": sources})
    return {"id": conversation_id, "messages": messages}


def _source_url(s: dict) -> str | None:
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
