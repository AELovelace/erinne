"""Feedback archive: every rating, kept indefinitely for admins and training data.

`feedback` holds the current rating of an answer and goes when the conversation
or the user does. When someone rates, `archive` also copies a snapshot into
`feedback_archive` (migration 0038): the question, the answer and its route,
model and prompt version, each source with the text it had then, and the
agent's tool calls for that answer. Nothing points back with a foreign key, so
deleting the conversation, the user or a file, or re-indexing it, never
changes or removes an archived row.

`backfill` archives feedback given before the archive existed (or written
some other way); the app runs it at start-up.
"""

from __future__ import annotations

import json
import sqlite3

# Text of an API source kept with the snapshot (api_calls keeps its own excerpt too).
API_EXCERPT_CHARS = 20_000


def _question(conn: sqlite3.Connection, answer: sqlite3.Row) -> sqlite3.Row | None:
    return conn.execute("SELECT content, scope, created_at FROM messages WHERE conversation_id = ? "
                        "AND role = 'user' AND id < ? ORDER BY id DESC LIMIT 1",
                        (answer["conversation_id"], answer["id"])).fetchone()


def _sources(conn: sqlite3.Connection, message_id: int) -> list[dict]:
    rows = conn.execute(
        "SELECT ms.n, ms.cited, ms.chunk_id, ms.file_id, ms.page_start, ms.anchor, ms.ext_kind, ms.ext_title, "
        "ms.ext_location, ms.ext_url, ms.api_call_id, f.display_name AS file, c.section_path, c.page_end, c.text, "
        "a.api_name, a.operation_name, a.params AS api_params, a.at AS api_at, a.response_excerpt "
        "FROM message_sources ms LEFT JOIN files f ON f.id = ms.file_id LEFT JOIN chunks c ON c.id = ms.chunk_id "
        "LEFT JOIN api_calls a ON a.id = ms.api_call_id WHERE ms.message_id = ? ORDER BY ms.n",
        (message_id,)).fetchall()
    out = []
    for r in rows:
        source = {k: r[k] for k in ("n", "chunk_id", "file_id", "file", "section_path", "page_start", "page_end",
                                    "anchor", "text") if r[k] is not None}
        source["cited"] = bool(r["cited"])
        if r["ext_kind"]:
            source.update(ext_kind=r["ext_kind"], ext_title=r["ext_title"], ext_location=r["ext_location"],
                          ext_url=r["ext_url"])
        if r["api_call_id"] is not None:
            source.update(api_call_id=r["api_call_id"], api=r["api_name"], operation=r["operation_name"],
                          api_params=r["api_params"], api_at=r["api_at"],
                          text=(r["response_excerpt"] or "")[:API_EXCERPT_CHARS])
        out.append(source)
    return out


def _tool_calls(conn: sqlite3.Connection, conversation_id: str, since: str | None, until: str) -> list[dict]:
    """The agent's tool calls between the question and the answer (none outside the agent route)."""
    rows = conn.execute(
        "SELECT at, tool, args, file_ids, ok, ms, error FROM agent_calls WHERE conversation_id = ? "
        "AND at >= ? AND at <= ? ORDER BY id", (conversation_id, since or until, until)).fetchall()
    return [{"at": r["at"], "tool": r["tool"], "args": json.loads(r["args"] or "{}"),
             "file_ids": json.loads(r["file_ids"] or "[]"), "ok": bool(r["ok"]), "ms": r["ms"],
             "error": r["error"]} for r in rows]


def archive(conn: sqlite3.Connection, feedback_id: int) -> int | None:
    """Copy one feedback row, with its snapshot, into the archive. Returns the archive id (None if the feedback
    or its answer is already gone). Call it in the same transaction as the insert."""
    f = conn.execute("SELECT * FROM feedback WHERE id = ?", (feedback_id,)).fetchone()
    if f is None:
        return None
    answer = conn.execute("SELECT * FROM messages WHERE id = ?", (f["message_id"],)).fetchone()
    if answer is None:
        return None
    question = _question(conn, answer)
    user = conn.execute("SELECT username FROM users WHERE id = ?", (f["user_id"],)).fetchone() \
        if f["user_id"] is not None else None
    cur = conn.execute(
        "INSERT INTO feedback_archive(feedback_id, rated_at, message_id, conversation_id, user_id, "
        "username, rating, comment, question, question_scope, answer, answered_at, status, route, model, "
        "prompt_version, sources, tool_calls) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (f["id"], f["created_at"], answer["id"], answer["conversation_id"], f["user_id"],
         user["username"] if user else None, f["rating"], f["comment"],
         question["content"] if question else None, question["scope"] if question else None,
         answer["content"], answer["created_at"], answer["status"], answer["route"], answer["model"],
         answer["prompt_version"], json.dumps(_sources(conn, answer["id"]), ensure_ascii=False),
         json.dumps(_tool_calls(conn, answer["conversation_id"], question["created_at"] if question else None,
                                answer["created_at"]), ensure_ascii=False)))
    return cur.lastrowid


def backfill(conn: sqlite3.Connection) -> int:
    """Archive every feedback row that isn't yet (start-up). Returns how many were added. Matched on what was
    rated, by whom, when and how, not on feedback_id: SQLite can give a replaced rating's id to the next one."""
    ids = [r[0] for r in conn.execute(
        "SELECT f.id FROM feedback f WHERE NOT EXISTS (SELECT 1 FROM feedback_archive a "
        "WHERE a.message_id = f.message_id AND a.user_id IS f.user_id AND a.rated_at = f.created_at "
        "AND a.rating = f.rating) ORDER BY f.id")]
    added = 0
    for feedback_id in ids:
        if archive(conn, feedback_id) is not None:
            added += 1
    return added
