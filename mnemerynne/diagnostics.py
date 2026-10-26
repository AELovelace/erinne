"""Development diagnostics: raw internals for the Claude of people in one group.

An admin names the group on Access → Settings → Development (meta 'dev_diagnostics_group', a
group_refs reference: an AD or Entra group, through people's linked accounts). While a group is
set, the connector's `diagnose` tool lets a member's connection pull what the web pages summarise
or hide: the tool-call log with its errors, what retrieval scored for a question and why the
not-found gate fired, a file's or chunk's indexing state, the ingestion queue, the server's recent
log lines, and the effective settings (secrets left out). No group, no diagnostics; nothing here
changes anything.
"""

from __future__ import annotations

import collections
import json
import logging
import sqlite3
import threading
import time
from typing import Any

from mnemerynne import db, group_refs, retrieval, synonyms
from mnemerynne.config import Settings

KEY = "dev_diagnostics_group"
LOG_KEEP = 500
SECRET_SETTINGS = {"secret_key", "m365_client_secret", "teacher_api_key"}
KINDS = ("calls", "search", "file", "chunk", "jobs", "log", "config")


def group(conn: sqlite3.Connection) -> str:
    """The group whose members may diagnose (group_refs text); '' = off."""
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (KEY,)).fetchone()
    return (row["value"] if row else "") or ""


def set_group(conn: sqlite3.Connection, value: str) -> str:
    """Store a resolved group reference (group_refs.resolve), or '' to switch diagnostics off."""
    value = (value or "").strip()
    if value:
        conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)", (KEY, value))
    else:
        conn.execute("DELETE FROM meta WHERE key = ?", (KEY,))
    return value


def enabled(conn: sqlite3.Connection) -> bool:
    return bool(group(conn))


def allowed(conn: sqlite3.Connection, user: sqlite3.Row, directory) -> bool:
    """A group is set and this person is in it (through their linked domain or Microsoft account)."""
    value = group(conn)
    return bool(value) and group_refs.in_any(conn, user, directory, value)


# ------------------------------------------------------------ the server log
class RingHandler(logging.Handler):
    """Keeps the last LOG_KEEP records of this process in memory."""

    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.records: collections.deque = collections.deque(maxlen=LOG_KEEP)
        self._lock = threading.Lock()

    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = record.getMessage()
        except Exception:   # a bad format string must never break logging
            message = str(record.msg)
        if record.exc_info:
            message += "\n" + logging.Formatter().formatException(record.exc_info)
        with self._lock:
            self.records.append({"at": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(record.created)) + " UTC",
                                 "level": record.levelname, "logger": record.name, "message": message[:4000]})

    def recent(self, limit: int = 100, level: str = "INFO", logger: str | None = None) -> list[dict]:
        floor = logging.getLevelName((level or "INFO").upper())
        floor = floor if isinstance(floor, int) else logging.INFO
        with self._lock:
            rows = [r for r in self.records if logging.getLevelName(r["level"]) >= floor
                    and (not logger or r["logger"].startswith(logger))]
        return rows[-max(1, min(int(limit), LOG_KEEP)):]


_handler: RingHandler | None = None


def install_log_capture() -> RingHandler:
    """Attach the ring buffer to the root logger once per process (the web app does this at start)."""
    global _handler
    if _handler is None:
        _handler = RingHandler()
        logging.getLogger().addHandler(_handler)
    return _handler


def recent_log(limit: int = 100, level: str = "INFO", logger: str | None = None) -> list[dict]:
    return install_log_capture().recent(limit, level, logger)


# ------------------------------------------------------------------ queries
def _rows(cursor) -> list[dict]:
    return [dict(r) for r in cursor.fetchall()]


def calls(conn: sqlite3.Connection, *, tool: str | None = None, user_id: int | None = None, failed_only: bool = False,
          since_minutes: int = 60, limit: int = 50) -> dict:
    """The three tool-call logs: Claude's (mcp_calls, with the error each failed call raised), Erynne's
    (agent_calls) and the internal APIs' (api_calls), newest first."""
    since = f"-{max(1, int(since_minutes))} minutes"
    limit = max(1, min(int(limit), 200))
    where, args = ["m.at >= datetime('now', ?)"], [since]
    if tool:
        where.append("m.tool = ?")
        args.append(tool)
    if user_id is not None:
        where.append("m.user_id = ?")
        args.append(user_id)
    if failed_only:
        where.append("m.ok = 0")
    mcp = _rows(conn.execute(
        f"SELECT m.id, m.at, u.username, m.tool, m.args, m.file_ids, m.ok, m.ms, m.error, m.token_id, m.grant_id "
        f"FROM mcp_calls m LEFT JOIN users u ON u.id = m.user_id WHERE {' AND '.join(where)} "
        f"ORDER BY m.id DESC LIMIT ?", (*args, limit)))
    for r in mcp:
        for key in ("args", "file_ids"):
            try:
                r[key] = json.loads(r[key])
            except (TypeError, ValueError):
                pass
    agent_where = [w.replace("m.", "a.") for w in where if "m.user_id" in w or "m.at" in w or "m.tool" in w]
    if failed_only:
        agent_where.append("a.ok = 0")
    agent_args = [a for w, a in zip(where, args) if "m.ok" not in w] if not failed_only else args[:-0 or None]
    agent = _rows(conn.execute(
        f"SELECT a.id, a.at, u.username, a.conversation_id, a.tool, a.args, a.file_ids, a.ok, a.ms, a.error "
        f"FROM agent_calls a LEFT JOIN users u ON u.id = a.user_id WHERE {' AND '.join(agent_where)} "
        f"ORDER BY a.id DESC LIMIT ?", (*[a for w, a in zip(where, args) if "m.ok" not in w], limit)))
    for r in agent:
        for key in ("args", "file_ids"):
            try:
                r[key] = json.loads(r[key])
            except (TypeError, ValueError):
                pass
    api_where, api_args = ["c.at >= datetime('now', ?)"], [since]
    if user_id is not None:
        api_where.append("c.user_id = ?")
        api_args.append(user_id)
    if failed_only:
        api_where.append("c.ok = 0")
    api = _rows(conn.execute(
        f"SELECT c.id, c.at, u.username, c.via, c.api_name, c.operation_name, c.params, c.status_code, c.ok, c.cached, "
        f"c.ms, c.error, c.response_bytes, substr(c.response_excerpt, 1, 600) AS response_excerpt "
        f"FROM api_calls c LEFT JOIN users u ON u.id = c.user_id WHERE {' AND '.join(api_where)} "
        f"ORDER BY c.id DESC LIMIT ?", (*api_args, limit)))
    return {"claude": mcp, "erynne": agent, "apis": api}


def search_trace(conn: sqlite3.Connection, settings: Settings, embedder, user_id: int, question: str,
                 limit: int = 20) -> dict:
    """What retrieval saw for a question as this person: the keyword query, the jargon variants, every
    candidate with its fused score, keyword and vector ranks and distance, which were chosen, and the
    not-found gate's decision."""
    result = retrieval.search_for_user(conn, settings, embedder, user_id, question)
    chosen = {h.chunk_id for h in result.hits}
    lexical = result.lexical_hits > 0
    gate_limit = settings.vector_max_distance if lexical else settings.vector_max_distance_no_keywords
    return {
        "question": question, "fts_query": retrieval.fts_query(question),
        "variants": synonyms.variants(question, retrieval._synonym_groups(settings)),
        "gate": {"lexical_hits": result.lexical_hits, "best_distance": result.best_distance,
                 "distance_limit": gate_limit if result.best_distance is not None else None,
                 "passed": bool(result.hits), "retrieval_k": settings.retrieval_k,
                 "context_chunks": settings.context_chunks, "context_tokens": settings.context_tokens},
        "chosen": [h.chunk_id for h in result.hits],
        "candidates": [{"chunk_id": h.chunk_id, "file_id": h.file_id, "file": h.file_name, "where": h.location,
                        "score": round(h.score, 5), "fts_rank": h.fts_rank, "vector_rank": h.vector_rank,
                        "distance": None if h.distance is None else round(h.distance, 4),
                        "chosen": h.chunk_id in chosen, "tokens": h.tokens, "text": h.text[:300]}
                       for h in result.candidates[:max(1, min(int(limit), 100))]],
        "candidates_total": len(result.candidates),
    }


def file_state(conn: sqlite3.Connection, settings: Settings, file_id: int) -> dict:
    """Everything the index knows about one file: its row, blob, units, chunks and vectors, its jobs
    (with their errors), versions, and whether its bytes are where they should be."""
    row = conn.execute("SELECT f.*, b.kind, b.mime, b.size, b.unit_count, b.rel_path, b.ocr_rel_path, c.name AS collection, "
                       "c.kind AS collection_kind, c.share_path FROM files f JOIN blobs b ON b.sha256 = f.blob_sha256 "
                       "JOIN collections c ON c.id = f.collection_id WHERE f.id = ?", (file_id,)).fetchone()
    if row is None:
        return {"file_id": file_id, "found": False}
    out = dict(row)
    units = conn.execute("SELECT count(*), sum(ocr) FROM blob_units WHERE sha256 = ?", (row["blob_sha256"],)).fetchone()
    out["units"] = {"count": units[0], "ocr": units[1] or 0}
    out["chunks"] = conn.execute("SELECT count(*) FROM chunks WHERE file_id = ?", (file_id,)).fetchone()[0]
    out["embedded"] = conn.execute("SELECT count(*) FROM chunk_vec WHERE rowid IN (SELECT id FROM chunks WHERE file_id = ?)",
                                   (file_id,)).fetchone()[0]
    out["jobs"] = _rows(conn.execute("SELECT id, kind, status, attempts, run_after, locked_at, error, created_at FROM jobs "
                                     "WHERE file_id = ? ORDER BY id DESC LIMIT 10", (file_id,)))
    out["versions"] = conn.execute("SELECT count(*) FROM file_versions WHERE file_id = ?", (file_id,)).fetchone()[0]
    out["comments"] = conn.execute("SELECT count(*) FROM file_comments WHERE file_id = ?", (file_id,)).fetchone()[0]
    try:
        from mnemerynne.ingest import store
        path = store.content_path(conn, settings, file_id)
        out["content"] = {"path": str(path), "exists": path.exists(), "bytes": path.stat().st_size if path.exists() else None}
    except Exception as exc:   # the share is away, or the row is odd: say so rather than fail
        out["content"] = {"error": f"{type(exc).__name__}: {exc}"}
    return {"found": True, **out}


def chunk_state(conn: sqlite3.Connection, chunk_id: int) -> dict:
    """One passage as the index holds it, with whether its vector exists and its file's state."""
    row = conn.execute("SELECT c.*, f.display_name, f.status AS file_status, f.error AS file_error, f.deleted_at, "
                       "f.claude_hidden FROM chunks c JOIN files f ON f.id = c.file_id WHERE c.id = ?", (chunk_id,)).fetchone()
    if row is None:
        return {"chunk_id": chunk_id, "found": False}
    out = dict(row)
    out["has_vector"] = conn.execute("SELECT count(*) FROM chunk_vec WHERE rowid = ?", (chunk_id,)).fetchone()[0] == 1
    out["fts_rows"] = conn.execute("SELECT count(*) FROM chunks_fts WHERE rowid = ?", (chunk_id,)).fetchone()[0] \
        if conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'chunks_fts'").fetchone() else None
    return {"found": True, **out}


def jobs(conn: sqlite3.Connection, status: str | None = None, limit: int = 50) -> dict:
    where, args = ([], []) if not status else (["j.status = ?"], [status])
    rows = _rows(conn.execute(
        f"SELECT j.id, j.kind, j.file_id, f.display_name, j.status, j.attempts, j.run_after, j.locked_at, j.error, "
        f"j.created_at FROM jobs j LEFT JOIN files f ON f.id = j.file_id {'WHERE ' + ' AND '.join(where) if where else ''} "
        f"ORDER BY j.id DESC LIMIT ?", (*args, max(1, min(int(limit), 200)))))
    counts = {r[0]: r[1] for r in conn.execute("SELECT status, count(*) FROM jobs GROUP BY status")}
    return {"counts": counts, "jobs": rows}


def config(conn: sqlite3.Connection, settings: Settings) -> dict:
    """The effective settings (secrets left out), the schema version and the stored settings."""
    values = {k: (str(v) if not isinstance(v, (int, float, bool, str, type(None))) else v)
              for k, v in settings.model_dump().items() if k not in SECRET_SETTINGS}
    meta = {}
    for r in conn.execute("SELECT key, value FROM meta ORDER BY key"):
        value = r["value"]
        try:
            parsed: Any = json.loads(value)
            if isinstance(parsed, dict):
                parsed = {k: ("(set)" if "key" in k.lower() or "secret" in k.lower() else v) for k, v in parsed.items()}
            meta[r["key"]] = parsed
        except (TypeError, ValueError):
            meta[r["key"]] = value
    return {"settings": values, "schema": db.schema_version(conn), "meta": meta,
            "tables": {t: conn.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
                       for t in ("users", "collections", "files", "chunks", "jobs", "mcp_calls", "agent_calls",
                                 "api_calls", "agent_proposals", "user_messages")}}
