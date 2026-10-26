"""Erynne's file changes wait for the person.

When Erynne (the agent in chat) edits or creates a file, the tool works out
the new content with the same code Claude's edits use, then stores it here as
a proposal instead of saving it. The person sees it under the answer and
presses Apply, which saves it through editing.save/create with every check
made again (keeping the previous version for Restore), or Dismiss. A proposal
whose file changed in the meantime turns 'stale': Erynne has to redo it.

The proposed bytes live under <data_dir>/proposals until it is decided;
worker.py drops decided and old ones.

API actions (kind 'api', apis.py) wait the same way: closing a ticket or
resolving an alert is only called when the person applies it, after checking
again that they may use that operation, and the call is linked from the card.
So do Claude's deletes (kind 'delete') and its requests to edit where it is
refused (kind 'access'): the person applies them with their own rights.

A few wait for a Mnemerynne admin instead (approver 'admin'): a whole library's AI edit asked for by
someone who isn't an admin, and API actions marked "needs admin approval". The person can only withdraw
those; an admin approves them (run on the person's behalf) or rejects them with a note, from Access →
Approvals. Either way decided_by and decision_note say who and why, for the person and their Claude.

Every proposal carries a one-sentence `summary` of what will change (the approve page and Claude's
answer show the same words) and an `expires_at` (settings.proposal_days): one nobody decides in time
becomes 'expired'. Dismissing can carry a note too, so a declined proposal has its reason.
"""

from __future__ import annotations

import difflib
import json
import secrets
import sqlite3
import time
from pathlib import Path

from mnemerynne import editing, fs, permissions
from mnemerynne.api_tokens import Caller
from mnemerynne.config import Settings, get_settings
from mnemerynne.db import transaction

MAX_DIFF_LINES = 400
MAX_DIFF_CHARS = 40_000


class ProposalError(ValueError):
    status = 400


def _dir(settings: Settings) -> Path:
    return settings.data_dir / "proposals"


def _expiry(settings: Settings | None) -> str:
    """SQLite modifier for a new proposal's expires_at."""
    days = (settings or get_settings()).proposal_days
    return f"+{max(1, int(days))} days"


def _requested_via(conn: sqlite3.Connection, row: sqlite3.Row) -> str:
    """Who asked: Erynne in chat (a conversation), or the person's Claude through the connector."""
    if row["conversation_id"]:
        return "Erynne"
    by = conn.execute("SELECT username FROM users WHERE id = ?", (row["user_id"],)).fetchone()
    return f"Claude for {by['username']}" if by else "Claude"


def summary_for(kind: str, name: str, *, where: str = "", changes: list[str] | None = None,
                params: dict | None = None, api: str = "", operation: str = "", item_type: str = "",
                inside: int | None = None, subject: str = "", to: str = "") -> str:
    """One plain sentence saying what will change, written when the proposal is made."""
    if kind == "api":
        values = ", ".join(f"{k} {v}" for k, v in (params or {}).items())
        return f"{operation} in {api}" + (f": {values}" if values else "")
    if kind == "delete":
        if item_type == "folder":
            return f"Move the folder “{name}” ({inside or 0} file{'s' if inside != 1 else ''}) to the recycle bin"
        return f"Move the file “{name}” to the recycle bin"
    if kind == "access":
        first = (changes or [name])[0].rstrip(".")
        return first.split(" (")[0]
    if kind == "edit":
        return f"Change “{name}”" + (f" in {where}" if where else "")
    if kind == "create":
        return f"Create “{name}”" + (f" in {where}" if where else "")
    if kind == "mail":
        return f"Save a draft email to {to}: {subject}" if to else f"Save a draft email: {subject}"
    if kind == "batch":
        return name
    return name


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
    summary = summary_for(kind, name, where=where(conn, collection_id, folder_id))
    return conn.execute(
        "INSERT INTO agent_proposals(user_id, conversation_id, kind, file_id, collection_id, folder_id, name, "
        "base_sha256, new_sha256, rel_path, changes, diff, summary, expires_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now', ?))",
        (caller.user_id, getattr(caller, "conversation_id", None), kind, file_id, collection_id, folder_id, name,
         base, editing.sha256(data), rel, json.dumps(changes[:200]), diff, summary, _expiry(settings))).lastrowid


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


def propose_mail(conn: sqlite3.Connection, caller, payload: dict, settings: Settings | None = None) -> int:
    """An email draft (m365_mail): saved to the person's Drafts only when they apply it."""
    from mnemerynne import m365_mail

    subject = (payload.get("subject") or "(no subject)")[:300]
    to = payload.get("to")
    to = ", ".join(to) if isinstance(to, list) else (to or "")
    return conn.execute(
        "INSERT INTO agent_proposals(user_id, conversation_id, kind, name, changes, diff, payload, summary, expires_at) "
        "VALUES (?, ?, 'mail', ?, ?, ?, ?, ?, datetime('now', ?))",
        (caller.user_id, getattr(caller, "conversation_id", None), subject,
         json.dumps(m365_mail.draft_changes(payload)), payload.get("body", ""), m365_mail.payload_json(payload),
         summary_for("mail", subject, subject=subject, to=str(to)[:200]), _expiry(settings))
    ).lastrowid


def propose_api(conn: sqlite3.Connection, caller, op: sqlite3.Row, params: dict | None,
                settings: Settings | None = None) -> int:
    """An API action (apis.py: op["action"]) the person applies; its parameters are checked now, so a
    card never offers what can't run."""
    from mnemerynne import apis

    checked = apis.validate_params(apis.param_specs(op), params)
    changes = [f"{name}: {value}" for name, value in checked.items()]
    # An admin decides operations marked "needs admin approval"; the person decides the rest.
    approver = "admin" if op["admin_approval"] else "self"
    summary = summary_for("api", op["name"], params=checked, api=op["api_name"], operation=op["name"])
    return conn.execute(
        "INSERT INTO agent_proposals(user_id, conversation_id, kind, name, changes, diff, payload, approver, "
        "summary, expires_at) VALUES (?, ?, 'api', ?, ?, ?, ?, ?, ?, datetime('now', ?))",
        (caller.user_id, getattr(caller, "conversation_id", None), f"{op['api_name']} · {op['name']}",
         json.dumps(changes), op["description"] or "", json.dumps({"operation_id": op["id"], "params": checked}),
         approver, summary, _expiry(settings))
    ).lastrowid


def propose_batch(conn: sqlite3.Connection, caller, items: list[tuple[sqlite3.Row, dict | None]],
                  settings: Settings | None = None) -> int:
    """Several API actions under one approval (propose_batch): each item is (operation row, params),
    checked now like propose_api. The person ticks which to run; an admin decides the whole batch when
    any item needs admin approval."""
    from mnemerynne import apis

    if not items:
        raise ProposalError("A batch needs at least one change.")
    limit = (settings or get_settings()).proposal_batch_max
    if len(items) > limit:
        raise ProposalError(f"A batch holds at most {limit} changes; split it.")
    rows = []
    for op, params in items:
        checked = apis.validate_params(apis.param_specs(op), params)
        rows.append({"operation_id": op["id"], "api": op["api_name"], "operation": op["name"], "params": checked,
                     "summary": summary_for("api", op["name"], params=checked, api=op["api_name"], operation=op["name"]),
                     "status": "pending"})
    approver = "admin" if any(op["admin_approval"] for op, _ in items) else "self"
    names = sorted({op["api_name"] for op, _ in items})
    name = f"{len(rows)} change{'s' if len(rows) != 1 else ''} in {', '.join(names)}"
    shown = [r["summary"] for r in rows[:3]]
    summary = name + ": " + "; ".join(shown) + (f"; and {len(rows) - 3} more" if len(rows) > 3 else "")
    return conn.execute(
        "INSERT INTO agent_proposals(user_id, conversation_id, kind, name, changes, diff, payload, approver, "
        "summary, expires_at) VALUES (?, ?, 'batch', ?, ?, '', ?, ?, ?, datetime('now', ?))",
        (caller.user_id, getattr(caller, "conversation_id", None), name, json.dumps([r["summary"] for r in rows]),
         json.dumps({"items": rows}), approver, summary, _expiry(settings))).lastrowid


def _apply_batch(conn: sqlite3.Connection, settings: Settings, user: sqlite3.Row, row: sqlite3.Row, directory,
                 chosen: list[int] | None) -> dict:
    """Run the ticked items (all when chosen is None) one after another as the person, recording each
    item's outcome; unticked items are declined. The batch is 'applied' when any item ran."""
    from mnemerynne import apis

    payload = json.loads(row["payload"] or "{}")
    items = payload.get("items", [])
    wanted = set(range(len(items))) if chosen is None else {int(i) for i in chosen}
    person = conn.execute("SELECT id, ad_account FROM users WHERE id = ?", (user["id"],)).fetchone()
    allowed = {o["id"] for o in apis.allowed_operations(conn, person, directory)}
    ran = 0
    for index, item in enumerate(items):
        if item.get("status") != "pending":
            continue
        if index not in wanted:
            item["status"] = "declined"
            continue
        if item["operation_id"] not in allowed:
            item["status"] = "failed"
            item["error"] = "You can't use that operation any more."
            continue
        try:
            result = apis.call(conn, settings, item["operation_id"], item.get("params"), user_id=user["id"],
                               via="apply")
        except apis.ApiError as exc:
            item["status"], item["error"] = "failed", str(exc)
            continue
        if result.ok:
            item["status"], item["result_url"] = "applied", f"/apis/calls/{result.call_id}"
            ran += 1
        else:
            item["status"], item["error"] = "failed", f"{result.api_name} refused it: {result.error}"
            item["result_url"] = f"/apis/calls/{result.call_id}"
    status = "applied" if ran or any(i["status"] == "failed" for i in items) else "dismissed"
    conn.execute("UPDATE agent_proposals SET payload = ? WHERE id = ?", (json.dumps({"items": items}), row["id"]))
    _decide(conn, row["id"], status, by=user["id"])
    return view(conn, get(conn, row["id"], row["user_id"]))


def propose_delete(conn: sqlite3.Connection, caller, item: fs.Item, *, via: str = "claude",
                   settings: Settings | None = None) -> int:
    """Moving a file or folder to the recycle bin, which the person applies (with their own rights). Only
    where the assistant could change it anyway (editing.check_item_edit)."""
    row = editing.check_item_edit(conn, caller, item, via=via)
    inside = None
    if item.type == "file":
        name, folder_id, file_id = row["display_name"], row["folder_id"], row["id"]
        changes = ["Move this file to the recycle bin (it can be restored from there)."]
    else:
        below = fs.subtree(conn, item.id)
        marks = ",".join("?" * len(below))
        inside = conn.execute(f"SELECT count(*) FROM files WHERE folder_id IN ({marks}) AND deleted_at IS NULL",
                              below).fetchone()[0]
        name, folder_id, file_id = row["name"], row["parent_id"], None
        changes = [f"Move this folder and the {inside} file(s) in it to the recycle bin (it can be restored "
                   f"from there)."]
    pending = conn.execute("SELECT id FROM agent_proposals WHERE user_id = ? AND kind = 'delete' AND status = 'pending' "
                           "AND payload = ?", (caller.user_id, json.dumps({"type": item.type, "id": item.id}))).fetchone()
    if pending:
        return pending["id"]
    summary = summary_for("delete", name, item_type=item.type, inside=inside)
    return conn.execute(
        "INSERT INTO agent_proposals(user_id, conversation_id, kind, file_id, collection_id, folder_id, name, changes, "
        "payload, summary, expires_at) VALUES (?, ?, 'delete', ?, ?, ?, ?, ?, ?, ?, datetime('now', ?))",
        (caller.user_id, getattr(caller, "conversation_id", None), file_id, row["collection_id"], folder_id, name,
         json.dumps(changes), json.dumps({"type": item.type, "id": item.id}), summary, _expiry(settings))).lastrowid


def propose_access(conn: sqlite3.Connection, caller, target: fs.Item, name: str, collection_id: int,
                   folder_id: int | None, changes: list[str], *, approver: str = "self",
                   settings: Settings | None = None) -> int:
    """Letting the AI edit somewhere it is refused, which the person applies: target is the file or folder
    whose "AI can edit" switch goes on (their own File box, or clearing a read-only switch), or a library
    ({type: "library"}), which only an admin can apply. The tool has worked out which (request_edit_access).
    approver "admin": a library asked for by someone who isn't an admin waits in the admins' queue."""
    payload = json.dumps({"type": target.type, "id": target.id})
    pending = conn.execute("SELECT id FROM agent_proposals WHERE user_id = ? AND kind = 'access' AND status = 'pending' "
                           "AND payload = ? AND approver = ?", (caller.user_id, payload, approver)).fetchone()
    if pending:
        return pending["id"]
    return conn.execute(
        "INSERT INTO agent_proposals(user_id, conversation_id, kind, file_id, collection_id, folder_id, name, changes, "
        "payload, approver, summary, expires_at) VALUES (?, ?, 'access', ?, ?, ?, ?, ?, ?, ?, ?, datetime('now', ?))",
        (caller.user_id, getattr(caller, "conversation_id", None), target.id if target.type == "file" else None,
         collection_id, folder_id, name, json.dumps(changes), payload, approver,
         summary_for("access", name, changes=changes), _expiry(settings))).lastrowid


def _apply_access(conn: sqlite3.Connection, user: sqlite3.Row, row: sqlite3.Row, admin: bool) -> None:
    payload = json.loads(row["payload"])
    if payload["type"] == "library":
        if not admin:
            raise ProposalError("Only a Mnemerynne admin can let the AI edit a whole library, so nothing changed. "
                                "Ask an admin to allow AI edits for it (Access → Files & folders).")
        library = conn.execute("SELECT name FROM collections WHERE id = ? AND kind = 'library'",
                               (payload["id"],)).fetchone()
        if library is None:
            raise ProposalError("That library is gone, so nothing changed.")
        with transaction(conn):
            conn.execute("UPDATE collections SET claude_edit = 1 WHERE id = ?", (payload["id"],))
            conn.execute("INSERT INTO access_log(actor_id, actor_name, action, target, detail) VALUES (?, ?, ?, ?, ?)",
                         (user["id"], user["username"], "claude.edit.on", library["name"], "requested by Claude"))
        return
    try:
        fs.set_ai_edit(conn, fs.Access.of(conn, user["id"]), fs.Item(payload["type"], payload["id"]), True)
    except fs.FsError as exc:
        raise ProposalError(f"Nothing changed: {exc}") from None


def in_agents_folder(conn: sqlite3.Connection, user_id: int, row: sqlite3.Row) -> bool:
    """A file proposal that stays inside the person's own Agents folder: all that people outside the
    agent group may change (accounts.is_agent_user), checked when it's proposed and when it's applied."""
    own = permissions.private_collection_id(conn, user_id)
    if row["kind"] == "create":
        place = (row["collection_id"], row["folder_id"])
    elif row["kind"] == "edit":
        f = conn.execute("SELECT collection_id, folder_id FROM files WHERE id = ?", (row["file_id"],)).fetchone()
        place = (f["collection_id"], f["folder_id"]) if f else (None, None)
    else:
        return False
    return own is not None and place[0] == own and place[1] in editing._agents_subtree(conn, own)


def where(conn: sqlite3.Connection, collection_id: int, folder_id: int | None) -> str:
    coll = conn.execute("SELECT kind, name FROM collections WHERE id = ?", (collection_id,)).fetchone()
    path = "/".join(p["name"] for p in fs.folder_path(conn, folder_id))
    label = permissions.PRIVATE_LABEL if coll is None or coll["kind"] == "private" else coll["name"]
    return label + (f" / {path}" if path else "")


def view(conn: sqlite3.Connection, row: sqlite3.Row) -> dict:
    """What the chat card shows, and who decides it (and, once an admin has, who and why)."""
    out = _card(conn, row)
    out["approver"] = row["approver"]
    out["created_at"], out["decided_at"] = row["created_at"], row["decided_at"]
    out["summary"] = row["summary"] or summary_for(row["kind"], row["name"], changes=json.loads(row["changes"]))
    out["expires_at"] = row["expires_at"] if row["status"] == "pending" else None
    out["requested_via"] = _requested_via(conn, row)
    if row["decided_by"] is not None:
        by = conn.execute("SELECT username FROM users WHERE id = ?", (row["decided_by"],)).fetchone()
        out["decided_by"] = by["username"] if by else "a removed account"
    if row["decision_note"]:
        out["decision_note"] = row["decision_note"]
    return out


def _call_result(conn: sqlite3.Connection, url: str | None) -> str | None:
    """What the API answered when an action ran ('/apis/calls/<id>'), while the call log keeps it."""
    if not url or not url.startswith("/apis/calls/"):
        return None
    call = conn.execute("SELECT api_name, status_code, ok, error FROM api_calls WHERE id = ?",
                        (int(url.rsplit("/", 1)[1]),)).fetchone()
    if call is None:
        return None
    if call["ok"]:
        return f"{call['api_name']} accepted it (HTTP {call['status_code']})"
    return f"{call['api_name']} refused it" + (f": {call['error']}" if call["error"] else "")


def _card(conn: sqlite3.Connection, row: sqlite3.Row) -> dict:
    if row["kind"] == "api":
        api = row["name"].split(" · ")[0]
        return {"id": row["id"], "kind": "api", "file": row["name"], "file_id": None, "where": api,
                "changes": json.loads(row["changes"]), "diff": "", "body": row["diff"],
                "status": row["status"], "url": row["result_url"], "result": _call_result(conn, row["result_url"])}
    if row["kind"] == "batch":
        items = json.loads(row["payload"] or "{}").get("items", [])
        for item in items:
            item["result"] = _call_result(conn, item.get("result_url"))
        return {"id": row["id"], "kind": "batch", "file": row["name"], "file_id": None,
                "where": ", ".join(sorted({i["api"] for i in items})) or "internal APIs",
                "changes": json.loads(row["changes"]), "diff": "", "body": "", "status": row["status"],
                "url": None, "items": items,
                "counts": {s: sum(1 for i in items if i.get("status") == s)
                           for s in ("pending", "applied", "declined", "failed")}}
    if row["kind"] == "mail":
        payload = json.loads(row["payload"] or "{}")
        account = conn.execute("SELECT t.label, i.upn FROM m365_identities i JOIN m365_tenants t ON t.id = i.tenant_row "
                               "WHERE i.id = ?", (payload.get("identity_id"),)).fetchone()
        return {"id": row["id"], "kind": "mail", "file": row["name"], "file_id": None,
                "where": f"{account['upn']} ({account['label']})" if account else "a Microsoft account no longer linked",
                "changes": json.loads(row["changes"]), "diff": "", "body": payload.get("body", ""),
                "status": row["status"], "url": row["result_url"]}
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


def _decide(conn: sqlite3.Connection, proposal_id: int, status: str, result_file_id: int | None = None, *,
            by: int | None = None, note: str | None = None) -> None:
    conn.execute("UPDATE agent_proposals SET status = ?, decided_at = datetime('now'), expires_at = NULL, "
                 "result_file_id = COALESCE(?, result_file_id), decided_by = COALESCE(?, decided_by), "
                 "decision_note = COALESCE(?, decision_note) WHERE id = ?",
                 (status, result_file_id, by, (note or "").strip()[:1000] or None, proposal_id))


def apply(conn: sqlite3.Connection, settings: Settings, user: sqlite3.Row, proposal_id: int,
          directory=None, *, admin: bool = False, items: list[int] | None = None) -> dict:
    """Save a pending proposal as the person (every check made again). For files and mail the
    caller has checked the person is still in the agent (or Claude) group; an API action checks
    here that they may still use its operation (directory: their groups). admin: the person is a
    Mnemerynne admin (only they can apply a library-wide access request)."""
    row = get(conn, proposal_id, user["id"])
    if row["status"] != "pending":
        raise ProposalError(f"This change was already {row['status']}.")
    if row["approver"] == "admin":
        raise ProposalError("This one waits for a Mnemerynne admin to approve it. You can withdraw it meanwhile.")
    if row["kind"] == "api":
        return _apply_api(conn, settings, user, row, directory)
    if row["kind"] == "batch":
        return _apply_batch(conn, settings, user, row, directory, items)
    if row["kind"] == "access":
        _apply_access(conn, user, row, admin)
        _decide(conn, proposal_id, "applied", by=user["id"])
        return view(conn, get(conn, proposal_id, user["id"]))
    if row["kind"] == "delete":
        payload = json.loads(row["payload"])
        try:
            result = fs.trash(conn, fs.Access.of(conn, user["id"]), [fs.Item(payload["type"], payload["id"])])[0]
        except fs.FsError as exc:
            raise ProposalError(f"Nothing was deleted: {exc}") from None
        if not result.ok:
            raise ProposalError(f"Nothing was deleted: {result.error}")
        _decide(conn, proposal_id, "applied", by=user["id"])
        return view(conn, get(conn, proposal_id, user["id"]))
    if row["kind"] == "mail":
        from mnemerynne import m365_mail

        link = m365_mail.save_draft(conn, settings, user["id"], json.loads(row["payload"]))
        conn.execute("UPDATE agent_proposals SET status = 'applied', decided_at = datetime('now'), expires_at = NULL, "
                     "result_url = ?, decided_by = ? WHERE id = ?", (link, user["id"], proposal_id))
        return view(conn, get(conn, proposal_id, user["id"]))
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
        _decide(conn, proposal_id, "applied", file_id, by=user["id"])
    path.unlink(missing_ok=True)
    return view(conn, get(conn, proposal_id, user["id"]))


def _apply_api(conn: sqlite3.Connection, settings: Settings, user: sqlite3.Row, row: sqlite3.Row,
               directory) -> dict:
    from mnemerynne import apis

    payload = json.loads(row["payload"] or "{}")
    person = conn.execute("SELECT id, ad_account FROM users WHERE id = ?", (user["id"],)).fetchone()
    if payload.get("operation_id") not in {o["id"] for o in apis.allowed_operations(conn, person, directory)}:
        raise ProposalError("You can't use that operation any more (it was removed, turned off, or you left "
                            "its group), so nothing was changed.")
    try:
        result = apis.call(conn, settings, payload["operation_id"], payload.get("params"), user_id=user["id"],
                           via="apply")
    except apis.ApiError as exc:
        raise ProposalError(f"Nothing was changed: {exc}") from None
    if not result.ok:
        raise ProposalError(f"{result.api_name} refused it, so nothing was changed: {result.error}.")
    conn.execute("UPDATE agent_proposals SET status = 'applied', decided_at = datetime('now'), expires_at = NULL, "
                 "result_url = ?, decided_by = COALESCE(decided_by, ?) WHERE id = ?",
                 (f"/apis/calls/{result.call_id}", user["id"], row["id"]))
    return view(conn, get(conn, row["id"], user["id"]))


def _changed(conn: sqlite3.Connection, settings: Settings, row: sqlite3.Row) -> bool:
    from mnemerynne.ingest import store

    try:
        return editing.sha256(store.content_path(conn, settings, row["file_id"]).read_bytes()) != row["base_sha256"]
    except OSError:
        return False   # the share is away: try again later, it isn't stale


def dismiss(conn: sqlite3.Connection, settings: Settings, user_id: int, proposal_id: int, note: str = "") -> dict:
    """The person declines (or withdraws) their own pending proposal; `note` says why, for their Claude."""
    row = get(conn, proposal_id, user_id)
    if row["status"] == "pending":
        _decide(conn, proposal_id, "dismissed", by=user_id, note=note)
        if row["rel_path"]:
            (_dir(settings) / row["rel_path"]).unlink(missing_ok=True)
    return view(conn, get(conn, proposal_id, user_id))


# ------------------------------------------------------------ admin approvals
def for_admins(conn: sqlite3.Connection, *, decided_days: int = 7, limit: int = 100) -> list[dict]:
    """The admins' queue: proposals waiting for an admin, oldest first, then the ones decided lately."""
    rows = conn.execute(
        "SELECT p.*, u.username AS requester FROM agent_proposals p JOIN users u ON u.id = p.user_id "
        "WHERE p.approver = 'admin' AND (p.status = 'pending' OR p.decided_at >= datetime('now', ?)) "
        "ORDER BY p.status <> 'pending', CASE WHEN p.status = 'pending' THEN p.id ELSE -p.id END LIMIT ?",
        (f"-{int(decided_days)} days", limit)).fetchall()
    return [{**view(conn, r), "requester": r["requester"]} for r in rows]


def _admin_row(conn: sqlite3.Connection, proposal_id: int) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM agent_proposals WHERE id = ? AND approver = 'admin'",
                       (proposal_id,)).fetchone()
    if row is None:
        raise editing.EditNotFound("No such request for admins.")
    if row["status"] != "pending":
        raise ProposalError(f"This request was already {row['status']}.")
    return row


def _record_decision(conn: sqlite3.Connection, proposal_id: int, admin: sqlite3.Row, note: str) -> None:
    conn.execute("UPDATE agent_proposals SET decided_by = ?, decision_note = ? WHERE id = ?",
                 (admin["id"], (note or "").strip()[:1000] or None, proposal_id))


def approve(conn: sqlite3.Connection, settings: Settings, admin: sqlite3.Row, proposal_id: int, note: str = "",
            directory=None) -> dict:
    """An admin approves a request waiting for them; it runs on the requester's behalf. An API action
    checks again that the requester may still use its operation (directory: their groups), so an admin
    can't run something for someone who has lost access to it."""
    row = _admin_row(conn, proposal_id)
    requester = conn.execute("SELECT * FROM users WHERE id = ?", (row["user_id"],)).fetchone()
    if row["kind"] == "api":
        _apply_api(conn, settings, requester, row, directory)
    elif row["kind"] == "batch":
        _apply_batch(conn, settings, requester, row, directory, None)
    elif row["kind"] == "access":
        _apply_access(conn, admin, row, admin=True)
        _decide(conn, proposal_id, "applied", by=admin["id"])
    else:
        raise ProposalError(f"Admins don't decide '{row['kind']}' changes; the person applies those.")
    _record_decision(conn, proposal_id, admin, note)
    return view(conn, get(conn, proposal_id, row["user_id"]))


def reject(conn: sqlite3.Connection, admin: sqlite3.Row, proposal_id: int, note: str = "") -> dict:
    """An admin turns a request down; the note tells the requester (and their Claude) why."""
    row = _admin_row(conn, proposal_id)
    _decide(conn, proposal_id, "rejected", by=admin["id"])
    _record_decision(conn, proposal_id, admin, note)
    return view(conn, get(conn, proposal_id, row["user_id"]))


def for_person(conn: sqlite3.Connection, user_id: int, status: str = "all", since_days: int = 7,
               limit: int = 20, since: str | None = None) -> list[dict]:
    """A person's own proposals for their Claude (list_proposals): 'pending', those 'decided' within
    since_days (or since a UTC time), or 'all' of both, newest first."""
    cutoff = ("datetime(?)", since) if since else ("datetime('now', ?)", f"-{int(since_days)} days")
    where = {"pending": "status = 'pending'",
             "decided": f"status <> 'pending' AND decided_at >= {cutoff[0]}",
             "all": f"(status = 'pending' OR decided_at >= {cutoff[0]})"}[status]
    args = (user_id,) + ((cutoff[1],) if status != "pending" else ()) + (limit,)
    rows = conn.execute(f"SELECT * FROM agent_proposals WHERE user_id = ? AND {where} ORDER BY id DESC LIMIT ?",
                        args).fetchall()
    return [view(conn, r) for r in rows]


def purge(conn: sqlite3.Connection, settings: Settings) -> int:
    """Pending proposals past their expires_at (proposal_days; version_days for ones made before there
    was one) expire; their content (and any decided one's) is removed. Rows stay, so old chats still
    show them."""
    expired = conn.execute("UPDATE agent_proposals SET status = 'expired', decided_at = datetime('now'), "
                           "expires_at = NULL WHERE status = 'pending' AND COALESCE(expires_at, "
                           "datetime(created_at, ?)) <= datetime('now')",
                           (f"+{int(settings.version_days)} days",)).rowcount
    base = _dir(settings)
    if base.is_dir():
        pending = {r[0] for r in conn.execute("SELECT rel_path FROM agent_proposals WHERE status = 'pending'")}
        for entry in base.iterdir():
            # Recent files may belong to a proposal being written right now.
            if entry.is_file() and entry.name not in pending and time.time() - entry.stat().st_mtime > 3600:
                entry.unlink(missing_ok=True)
    return expired
