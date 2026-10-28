"""Mnemerynne's MCP endpoint: lets Claude Desktop (through the Mnemerynne extension)
and other MCP clients search people's documents, and edit text, Markdown, Word
and Excel files where that is allowed.

Everything acts as the token's user, limited to the collections that user may
search from Claude (permissions.claude_collection_ids): an fs.Access built from
that set makes every file helper respect the Claude switches with no extra
checks. Claude writes the answers; Mnemerynne only finds and returns passages
with their citations. The edit tools go through editing.py, which checks the
token, the person's own rights and the "Claude can edit" switches (on for each
inbox's Agents folder), and keeps every replaced version for Restore. Every
call is logged in mcp_calls.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import io
import json
import logging
import re
import sqlite3
import time
from collections import defaultdict, deque
from typing import Any, Callable

import anyio
import pypdfium2 as pdfium
from mcp.server import MCPServer
from mcp.server.mcpserver import Context, Image
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from starlette.responses import JSONResponse
from starlette.types import Receive, Scope, Send

from mnemerynne import (diagnostics, messages, notices, rooms, accounts, activity, api_tokens, apis, automations,
                        automations_run, briefings, claude_links, comments, db, editing, editing_formats,
                        fs, m365_mail, permissions, proposals, retrieval, sharepoint)
from mnemerynne.api_tokens import Caller
from mnemerynne.config import Settings
from mnemerynne.ingest import store

log = logging.getLogger("mnemerynne.mcp")

# The MCP server's own version, separate from the app's (pyproject): clients
# see it in initialize's serverInfo. Bump it when tools or their contracts change.
# 1.0.0: the "Road to 1.0" blockers (editable flags, request_edit_access,
# forgiving names, ids or paths, error codes), verified live 2026-10-25.
# 1.1.0: admin approvals (status awaiting_admin), list_proposals and get_proposal.
# 1.3.0: Rooms (channels and direct messages): list_rooms, read_room, post_to_room, react_in_room.
# 1.4.0: mail and calendar for the group an admin names: search_mail, recent_mail, read_mail, calendar,
#        draft_reply, draft_mail (drafts are proposals; nothing sends).
MCP_VERSION = "1.5.0"

PAGE_WIDTH_PX = 1200     # rendered page width: small print stays legible, JPEG stays ~100-300 KB
PAGE_MAX_PX = 2000       # cap on either side, for unusually long or wide pages

INSTRUCTIONS = """\
Mnemerynne holds this organisation's documents: each person's private File box (their own files) and
the shared libraries they have been given (brand standards, system manuals and so on).
Use these tools whenever a question could be answered from those documents.

- Answer only from the passages the tools return, and cite every claim with the passage's `citation`
  and `link`, e.g. "Run End of Day before 2 am ([OPERA Cloud Manual · p. 412](link))".
- If search_documents returns found: false, try once more with different wording (jargon, the system's
  own terms). If it is still not found, say plainly that the documents don't cover it; don't fill the
  gap from general knowledge without saying so.
- Use read_passage to see the text around a hit before relying on a fragment.
- Use view_page to look at a PDF page as an image when a passage mentions a table, figure, form or
  screenshot, when its text looks garbled (scanned pages), or when the person asks to see the page.
- Use list_libraries, browse_folder and find_files to see what exists or to narrow a search. Libraries and
  folders can be named (any case; "IT Ops" finds "IT-OPS") or given by the ids these tools return; a
  folder given as a number is an id, as text a path.
- When a tool refuses, its message carries a code: [not_found], [not_allowed], [conflict],
  [rate_limited] (wait and try again), [unavailable] (try again later) or [invalid] (fix the request).
- The `link`s are for the person to open in their browser, where they are signed in to Mnemerynne.
  Don't fetch them yourself: they need that login. Cite with `link`.
- Results that point at a file may also carry `fetch`: a private address for you, not the person, that
  your web fetch tool can open to read that file's text (from the cited page on, with a "more" address
  for the rest). It expires after a while; if it no longer works, search again for a fresh one. Never
  show a `fetch` address to the person or cite it. The tools above stay the first way to read.

Editing files (.txt, .md, .docx, .xlsx):
- Always open_file first and pass its `version` to the edit tool: edit_text (.txt, .md), edit_document
  (.docx) or edit_spreadsheet (.xlsx). If an edit is refused because the file changed ([conflict]), open
  it again and redo the change on the new version.
- browse_folder, find_files and search_documents mark each item `editable` for you. Where it is false,
  don't try: request_edit_access returns an `approve` link for the person when it's theirs to allow, or
  says who can. Never say access is granted until they've applied it.
- list_versions shows a file's earlier versions (from edits through Claude or Erynne, and restores).
- Make the smallest change that does what was asked; don't rewrite text you weren't asked to change.
- New files go in the person's Agents folder unless they name somewhere else (list_libraries shows where
  you may edit). Every change keeps the previous version, which the person can restore from Files.
- create_folder makes a folder where you may edit (the Agents folder unless they name somewhere else);
  then pass it as create_file's `folder`. rename_item and move_item rename or move a file or folder
  between places you may edit (move_item: what moves is file_id or folder_id; where it goes is to_library
  and to_folder). delete_item only proposes moving it to the recycle bin: give the person the `approve`
  link, and never say it's deleted.

Working with others (people, and other people's Claude):
- list_changes shows what was added, changed, renamed or moved, commented on, binned or restored in a
  library or folder since a time, and by whom: use it to notice replies instead of browsing and guessing.
- Talk about a document with add_comment (reply_to answers a comment) and read the thread with
  list_comments, rather than editing someone else's document to leave a message.
- Any other kind of file you made yourself (an image, a PDF, a zip, a deck) is saved with create_file's
  `data`, its bytes in base64. A file on the person's own computer (a screenshot, a photo) you can't
  reach: give them the `upload` link from list_libraries, which opens their Agents folder to add it.
- send_message leaves a short note for a colleague inside Mnemerynne (`to` is their Mnemerynne username;
  they see it under Messages); it isn't email. list_messages shows the person's own unread notes,
  what Mnemerynne told them (a proposal decided, a comment on their file) and announcements waiting
  for them; mention those when they're relevant.
- Rooms are the team's chat: channels (#front-office) and direct messages. list_rooms shows the
  person's rooms with unread counts; read_room reads one (a channel name, "@username" for a DM, or an
  id; `thread` for one thread); post_to_room writes there as "Claude for <name>" (reply in a thread
  with `thread`); react_in_room adds an emoji. Everyone in the room reads what you post: write only what
  the person asked you to say, and never paste documents they didn't ask you to share.

Briefings and security:
- my_briefing lists what's waiting for the person in Mnemerynne; security_feeds the new advisories from
  the feeds admins chose (cite each with its link). For admins, mnemerynne_updates says what updates are
  waiting and which installed packages have known vulnerabilities, and m365_security reads Microsoft 365
  alerts, risky users, the Secure Score and risky sign-ins.

Automator (alpha; only where an admin turned it on for the person):
- list_automations shows the person's scheduled automations (a morning briefing, a security digest);
  run_automation starts one now and get_automation_run reads its answer.
- When you were started by a Mnemerynne automation (the task names a run id), do the task with these
  tools, then call report_automation_run with that run_id and your report as Markdown, citing as usual.
  If there's nothing worth reporting, report exactly NOTHING_TO_REPORT. list_automation_tasks lists
  runs still waiting for a report.

Mail and calendar (only for people an admin put in the mail group, with a linked Microsoft 365 account;
for everyone else these tools refuse with [not_allowed], so don't retry them):
- search_mail and recent_mail list messages with an `id`, `citation` and `link`; read_mail reads one in
  full; calendar lists the person's events between two days. Cite mail like documents. Quote only what
  the question needs: a mailbox holds other people's words.
- draft_reply and draft_mail never send anything: each is a proposal the person saves to their Outlook
  Drafts from the approve link, like any proposal. Nothing here can send, move or delete mail.

Internal APIs (live):
- list_apis shows the internal systems you may query for this person (night-audit health, hotel stats,
  IT tickets and so on); query_api calls one. Cite answers with the result's `citation` and `link`, and
  say the figures are live as of its time. Don't guess values the response doesn't contain.
- Long lists come in pages: when the result's `rows` has a `next_offset`, call query_api again with
  the same params and that `offset` before concluding something isn't there. Call APIs marked
  one_at_a_time one after another, not in parallel.
- When search_documents finds nothing but returns `try_api`, the answer is probably live data: query it.
- Operations marked changes_data (closing a ticket, resolving an alert) only go through
  propose_api_action: it returns an `approve` link for the person, who applies it in Mnemerynne. Check
  the current state with query_api first, and never say the change is made.

Proposals and approvals:
- Proposals do nothing until the person approves them. propose_api_action, propose_batch, delete_item
  and request_edit_access return the same shape: `proposal_id`, `summary` (one sentence saying exactly
  what will change; the approve page shows the same words), `approve` (the link), `expires_at` and
  `status`. Give the person the link and the summary, carry on with the rest of the task, and check
  get_proposal before relying on the change. Never say a proposed change is done.
- Some wait for a Mnemerynne admin instead (status awaiting_admin): say it's with the admins; there is
  no link to give.
- get_proposal tells you what became of one (applied with its result, dismissed with the person's note,
  rejected with the admin's note, stale, or expired); list_proposals shows the person's pending and
  recently decided ones. withdraw_proposal takes back one of yours that's no longer needed. Nothing you
  can call approves, applies or rejects a proposal: only the person, in Mnemerynne."""

_READ_ONLY = ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True,
                             open_world_hint=False)
# Changes files, but never destructively: every change keeps the previous version.
_EDITS = ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=False,
                         open_world_hint=False)
# Proposes a change: nothing happens until the person approves it in Mnemerynne.
_PROPOSES = ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=False,
                            open_world_hint=False)
# Asking again for the same thing returns the same pending request; withdrawing twice is harmless.
_PROPOSES_ONCE = ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=True,
                                 open_world_hint=False)
# Names for the person's File box a model might use (older ones too: "inbox" was its name before).
_INBOX_WORDS = {"file box", "my file box", "filebox", "inbox", "my inbox", "my files", "my documents"}
_PROPOSED = ("Nothing has changed yet: the person sees this change under your answer and decides whether to "
             "apply it. Tell them briefly what you proposed.")


class Unauthorized(Exception):
    pass


# Every refusal reaches the model as "[code] message" (build_server), so an agent can react to the code
# without parsing the sentence.
ERROR_CODES = ("not_found", "not_allowed", "conflict", "rate_limited", "unavailable", "invalid")


class Refused(ToolError):
    """A ToolError with one of ERROR_CODES; the message stays plain (Erynne's tools show it as it is)."""

    def __init__(self, message: str, code: str = "invalid"):
        super().__init__(message)
        self.code = code


def error_code(exc: Exception) -> str:
    """The ERROR_CODES entry for a refusal from the tools, the edit checks or the file manager."""
    if isinstance(exc, Refused):
        return exc.code
    status = getattr(exc, "status", None)
    if isinstance(exc, (editing.EditError, fs.FsError)) and status in (403, 404, 409, 503):
        return {403: "not_allowed", 404: "not_found", 409: "conflict", 503: "unavailable"}[status]
    if str(exc).startswith(("No such", "There is no", "No folder")):
        return "not_found"
    return "invalid"


# ------------------------------------------------------------------ helpers
def _collections(conn: sqlite3.Connection, access: fs.Access) -> dict[int, sqlite3.Row]:
    if not access.readable:
        return {}
    marks = ",".join("?" * len(access.readable))
    return {r["id"]: r for r in conn.execute(
        f"SELECT id, kind, name FROM collections WHERE id IN ({marks})", tuple(access.readable))}


def _label(row: sqlite3.Row) -> str:
    return permissions.PRIVATE_LABEL if row["kind"] == "private" else row["name"]


def _is_id(ref) -> bool:
    return isinstance(ref, int) and not isinstance(ref, bool)


def _resolve_collection(conn: sqlite3.Connection, access: fs.Access, ref: int | str, claude: bool = True) -> sqlite3.Row:
    """A library by id (a number) or name. Names match exactly (any case) first, then forgivingly
    ("IT Ops" for "IT-OPS", fs.loose); a name made of digits is tried as an id last."""
    collections = _collections(conn, access)
    missing = (" that you can search from Claude" if claude else "") + ". Call list_libraries to see the names."
    if _is_id(ref):
        if ref in collections:
            return collections[ref]
        raise Refused(f"There is no library with id {ref}{missing}", "not_found")
    ref = str(ref or "").strip()
    inbox = {fs.loose(w) for w in _INBOX_WORDS}
    for row in collections.values():
        if (row["kind"] == "private" and ref.lower() in _INBOX_WORDS) or _label(row).casefold() == ref.casefold():
            return row
    key = fs.loose(ref)
    close = [row for row in collections.values()
             if key and (fs.loose(_label(row)) == key or (row["kind"] == "private" and key in inbox))]
    if len(close) == 1:
        return close[0]
    if len(close) > 1:
        raise Refused(f"“{ref}” could be any of: {', '.join(sorted(_label(r) for r in close))}. Give the exact "
                      f"name.", "invalid")
    if ref.isdigit() and int(ref) in collections:
        return collections[int(ref)]
    raise Refused(f"There is no library called “{ref}”{missing}", "not_found")


def _split_place(library, folder) -> tuple:
    """"File box / Agents" given as the library: the library and the folder in it."""
    if isinstance(library, str) and "/" in library and folder in (None, ""):
        library, folder = (part.strip() for part in library.split("/", 1))
    return library, folder


def _resolve_folder(conn: sqlite3.Connection, access: fs.Access, collection_id: int, ref: int | str | None) -> int | None:
    """A folder among those the user may see: by id (a number), or by a path like 'Front Office/Night Audit',
    each part matched exactly (any case) first, then forgivingly (fs.loose). A path made of digits, such as
    "2025", is a folder's name; only if no folder has that name is it tried as an id."""
    if ref is None:
        return None
    live = fs.live_folder_ids(conn, collection_id) & access.visible(collection_id)
    if _is_id(ref):
        if ref in live:
            return ref
        raise Refused(f"No folder with id {ref} in that library. Call browse_folder or find_files for ids.",
                      "not_found")
    ref = str(ref).strip().strip("/")
    if not ref:
        return None
    try:
        return _folder_by_path(conn, collection_id, live, ref)
    except Refused as exc:
        if exc.code == "not_found" and ref.isdigit() and int(ref) in live:
            return int(ref)
        raise


def _folder_by_path(conn: sqlite3.Connection, collection_id: int, live: set[int], ref: str) -> int:
    parent = None
    for part in [p.strip() for p in ref.split("/") if p.strip()]:
        children = [r for r in conn.execute(
            "SELECT id, name FROM folders WHERE collection_id = ? AND COALESCE(parent_id, 0) = ? "
            "AND deleted_at IS NULL", (collection_id, parent or 0)) if r["id"] in live]
        found = [r for r in children if r["name"].casefold() == part.casefold()] or \
            [r for r in children if fs.loose(part) and fs.loose(r["name"]) == fs.loose(part)]
        if len(found) > 1:
            raise Refused(f"“{part}” could be any of: {', '.join(sorted(r['name'] for r in found))}. Give the exact "
                          f"name, or the folder's id.", "invalid")
        if not found:
            raise Refused(f"No folder “{ref}” in that library. Call browse_folder to see its folders.", "not_found")
        parent = found[0]["id"]
    return parent


def _path(conn: sqlite3.Connection, folder_id: int | None) -> str:
    return "/".join(p["name"] for p in fs.folder_path(conn, folder_id))


def _file_link(base: str, file_id: int, kind: str) -> str:
    if kind == "pdf":
        return f"{base}/files/{file_id}/pdf"
    if kind in ("md", "txt", "doc", "code"):
        return f"{base}/files/{file_id}/view"
    return f"{base}/files/{file_id}/raw"


def _duplicates(conn: sqlite3.Connection, access: fs.Access, file_ids: list[int]) -> dict[int, list[int]]:
    """For each of these files, the other files the caller can read with exactly the same content."""
    if not file_ids:
        return {}
    marks = ",".join("?" * len(file_ids))
    rows = conn.execute(
        f"SELECT o.id AS original, f.id, f.collection_id, f.folder_id, f.claude_hidden FROM files o "
        f"JOIN files f ON f.blob_sha256 = o.blob_sha256 AND f.id <> o.id AND f.deleted_at IS NULL "
        f"WHERE o.id IN ({marks}) ORDER BY f.id", file_ids).fetchall()
    access.prefetch(rows)
    out: dict[int, list[int]] = defaultdict(list)
    for r in rows:
        if fs.is_live_folder(conn, r["folder_id"]) and access.can_read_file(r):
            out[r["original"]].append(r["id"])
    return out


def _api_code(exc: Exception) -> str:
    text = str(exc)
    if "still busy" in text:
        return "rate_limited"
    return "not_found" if text.startswith("There's no API operation") else "invalid"


def _mail_cite(kind: str, key: str, title: str, location: str, url: str | None) -> dict:
    """m365_mail's `cite` for the connector: a citation and a link on the item, like passages."""
    return {"citation": f"{title} · {location}" if location else title, "link": url or ""}


def logged_args(name: str, args: dict) -> dict:
    """A call's arguments as the log keeps them: a mail draft's text is replaced by its length, so the
    log holds message ids, never mail."""
    if name in m365_mail.CLAUDE_TOOL_NAMES:
        return {k: (f"({len(v)} characters)" if k == "body" and isinstance(v, str) else v) for k, v in args.items()}
    return args


def _passage(hit: retrieval.Hit, base: str, library: str, n: int | None = None,
             editable: bool | None = None) -> dict:
    # PDFs: file · page · section. Markdown's location is its last heading,
    # which the section path already ends with.
    page = hit.location if hit.kind == "pdf" else ""
    citation = " · ".join(p for p in (hit.file_name, page, hit.section_path) if p)
    out = {"citation": citation, "link": base + hit.url, "file": hit.file_name, "file_id": hit.file_id,
           "library": library, "location": hit.location, "section": hit.section_path,
           "chunk_id": hit.chunk_id, "text": hit.text, **({"editable": editable} if editable is not None else {})}
    if hit.kind == "pdf" and hit.page_start:
        out["page"] = hit.page_start   # for view_page
    return {"n": n, **out} if n is not None else out


# -------------------------------------------------------------------- tools
def _decode_base64(text: str) -> bytes:
    """The `data` of create_file: base64, with or without a data: URL prefix, whitespace ignored."""
    if not isinstance(text, str):
        raise editing.EditError("`data` is the file's bytes as a base64 string.")
    if text.startswith("data:"):
        head, sep, text = text.partition(",")
        if not sep or not head.endswith(";base64"):
            raise editing.EditError("A data: URL in `data` must be base64 (data:<type>;base64,...).")
    text = "".join(text.split())
    try:
        data = base64.b64decode(text, validate=True)
    except (binascii.Error, ValueError):
        raise editing.EditError("`data` isn't valid base64. Send the file's bytes base64-encoded, nothing else; "
                                "for text, Word and Excel files use content, paragraphs or rows instead.") from None
    if not data:
        raise editing.EditError("The file is empty.")
    return data


class Tools:
    """The tool bodies, synchronous and testable without the MCP transport.

    mode "claude": Claude through /mcp, limited to what is shared with Claude.
    mode "erynne": the agent in chat (agent.py), which runs here and reads what
    the person can read; its edits and new files become proposals the person
    applies (proposals.py) instead of changing anything.
    propose: edits become proposals (default: in "erynne" mode only). Claude
    in chat (claude_chat.py) is mode "claude" with propose=True.
    """

    def __init__(self, settings: Settings, get_embedder: Callable[[], Any],
                 get_directory: Callable[[], Any] = lambda: None, *, mode: str = "claude",
                 propose: bool | None = None):
        self.settings = settings
        self.get_embedder = get_embedder
        self.get_directory = get_directory
        self.mode = mode
        self.propose = mode != "claude" if propose is None else propose

    @property
    def base(self) -> str:
        return self.settings.public_url.rstrip("/")

    @property
    def claude(self) -> bool:
        return self.mode == "claude"

    def _call_link(self, call_id: int) -> str:
        """An API call's page. Claude's links carry a view key: Desktop opens them in a browser that is
        often signed out. Erynne's (in Rooms) don't, since everyone in the room would see them."""
        return apis.view_link(self.settings, call_id) if self.claude else f"/apis/calls/{call_id}"

    def _access(self, conn: sqlite3.Connection, caller: Caller) -> fs.Access:
        return editing.ai_access(conn, caller.user_id, self.mode)

    def _editable(self, conn: sqlite3.Connection, caller: Caller) -> editing.EditCheck:
        """For the `editable` flag on listings: may the assistant change this file, or files in this folder
        (the content of .txt, .md, .docx and .xlsx files; renaming and moving any file)?"""
        return editing.EditCheck(conn, caller.user_id, caller.can_edit, self.mode)

    def search_documents(self, conn, caller: Caller, query: str, library: str | None = None,
                         folder: str | None = None, limit: int = 8) -> tuple[dict, list[int]]:
        query = (query or "").strip()
        if not query:
            raise ToolError("Give a question or some words to search for.")
        if len(query) > self.settings.max_question_chars:
            raise ToolError(f"Keep the search under {self.settings.max_question_chars} characters.")
        limit = max(1, min(int(limit), 20))
        library, folder = _split_place(library, folder)
        access = self._access(conn, caller)
        everything = allowed = retrieval.Scope.of_reach(access.reaches.values(), claude=access.claude)
        notes = []
        # Erynne: the "Search in…" place picked in chat, unless she names one.
        scope = getattr(caller, "scope", None)
        if scope and not library:
            allowed = allowed.narrow(scope["collection_id"],
                                     set(scope["folders"]) if scope.get("folders") is not None else None)
        try:
            if library:
                row = _resolve_collection(conn, access, library, self.claude)
                folder_id = _resolve_folder(conn, access, row["id"], folder)
                allowed = allowed.narrow(row["id"], fs.scope_folders(conn, access, row["id"], folder_id))
            elif folder:
                raise ToolError("Name the library the folder is in, too.")
        except ToolError:
            if self.claude:
                raise
            # Small models guess places; search everywhere rather than fail.
            place = " / ".join(p for p in (library, folder) if p)
            notes.append(f"Searched everywhere: there's no “{place}” here.")
            library = None
        if not allowed:
            return {"found": False, "query": query, "results": [],
                    "note": "Nothing is shared with " + ("Claude for " if self.claude else "") +
                            "you yet; ask a Mnemerynne admin."}, []
        keep = sharepoint.trimmer(conn, caller.user_id)
        result = retrieval.search(conn, self.settings, self.get_embedder(), query, allowed, keep=keep)
        if not result.found_anything and library and not self.claude:
            notes.append(f"Nothing close in {library}; these are from everywhere.")
            result = retrieval.search(conn, self.settings, self.get_embedder(), query, everything, keep=keep)
        if not result.found_anything:
            hint = self._api_hint(conn, caller, query)
            tip = ([f"This may be live data rather than a document: try query_api with api “{hint['api']}” "
                    f"and operation “{hint['operation']}” (see list_apis)."] if hint else [])
            return {"found": False, "query": query, "results": [], **({"try_api": hint} if hint else {}),
                    "note": " ".join(notes + ["Nothing in these documents is close to this question."
                                              + (" Search again without library and folder to look everywhere."
                                                 if library else "")] + tip)}, []
        names = {cid: _label(row) for cid, row in _collections(conn, access).items()}
        budget, picked = self.settings.mcp_max_tokens, []
        for hit in result.candidates:
            if len(picked) >= limit:
                break
            if hit.tokens > budget and picked:
                continue
            picked.append(hit)
            budget -= hit.tokens
        check = self._editable(conn, caller)
        marks = ",".join("?" * len(picked))
        folders = {r["id"]: r["folder_id"] for r in conn.execute(
            f"SELECT id, folder_id FROM files WHERE id IN ({marks})", [h.file_id for h in picked])}
        results = [_passage(h, self.base, names.get(h.collection_id, ""), n,
                            check.allows(h.collection_id, folders.get(h.file_id), h.file_id))
                   for n, h in enumerate(picked, 1)]
        return {"found": True, "query": query, "results": results, **({"note": " ".join(notes)} if notes else {})}, \
            sorted({h.file_id for h in picked})

    def read_passage(self, conn, caller: Caller, chunk_id: int, around: int = 1) -> tuple[dict, list[int]]:
        around = max(0, min(int(around), 3))
        access = self._access(conn, caller)
        row = conn.execute("SELECT c.file_id, c.ord, c.collection_id, f.folder_id, f.claude_hidden FROM chunks c "
                           "JOIN files f ON f.id = c.file_id WHERE c.id = ? AND f.deleted_at IS NULL",
                           (chunk_id,)).fetchone()
        # Chunks of a file in the bin (or under a binned folder) carry collection 0.
        if row is None or row["collection_id"] == fs.TRASH_COLLECTION or not access.can_read_file(row):
            raise ToolError("No such passage" + (" (or it isn't shared with Claude)" if self.claude else "")
                            + ". Use a chunk_id from search_documents.")
        ids = [r[0] for r in conn.execute("SELECT id FROM chunks WHERE file_id = ? AND ord BETWEEN ? AND ? ORDER BY ord",
                                          (row["file_id"], row["ord"] - around, row["ord"] + around))]
        hits = retrieval._load_hits(conn, ids)
        library = _label(_collections(conn, access)[row["collection_id"]])
        return {"passages": [_passage(hits[i], self.base, library) for i in ids]}, [row["file_id"]]

    def _user(self, conn, caller: Caller):
        return conn.execute("SELECT id, ad_account FROM users WHERE id = ?", (caller.user_id,)).fetchone()

    def _api_hint(self, conn, caller: Caller, query: str) -> dict | None:
        """The reading API operation whose example questions are closest to a search that found
        nothing in the documents, if it is close enough (settings.api_hint_threshold)."""
        ops = [o for o in apis.allowed_operations(conn, self._user(conn, caller), self.get_directory(),
                                                  for_claude=self.claude) if not o["action"]]
        if not ops:
            return None
        best = apis.match(conn, self.get_embedder().embed_query(query), [o["id"] for o in ops])
        if best is None or best[1] < self.settings.api_hint_threshold:
            return None
        op = next(o for o in ops if o["id"] == best[0])
        return {"api": op["api_name"], "operation": op["name"]}

    def list_apis(self, conn, caller: Caller) -> tuple[dict, list[int]]:
        ops = apis.allowed_operations(conn, self._user(conn, caller), self.get_directory(), for_claude=self.claude)
        out: dict[str, dict] = {}
        for op in ops:
            entry = out.setdefault(op["api_name"], {"api": op["api_name"], "operations": []})
            if op["one_at_a_time"]:
                entry["one_at_a_time"] = True
            entry["operations"].append({
                "operation": op["name"], "description": op["description"],
                **({"changes_data": True} if op["action"] else {}),
                **({"needs_admin_approval": True} if op["action"] and op["admin_approval"] else {}),
                "parameters": [{k: v for k, v in s.items() if k in ("name", "type", "required", "options",
                                                                    "min", "max", "default", "help")}
                               for s in apis.param_specs(op)]})
        if not out:
            return {"apis": [], "note": "No internal APIs are open to " + ("Claude for " if self.claude else "")
                    + "this person."}, []
        return {"apis": list(out.values())}, []

    def query_api(self, conn, caller: Caller, api: str, operation: str,
                  params: dict | None = None, offset: int = 0, find: str | None = None) -> tuple[dict, list[int]]:
        try:
            op = apis.operation_for(conn, self._user(conn, caller), self.get_directory(), api, operation,
                                    for_claude=self.claude)
            if op["action"]:
                raise ToolError(f"“{operation}” changes data in {api}: use propose_api_action, and the person "
                                "applies it.")
            result = apis.call(conn, self.settings, op["id"], params, user_id=caller.user_id,
                               via="claude" if self.claude else "chat")
        except apis.ApiError as exc:
            raise Refused(f"{exc} Use list_apis to see what you can query.", _api_code(exc)) from None
        if not result.ok:
            raise Refused(f"{result.api_name} didn't answer: {result.error}.",
                          "rate_limited" if "429" in str(result.error) else "unavailable")
        live = f"live, {result.at[11:16]} UTC"
        data, rows = apis.page(result.data, offset or 0, 40_000, find=find)
        notes = []
        if rows and "matched_of" in rows:
            notes.append(f"{rows['total']} of the {rows['matched_of']} rows the API returned mention "
                         f"“{find.strip()}”. Rows the API didn't return (its other pages) weren't searched.")
        if rows and "next_offset" in rows:
            notes.append(f"Rows {rows['offset'] + 1}–{rows['offset'] + rows['returned']} of {rows['total']}: call "
                         f"again with the same params and offset={rows['next_offset']} for the next ones.")
        if result.truncated:
            notes.append("The API's answer was over 256 KB and was cut off, so this isn't all of it: narrow the "
                         "params (a shorter period, one property) if the part you need is missing.")
        return {"api": result.api_name, "operation": result.operation_name, "at": result.at + " UTC",
                "citation": f"{result.api_name} · {result.operation_name} ({live})",
                "link": self.base + self._call_link(result.call_id), "call_id": result.call_id,
                **({"rows": rows} if rows else {}), **({"note": " ".join(notes)} if notes else {}),
                "data": data}, []

    def propose_api_action(self, conn, caller: Caller, api: str, operation: str,
                           params: dict | None = None) -> tuple[dict, list[int]]:
        """An operation that changes data becomes a proposal the person applies (proposals.propose_api)."""
        try:
            op = apis.operation_for(conn, self._user(conn, caller), self.get_directory(), api, operation,
                                    for_claude=self.claude)
            if not op["action"]:
                raise ToolError(f"“{operation}” only reads; use query_api.")
            proposal = proposals.propose_api(conn, caller, op, params, self.settings)
        except apis.ApiError as exc:
            raise Refused(f"{exc} Use list_apis to see the operations and their parameters.", _api_code(exc)) from None
        return self._proposed(conn, caller, proposal, {"api": op["api_name"], "operation": op["name"]},
                              "Nothing has changed yet: this operation needs a Mnemerynne admin's approval, so it now "
                              "waits for one (Access → Approvals). Tell the person it's with the admins; get_proposal "
                              "shows the outcome. Don't say it's done.",
                              "Nothing has changed yet. Give the person the approve link and the summary: they check "
                              "it and apply it in Mnemerynne themselves. Carry on with the rest; get_proposal shows "
                              "the outcome. Don't say it's done."), []

    def propose_batch(self, conn, caller: Caller, items: list[dict]) -> tuple[dict, list[int]]:
        """Several API actions as one proposal with one approve link (proposals.propose_batch); any item that
        can't run refuses the whole batch, so a card never offers what can't run."""
        if not isinstance(items, list) or not items:
            raise Refused("items is a list of {api, operation, params}, at least one.", "invalid")
        if len(items) > self.settings.proposal_batch_max:
            raise Refused(f"A batch holds at most {self.settings.proposal_batch_max} changes; split it.", "invalid")
        checked = []
        for n, item in enumerate(items, 1):
            if not isinstance(item, dict) or not item.get("api") or not item.get("operation"):
                raise Refused(f"Item {n} needs api and operation (and params).", "invalid")
            try:
                op = apis.operation_for(conn, self._user(conn, caller), self.get_directory(), item["api"],
                                        item["operation"], for_claude=self.claude)
                if not op["action"]:
                    raise ToolError(f"Item {n}: “{item['operation']}” only reads; use query_api.")
                apis.validate_params(apis.param_specs(op), item.get("params"))
            except apis.ApiError as exc:
                raise Refused(f"Item {n}: {exc} Use list_apis to see the operations and their parameters.",
                              _api_code(exc)) from None
            checked.append((op, item.get("params")))
        try:
            proposal = proposals.propose_batch(conn, caller, checked, self.settings)
        except (proposals.ProposalError, apis.ApiError) as exc:
            raise Refused(str(exc), "invalid") from None
        return self._proposed(conn, caller, proposal, {},
                              "Nothing has changed yet: a change in this batch needs a Mnemerynne admin's approval, "
                              "so the whole batch waits for one (Access → Approvals). Tell the person it's with the "
                              "admins; get_proposal shows each item's outcome. Don't say it's done.",
                              "Nothing has changed yet. Give the person the approve link and the summary: they tick "
                              "the changes to run and apply them in Mnemerynne themselves; get_proposal then shows "
                              "each item's outcome. Carry on with the rest. Don't say it's done."), []

    def _proposed(self, conn, caller: Caller, proposal: int, extra: dict, admin_note: str, note: str) -> dict:
        """The shape every proposing tool returns: proposal_id, summary, expires_at, status and, when the person
        decides it, the approve link (not in chat: the card is under the answer)."""
        card = proposals.view(conn, proposals.get(conn, proposal, caller.user_id))
        out = {**self._proposal_out(card), **extra, "status": "proposed"}
        if card["approver"] == "admin":
            out.pop("approve", None)
            return {**out, "status": "awaiting_admin", "note": admin_note}
        if self.propose:
            out.pop("approve", None)
            return {**out, "note": _PROPOSED}
        return {**out, "note": note}

    def list_libraries(self, conn, caller: Caller) -> tuple[dict, list[int]]:
        access = self._access(conn, caller)
        out = []
        for cid, row in sorted(_collections(conn, access).items(),
                               key=lambda kv: (kv[1]["kind"] != "private", _label(kv[1]).lower())):
            files, searchable, total = fs.file_counts(conn, access, cid)
            out.append({"library": _label(row), "id": cid,
                        "kind": "file box" if row["kind"] == "private" else "library",
                        "files": files, "searchable_files": searchable,
                        # Files in folders this connection can't open, or taken out of Claude's view.
                        **({"not_shown": total - files} if total > files else {})})
        edit = ([f"{p['library']}" + (f" / {p['folder']}" if p["folder"] else "") for p in
                 editing.editable_places(conn, caller.user_id, self.mode)] if caller.can_edit else [])
        try:
            agents = editing.agents_folder(conn, caller.user_id)
        except (fs.FsError, OSError):
            agents = None   # the share is away; no upload link this time
        inbox = permissions.private_collection_id(conn, caller.user_id)
        upload = ({"upload": f"{self.base}/files#c={inbox}&f={agents}"} if agents and inbox else {})
        return {"libraries": out, "you_can_edit_in": edit, **upload,
                **({} if caller.can_edit else {"editing": "This connection can only search; the person can turn "
                                                          "on editing for it on the Claude page in Mnemerynne."})}, []

    def browse_folder(self, conn, caller: Caller, library: int | str,
                      folder: int | str | None = None) -> tuple[dict, list[int]]:
        library, folder = _split_place(library, folder)
        access = self._access(conn, caller)
        row = _resolve_collection(conn, access, library, self.claude)
        folder_id = _resolve_folder(conn, access, row["id"], folder)
        listing = fs.browse(conn, access, row["id"], folder_id)
        same = _duplicates(conn, access, [f["id"] for f in listing["files"]])
        check = self._editable(conn, caller)
        return {
            "library": _label(row), "library_id": row["id"], "path": _path(conn, folder_id) or "/",
            **({"folder_id": folder_id} if folder_id is not None else {}),
            "editable": check.allows(row["id"], folder_id),
            "folders": [{"name": f["name"], "id": f["id"], "folder_id": f["id"], "items": f["items"],
                         "editable": check.allows(row["id"], f["id"])} for f in listing["folders"]],
            "files": [{"name": f["display_name"], "file_id": f["id"], "type": f["kind"], "pages": f["unit_count"],
                       "searchable": f["status"] == "ready", "editable": check.allows(row["id"], folder_id, f["id"]),
                       "link": _file_link(self.base, f["id"], f["kind"]),
                       **({"same_content_as": same[f["id"]]} if f["id"] in same else {})}
                      for f in listing["files"]],
        }, []

    def find_files(self, conn, caller: Caller, name: str) -> tuple[dict, list[int]]:
        """Names that contain `name`, forgivingly (any case; spaces, hyphens, underscores and dots ignored):
        libraries, folders and files. Folders carry their folder_id, files the folder_id they are in."""
        access = self._access(conn, caller)
        found = fs.search_names(conn, access, name, limit=50, forgiving=True)
        collections = _collections(conn, access)
        names = {cid: _label(row) for cid, row in collections.items()}
        key = fs.loose(name)
        libraries = [{"library": label, "id": cid} for cid, label in sorted(names.items(), key=lambda kv: kv[1].lower())
                     if key and key in fs.loose(label)]
        same = _duplicates(conn, access, [f["id"] for f in found["files"]])
        check = self._editable(conn, caller)
        return {
            **({"libraries": libraries} if libraries else {}),
            "folders": [{"name": f["name"], "folder_id": f["id"], "library": names[f["collection_id"]],
                         "path": _path(conn, f["id"]), "editable": check.allows(f["collection_id"], f["id"])}
                        for f in found["folders"]],
            "files": [{"name": f["display_name"], "file_id": f["id"], "library": names[f["collection_id"]],
                       "folder": _path(conn, f["folder_id"]) or "/",
                       **({"folder_id": f["folder_id"]} if f["folder_id"] is not None else {}), "type": f["kind"],
                       "searchable": f["status"] == "ready",
                       "editable": check.allows(f["collection_id"], f["folder_id"], f["id"]),
                       "link": _file_link(self.base, f["id"], f["kind"]),
                       **({"same_content_as": same[f["id"]]} if f["id"] in same else {})}
                      for f in found["files"]],
        }, []

    def view_page(self, conn, caller: Caller, file_id: int, page: int) -> tuple[list, list[int]]:
        """A PDF page as a JPEG, plus a line saying what it is."""
        access = self._access(conn, caller)
        row = fs.file_row(conn, file_id)
        if row is None or row["deleted_at"] is not None or not fs.is_live_folder(conn, row["folder_id"]) \
                or not access.can_read_file(row):
            raise ToolError("No such file" + (" (or it isn't shared with Claude)" if self.claude else "")
                            + ". Use a file_id from the other tools.")
        if row["kind"] != "pdf":
            raise ToolError(f"“{row['display_name']}” isn't a PDF; use read_passage for its text.")
        pages = row["unit_count"] or 0
        if not 1 <= page <= max(pages, 1):
            raise ToolError(f"“{row['display_name']}” has {pages} page(s); ask for 1-{pages}.")
        if not self.claude:
            # Erynne's models don't take images: hand the person the page instead.
            return {"file": row["display_name"], "page": page, "pages": pages,
                    "link": f"{self.base}/files/{file_id}/pdf#page={page}",
                    "note": "You can't see images. The person can open the page with this link; use "
                            "search_documents or read_passage for its text."}, [file_id]
        path = store.content_path(conn, self.settings, file_id)
        if not path.exists():
            raise ToolError("The file's content is missing on the server; tell a Mnemerynne admin.")
        pdf = pdfium.PdfDocument(str(path))
        try:
            pdf_page = pdf[page - 1]
            width, height = pdf_page.get_size()
            scale = min(PAGE_WIDTH_PX / width, PAGE_MAX_PX / max(width, height))
            picture = pdf_page.render(scale=scale).to_pil().convert("RGB")
        finally:
            pdf.close()
        buffer = io.BytesIO()
        picture.save(buffer, format="JPEG", quality=82, optimize=True)
        caption = (f"{row['display_name']} · p. {page} of {pages}. Link for the person: "
                   f"{self.base}/files/{file_id}/pdf#page={page}")
        return [caption, Image(data=buffer.getvalue(), format="jpeg")], [file_id]

    # ----------------------------------------------------------- editing
    def _where(self, conn, row) -> str:
        coll = conn.execute("SELECT kind, name FROM collections WHERE id = ?", (row["collection_id"],)).fetchone()
        path = _path(conn, row["folder_id"])
        return (permissions.PRIVATE_LABEL if coll["kind"] == "private" else coll["name"]) + (f" / {path}" if path else "")

    def open_file(self, conn, caller: Caller, file_id: int, start: int = 1, limit: int | None = None,
                  sheet: str | None = None, cells: str | None = None) -> tuple[dict, list[int]]:
        loaded = editing.load(conn, self.settings, caller.user_id, file_id, via=self.mode)
        if loaded.fmt == "text":
            view = editing_formats.text_view(loaded.data, start, limit or 400)
        elif loaded.fmt == "docx":
            view = editing_formats.docx_view(loaded.data, start, limit or 200)
        else:
            view = editing_formats.xlsx_view(loaded.data, sheet, cells)
        try:
            editing.check_edit(conn, caller.user_id, caller.can_edit, loaded.row["collection_id"],
                               loaded.row["folder_id"], via=self.mode, file_id=file_id)
            can_edit, why = True, None
        except editing.EditError as exc:
            can_edit, why = False, str(exc)
        return {"file_id": file_id, "file": loaded.row["display_name"], "where": self._where(conn, loaded.row),
                "version": loaded.version, "can_edit": can_edit, **({"why_not": why} if why else {}),
                "link": _file_link(self.base, file_id, loaded.row["kind"]), **view}, [file_id]

    def _edited(self, conn, caller: Caller, file_id: int, version: str, fmt: str, apply) -> tuple[dict, list[int]]:
        loaded = editing.load(conn, self.settings, caller.user_id, file_id, via=self.mode)
        if loaded.fmt != fmt:
            tool = {"text": "edit_text", "docx": "edit_document", "xlsx": "edit_spreadsheet"}[loaded.fmt]
            raise editing.EditError(f"“{loaded.row['display_name']}” is edited with {tool}.")
        editing.check_edit(conn, caller.user_id, caller.can_edit, loaded.row["collection_id"], loaded.row["folder_id"],
                           via=self.mode, file_id=file_id)
        if version != loaded.version:
            raise editing.EditConflict(f"“{loaded.row['display_name']}” changed since that version was opened. "
                                       f"Open it again and redo the change on the new version.")
        result = apply(loaded.data)
        if self.propose:
            proposal = proposals.propose_edit(conn, self.settings, caller, loaded, result.data, result.changes)
            return {"proposal": proposal, "file_id": file_id, "file": loaded.row["display_name"],
                    "changes": result.changes, "status": "proposed", "note": _PROPOSED}, [file_id]
        new_version = editing.save(conn, self.settings, caller, file_id, result.data, expected=version,
                                   summary="; ".join(result.changes))
        return {"file_id": file_id, "file": loaded.row["display_name"], "version": new_version,
                "changes": result.changes, "link": _file_link(self.base, file_id, loaded.row["kind"]),
                "note": "The previous version is kept; the person can restore it from Files (Versions)."}, [file_id]

    def edit_text(self, conn, caller: Caller, file_id: int, version: str, edits: list[dict] | None = None,
                  content: str | None = None) -> tuple[dict, list[int]]:
        return self._edited(conn, caller, file_id, version, "text", lambda data: editing_formats.text_apply(
            data, edits, content, self.settings.edit_max_operations))

    def edit_document(self, conn, caller: Caller, file_id: int, version: str,
                      operations: list[dict]) -> tuple[dict, list[int]]:
        return self._edited(conn, caller, file_id, version, "docx", lambda data: editing_formats.docx_apply(
            data, operations, self.settings.edit_max_operations))

    def edit_spreadsheet(self, conn, caller: Caller, file_id: int, version: str,
                         operations: list[dict]) -> tuple[dict, list[int]]:
        return self._edited(conn, caller, file_id, version, "xlsx", lambda data: editing_formats.xlsx_apply(
            data, operations, self.settings.edit_max_operations, self.settings.edit_max_cells))

    def create_file(self, conn, caller: Caller, name: str, content: str | None = None, paragraphs: list | None = None,
                    rows: list | None = None, sheet: str | None = None, library: str | None = None,
                    folder: str | None = None, data: str | None = None) -> tuple[dict, list[int]]:
        fmt = editing.format_of(name or "")
        raw = data is not None
        if raw:
            if content is not None or paragraphs is not None or rows is not None:
                raise editing.EditError("Give either `data` (the file's bytes, base64) or content/paragraphs/rows, "
                                        "not both.")
            data = _decode_base64(data)
        elif fmt == "text":
            data = editing_formats.text_new(content or "")
        elif fmt == "docx":
            data = editing_formats.docx_new(paragraphs if paragraphs is not None else
                                            (content or "").split("\n\n") if content else [])
        elif fmt == "xlsx":
            data = editing_formats.xlsx_new(rows or [], sheet)
        else:
            raise editing.EditError("Name the file with .txt, .md, .docx or .xlsx at the end, or send any other kind "
                                    "of file you made (an image, a PDF, a zip) as `data`, its bytes in base64. For "
                                    "a file on the person's own computer, give them the upload link from "
                                    "list_libraries.")
        collection_id, folder_id = self._place(conn, caller, library, folder)
        if self.propose:
            proposal = proposals.propose_new(conn, self.settings, caller, name, data, collection_id, folder_id,
                                             [f"new file {name}"], raw=raw)
            row = conn.execute("SELECT name, collection_id, folder_id FROM agent_proposals WHERE id = ?",
                               (proposal,)).fetchone()
            return {"proposal": proposal, "file": row["name"],
                    "where": proposals.where(conn, row["collection_id"], row["folder_id"]),
                    "status": "proposed", "note": _PROPOSED}, []
        file_id, version = editing.create(conn, self.settings, caller, name, data, collection_id, folder_id, raw=raw)
        row = conn.execute("SELECT * FROM files f JOIN blobs b ON b.sha256 = f.blob_sha256 WHERE f.id = ?",
                           (file_id,)).fetchone()
        out = {"file_id": file_id, "file": row["display_name"], "where": self._where(conn, row), "version": version,
               "link": _file_link(self.base, file_id, row["kind"])}
        if raw:
            out["size"] = row["size"]
            out["kind"] = row["kind"]
            if row["notes"]:
                out["note"] = row["notes"]
        return out, [file_id]

    def create_folder(self, conn, caller: Caller, name: str, library: str | None = None,
                      folder: str | None = None) -> tuple[dict, list[int]]:
        if self.propose:
            raise ToolError("Folders can't be proposed in chat; the person makes them in Files.")
        collection_id, parent_id = self._place(conn, caller, library, folder)
        folder_id, created = editing.create_folder(conn, caller, name, collection_id, parent_id, via=self.mode)
        row = conn.execute("SELECT collection_id FROM folders WHERE id = ?", (folder_id,)).fetchone()
        coll = conn.execute("SELECT kind, name FROM collections WHERE id = ?", (row["collection_id"],)).fetchone()
        library = permissions.PRIVATE_LABEL if coll["kind"] == "private" else coll["name"]
        return {"folder_id": folder_id, "library": library, "folder": _path(conn, folder_id), "created": created,
                "link": f"{self.base}/files#c={row['collection_id']}&f={folder_id}",
                **({} if created else {"note": "It was already there; nothing changed."})}, []

    def _item(self, file_id: int | None, folder_id: int | None) -> fs.Item:
        if (file_id is None) == (folder_id is None):
            raise Refused("Give either file_id or folder_id (from browse_folder or find_files).")
        return fs.Item("file", file_id) if file_id is not None else fs.Item("folder", folder_id)

    def _place(self, conn, caller: Caller, library: int | str | None,
               folder: int | str | None) -> tuple[int | None, int | None]:
        """A place: a library (name or id) and a folder in it (path or id), a folder id alone, or (None, None)
        for the person's Agents folder."""
        library, folder = _split_place(library, folder)
        access = self._access(conn, caller)
        if library in (None, ""):
            if _is_id(folder):
                row = conn.execute("SELECT collection_id FROM folders WHERE id = ?", (folder,)).fetchone()
                if row is None or row["collection_id"] not in _collections(conn, access):
                    raise Refused(f"No folder with id {folder}. Call browse_folder or find_files for ids.",
                                  "not_found")
                return row["collection_id"], _resolve_folder(conn, access, row["collection_id"], folder)
            if folder not in (None, ""):
                raise editing.EditError("Name the library the folder is in, too, or give the folder's id (or leave "
                                        "both out for the Agents folder).")
            return None, None
        row = _resolve_collection(conn, access, library, self.claude)
        return row["id"], _resolve_folder(conn, access, row["id"], folder)

    def _no_proposals(self) -> None:
        if self.propose:
            raise ToolError("This can't be proposed in chat; the person does it in Files.")

    def rename_item(self, conn, caller: Caller, name: str, file_id: int | None = None,
                    folder_id: int | None = None) -> tuple[dict, list[int]]:
        self._no_proposals()
        item = self._item(file_id, folder_id)
        new = editing.rename_item(conn, caller, item, name, via=self.mode)
        return {"renamed": item.type, "id": item.id, "name": new}, [item.id] if item.type == "file" else []

    def move_item(self, conn, caller: Caller, file_id: int | None = None, folder_id: int | None = None,
                  library: int | str | None = None, folder: int | str | None = None,
                  to_library: int | str | None = None, to_folder: int | str | None = None) -> tuple[dict, list[int]]:
        """file_id or folder_id is what moves; to_library and to_folder (or the older library and folder) are
        where it goes."""
        self._no_proposals()
        if file_id is not None and folder_id is not None:
            raise Refused("folder_id is a folder to move, not where to put it. Give file_id alone (or folder_id "
                          "alone) and the destination as to_library and to_folder (a path, or a folder id).")
        if {to_library, to_folder} - {None, ""} and {library, folder} - {None, ""}:
            raise Refused("Give the destination once, as to_library and to_folder.")
        if {to_library, to_folder} - {None, ""}:
            library, folder = to_library, to_folder
        item = self._item(file_id, folder_id)
        collection_id, parent_id = self._place(conn, caller, library, folder)
        name = editing.move_item(conn, caller, item, collection_id, parent_id, via=self.mode)
        table = "files" if item.type == "file" else "folders"
        row = conn.execute(f"SELECT collection_id, {'folder_id' if item.type == 'file' else 'parent_id'} AS place "
                           f"FROM {table} WHERE id = ?", (item.id,)).fetchone()
        return {"moved": item.type, "id": item.id, "name": name,
                "where": proposals.where(conn, row["collection_id"], row["place"])}, \
            [item.id] if item.type == "file" else []

    def delete_item(self, conn, caller: Caller, file_id: int | None = None,
                    folder_id: int | None = None) -> tuple[dict, list[int]]:
        self._no_proposals()
        item = self._item(file_id, folder_id)
        proposal = proposals.propose_delete(conn, caller, item, via=self.mode, settings=self.settings)
        return self._proposed(conn, caller, proposal, {}, "",
                              "Nothing is deleted yet. Give the person the approve link and the summary: they check "
                              "it and move it to the recycle bin in Mnemerynne themselves. Don't say it's done."), []

    def list_changes(self, conn, caller: Caller, library: int | str, folder: int | str | None = None,
                     since: str | None = None) -> tuple[dict, list[int]]:
        library, folder = _split_place(library, folder)
        access = self._access(conn, caller)
        row = _resolve_collection(conn, access, library, self.claude)
        folder_id = _resolve_folder(conn, access, row["id"], folder)
        try:
            moment = activity.parse_since(since)
        except ValueError as exc:
            raise Refused(str(exc)) from None
        found = activity.changes(conn, access, row["id"], folder_id, moment)
        folders = activity.folder_changes(conn, access, row["id"], folder_id, moment)
        for entry in found:
            if "binned" in entry:
                entry["in_recycle_bin"] = True    # no link: it doesn't open from the bin
                continue
            kind = conn.execute("SELECT b.kind FROM files f JOIN blobs b ON b.sha256 = f.blob_sha256 WHERE f.id = ?",
                                (entry["file_id"],)).fetchone()["kind"]
            entry["link"] = _file_link(self.base, entry["file_id"], kind)
        for entry in folders:
            if "binned" in entry:
                entry["in_recycle_bin"] = True
            else:
                entry["link"] = f"{self.base}/files#c={row['id']}&f={entry['folder_id']}"
        full = len(found) >= activity.MAX_FILES or len(folders) >= activity.MAX_FILES
        return {"library": _label(row), "folder": _path(conn, folder_id) or "/", "since": moment + " UTC",
                "files": found, **({"folders": folders} if folders else {}),
                **({} if found or folders else {"note": "Nothing changed here since then."}),
                **({"note": f"Showing the {activity.MAX_FILES} most recent; ask for a later `since`."}
                   if full else {})}, [e["file_id"] for e in found]

    def request_edit_access(self, conn, caller: Caller, file_id: int | None = None, folder_id: int | None = None,
                            library: int | str | None = None, folder: int | str | None = None) -> tuple[dict, list[int]]:
        """Where the assistant may not edit: what would let it, as a proposal the person applies when it is
        theirs to give (AI edit in their own File box, or a read-only switch they can clear; a whole library
        when they are an admin), or else who to ask."""
        if sum(x not in (None, "") for x in (file_id, folder_id, library if library is not None else folder)) != 1:
            raise Refused("Give one of file_id, folder_id, or library (and folder).")
        access = self._access(conn, caller)
        if file_id is not None:
            row = fs.file_row(conn, file_id)
            if row is None or row["deleted_at"] is not None or not fs.is_live_folder(conn, row["folder_id"]) \
                    or not access.can_read_file(row):
                raise Refused("No such file" + (" (or it isn't shared with Claude)" if self.claude else "")
                              + ". Use a file_id from the other tools.", "not_found")
            cid, fid, name = row["collection_id"], row["folder_id"], row["display_name"]
        elif folder_id is not None:
            row = conn.execute("SELECT collection_id, name FROM folders WHERE id = ?", (folder_id,)).fetchone()
            if row is None or row["collection_id"] not in _collections(conn, access):
                raise Refused(f"No folder with id {folder_id}. Call browse_folder or find_files for ids.", "not_found")
            cid, fid = row["collection_id"], _resolve_folder(conn, access, row["collection_id"], folder_id)
            name = row["name"]
        else:
            cid, fid = self._place(conn, caller, library, folder)
            name = None
        coll = conn.execute("SELECT kind, name, owner_user_id, claude_edit FROM collections WHERE id = ?",
                            (cid,)).fetchone()
        name = name or (_path(conn, fid).rsplit("/", 1)[-1] if fid else _label(coll))
        where = proposals.where(conn, cid, fid)
        try:
            editing.check_edit(conn, caller.user_id, caller.can_edit, cid, fid, via=self.mode, file_id=file_id)
            return {"editable": True, "where": where, "note": "You may already edit here; go ahead."}, []
        except editing.EditNotFound:
            raise
        except editing.EditForbidden:
            pass
        who = editing.ASSISTANTS[self.mode]
        if not caller.can_edit:
            return {"editable": False, "status": "needs_person", "link": f"{self.base}/preferences#claude",
                    "note": "This connection can only search. The person turns on “Let Claude edit files” for it on "
                            "their Preferences page (link); then try again."}, []
        if not fs.Access.of(conn, caller.user_id).can_write(cid, fid):
            return {"editable": False, "status": "not_possible", "where": where,
                    "note": "The person can't change files here themselves, so they can't let you either. Only a "
                            "Mnemerynne admin can give them edit access to it."}, []
        own_inbox = coll["kind"] == "private" and coll["owner_user_id"] == caller.user_id
        blocked = editing.no_edit_item(conn, fid, file_id)
        if not own_inbox and not coll["claude_edit"] and coll["kind"] != "library":
            return {"editable": False, "status": "needs_admin", "where": where,
                    "note": "This is someone else's File box: only a Mnemerynne admin can let the AI edit in it. "
                            "Meanwhile work in the person's own Agents folder."}, []
        approver = "self"
        if not own_inbox and not coll["claude_edit"]:
            user = conn.execute("SELECT * FROM users WHERE id = ?", (caller.user_id,)).fetchone()
            target = fs.Item("library", cid)
            if accounts.is_admin(conn, user, self.get_directory(), self.settings.admin_usernames):
                changes = [f"Let the AI edit in the whole {_label(coll)} library, for everyone who can change files "
                           f"there (you're an admin; you can switch it off again in Access → Files & folders)."]
            else:
                # Not theirs to give: it waits in the admins' queue (Access → Approvals).
                approver = "admin"
                changes = [f"Let the AI edit in the whole {_label(coll)} library, for everyone who can change files "
                           f"there. Asked for by {user['username']}; an admin can switch it off again in Access → "
                           f"Files & folders."]
        elif blocked is not None:
            target = blocked
            label = name if blocked.type == "file" and blocked.id == file_id else \
                conn.execute(f"SELECT {'display_name' if blocked.type == 'file' else 'name'} FROM "
                             f"{blocked.type}s WHERE id = ?", (blocked.id,)).fetchone()[0]
            changes = [f"Turn “AI can edit” back on for “{label}”: it is read-only for the AI now."]
        elif file_id is not None or fid is not None:
            target = fs.Item("file", file_id) if file_id is not None else fs.Item("folder", fid)
            changes = [f"Let the AI edit “{name}” in your File box (you can switch it off again in Files)."]
        else:
            return {"editable": False, "status": "not_possible", "where": where,
                    "note": "AI edit is switched on per folder or file in the File box, not for all of it. Ask for "
                            "a folder or file in it, or work in the Agents folder."}, []
        proposal = proposals.propose_access(conn, caller, target, name, cid, fid, changes, approver=approver,
                                            settings=self.settings)
        return self._proposed(conn, caller, proposal, {"editable": False, "where": where, "changes": changes},
                              f"Nothing has changed yet: only a Mnemerynne admin can let {who} edit in all of "
                              f"{_label(coll)}, so the request now waits for one (Access → Approvals). Tell the person "
                              f"it's with the admins; get_proposal shows the outcome. Meanwhile work in their Agents "
                              f"folder. Don't say it's done.",
                              f"Nothing has changed yet. Give the person the approve link and the summary: once they "
                              f"allow it there, {who} can edit here. Don't say it's done."), []

    def list_comments(self, conn, caller: Caller, file_id: int) -> tuple[dict, list[int]]:
        try:
            found = comments.listing(conn, self._access(conn, caller), file_id)
        except comments.CommentError as exc:
            raise ToolError(str(exc)) from None
        return {"file_id": file_id, "comments": found,
                **({} if found else {"note": "No comments yet."})}, [file_id]

    def add_comment(self, conn, caller: Caller, file_id: int, text: str,
                    reply_to: int | None = None) -> tuple[dict, list[int]]:
        self._no_proposals()
        if not caller.can_edit:
            raise ToolError("This connection can only search. To let Claude comment, open Claude in Mnemerynne and "
                            "turn on “Let Claude edit files” for this token.")
        try:
            comment_id = comments.add(conn, self._access(conn, caller), file_id, text, via="claude",
                                      reply_to=reply_to)
        except comments.CommentError as exc:
            raise ToolError(str(exc)) from None
        return {"comment_id": comment_id, "file_id": file_id, **({"reply_to": reply_to} if reply_to else {}),
                "note": "Saved. People see it in Files (the file's details), and other agents with list_comments."}, \
            [file_id]

    def list_messages(self, conn, caller: Caller, unread_only: bool = True, limit: int = 20,
                      mark_read: bool = False) -> tuple[dict, list[int]]:
        """The person's notes and system messages (messages.py), and the announcements waiting for them."""
        user = conn.execute("SELECT * FROM users WHERE id = ?", (caller.user_id,)).fetchone()
        listed = messages.inbox(conn, caller.user_id, unread_only=unread_only, limit=max(1, min(int(limit), 50)))
        if mark_read:
            for m in listed:
                messages.mark_read(conn, caller.user_id, m["message_id"])
        out = [{"message_id": m["message_id"], "from": m["from"], "kind": m["kind"], "at": m["at"], "text": m["text"],
                **({"link": self.base + m["link"]} if m["link"] else {}), "read": m["read"] or mark_read}
               for m in listed]
        pending = [{"title": a["title"], **({"link": self.base + a["link"]} if a["link"] else {}),
                    "must_ack": a["must_ack"]} for a in notices.pending_for(conn, user, self.get_directory())]
        unread = messages.unread_count(conn, caller.user_id)
        return {"unread": unread, "messages": out, "announcements": pending,
                "note": ("Nothing unread." if not out and not pending else
                         "Tell the person briefly what's there. ") +
                        ("" if mark_read else "They mark messages read on their Messages page (or pass mark_read)."),
                }, []

    def send_message(self, conn, caller: Caller, to: str, text: str, link: str | None = None) -> tuple[dict, list[int]]:
        """A short note to a colleague, inside Mnemerynne (messages.py): it isn't email."""
        if not caller.can_edit:
            raise ToolError("This connection can only search. To let Claude send notes, open Claude in Mnemerynne and "
                            "turn on “Let Claude edit files” for this token.")
        user = conn.execute("SELECT * FROM users WHERE id = ?", (caller.user_id,)).fetchone()
        try:
            message_id = messages.send(conn, user, to, text, via=self.mode, link=link, public_url=self.base)
        except messages.MessageError as exc:
            raise Refused(str(exc), "invalid") from None
        return {"message_id": message_id, "to": (to or "").strip().lower(),
                "note": "Sent. They see it under Messages in Mnemerynne (and their Claude with list_messages); "
                        "it isn't an email."}, []

    # ------------------------------------------------------------- rooms
    def _room_user(self, conn, caller: Caller) -> sqlite3.Row:
        if not rooms.enabled(conn):
            raise Refused("Rooms isn't turned on in this organisation's Mnemerynne.", "not_allowed")
        return conn.execute("SELECT * FROM users WHERE id = ?", (caller.user_id,)).fetchone()

    def _room_message(self, m: dict) -> dict:
        out = {"message_id": m["id"], "from": m["who"], "at": m["created_at"] + " UTC",
               "text": "(deleted)" if m["deleted"] else m["body"]}
        if m.get("replies"):
            out["replies"] = m["replies"]
        if m.get("edited_at"):
            out["edited"] = True
        if m.get("reactions"):
            out["reactions"] = {r["emoji"]: len(r["users"]) for r in m["reactions"]}
        if m.get("attachments"):
            out["files"] = [a["name"] for a in m["attachments"]]
        return out

    def list_rooms(self, conn, caller: Caller) -> tuple[dict, list[int]]:
        """The person's channels and direct messages, with what's unread (rooms.py)."""
        listed = rooms.rooms_for(conn, self._room_user(conn, caller), self.get_directory())
        out = [{"room": ("#" + r["name"]) if r["kind"] == "channel" else r["name"], "room_id": r["id"],
                "kind": r["kind"], "unread": r["unread"], "mentions_you": r["mentions"],
                **({"topic": r["topic"]} if r["topic"] else {})} for r in listed]
        return {"rooms": out, "link": f"{self.base}/rooms",
                **({} if out else {"note": "No rooms yet; the person joins or starts them under Rooms."})}, []

    def read_room(self, conn, caller: Caller, room: int | str, limit: int = 30, before: int | None = None,
                  thread: int | None = None) -> tuple[dict, list[int]]:
        user, directory = self._room_user(conn, caller), self.get_directory()
        try:
            r = rooms.find_room(conn, user, room, directory)
            if thread is not None:
                listed = rooms.thread(conn, user, thread, directory)
                if listed[0]["room_id"] != r["id"]:
                    raise rooms.NotFound("That thread isn't in this room.")
            else:
                listed = rooms.history(conn, user, r["id"], directory, before=before, limit=max(1, min(int(limit), 100)))
        except rooms.RoomError as exc:
            raise Refused(str(exc), "not_found" if exc.status == 404 else "not_allowed") from None
        label = ("#" + r["name"]) if r["kind"] == "channel" else rooms.view_room(conn, r, user)["name"]
        out = {"room": label, "room_id": r["id"], "messages": [self._room_message(m) for m in listed],
               "link": f"{self.base}/rooms#room={r['id']}"}
        if thread is None and listed and len(listed) >= limit:
            out["note"] = f"Older messages: call again with before={listed[0]['id']}."
        return out, []

    def post_to_room(self, conn, caller: Caller, room: int | str, text: str,
                     thread: int | None = None) -> tuple[dict, list[int]]:
        """Write in a room as "Claude for <name>" (rooms.post)."""
        user, directory = self._room_user(conn, caller), self.get_directory()
        if not caller.can_edit:
            raise ToolError("This connection can only search. To let Claude write in Rooms, open Claude in Mnemerynne "
                            "and turn on “Let Claude edit files” for this token.")
        try:
            r = rooms.find_room(conn, user, room, directory)
            message, _ = rooms.post(conn, user, r["id"], text, directory, via=self.mode if self.mode in
                                    ("claude", "erynne") else "claude", thread_root_id=thread)
        except rooms.RoomError as exc:
            raise Refused(str(exc), "not_found" if exc.status == 404 else "invalid") from None
        return {"message_id": message["id"], "room_id": r["id"], "link": f"{self.base}/rooms#room={r['id']}",
                "note": "Posted. Everyone in the room sees it, marked as written by Claude for the person."}, []

    def react_in_room(self, conn, caller: Caller, message_id: int, emoji: str) -> tuple[dict, list[int]]:
        user = self._room_user(conn, caller)
        if not caller.can_edit:
            raise ToolError("This connection can only search.")
        try:
            m = rooms.react(conn, user, message_id, emoji, True, self.get_directory())
        except rooms.RoomError as exc:
            raise Refused(str(exc), "not_found" if exc.status == 404 else "invalid") from None
        return {"message_id": m["id"], "reactions": {r["emoji"]: len(r["users"]) for r in m["reactions"]}}, []

    # ------------------------------------------------- briefing and security
    def _admin_caller(self, conn, caller: Caller) -> sqlite3.Row:
        user = conn.execute("SELECT * FROM users WHERE id = ?", (caller.user_id,)).fetchone()
        if user is None or not accounts.is_admin(conn, user, self.get_directory(), self.settings.admin_usernames):
            raise Refused("Only Mnemerynne's admins can see this.", "not_allowed")
        return user

    def my_briefing(self, conn, caller: Caller) -> tuple[dict, list[int]]:
        """What's waiting for the person inside Mnemerynne."""
        user = conn.execute("SELECT * FROM users WHERE id = ?", (caller.user_id,)).fetchone()
        return briefings.briefing(conn, user, self.get_directory()), []

    def mnemerynne_updates(self, conn, caller: Caller) -> tuple[dict, list[int]]:
        self._admin_caller(conn, caller)
        return briefings.mnemerynne_updates(self.settings), []

    def m365_security(self, conn, caller: Caller, kind: str = "alerts", tenant: str | None = None) -> tuple[dict, list[int]]:
        self._admin_caller(conn, caller)
        try:
            return briefings.m365_security(conn, self.settings, kind, tenant), []
        except briefings.BriefingError as exc:
            raise Refused(str(exc), "invalid") from None

    def security_feeds(self, conn, caller: Caller, since_days: int = 7) -> tuple[dict, list[int]]:
        return briefings.security_feeds(conn, since_days), []

    # ------------------------------------------------------------ Automator
    def _automator_user(self, conn, caller: Caller) -> sqlite3.Row:
        user = conn.execute("SELECT * FROM users WHERE id = ?", (caller.user_id,)).fetchone()
        if not automations.may_use(conn, self.settings, user, self.get_directory()):
            raise Refused("Automator isn't turned on for this person.", "not_allowed")
        return user

    def list_automations(self, conn, caller: Caller) -> tuple[dict, list[int]]:
        """The person's automations (alpha): what each does, when, and how its last run went."""
        self._automator_user(conn, caller)
        out = []
        for row in automations.for_user(conn, caller.user_id):
            v = automations.view(conn, row)
            out.append({k: v[k] for k in ("id", "name", "when", "runner", "enabled", "next_run_at", "last_run",
                                          "paused_reason")} | {"prompt": v["prompt"][:500]})
        return {"automations": out, "link": f"{self.base}/automations"}, []

    def run_automation(self, conn, caller: Caller, automation: int | str) -> tuple[dict, list[int]]:
        self._automator_user(conn, caller)
        if not caller.can_edit:
            raise ToolError("This connection can only search; running an automation needs one that may edit.")
        scheduler = automations_run.ACTIVE
        if scheduler is None:
            raise Refused("Automator isn't running on the server right now.", "unavailable")
        try:
            with db.transaction(conn):
                row = automations.find(conn, caller.user_id, automation)
                run_id = scheduler.run_now(conn, row, "mcp")
        except automations.AutomationError as exc:
            raise Refused(str(exc), "invalid") from None
        return {"run_id": run_id, "status": "running", "link": f"{self.base}/automations#run={run_id}",
                "note": "Started. get_automation_run shows the answer when it's done (usually within minutes)."}, []

    def get_automation_run(self, conn, caller: Caller, run_id: int) -> tuple[dict, list[int]]:
        self._automator_user(conn, caller)
        run = automations.get_run(conn, run_id)
        if run is None or run["owner_id"] != caller.user_id:
            raise Refused("No such run.", "not_found")
        return {**automations.run_view(run), "automation": run["name"],
                "link": f"{self.base}/automations#run={run_id}"}, []

    def list_automation_tasks(self, conn, caller: Caller) -> tuple[dict, list[int]]:
        """Runs waiting for Claude (a routine was started for them): what to do, and the run_id to report."""
        self._automator_user(conn, caller)
        rows = conn.execute("SELECT r.id, r.started_at, a.name, a.prompt FROM automation_runs r JOIN automations a "
                            "ON a.id = r.automation_id WHERE a.owner_id = ? AND r.status = 'waiting' ORDER BY r.id",
                            (caller.user_id,)).fetchall()
        return {"tasks": [{"run_id": r["id"], "automation": r["name"], "started_at": r["started_at"] + " UTC",
                           "task": r["prompt"]} for r in rows]}, []

    def report_automation_run(self, conn, caller: Caller, run_id: int, text: str,
                              status: str = "done") -> tuple[dict, list[int]]:
        """Claude's answer for a waiting run: recorded and delivered where the automation sends it."""
        self._automator_user(conn, caller)
        run = automations.get_run(conn, run_id)
        if run is None or run["owner_id"] != caller.user_id:
            raise Refused("No such run.", "not_found")
        if run["status"] != "waiting":
            raise Refused(f"That run isn't waiting for a report (it's {run['status']}).", "conflict")
        if status not in ("done", "failed"):
            raise Refused("status is \"done\" or \"failed\".", "invalid")
        text = (text or "").strip()
        if not text or len(text) > 50_000:
            raise Refused("Report the result as Markdown (up to 50,000 characters).", "invalid")
        scheduler = automations_run.ACTIVE
        if scheduler is None:
            raise Refused("Automator isn't running on the server right now.", "unavailable")
        # Claim it first, so a second report can't deliver twice.
        claimed = conn.execute("UPDATE automation_runs SET status = 'running' WHERE id = ? AND status = 'waiting'",
                               (run_id,)).rowcount
        if not claimed:
            raise Refused("That run was just reported.", "conflict")
        if status == "failed":
            scheduler._finish(run_id, "failed", "", [], "claude routine", text[:2000])
            return {"run_id": run_id, "status": "failed"}, []
        try:
            out = asyncio.run_coroutine_threadsafe(scheduler.complete(run_id, text, [], "claude routine"),
                                                   scheduler.loop).result(timeout=120)
        except Exception as exc:
            scheduler._finish(run_id, "failed", text, [], "claude routine", f"Delivering it failed: {type(exc).__name__}.")
            raise Refused("The report was saved, but delivering it failed.", "unavailable") from None
        return {"run_id": run_id, **out, "link": f"{self.base}/automations#run={run_id}"}, []

    def diagnose(self, conn, caller: Caller, what: str, query: str | None = None, file_id: int | None = None,
                 chunk_id: int | None = None, tool: str | None = None, failed_only: bool = False,
                 since_minutes: int = 60, limit: int = 50, level: str = "INFO",
                 status: str | None = None) -> tuple[dict, list[int]]:
        """Raw internals for an admin's connection while Development diagnostics are on (diagnostics.py)."""
        if not diagnostics.enabled(conn):
            raise Refused("Development diagnostics are off: an admin names the group that may use them under "
                          "Access → Settings → Development.", "not_allowed")
        user = conn.execute("SELECT * FROM users WHERE id = ?", (caller.user_id,)).fetchone()
        if not diagnostics.allowed(conn, user, self.get_directory()):
            raise Refused("Only people in the development group may pull diagnostics (Access → Settings → "
                          "Development).", "not_allowed")
        what = (what or "").strip().lower()
        if what not in diagnostics.KINDS:
            raise Refused(f"what is one of {', '.join(diagnostics.KINDS)}.", "invalid")
        if what == "calls":
            return {"what": what, **diagnostics.calls(conn, tool=tool, failed_only=failed_only,
                                                       since_minutes=since_minutes, limit=limit)}, []
        if what == "search":
            if not query:
                raise Refused("search needs `query`: the question to trace.", "invalid")
            return {"what": what, **diagnostics.search_trace(conn, self.settings, self.get_embedder(), caller.user_id,
                                                              query, limit)}, []
        if what == "file":
            if file_id is None:
                raise Refused("file needs `file_id`.", "invalid")
            return {"what": what, **diagnostics.file_state(conn, self.settings, file_id)}, []
        if what == "chunk":
            if chunk_id is None:
                raise Refused("chunk needs `chunk_id` (a passage's chunk_id from search_documents).", "invalid")
            return {"what": what, **diagnostics.chunk_state(conn, chunk_id)}, []
        if what == "jobs":
            return {"what": what, **diagnostics.jobs(conn, status, limit)}, []
        if what == "log":
            return {"what": what, "records": diagnostics.recent_log(limit, level, tool)}, []
        return {"what": what, **diagnostics.config(conn, self.settings)}, []

    # ------------------------------------------------------------- mail
    def _mail(self, conn, caller: Caller) -> m365_mail.Context:
        """The person's mail for the connector (m365_mail): an admin's mail group must be set and hold
        them, and they need a usable linked Microsoft 365 account. Ids are opaque tokens (stateless)."""
        if not m365_mail.claude_enabled(conn):
            raise Refused("Mail and calendar are off for Claude: an admin names the group that gets them under "
                          "Access → Connectors → Mail and calendar for Claude.", "not_allowed")
        user = self._user(conn, caller)
        if not m365_mail.claude_allowed(conn, user, self.get_directory()):
            raise Refused("Only people in the mail group may reach their mail and calendar from Claude (Access → "
                          "Connectors → Mail and calendar for Claude).", "not_allowed")
        ctx = m365_mail.context(conn, user, self.get_directory(), opaque=True)
        if ctx is None:
            raise Refused("No Microsoft 365 account is linked: the person links one on Preferences first.",
                          "not_allowed")
        return ctx

    def _mail_run(self, conn, caller: Caller, name: str, args: dict) -> tuple[dict, list[int]]:
        """One mail tool as the person; a draft becomes a proposal (never a sent mail)."""
        ctx = self._mail(conn, caller)
        try:
            out = m365_mail.run(conn, self.settings, ctx, name, {k: v for k, v in args.items() if v is not None},
                                _mail_cite)
        except m365_mail.MailError as exc:
            raise Refused(str(exc), exc.code) from None
        except m365_mail.m365.M365Error as exc:
            raise Refused(str(exc), "unavailable") from None
        if "draft" in out:
            proposal = proposals.propose_mail(conn, caller, out["draft"], self.settings)
            return self._proposed(conn, caller, proposal, {},
                                  "Nothing is sent: the draft waits for a Mnemerynne admin (Access → Approvals). "
                                  "Don't say it's sent or saved.",
                                  "Nothing is sent. Give the person the approve link: they review the draft and save "
                                  "it to their Outlook Drafts themselves. Don't say it's sent or saved."), []
        for item in [out, *out.get("messages", []), *out.get("events", [])]:
            cite = item.pop("cite", None)
            if isinstance(cite, dict):
                item.update(cite)
        return out, []

    def search_mail(self, conn, caller: Caller, query: str, sender: str | None = None, since: str | None = None,
                    count: int = 10, account: str | None = None, mailbox: str | None = None) -> tuple[dict, list[int]]:
        return self._mail_run(conn, caller, "search_mail", {"query": query, "from": sender, "since": since,
                                                           "count": count, "account": account, "mailbox": mailbox})

    def recent_mail(self, conn, caller: Caller, folder: str = "inbox", count: int = 10, account: str | None = None,
                    mailbox: str | None = None) -> tuple[dict, list[int]]:
        return self._mail_run(conn, caller, "recent_mail", {"folder": folder, "count": count, "account": account,
                                                           "mailbox": mailbox})

    def read_mail(self, conn, caller: Caller, id: str) -> tuple[dict, list[int]]:
        return self._mail_run(conn, caller, "read_mail", {"id": id})

    def calendar(self, conn, caller: Caller, start: str, end: str, account: str | None = None) -> tuple[dict, list[int]]:
        return self._mail_run(conn, caller, "calendar", {"start": start, "end": end, "account": account})

    def draft_reply(self, conn, caller: Caller, id: str, body: str, reply_all: bool = False) -> tuple[dict, list[int]]:
        return self._mail_run(conn, caller, "draft_reply", {"id": id, "body": body, "reply_all": reply_all})

    def draft_mail(self, conn, caller: Caller, to: list[str], subject: str, body: str, cc: list[str] | None = None,
                   account: str | None = None) -> tuple[dict, list[int]]:
        return self._mail_run(conn, caller, "draft_mail", {"to": to, "cc": cc or [], "subject": subject, "body": body,
                                                          "account": account})

    def list_versions(self, conn, caller: Caller, file_id: int) -> tuple[dict, list[int]]:
        loaded = editing.load(conn, self.settings, caller.user_id, file_id, via=self.mode)
        return {"file_id": file_id, "file": loaded.row["display_name"], "current_version": loaded.version,
                "earlier_versions": editing.versions(conn, file_id),
                "note": "The person restores a version from Files → the file's info → Versions."}, [file_id]

    def _proposal_out(self, card: dict) -> dict:
        """A proposal as Claude sees it: absolute links, and `approve` only while it's the person's to apply."""
        out = {"proposal": card["id"], "proposal_id": card["id"], "kind": card["kind"], "what": card["file"],
               "where": card["where"], "summary": card["summary"], "changes": card["changes"],
               "status": card["status"], "approver": card["approver"], "requested_via": card["requested_via"],
               "created_at": card["created_at"] + " UTC"}
        if card.get("expires_at"):
            out["expires_at"] = card["expires_at"] + " UTC"
        for key in ("decided_by", "decision_note", "items", "counts"):
            if key in card:
                out[key] = card[key]
        if card["decided_at"]:
            out["decided_at"] = card["decided_at"] + " UTC"
        if card["status"] == "pending" and card["approver"] == "self":
            out["approve"] = f"{self.base}/proposals#p{card['id']}"
        elif card["status"] == "applied" and card.get("url"):
            url = card["url"]
            if url.startswith("/apis/calls/") and url[12:].isdigit():
                url = self._call_link(int(url[12:]))
            out["link"] = self.base + url if url.startswith("/") else url
            if card["kind"] == "api" and card.get("result"):
                out["result"] = card["result"]
        return out

    def list_proposals(self, conn, caller: Caller, status: str = "all", since_days: int = 7,
                       limit: int = 20, since: str | None = None) -> tuple[dict, list[int]]:
        """The person's own proposals (proposals.for_person), so Claude can tell what was applied, withdrawn,
        rejected by an admin or is still waiting."""
        if status not in ("pending", "decided", "all"):
            raise Refused("status is pending, decided or all.", "invalid")
        if since is not None and not re.fullmatch(r"\d{4}-\d{2}-\d{2}( \d{2}:\d{2}(:\d{2})?)?", since.strip()):
            raise Refused("since is a UTC time like 2026-10-24 or 2026-10-24 14:30.", "invalid")
        cards = proposals.for_person(conn, caller.user_id, status, max(1, min(int(since_days), 90)),
                                     max(1, min(int(limit), 50)), since=since.strip() if since else None)
        found = [self._proposal_out(c) for c in cards]
        return {"proposals": found, **({} if found else {"note": "No proposals in that period."})}, []

    def get_proposal(self, conn, caller: Caller, proposal: int) -> tuple[dict, list[int]]:
        """One of the person's proposals by the id a propose/request tool returned."""
        try:
            row = proposals.get(conn, proposal, caller.user_id)
        except editing.EditNotFound:
            raise Refused(f"No proposal {proposal} of this person's. list_proposals shows theirs.", "not_found") \
                from None
        out = self._proposal_out(proposals.view(conn, row))
        notes = {"pending": "Still waiting for the person to apply it (approve link)." if row["approver"] == "self"
                 else "Still waiting for a Mnemerynne admin.",
                 "applied": "Done: it was applied.",
                 "dismissed": "The person dismissed it (or it was withdrawn): nothing changed."
                              + (" Their note says why." if row["decision_note"] else ""),
                 "stale": "The file changed before it was applied, so nothing changed: redo it on the new version.",
                 "rejected": "An admin turned it down: nothing changed.",
                 "expired": "Nobody decided it before it expired: nothing changed. Propose it again if it's still "
                            "wanted."}
        if row["kind"] == "batch" and row["status"] == "applied":
            counts = out.get("counts") or {}
            return {**out, "note": f"Decided: {counts.get('applied', 0)} applied, {counts.get('declined', 0)} declined, "
                                   f"{counts.get('failed', 0)} failed; `items` says which."}, []
        return {**out, "note": notes[row["status"]]}, []

    def withdraw_proposal(self, conn, caller: Caller, proposal: int) -> tuple[dict, list[int]]:
        """Take back one of the person's own pending proposals (the only change to a proposal's status a tool
        can make: never approve, apply or reject)."""
        try:
            row = proposals.get(conn, proposal, caller.user_id)
        except editing.EditNotFound:
            raise Refused(f"No proposal {proposal} of this person's. list_proposals shows theirs.", "not_found") \
                from None
        if row["status"] != "pending":
            raise Refused(f"Proposal {proposal} was already {row['status']}; it can't be withdrawn now.", "conflict")
        card = proposals.dismiss(conn, self.settings, caller.user_id, proposal, note="withdrawn by Claude")
        return {**self._proposal_out(card), "note": "Withdrawn: nothing changed, and the approve link no longer works."}, []


# --------------------------------------------------------------- the server
ELICIT_SECONDS = 120          # how long a proposal tool waits for the person to answer in the chat
_clients_seen: set[tuple[str, str, str]] = set()


def _elicitation_modes(session) -> str:
    caps = getattr(session, "client_capabilities", None)
    el = getattr(caps, "elicitation", None) if caps else None
    if el is None:
        return "none"
    modes = [m for m in ("form", "url") if getattr(el, m, None) is not None]
    return ", ".join(modes) or "declared, no mode"


def note_client(session) -> None:
    """Log once per client (name, version) whether it supports elicitation: the 1.0.1 roadmap's first
    question, answered from real connections."""
    params = getattr(session, "client_params", None)
    info = getattr(params, "clientInfo", None) if params else None
    key = (getattr(info, "name", "?") or "?", getattr(info, "version", "?") or "?", _elicitation_modes(session))
    if key not in _clients_seen:
        _clients_seen.add(key)
        log.info("MCP client %s %s: elicitation %s; back-channel %s.", key[0], key[1], key[2],
                 "yes" if getattr(session, "can_send_request", False) else "no (stateless)")


async def offer_approval(ctx: Context, tools: "Tools", caller: Caller, out: dict) -> dict:
    """Phase C: when the client can show a page in the chat (URL elicitation) and the transport has a
    back-channel, ask it to open the approve page, wait for the person, then report the proposal's real
    state. Otherwise (this endpoint is stateless today) the answer carries the link as before. The
    decision itself always happens on the approve page, never through Claude."""
    session = getattr(ctx, "session", None)
    if session is None or not out.get("approve") or out.get("status") != "proposed":
        return out
    note_client(session)
    modes = _elicitation_modes(session)
    if "url" not in modes or not getattr(session, "can_send_request", False):
        return out
    try:
        with anyio.fail_after(ELICIT_SECONDS):
            answer = await session.elicit_url(message=out["summary"], url=out["approve"],
                                              elicitation_id=f"proposal-{out['proposal_id']}")
    except Exception as exc:   # no back-channel after all, a timeout, a client error: the link still works
        log.debug("Elicitation for proposal %s not answered: %s", out.get("proposal_id"), exc)
        return out

    def peek():
        conn = db.connect(tools.settings.db_path)
        try:
            return tools.get_proposal(conn, caller, out["proposal_id"])[0]
        finally:
            conn.close()

    current = await anyio.to_thread.run_sync(peek)
    merged = {**out, "elicited": answer.action}
    if current["status"] != "pending":
        merged.update({k: v for k, v in current.items() if k in ("status", "decided_by", "decision_note", "link",
                                                                  "result", "items", "counts", "decided_at")})
        merged.pop("approve", None)
        merged["note"] = current["note"]
    elif answer.action != "accept":
        merged["note"] = ("The person closed the approval without deciding; the approve link still works. "
                          "Don't say it's done.")
    return merged


def build_server(settings: Settings, get_embedder: Callable[[], Any],
                 get_directory: Callable[[], Any] = lambda: None) -> MCPServer:
    tools = Tools(settings, get_embedder, get_directory)
    server = MCPServer(name="mnemerynne", title="Mnemerynne", instructions=INSTRUCTIONS, version=MCP_VERSION)

    async def propose(ctx: Context, name: str, args: dict, fn) -> dict:
        """A proposing tool: the call, then the in-chat approval where that's possible."""
        out = await call(ctx, name, args, fn)
        return await offer_approval(ctx, tools, _caller_of(ctx), out)

    async def call(ctx: Context, name: str, args: dict, fn) -> dict:
        caller = _caller_of(ctx)
        started = time.monotonic()

        def work():
            conn = db.connect(settings.db_path)
            ok, file_ids, error = False, [], None
            try:
                try:
                    editing.agents_folder(conn, caller.user_id)   # every inbox has one; made on first use
                except (fs.FsError, OSError):
                    pass   # the share is away: the tools still work, the folder comes later
                try:
                    out, file_ids = fn(conn, caller, **args)
                except (ToolError, editing.EditError, fs.FsError) as exc:
                    error = f"[{error_code(exc)}] {exc}"
                    raise ToolError(error) from None
                except Exception as exc:   # a bug: logged with the call (diagnostics), then raised as before
                    error = f"{type(exc).__name__}: {exc}"
                    raise
                ok = True
                return claude_links.attach(conn, tools.base, caller, out)
            finally:
                conn.execute("INSERT INTO mcp_calls(user_id, token_id, grant_id, tool, args, file_ids, ok, ms, error) "
                             "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                             (caller.user_id, caller.token_id, caller.grant_id, name, json.dumps(logged_args(name, args)),
                              json.dumps(file_ids), int(ok), int((time.monotonic() - started) * 1000),
                              None if error is None else error[:2000]))
                conn.close()

        return await anyio.to_thread.run_sync(work)

    @server.tool(annotations=_READ_ONLY)
    async def search_documents(ctx: Context, query: str, library: int | str | None = None,
                               folder: int | str | None = None, limit: int = 8) -> dict:
        """Search the user's Mnemerynne documents (their File box and shared libraries) and return the best
        matching passages, each with a ready-to-use `citation` and `link`, and `editable` (whether you may
        change that file). Ask in natural language; hotel and system jargon works. Optionally narrow to one
        `library` (a name from list_libraries, "File box", or its id) and a `folder` in it (a path such as
        "Front Office/Night Audit", or a folder id). `limit` is 1-20.
        When `found` is false the documents don't cover it: rephrase once, then say so."""
        return await call(ctx, "search_documents",
                          {"query": query, "library": library, "folder": folder, "limit": limit},
                          tools.search_documents)

    @server.tool(annotations=_READ_ONLY)
    async def read_passage(ctx: Context, chunk_id: int, around: int = 1) -> dict:
        """Read a passage from search_documents together with up to `around` (0-3) passages before
        and after it in the same document, to check context before relying on it."""
        return await call(ctx, "read_passage", {"chunk_id": chunk_id, "around": around}, tools.read_passage)

    @server.tool(annotations=_READ_ONLY)
    async def list_apis(ctx: Context) -> dict:
        """List the internal systems (APIs) you may query live for this person, with their operations
        and parameters. Operations marked changes_data are proposed with propose_api_action, never run
        by you: the person applies them."""
        return await call(ctx, "list_apis", {}, tools.list_apis)

    @server.tool(annotations=ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True,
                                             open_world_hint=True))
    async def query_api(ctx: Context, api: str, operation: str, params: dict | None = None,
                        offset: int = 0, find: str | None = None) -> dict:
        """Call one operation of an internal API from list_apis, e.g. api="Atera",
        operation="Tickets", or operation="Ticket" with params {"ticketId": 4812}.
        Returns live data with a `citation` and `link` to cite. Long lists come in pages: `rows` says
        which rows you got; call again with `offset` = its `next_offset` for more. `find` keeps only
        the rows that mention a word or phrase in any field (e.g. find="Kennewick" on Tickets, to see
        whether a ticket is already open). It searches what the API returned, so to search everything,
        step through the API's own pages (its page param) too. APIs marked one_at_a_time take one
        call at a time, so call them one after another, not in parallel."""
        return await call(ctx, "query_api", {"api": api, "operation": operation, "params": params or {},
                                             "offset": offset, "find": find}, tools.query_api)

    @server.tool(annotations=_PROPOSES)
    async def propose_api_action(ctx: Context, api: str, operation: str, params: dict | None = None) -> dict:
        """Propose an operation from list_apis marked changes_data (e.g. close a ticket, resolve an alert),
        with its params. Nothing happens yet: you get a `summary`, an `approve` link to give the person,
        who checks it and applies it in Mnemerynne, and a `proposal_id` for get_proposal. Carry on with
        the task meanwhile. Never say the change is made."""
        return await propose(ctx, "propose_api_action", {"api": api, "operation": operation, "params": params or {}},
                             tools.propose_api_action)

    @server.tool(annotations=_PROPOSES)
    async def propose_batch(ctx: Context, items: list[dict]) -> dict:
        """Propose several changes_data operations (from list_apis) as ONE proposal with one approve link,
        e.g. ten ticket status changes: `items` is a list of {api, operation, params}. The person ticks
        which to run; get_proposal then reports each item's outcome. Every item is checked first, so a
        bad one refuses the whole batch. Never say the changes are made."""
        return await propose(ctx, "propose_batch", {"items": items}, tools.propose_batch)

    @server.tool(annotations=_READ_ONLY)
    async def list_libraries(ctx: Context) -> dict:
        """List what the user can search from Claude: their File box and each shared library, with file counts,
        where you may edit, and an `upload` link for the person to add files from their own computer."""
        return await call(ctx, "list_libraries", {}, tools.list_libraries)

    @server.tool(annotations=_READ_ONLY)
    async def browse_folder(ctx: Context, library: int | str, folder: int | str | None = None) -> dict:
        """List the folders and files in a library (a name, "File box", or its id), at its top level or inside
        `folder` (a path such as "Front Office/Night Audit", or a folder id). Every folder and file says whether
        it is `editable` for you: whether you may change that file, or files in that folder."""
        return await call(ctx, "browse_folder", {"library": library, "folder": folder}, tools.browse_folder)

    @server.tool(annotations=_READ_ONLY)
    async def find_files(ctx: Context, name: str) -> dict:
        """Find libraries, folders and files whose name contains `name`, across everything the user can
        search. Matching ignores case, spaces, hyphens and underscores ("IT Ops" finds "IT-OPS"). Folders come
        with their folder_id and files with theirs (and the folder_id they are in), plus `editable`."""
        return await call(ctx, "find_files", {"name": name}, tools.find_files)

    @server.tool(annotations=_READ_ONLY, structured_output=False)
    async def view_page(ctx: Context, file_id: int, page: int) -> list:
        """Look at one page of a PDF as an image: tables, figures, forms, screenshots and scanned
        pages that the text in search results can't show well. `file_id` and `page` come from
        search_documents results (their `file_id` and `page`) or from browse_folder/find_files."""
        return await call(ctx, "view_page", {"file_id": file_id, "page": page}, tools.view_page)

    @server.tool(annotations=_READ_ONLY)
    async def open_file(ctx: Context, file_id: int, start: int = 1, limit: int | None = None,
                        sheet: str | None = None, cells: str | None = None) -> dict:
        """Open a .txt, .md, .docx or .xlsx file to read it whole or edit it. Returns its `version`
        (pass it to the edit tool) and whether you may edit it here. Text: numbered lines from `start`
        (`limit` lines). Word: paragraphs (p1, p2, …) and tables (t1, cells t1.r2.c3) from block `start`.
        Excel: the sheets, and the cells of `sheet` (default the first) in `cells` (e.g. "A1:H50")."""
        return await call(ctx, "open_file", {"file_id": file_id, "start": start, "limit": limit, "sheet": sheet,
                                             "cells": cells}, tools.open_file)

    @server.tool(annotations=_EDITS)
    async def edit_text(ctx: Context, file_id: int, version: str, edits: list[dict] | None = None,
                        content: str | None = None) -> dict:
        """Change a .txt or .md file opened with open_file. `edits`: a list of {"old": exact text that
        appears once in the file, "new": its replacement}, applied in order. Or `content`: the whole new
        text (only when most of the file changes)."""
        return await call(ctx, "edit_text", {"file_id": file_id, "version": version, "edits": edits,
                                             "content": content}, tools.edit_text)

    @server.tool(annotations=_EDITS)
    async def edit_document(ctx: Context, file_id: int, version: str, operations: list[dict]) -> dict:
        """Change a Word (.docx) file opened with open_file; ids are those open_file showed. Operations:
        {"op": "replace_text", "paragraph": "p4", "old": "...", "new": "..."} (keeps formatting),
        {"op": "set_paragraph", "paragraph": "p4", "text": "...", "style"?: "Heading 2"},
        {"op": "insert_paragraph", "after" or "before": "p4" | "t1" | "end", "text": "...", "style"?: "..."},
        {"op": "delete_paragraph", "paragraph": "p4"}, {"op": "set_cell", "cell": "t1.r2.c3", "text": "..."},
        {"op": "add_table_row", "table": "t1", "cells": ["...", "..."], "after_row"?: 3}."""
        return await call(ctx, "edit_document", {"file_id": file_id, "version": version, "operations": operations},
                          tools.edit_document)

    @server.tool(annotations=_EDITS)
    async def edit_spreadsheet(ctx: Context, file_id: int, version: str, operations: list[dict]) -> dict:
        """Change an Excel (.xlsx) file opened with open_file. Operations:
        {"op": "set_cells", "sheet"?: "Rates", "cells": {"B4": 129.5, "C4": "=B4*1.1", "D4": "text"}},
        {"op": "append_rows", "sheet"?: "...", "rows": [["a", 1], ["b", 2]]},
        {"op": "insert_rows" | "delete_rows", "sheet"?: "...", "row": 5, "count"?: 2} (not in workbooks
        with formulas), {"op": "add_sheet", "name": "..."}, {"op": "rename_sheet", "sheet": "...", "name": "..."}.
        Send numbers as numbers. Workbooks with charts, pivot tables or pictures can't be edited here."""
        return await call(ctx, "edit_spreadsheet", {"file_id": file_id, "version": version,
                                                    "operations": operations}, tools.edit_spreadsheet)

    @server.tool(annotations=_EDITS)
    async def create_file(ctx: Context, name: str, content: str | None = None, paragraphs: list | None = None,
                          rows: list | None = None, sheet: str | None = None, library: int | str | None = None,
                          folder: int | str | None = None, data: str | None = None) -> dict:
        """Create a new file; the extension in `name` decides the kind. .txt/.md: `content`. .docx:
        `paragraphs`, a list of strings or {"text": "...", "style": "Heading 1"} (or `content`, paragraphs
        separated by blank lines). .xlsx: `rows`, a list of rows (lists of values), optional `sheet` name.
        Any other kind of file you made yourself (an image, a PDF, a zip, a .pptx, code): `data`, the file's
        bytes as base64 (a data: URL is fine), up to 20 MB; it's stored as is, like a browser upload.
        Goes in the person's Agents folder unless `library` (and `folder`, a path) name another place you may
        edit, or `folder` is a folder id. If the name is taken, it gets "(2)" added."""
        return await call(ctx, "create_file", {"name": name, "content": content, "paragraphs": paragraphs,
                                               "rows": rows, "sheet": sheet, "library": library, "folder": folder,
                                               "data": data},
                          tools.create_file)

    @server.tool(annotations=_EDITS)
    async def create_folder(ctx: Context, name: str, library: int | str | None = None,
                            folder: int | str | None = None) -> dict:
        """Create a folder called `name` in the person's Agents folder, or inside `folder` (a path in `library`,
        or a folder id) when they name another place you may edit (list_libraries shows where). Returns its
        `folder_id`, `library` and `folder` path to pass to create_file. If it already exists, it is returned
        unchanged."""
        return await call(ctx, "create_folder", {"name": name, "library": library, "folder": folder},
                          tools.create_folder)

    @server.tool(annotations=_EDITS)
    async def rename_item(ctx: Context, name: str, file_id: int | None = None, folder_id: int | None = None) -> dict:
        """Rename a file (give file_id; it keeps its extension) or a folder (folder_id), where you may edit.
        Ids come from browse_folder, find_files or list_changes."""
        return await call(ctx, "rename_item", {"name": name, "file_id": file_id, "folder_id": folder_id},
                          tools.rename_item)

    @server.tool(annotations=_EDITS)
    async def move_item(ctx: Context, file_id: int | None = None, folder_id: int | None = None,
                        to_library: int | str | None = None, to_folder: int | str | None = None,
                        library: int | str | None = None, folder: int | str | None = None) -> dict:
        """Move one file (file_id) or one folder (folder_id: the folder being moved) to `to_folder` of
        `to_library` (a path, or just a folder id as to_folder), or into the person's Agents folder when both
        are left out. Both places must be ones you may edit (list_libraries shows where). If the name is taken
        there, it gets " (2)" added. (`library` and `folder` are older names for to_library and to_folder.)"""
        return await call(ctx, "move_item", {"file_id": file_id, "folder_id": folder_id, "library": library,
                                             "folder": folder, "to_library": to_library, "to_folder": to_folder},
                          tools.move_item)

    @server.tool(annotations=_PROPOSES_ONCE)
    async def delete_item(ctx: Context, file_id: int | None = None, folder_id: int | None = None) -> dict:
        """Propose moving a file (file_id) or folder (folder_id) to the recycle bin. Nothing happens yet: you
        get a `summary` and an `approve` link to give the person, who checks it and applies it in Mnemerynne,
        and a `proposal_id` for get_proposal. Never say it's deleted."""
        return await propose(ctx, "delete_item", {"file_id": file_id, "folder_id": folder_id}, tools.delete_item)

    @server.tool(annotations=_READ_ONLY)
    async def list_changes(ctx: Context, library: int | str, folder: int | str | None = None,
                           since: str | None = None) -> dict:
        """What happened in a library (or a `folder` in it, a path or id, and below) since `since` (UTC, e.g.
        "2026-10-24" or "2026-10-24 14:30"; default the last 24 hours): files added, changed, renamed or moved,
        commented on, moved to the recycle bin or restored, and folders made, binned or restored, when and by
        whom ("Claude for rob" = Rob's Claude), newest first."""
        return await call(ctx, "list_changes", {"library": library, "folder": folder, "since": since},
                          tools.list_changes)

    @server.tool(annotations=_PROPOSES_ONCE)
    async def request_edit_access(ctx: Context, file_id: int | None = None, folder_id: int | None = None,
                                  library: int | str | None = None, folder: int | str | None = None) -> dict:
        """Ask for permission to edit where you aren't allowed (`editable` is false, or an edit was refused
        with [not_allowed]): a file, a folder, or a place (`library` and `folder`). When it's the person's to
        give, you get a `summary` and an `approve` link for them; nothing changes until they apply it
        (get_proposal says when). Otherwise `note` says who can allow it."""
        return await propose(ctx, "request_edit_access", {"file_id": file_id, "folder_id": folder_id,
                                                          "library": library, "folder": folder},
                             tools.request_edit_access)

    @server.tool(annotations=_READ_ONLY)
    async def list_comments(ctx: Context, file_id: int) -> dict:
        """The comments on a file, oldest first, with who wrote each and when; replies carry reply_to."""
        return await call(ctx, "list_comments", {"file_id": file_id}, tools.list_comments)

    @server.tool(annotations=_EDITS)
    async def add_comment(ctx: Context, file_id: int, text: str, reply_to: int | None = None) -> dict:
        """Comment on a file you can read (`reply_to`: a comment_id from list_comments to answer it). Use this to
        talk to people or other agents about a document instead of editing it."""
        return await call(ctx, "add_comment", {"file_id": file_id, "text": text, "reply_to": reply_to},
                          tools.add_comment)

    @server.tool(annotations=_READ_ONLY)
    async def list_messages(ctx: Context, unread_only: bool = True, limit: int = 20, mark_read: bool = False) -> dict:
        """The person's messages inside Mnemerynne: notes from colleagues (or their Claude), what Mnemerynne
        told them (a proposal an admin decided, a comment on their file, a change to their account) and
        the announcements waiting for them. `unread_only` false lists read ones too; `mark_read` marks the
        listed ones read."""
        return await call(ctx, "list_messages", {"unread_only": unread_only, "limit": limit, "mark_read": mark_read},
                          tools.list_messages)

    @server.tool(annotations=_EDITS)
    async def send_message(ctx: Context, to: str, text: str, link: str | None = None) -> dict:
        """Leave a short note for a colleague inside Mnemerynne (`to`: their Mnemerynne username; ask the
        person if unsure), optionally with a `link` to a page here (a file, a wiki page, a proposal). They
        see it under Messages, with "Claude for <name>" as the sender. It isn't email."""
        return await call(ctx, "send_message", {"to": to, "text": text, "link": link}, tools.send_message)

    @server.tool(annotations=_READ_ONLY)
    async def list_rooms(ctx: Context) -> dict:
        """The person's Rooms (the team chat inside Mnemerynne): channels such as #front-office and direct
        messages, with how many are unread and how many mention them."""
        return await call(ctx, "list_rooms", {}, tools.list_rooms)

    @server.tool(annotations=_READ_ONLY)
    async def read_room(ctx: Context, room: int | str, limit: int = 30, before: int | None = None,
                        thread: int | None = None) -> dict:
        """Read a room's latest messages, oldest first: `room` is a channel ("#front-office" or
        "front-office"), "@username" for the direct messages with them, or a room_id from list_rooms.
        `before` (a message_id) pages back; `thread` (a message_id) reads that thread's replies."""
        return await call(ctx, "read_room", {"room": room, "limit": limit, "before": before, "thread": thread},
                          tools.read_room)

    @server.tool(annotations=_EDITS)
    async def post_to_room(ctx: Context, room: int | str, text: str, thread: int | None = None) -> dict:
        """Write a message in a room (as read_room names it; "@username" opens a direct message), shown as
        "Claude for <name>". Markdown works; @name mentions someone. `thread` (a message_id) replies in that
        thread. Everyone in the room sees it: post only what the person asked you to."""
        return await call(ctx, "post_to_room", {"room": room, "text": text, "thread": thread}, tools.post_to_room)

    @server.tool(annotations=_EDITS)
    async def react_in_room(ctx: Context, message_id: int, emoji: str) -> dict:
        """Add an emoji reaction (e.g. "👍") to a room message, as the person."""
        return await call(ctx, "react_in_room", {"message_id": message_id, "emoji": emoji}, tools.react_in_room)

    @server.tool(annotations=_READ_ONLY)
    async def my_briefing(ctx: Context) -> dict:
        """What's waiting for the person inside Mnemerynne: unread notes, announcements, room mentions and
        unread direct messages, and proposals waiting for them to apply. (Mail and calendar: their own tools.)"""
        return await call(ctx, "my_briefing", {}, tools.my_briefing)

    @server.tool(annotations=_READ_ONLY)
    async def mnemerynne_updates(ctx: Context) -> dict:
        """(Admins) Mnemerynne updates waiting upstream and what they touch (migrations, services, packages),
        and known vulnerabilities in the installed Python packages (pip-audit, when installed)."""
        return await call(ctx, "mnemerynne_updates", {}, tools.mnemerynne_updates)

    @server.tool(annotations=_READ_ONLY)
    async def m365_security(ctx: Context, kind: str = "alerts", tenant: str | None = None) -> dict:
        """(Admins) Microsoft 365 security, per tenant (or one `tenant` by name): `kind` is "alerts" (open
        Defender alerts), "risky_users", "secure_score" or "sign_ins" (risky sign-ins in the last day)."""
        return await call(ctx, "m365_security", {"kind": kind, "tenant": tenant}, tools.m365_security)

    @server.tool(annotations=_READ_ONLY)
    async def security_feeds(ctx: Context, since_days: int = 7) -> dict:
        """New items from the security feeds admins set up (vendor advisories, CISA's known exploited
        vulnerabilities, NVD CVEs), kept to the products each feed watches, from the last `since_days` (1-90).
        Cite each item with its `link`."""
        return await call(ctx, "security_feeds", {"since_days": since_days}, tools.security_feeds)

    @server.tool(annotations=_READ_ONLY)
    async def list_automations(ctx: Context) -> dict:
        """(Alpha) The person's Automator automations: scheduled jobs such as a morning briefing or a security
        digest, with what each does, when it runs and how its last run went."""
        return await call(ctx, "list_automations", {}, tools.list_automations)

    @server.tool(annotations=_EDITS)
    async def run_automation(ctx: Context, automation: int | str) -> dict:
        """(Alpha) Start one of the person's automations now (its name or id from list_automations). Returns
        the run_id; get_automation_run reads the answer when it's done."""
        return await call(ctx, "run_automation", {"automation": automation}, tools.run_automation)

    @server.tool(annotations=_READ_ONLY)
    async def get_automation_run(ctx: Context, run_id: int) -> dict:
        """(Alpha) One automation run: its status, the answer (Markdown with [n] citations and `sources`),
        and where it was delivered."""
        return await call(ctx, "get_automation_run", {"run_id": run_id}, tools.get_automation_run)

    @server.tool(annotations=_READ_ONLY)
    async def list_automation_tasks(ctx: Context) -> dict:
        """(Alpha) Automation runs waiting for you to do them and report back (a Claude routine was started
        for each): the task and the run_id to pass to report_automation_run."""
        return await call(ctx, "list_automation_tasks", {}, tools.list_automation_tasks)

    @server.tool(annotations=_EDITS)
    async def report_automation_run(ctx: Context, run_id: int, text: str, status: str = "done") -> dict:
        """(Alpha) Report the result of an automation run you were started for: `text` is your report as
        Markdown (cite as usual; exactly NOTHING_TO_REPORT if there's nothing worth sending), `status`
        "done" or "failed" (then `text` says why). Mnemerynne delivers it where the automation sends it."""
        return await call(ctx, "report_automation_run", {"run_id": run_id, "text": text, "status": status},
                          tools.report_automation_run)

    @server.tool(annotations=_READ_ONLY)
    async def diagnose(ctx: Context, what: str, query: str | None = None, file_id: int | None = None,
                       chunk_id: int | None = None, tool: str | None = None, failed_only: bool = False,
                       since_minutes: int = 60, limit: int = 50, level: str = "INFO",
                       status: str | None = None) -> dict:
        """Development diagnostics (only for people in the development group an admin names under Access →
        Settings → Development; off while no group is set): Mnemerynne's raw internals, to find out why a tool
        call or a search went wrong. `what`: "calls" (the tool-call logs with each failure's error; `tool`, `failed_only`,
        `since_minutes`), "search" (what retrieval scored for `query` as this person: candidates, ranks,
        distances and the not-found gate), "file" (`file_id`: its indexing state, jobs and errors), "chunk"
        (`chunk_id`: one passage as indexed), "jobs" (the ingestion queue; `status`), "log" (the server's
        recent log lines; `level`, `tool` as a logger prefix) or "config" (effective settings, no secrets).
        Nothing here changes anything."""
        return await call(ctx, "diagnose", {"what": what, "query": query, "file_id": file_id, "chunk_id": chunk_id,
                                            "tool": tool, "failed_only": failed_only, "since_minutes": since_minutes,
                                            "limit": limit, "level": level, "status": status}, tools.diagnose)

    @server.tool(annotations=_READ_ONLY)
    async def search_mail(ctx: Context, query: str, sender: str | None = None, since: str | None = None,
                          count: int = 10, account: str | None = None, mailbox: str | None = None) -> dict:
        """Search the person's email for messages about something (only for people in the mail group an admin
        names under Access → Connectors → Mail and calendar for Claude, with a linked Microsoft 365 account;
        [not_allowed] otherwise). Each message comes with its `id` (for read_mail and draft_reply), sender,
        date, a preview, `citation` and `link`. `sender`: a name or address; `since`: YYYY-MM-DD; `count`:
        up to 20; `account`: which linked organisation when the person has several (a refusal names them);
        `mailbox`: the name of a shared mailbox the person may use, instead of their own."""
        return await call(ctx, "search_mail", {"query": query, "sender": sender, "since": since, "count": count,
                                               "account": account, "mailbox": mailbox}, tools.search_mail)

    @server.tool(annotations=_READ_ONLY)
    async def recent_mail(ctx: Context, folder: str = "inbox", count: int = 10, account: str | None = None,
                          mailbox: str | None = None) -> dict:
        """The newest messages in a folder of the person's email ("inbox", "sentitems", "drafts" or "archive"),
        same rules and shape as search_mail."""
        return await call(ctx, "recent_mail", {"folder": folder, "count": count, "account": account,
                                               "mailbox": mailbox}, tools.recent_mail)

    @server.tool(annotations=_READ_ONLY)
    async def read_mail(ctx: Context, id: str) -> dict:
        """Read one message in full by the `id` search_mail or recent_mail returned: subject, from, to, cc, date,
        its own text (quoted earlier mail and the signature cut), `citation` and `link`."""
        return await call(ctx, "read_mail", {"id": id}, tools.read_mail)

    @server.tool(annotations=_READ_ONLY)
    async def calendar(ctx: Context, start: str, end: str, account: str | None = None) -> dict:
        """The person's calendar events from `start` to `end` (YYYY-MM-DD, inclusive, at most two months): subject,
        times, location, organiser, attendees and a `link`. Same mail-group rule as search_mail."""
        return await call(ctx, "calendar", {"start": start, "end": end, "account": account}, tools.calendar)

    @server.tool(annotations=_PROPOSES)
    async def draft_reply(ctx: Context, id: str, body: str, reply_all: bool = False) -> dict:
        """Draft a reply to a message (its `id`) with `body` as the text. Nothing is sent: it is a proposal with
        an `approve` link, where the person reviews the draft and saves it to their Outlook Drafts. Never say it
        was sent or saved."""
        return await propose(ctx, "draft_reply", {"id": id, "body": body, "reply_all": reply_all}, tools.draft_reply)

    @server.tool(annotations=_PROPOSES)
    async def draft_mail(ctx: Context, to: list[str], subject: str, body: str, cc: list[str] | None = None,
                         account: str | None = None) -> dict:
        """Draft a new email (`to` and `cc` are addresses). Nothing is sent: it is a proposal with an `approve`
        link, where the person reviews it and saves it to their Outlook Drafts. Never say it was sent or saved."""
        return await propose(ctx, "draft_mail", {"to": to, "subject": subject, "body": body, "cc": cc,
                                                 "account": account}, tools.draft_mail)

    @server.tool(annotations=_READ_ONLY)
    async def list_versions(ctx: Context, file_id: int) -> dict:
        """Earlier versions of a file kept when it was changed through Claude, newest first, with what
        each change was. The person can restore any of them from Files."""
        return await call(ctx, "list_versions", {"file_id": file_id}, tools.list_versions)

    @server.tool(annotations=_READ_ONLY)
    async def list_proposals(ctx: Context, status: str = "all", since_days: int = 7, limit: int = 20,
                             since: str | None = None) -> dict:
        """The person's proposals (from propose_api_action, propose_batch, delete_item, request_edit_access),
        newest first: `status` "pending" (still waiting), "decided" (applied, dismissed, rejected, stale or
        expired within `since_days`, 1-90, or since a UTC time `since`) or "all". Each carries its `summary`,
        `expires_at`, who decides it (`approver`: the person or an admin) and, once decided, `decided_by`
        and any `decision_note`. Use it to report outcomes instead of guessing."""
        return await call(ctx, "list_proposals", {"status": status, "since_days": since_days, "limit": limit,
                                                  "since": since}, tools.list_proposals)

    @server.tool(annotations=_READ_ONLY)
    async def get_proposal(ctx: Context, proposal: int) -> dict:
        """One proposal by the `proposal_id` a propose or request tool returned: its status (pending,
        applied, dismissed, stale, rejected or expired), who decided it, why (`decision_note`), and for an
        applied API action its `result` and `link`. Only say a change is made once this says applied."""
        return await call(ctx, "get_proposal", {"proposal": proposal}, tools.get_proposal)

    @server.tool(annotations=_PROPOSES_ONCE)
    async def withdraw_proposal(ctx: Context, proposal: int) -> dict:
        """Take back a pending proposal of yours (its `proposal_id`) that turned out wrong or unneeded, so a
        stale approve link doesn't linger. Only pending ones you made for this person; nothing else about a
        proposal can be changed from here."""
        return await call(ctx, "withdraw_proposal", {"proposal": proposal}, tools.withdraw_proposal)

    return server


def _caller_of(ctx: Context) -> Caller:
    """The caller McpEndpoint verified for this HTTP request."""
    request = ctx.request_context.request
    caller = getattr(request, "scope", {}).get("mnemerynne.caller") if request is not None else None
    if caller is None:
        raise ToolError("Not authenticated.")
    return caller


def public_base(settings: Settings, headers: dict[str, str], scope: Scope | None = None) -> str:
    """This server's address as the outside world uses it: MNEMERYNNE_PUBLIC_URL,
    or the Host the request came in on (https when the proxy says so)."""
    if settings.public_url:
        return settings.public_url.rstrip("/")
    host = headers.get("x-forwarded-host") or headers.get("host") or "localhost"
    proto = headers.get("x-forwarded-proto") or (scope or {}).get("scheme") or "https"
    return f"{proto}://{host}"


class McpEndpoint:
    """ASGI endpoint for /mcp: checks the bearer token and the rate limit, then
    hands the request to the SDK's (stateless, JSON) session manager."""

    def __init__(self, settings: Settings, server: MCPServer):
        self.settings = settings
        # Building the app creates server.session_manager. Host/Origin checks are
        # off: the bearer token is the protection, and the Host differs behind nginx.
        server.streamable_http_app(
            stateless_http=True, json_response=True,
            transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False))
        self.manager = server.session_manager
        self.recent: dict[str, deque[float]] = defaultdict(deque)

    def _verify(self, token: str | None) -> Caller | None:
        """A personal token (the Desktop extension) or an OAuth access token (claude.ai, Claude Code)."""
        from mnemerynne import oauth

        conn = db.connect(self.settings.db_path)
        try:
            return api_tokens.verify(conn, token) or oauth.verify(conn, token)
        finally:
            conn.close()

    def _over_limit(self, key: str) -> bool:
        now, window = time.monotonic(), self.recent[key]
        while window and now - window[0] > 60:
            window.popleft()
        if len(window) >= self.settings.mcp_calls_per_minute:
            return True
        window.append(now)
        return False

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}
        match = re.fullmatch(r"Bearer\s+(\S+)", headers.get("authorization", ""), re.IGNORECASE)
        caller = await anyio.to_thread.run_sync(self._verify, match.group(1) if match else None)
        if caller is None:
            # Where OAuth clients (claude.ai, Claude Code) find out how to sign in (RFC 9728).
            metadata = public_base(self.settings, headers, scope) + "/.well-known/oauth-protected-resource"
            response = JSONResponse({"error": "invalid_token",
                                     "error_description": "Missing, expired or revoked Mnemerynne token."},
                                    status_code=401, headers={"WWW-Authenticate":
                                                              f'Bearer realm="mnemerynne", resource_metadata="{metadata}"'})
            return await response(scope, receive, send)
        key = f"t{caller.token_id}" if caller.token_id is not None else f"g{caller.grant_id}"
        if scope["method"] == "POST" and self._over_limit(key):
            response = JSONResponse({"error": "rate_limited",
                                     "error_description": "Too many requests; wait a minute."},
                                    status_code=429, headers={"Retry-After": "60"})
            return await response(scope, receive, send)
        scope["mnemerynne.caller"] = caller
        await self.manager.handle_request(scope, receive, _with_security_schemes(send))


# Every tool needs the person signed in. ChatGPT reads that per tool, from a `securitySchemes` key beside
# `name` and `inputSchema` (developers.openai.com/plugins/build/auth); the SDK's Tool model has no such
# field and drops unknown ones, so it is added to the tools/list reply here. Other clients ignore it.
SECURITY_SCHEMES = [{"type": "oauth2", "scopes": ["mcp"]}]


def _with_security_schemes(send: Send) -> Send:
    """Wrap `send` so a JSON tools/list reply gets securitySchemes on each tool. The endpoint is stateless and
    answers in plain JSON (one body), so the reply is held until it is complete; anything else passes as is."""
    held: dict = {}

    async def wrapped(message) -> None:
        if message["type"] == "http.response.start":
            headers = {k.decode().lower(): v.decode() for k, v in message.get("headers", [])}
            if headers.get("content-type", "").startswith("application/json"):
                held["start"], held["body"] = message, b""
                return
            await send(message)
            return
        if message["type"] == "http.response.body" and "start" in held:
            held["body"] += message.get("body", b"")
            if message.get("more_body"):
                return
            body = _add_security_schemes(held["body"])
            start = dict(held.pop("start"))
            start["headers"] = [(k, v) for k, v in start.get("headers", []) if k.lower() != b"content-length"] + \
                [(b"content-length", str(len(body)).encode())]
            await send(start)
            await send({"type": "http.response.body", "body": body})
            return
        await send(message)

    return wrapped


def _add_security_schemes(body: bytes) -> bytes:
    try:
        reply = json.loads(body)
    except ValueError:
        return body
    tools = reply.get("result", {}).get("tools") if isinstance(reply, dict) else None
    if not isinstance(tools, list):
        return body
    for tool in tools:
        if isinstance(tool, dict):
            tool.setdefault("securitySchemes", SECURITY_SCHEMES)
    return json.dumps(reply, ensure_ascii=False, separators=(",", ":")).encode()
