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

from mnemerynne import (accounts, activity, api_tokens, apis, claude_links, comments, db, editing, editing_formats,
                        fs, permissions, proposals, retrieval, sharepoint)
from mnemerynne.api_tokens import Caller
from mnemerynne.config import Settings
from mnemerynne.ingest import store

log = logging.getLogger("mnemerynne.mcp")

# The MCP server's own version, separate from the app's (pyproject): clients
# see it in initialize's serverInfo. Bump it when tools or their contracts change.
# 1.0.0: the "Road to 1.0" blockers (editable flags, request_edit_access,
# forgiving names, ids or paths, error codes), verified live 2026-10-25.
# 1.1.0: admin approvals (status awaiting_admin), list_proposals and get_proposal.
MCP_VERSION = "1.1.0"

PAGE_WIDTH_PX = 1200     # rendered page width: small print stays legible, JPEG stays ~100-300 KB
PAGE_MAX_PX = 2000       # cap on either side, for unusually long or wide pages

INSTRUCTIONS = """\
Mnemerynne holds this organisation's documents: each person's private File box (their own files, not
their email) and the shared libraries they have been given (brand standards, system manuals and so on).
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
- You can't save images (screenshots, photos) or other files you didn't write: give the person the
  `upload` link from list_libraries, which opens their Agents folder for them to add the file.

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
- propose_api_action, delete_item and request_edit_access return a `proposal` id. Most the person
  applies (the `approve` link); some wait for a Mnemerynne admin instead (status awaiting_admin): then
  say it's with the admins, and there is no link to give.
- get_proposal tells you what became of one (applied, dismissed, stale, or rejected with the admin's
  note); list_proposals shows the person's pending and recently decided ones. Check there before saying
  anything was done, and report rejections with their note."""

_READ_ONLY = ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True,
                             open_world_hint=False)
# Changes files, but never destructively: every change keeps the previous version.
_EDITS = ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=False,
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
                "link": f"{self.base}/apis/calls/{result.call_id}", "call_id": result.call_id,
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
            proposal = proposals.propose_api(conn, caller, op, params)
        except apis.ApiError as exc:
            raise Refused(f"{exc} Use list_apis to see the operations and their parameters.", _api_code(exc)) from None
        out = {"proposal": proposal, "api": op["api_name"], "operation": op["name"], "status": "proposed"}
        if op["admin_approval"]:
            return {**out, "status": "awaiting_admin",
                    "note": "Nothing has changed yet: this operation needs a Mnemerynne admin's approval, so it now "
                            "waits for one (Access → Approvals). Tell the person it's with the admins; get_proposal "
                            "shows the outcome. Don't say it's done."}, []
        if self.propose:
            return {**out, "note": _PROPOSED}, []
        return {**out, "approve": f"{self.base}/proposals#p{proposal}",
                "note": "Nothing has changed yet. Give the person the approve link: they check it and apply it "
                        "in Mnemerynne themselves. Don't say it's done."}, []

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
                    folder: str | None = None) -> tuple[dict, list[int]]:
        fmt = editing.format_of(name or "")
        if fmt == "text":
            data = editing_formats.text_new(content or "")
        elif fmt == "docx":
            data = editing_formats.docx_new(paragraphs if paragraphs is not None else
                                            (content or "").split("\n\n") if content else [])
        elif fmt == "xlsx":
            data = editing_formats.xlsx_new(rows or [], sheet)
        else:
            raise editing.EditError("Name the file with .txt, .md, .docx or .xlsx at the end. To save an image or "
                                    "another kind of file, give the person the upload link from list_libraries.")
        collection_id, folder_id = self._place(conn, caller, library, folder)
        if self.propose:
            proposal = proposals.propose_new(conn, self.settings, caller, name, data, collection_id, folder_id,
                                             [f"new file {name}"])
            row = conn.execute("SELECT name, collection_id, folder_id FROM agent_proposals WHERE id = ?",
                               (proposal,)).fetchone()
            return {"proposal": proposal, "file": row["name"],
                    "where": proposals.where(conn, row["collection_id"], row["folder_id"]),
                    "status": "proposed", "note": _PROPOSED}, []
        file_id, version = editing.create(conn, self.settings, caller, name, data, collection_id, folder_id)
        row = conn.execute("SELECT * FROM files f JOIN blobs b ON b.sha256 = f.blob_sha256 WHERE f.id = ?",
                           (file_id,)).fetchone()
        return {"file_id": file_id, "file": row["display_name"], "where": self._where(conn, row), "version": version,
                "link": _file_link(self.base, file_id, row["kind"])}, [file_id]

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
        proposal = proposals.propose_delete(conn, caller, item, via=self.mode)
        return {"proposal": proposal, "status": "proposed", "approve": f"{self.base}/proposals#p{proposal}",
                "note": "Nothing is deleted yet. Give the person the approve link: they check it and move it to the "
                        "recycle bin in Mnemerynne themselves. Don't say it's done."}, []

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
            return {"editable": False, "status": "needs_person", "link": f"{self.base}/settings#claude",
                    "note": "This connection can only search. The person turns on “Let Claude edit files” for it on "
                            "their Claude settings (link); then try again."}, []
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
        proposal = proposals.propose_access(conn, caller, target, name, cid, fid, changes, approver=approver)
        out = {"proposal": proposal, "status": "proposed", "editable": False, "where": where, "changes": changes}
        if approver == "admin":
            return {**out, "status": "awaiting_admin",
                    "note": f"Nothing has changed yet: only a Mnemerynne admin can let {who} edit in all of "
                            f"{_label(coll)}, so the request now waits for one (Access → Approvals). Tell the person "
                            f"it's with the admins; get_proposal shows the outcome. Meanwhile work in their Agents "
                            f"folder. Don't say it's done."}, []
        if self.propose:
            return {**out, "note": _PROPOSED}, []
        return {**out, "approve": f"{self.base}/proposals#p{proposal}",
                "note": f"Nothing has changed yet. Give the person the approve link: once they allow it there, "
                        f"{who} can edit here. Don't say it's done."}, []

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

    def list_versions(self, conn, caller: Caller, file_id: int) -> tuple[dict, list[int]]:
        loaded = editing.load(conn, self.settings, caller.user_id, file_id, via=self.mode)
        return {"file_id": file_id, "file": loaded.row["display_name"], "current_version": loaded.version,
                "earlier_versions": editing.versions(conn, file_id),
                "note": "The person restores a version from Files → the file's info → Versions."}, [file_id]

    def _proposal_out(self, card: dict) -> dict:
        """A proposal as Claude sees it: absolute links, and `approve` only while it's the person's to apply."""
        out = {"proposal": card["id"], "kind": card["kind"], "what": card["file"], "where": card["where"],
               "changes": card["changes"], "status": card["status"], "approver": card["approver"],
               "created_at": card["created_at"] + " UTC"}
        for key in ("decided_by", "decision_note"):
            if key in card:
                out[key] = card[key]
        if card["decided_at"]:
            out["decided_at"] = card["decided_at"] + " UTC"
        if card["status"] == "pending" and card["approver"] == "self":
            out["approve"] = f"{self.base}/proposals#p{card['id']}"
        elif card["status"] == "applied" and card.get("url"):
            out["link"] = self.base + card["url"] if card["url"].startswith("/") else card["url"]
        return out

    def list_proposals(self, conn, caller: Caller, status: str = "all", since_days: int = 7,
                       limit: int = 20) -> tuple[dict, list[int]]:
        """The person's own proposals (proposals.for_person), so Claude can tell what was applied, withdrawn,
        rejected by an admin or is still waiting."""
        if status not in ("pending", "decided", "all"):
            raise Refused("status is pending, decided or all.", "invalid")
        cards = proposals.for_person(conn, caller.user_id, status, max(1, min(int(since_days), 90)),
                                     max(1, min(int(limit), 50)))
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
                 "applied": "Done: it was applied.", "dismissed": "Withdrawn (or it expired): nothing changed.",
                 "stale": "The file changed before it was applied, so nothing changed: redo it on the new version.",
                 "rejected": "An admin turned it down: nothing changed."}
        return {**out, "note": notes[row["status"]]}, []


# --------------------------------------------------------------- the server
def build_server(settings: Settings, get_embedder: Callable[[], Any],
                 get_directory: Callable[[], Any] = lambda: None) -> MCPServer:
    tools = Tools(settings, get_embedder, get_directory)
    server = MCPServer(name="mnemerynne", title="Mnemerynne", instructions=INSTRUCTIONS, version=MCP_VERSION)

    async def call(ctx: Context, name: str, args: dict, fn) -> dict:
        caller = _caller_of(ctx)
        started = time.monotonic()

        def work():
            conn = db.connect(settings.db_path)
            ok, file_ids = False, []
            try:
                try:
                    editing.agents_folder(conn, caller.user_id)   # every inbox has one; made on first use
                except (fs.FsError, OSError):
                    pass   # the share is away: the tools still work, the folder comes later
                try:
                    out, file_ids = fn(conn, caller, **args)
                except (ToolError, editing.EditError, fs.FsError) as exc:
                    raise ToolError(f"[{error_code(exc)}] {exc}") from None
                ok = True
                return claude_links.attach(conn, tools.base, caller, out)
            finally:
                conn.execute("INSERT INTO mcp_calls(user_id, token_id, grant_id, tool, args, file_ids, ok, ms) "
                             "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                             (caller.user_id, caller.token_id, caller.grant_id, name, json.dumps(args),
                              json.dumps(file_ids), int(ok), int((time.monotonic() - started) * 1000)))
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

    @server.tool(annotations=_EDITS)
    async def propose_api_action(ctx: Context, api: str, operation: str, params: dict | None = None) -> dict:
        """Propose an operation from list_apis marked changes_data (e.g. close a ticket, resolve an alert),
        with its params. Nothing happens yet: you get an `approve` link to give the person, who checks
        it and applies it in Mnemerynne. Never say the change is made."""
        return await call(ctx, "propose_api_action", {"api": api, "operation": operation, "params": params or {}},
                          tools.propose_api_action)

    @server.tool(annotations=_READ_ONLY)
    async def list_libraries(ctx: Context) -> dict:
        """List what the user can search from Claude: their File box and each shared library, with file counts,
        where you may edit, and an `upload` link for the person to add files you can't write (images)."""
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
                          folder: int | str | None = None) -> dict:
        """Create a new file; the extension in `name` decides the kind. .txt/.md: `content`. .docx:
        `paragraphs`, a list of strings or {"text": "...", "style": "Heading 1"} (or `content`, paragraphs
        separated by blank lines). .xlsx: `rows`, a list of rows (lists of values), optional `sheet` name.
        Goes in the person's Agents folder unless `library` (and `folder`, a path) name another place you may
        edit, or `folder` is a folder id. If the name is taken, it gets "(2)" added."""
        return await call(ctx, "create_file", {"name": name, "content": content, "paragraphs": paragraphs,
                                               "rows": rows, "sheet": sheet, "library": library, "folder": folder},
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

    @server.tool(annotations=_EDITS)
    async def delete_item(ctx: Context, file_id: int | None = None, folder_id: int | None = None) -> dict:
        """Propose moving a file (file_id) or folder (folder_id) to the recycle bin. Nothing happens yet: you
        get an `approve` link to give the person, who checks it and applies it in Mnemerynne. Never say it's
        deleted."""
        return await call(ctx, "delete_item", {"file_id": file_id, "folder_id": folder_id}, tools.delete_item)

    @server.tool(annotations=_READ_ONLY)
    async def list_changes(ctx: Context, library: int | str, folder: int | str | None = None,
                           since: str | None = None) -> dict:
        """What happened in a library (or a `folder` in it, a path or id, and below) since `since` (UTC, e.g.
        "2026-10-24" or "2026-10-24 14:30"; default the last 24 hours): files added, changed, renamed or moved,
        commented on, moved to the recycle bin or restored, and folders made, binned or restored, when and by
        whom ("Claude for rob" = Rob's Claude), newest first."""
        return await call(ctx, "list_changes", {"library": library, "folder": folder, "since": since},
                          tools.list_changes)

    @server.tool(annotations=_EDITS)
    async def request_edit_access(ctx: Context, file_id: int | None = None, folder_id: int | None = None,
                                  library: int | str | None = None, folder: int | str | None = None) -> dict:
        """Ask for permission to edit where you aren't allowed (`editable` is false, or an edit was refused
        with [not_allowed]): a file, a folder, or a place (`library` and `folder`). When it's the person's to
        give, you get an `approve` link for them; nothing changes until they apply it. Otherwise `note` says
        who can allow it."""
        return await call(ctx, "request_edit_access", {"file_id": file_id, "folder_id": folder_id,
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
    async def list_versions(ctx: Context, file_id: int) -> dict:
        """Earlier versions of a file kept when it was changed through Claude, newest first, with what
        each change was. The person can restore any of them from Files."""
        return await call(ctx, "list_versions", {"file_id": file_id}, tools.list_versions)

    @server.tool(annotations=_READ_ONLY)
    async def list_proposals(ctx: Context, status: str = "all", since_days: int = 7, limit: int = 20) -> dict:
        """The person's proposals (from propose_api_action, delete_item, request_edit_access), newest first:
        `status` "pending" (still waiting), "decided" (applied, withdrawn, rejected or stale within
        `since_days`, 1-90) or "all". Each says who decides it (`approver`: the person or an admin) and,
        once decided, `decided_by` and any `decision_note`. Use it to report outcomes instead of guessing."""
        return await call(ctx, "list_proposals", {"status": status, "since_days": since_days, "limit": limit},
                          tools.list_proposals)

    @server.tool(annotations=_READ_ONLY)
    async def get_proposal(ctx: Context, proposal: int) -> dict:
        """One proposal by the `proposal` id a propose or request tool returned: its status (pending,
        applied, dismissed, stale or rejected), who decided it and why. Only say a change is made once
        this says applied."""
        return await call(ctx, "get_proposal", {"proposal": proposal}, tools.get_proposal)

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
        await self.manager.handle_request(scope, receive, send)
