"""Erynne's file changes wait for the person.

When Erynne (the agent in chat) edits or creates a file, the tool works out
the new content with the same code Claude's edits use, then stores it here as
a proposal instead of saving it. The person sees it under the answer and
presses Apply, which saves it through editing.save/create with every check
made again (keeping the previous version for Restore), or Dismiss. A proposal
whose file changed in the meantime turns 'stale': Erynne has to redo it.

The proposed bytes live under <data_dir>/proposals until it is decided;
worker.py drops decided and old ones.
"""

from __future__ import annotations

import difflib
import json
import secrets
import sqlite3
import time
from pathlib import Path

from mnemerynne import editing, fs
from mnemerynne.api_tokens import Caller
from mnemerynne.config import Settings
from mnemerynne.db import transaction

MAX_DIFF_LINES = 400
MAX_DIFF_CHARS = 40_000


class ProposalError(ValueError):
    status = 400


def _dir(settings: Settings) -> Path:
    return settings.data_dir / "proposals"


def text_diff(old: bytes, new: bytes, name: str) -> str:
    """A unified diff of two text files, cut short when it is long."""
    lines = list(difflib.unified_diff(old.decode("utf-8", "replace").splitlines(),
                                      new.decode("utf-8", "replace").splitlines(),
                                      fromfile=name, tofile=name, lineterm="", n=1))[2:]   # no ---/+++ header
    out, size = [], 0
    for line in lines:
        if len(out) >= MAX_DIFF_LINES or size > MAX_DIFF_CHARS:
            out.append(f"… {len(lines) - len(out)} more line(s)")
            break
        out.append(line)
        size += len(line) + 1
    return "\n".join(out)


def _store(conn: sqlite3.Connection, settings: Settings, caller, *, kind: str, data: bytes, name: str,
           collection_id: int, folder_id: int | None, file_id: int | None, base: str | None,
           changes: list[str], diff: str) -> int:
    rel = f"{secrets.token_hex(12)}.bin"
    path = _dir(settings) / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return conn.execute(
        "INSERT INTO agent_proposals(user_id, conversation_id, kind, file_id, collection_id, folder_id, name, "
        "base_sha256, new_sha256, rel_path, changes, diff) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (caller.user_id, getattr(caller, "conversation_id", None), kind, file_id, collection_id, folder_id, name,
         base, editing.sha256(data), rel, json.dumps(changes[:200]), diff)).lastrowid


def propose_edit(conn: sqlite3.Connection, settings: Settings, caller, loaded: editing.Loaded, data: bytes,
                 changes: list[str]) -> int:
    """A change to an existing file (permission already checked by the tool)."""
    diff = text_diff(loaded.data, data, loaded.row["display_name"]) if loaded.fmt == "text" else ""
    return _store(conn, settings, caller, kind="edit", data=data, name=loaded.row["display_name"],
                  collection_id=loaded.row["collection_id"], folder_id=loaded.row["folder_id"],
                  file_id=loaded.row["id"], base=loaded.version, changes=changes, diff=diff)


def propose_new(conn: sqlite3.Connection, settings: Settings, caller, name: str, data: bytes,
                collection_id: int | None, folder_id: int | None, changes: list[str]) -> int:
    """A new file; it goes in the Agents folder unless a place is given."""
    from mnemerynne.ingest import store

    name = store.clean_display_name(name)
    fmt = editing.format_of(name)
    if fmt is None:
        raise editing.EditError(f"Erynne can create {', '.join(sorted(editing.EDITABLE))} files; "
                                f"“{name}” is none of those.")
    if len(data) > settings.edit_max_bytes:
        raise editing.EditError(f"The file would be over {settings.edit_max_bytes // (1024 * 1024)} MB.")
    collection_id, folder_id = editing.new_file_place(conn, caller.user_id, collection_id, folder_id)
    editing.check_edit(conn, caller.user_id, True, collection_id, folder_id, via="erynne")
    diff = text_diff(b"", data, name) if fmt == "text" else ""
    return _store(conn, settings, caller, kind="create", data=data, name=name, collection_id=collection_id,
                  folder_id=folder_id, file_id=None, base=None, changes=changes, diff=diff)


def where(conn: sqlite3.Connection, collection_id: int, folder_id: int | None) -> str:
    coll = conn.execute("SELECT kind, name FROM collections WHERE id = ?", (collection_id,)).fetchone()
    path = "/".join(p["name"] for p in fs.folder_path(conn, folder_id))
    label = "My inbox" if coll is None or coll["kind"] == "private" else coll["name"]
    return label + (f" / {path}" if path else "")


def view(conn: sqlite3.Connection, row: sqlite3.Row) -> dict:
    """What the chat card shows."""
    file_id = row["result_file_id"] or row["file_id"]
    return {"id": row["id"], "kind": row["kind"], "file": row["name"], "file_id": file_id,
            "where": where(conn, row["collection_id"], row["folder_id"]), "changes": json.loads(row["changes"]),
            "diff": row["diff"], "status": row["status"],
            # The folder it is (or will be) in, on the Files page.
            "url": f"/files#c={row['collection_id']}" + (f"&f={row['folder_id']}" if row["folder_id"] else "")}


def get(conn: sqlite3.Connection, proposal_id: int, user_id: int) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM agent_proposals WHERE id = ? AND user_id = ?",
                       (proposal_id, user_id)).fetchone()
    if row is None:
        raise editing.EditNotFound("No such proposal.")
    return row


def for_messages(conn: sqlite3.Connection, message_ids: list[int]) -> dict[int, list[dict]]:
    if not message_ids:
        return {}
    marks = ",".join("?" * len(message_ids))
    out: dict[int, list[dict]] = {}
    for row in conn.execute(f"SELECT * FROM agent_proposals WHERE message_id IN ({marks}) ORDER BY id",
                            tuple(message_ids)).fetchall():
        out.setdefault(row["message_id"], []).append(view(conn, row))
    return out


def attach(conn: sqlite3.Connection, proposal_ids: list[int], message_id: int) -> None:
    conn.executemany("UPDATE agent_proposals SET message_id = ? WHERE id = ?",
                     [(message_id, pid) for pid in proposal_ids])


def _decide(conn: sqlite3.Connection, proposal_id: int, status: str, result_file_id: int | None = None) -> None:
    conn.execute("UPDATE agent_proposals SET status = ?, decided_at = datetime('now'), "
                 "result_file_id = COALESCE(?, result_file_id) WHERE id = ?", (status, result_file_id, proposal_id))


def apply(conn: sqlite3.Connection, settings: Settings, user: sqlite3.Row, proposal_id: int) -> dict:
    """Save a pending proposal as the person (every check made again). The
    caller has checked the person is still in the agent group."""
    row = get(conn, proposal_id, user["id"])
    if row["status"] != "pending":
        raise ProposalError(f"This change was already {row['status']}.")
    path = _dir(settings) / row["rel_path"]
    try:
        data = path.read_bytes()
    except OSError:
        raise editing.EditNotFound("This proposal's content is gone (it expired); ask Erynne again.") from None
    caller = Caller(user["id"], user["username"], None, True)
    summary = "; ".join(json.loads(row["changes"])) or "change proposed in chat"
    try:
        if row["kind"] == "edit":
            editing.save(conn, settings, caller, row["file_id"], data, expected=row["base_sha256"],
                         summary=summary, via="erynne")
            file_id = row["file_id"]
        else:
            file_id, _ = editing.create(conn, settings, caller, row["name"], data, row["collection_id"],
                                        row["folder_id"], via="erynne")
    except editing.EditConflict:
        if row["kind"] == "edit" and _changed(conn, settings, row):
            _decide(conn, proposal_id, "stale")
            path.unlink(missing_ok=True)
            raise editing.EditConflict(f"“{row['name']}” changed since Erynne made this proposal, so it "
                                       f"wasn't applied. Ask her to redo it on the current version.") from None
        raise
    with transaction(conn):
        _decide(conn, proposal_id, "applied", file_id)
    path.unlink(missing_ok=True)
    return view(conn, get(conn, proposal_id, user["id"]))


def _changed(conn: sqlite3.Connection, settings: Settings, row: sqlite3.Row) -> bool:
    from mnemerynne.ingest import store

    try:
        return editing.sha256(store.content_path(conn, settings, row["file_id"]).read_bytes()) != row["base_sha256"]
    except OSError:
        return False   # the share is away: try again later, it isn't stale


def dismiss(conn: sqlite3.Connection, settings: Settings, user_id: int, proposal_id: int) -> dict:
    row = get(conn, proposal_id, user_id)
    if row["status"] == "pending":
        _decide(conn, proposal_id, "dismissed")
        (_dir(settings) / row["rel_path"]).unlink(missing_ok=True)
    return view(conn, get(conn, proposal_id, user_id))


def purge(conn: sqlite3.Connection, settings: Settings) -> int:
    """Pending proposals older than version_days expire; their content (and
    any decided one's) is removed. Rows stay, so old chats still show them."""
    expired = conn.execute("UPDATE agent_proposals SET status = 'dismissed', decided_at = datetime('now') "
                           "WHERE status = 'pending' AND created_at < datetime('now', ?)",
                           (f"-{int(settings.version_days)} days",)).rowcount
    base = _dir(settings)
    if base.is_dir():
        pending = {r[0] for r in conn.execute("SELECT rel_path FROM agent_proposals WHERE status = 'pending'")}
        for entry in base.iterdir():
            # Recent files may belong to a proposal being written right now.
            if entry.is_file() and entry.name not in pending and time.time() - entry.stat().st_mtime > 3600:
                entry.unlink(missing_ok=True)
    return expired
