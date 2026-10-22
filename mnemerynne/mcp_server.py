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

from mnemerynne import api_tokens, apis, db, editing, editing_formats, fs, permissions, retrieval
from mnemerynne.api_tokens import Caller
from mnemerynne.config import Settings
from mnemerynne.ingest import store

log = logging.getLogger("mnemerynne.mcp")

PAGE_WIDTH_PX = 1200     # rendered page width: small print stays legible, JPEG stays ~100-300 KB
PAGE_MAX_PX = 2000       # cap on either side, for unusually long or wide pages

INSTRUCTIONS = """\
Mnemerynne holds this organisation's documents: each person's private inbox and the shared libraries they
have been given (brand standards, system manuals and so on). Use these tools whenever a question could
be answered from those documents.

- Answer only from the passages the tools return, and cite every claim with the passage's `citation`
  and `link`, e.g. "Run End of Day before 2 am ([OPERA Cloud Manual · p. 412](link))".
- If search_documents returns found: false, try once more with different wording (jargon, the system's
  own terms). If it is still not found, say plainly that the documents don't cover it; don't fill the
  gap from general knowledge without saying so.
- Use read_passage to see the text around a hit before relying on a fragment.
- Use view_page to look at a PDF page as an image when a passage mentions a table, figure, form or
  screenshot, when its text looks garbled (scanned pages), or when the person asks to see the page.
- Use list_libraries, browse_folder and find_files to see what exists or to narrow a search.
- The `link`s are for the person to open in their browser, where they are signed in to Mnemerynne.
  Don't fetch them yourself: they need that login. Use these tools to read documents instead.

Editing files (.txt, .md, .docx, .xlsx):
- Always open_file first and pass its `version` to the edit tool. If an edit is refused because the file
  changed, open it again and redo the change on the new version.
- Make the smallest change that does what was asked; don't rewrite text you weren't asked to change.
- New files go in the person's Agents folder unless they name somewhere else (list_libraries shows where
  you may edit). Every change keeps the previous version, which the person can restore from Files.
- You can't delete, move or rename files; tell the person to do that in Mnemerynne if it's needed.

Internal APIs (live, read-only):
- list_apis shows the internal systems you may query for this person (night-audit health, hotel stats,
  IT tickets and so on); query_api calls one. Cite answers with the result's `citation` and `link`, and
  say the figures are live as of its time. Don't guess values the response doesn't contain."""

_READ_ONLY = ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True,
                             open_world_hint=False)
# Changes files, but never destructively: every change keeps the previous version.
_EDITS = ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=False,
                         open_world_hint=False)
_INBOX_WORDS = {"inbox", "my inbox", "my files", "my documents"}


class Unauthorized(Exception):
    pass


# ------------------------------------------------------------------ helpers
def _collections(conn: sqlite3.Connection, access: fs.Access) -> dict[int, sqlite3.Row]:
    if not access.readable:
        return {}
    marks = ",".join("?" * len(access.readable))
    return {r["id"]: r for r in conn.execute(
        f"SELECT id, kind, name FROM collections WHERE id IN ({marks})", tuple(access.readable))}


def _label(row: sqlite3.Row) -> str:
    return "My inbox" if row["kind"] == "private" else row["name"]


def _resolve_collection(conn: sqlite3.Connection, access: fs.Access, ref: str) -> sqlite3.Row:
    ref = (ref or "").strip()
    collections = _collections(conn, access)
    if ref.isdigit() and int(ref) in collections:
        return collections[int(ref)]
    for row in collections.values():
        if (row["kind"] == "private" and ref.lower() in _INBOX_WORDS) or _label(row).lower() == ref.lower():
            return row
    raise ToolError(f"There is no library called “{ref}” that you can search from Claude. "
                    "Call list_libraries to see the names.")


def _resolve_folder(conn: sqlite3.Connection, access: fs.Access, collection_id: int, ref: str | None) -> int | None:
    """A folder id from an id or a path like 'Front Office/Night Audit' (case-insensitive),
    among the folders the user may see."""
    ref = (ref or "").strip().strip("/")
    if not ref:
        return None
    live = fs.live_folder_ids(conn, collection_id) & access.visible(collection_id)
    if ref.isdigit() and int(ref) in live:
        return int(ref)
    parent = None
    for part in [p.strip() for p in ref.split("/") if p.strip()]:
        row = conn.execute("SELECT id FROM folders WHERE collection_id = ? AND COALESCE(parent_id, 0) = ? "
                           "AND lower(name) = lower(?) AND deleted_at IS NULL",
                           (collection_id, parent or 0, part)).fetchone()
        if row is None or row["id"] not in live:
            raise ToolError(f"No folder “{ref}” in that library. Call browse_folder to see its folders.")
        parent = row["id"]
    return parent


def _path(conn: sqlite3.Connection, folder_id: int | None) -> str:
    return "/".join(p["name"] for p in fs.folder_path(conn, folder_id))


def _file_link(base: str, file_id: int, kind: str) -> str:
    if kind == "pdf":
        return f"{base}/files/{file_id}/pdf"
    if kind in ("md", "txt", "doc", "code"):
        return f"{base}/files/{file_id}/view"
    return f"{base}/files/{file_id}/raw"


def _passage(hit: retrieval.Hit, base: str, library: str, n: int | None = None) -> dict:
    # PDFs: file · page · section. Markdown's location is its last heading,
    # which the section path already ends with.
    page = hit.location if hit.kind == "pdf" else ""
    citation = " · ".join(p for p in (hit.file_name, page, hit.section_path) if p)
    out = {"citation": citation, "link": base + hit.url, "file": hit.file_name, "file_id": hit.file_id,
           "library": library, "location": hit.location, "section": hit.section_path,
           "chunk_id": hit.chunk_id, "text": hit.text}
    if hit.kind == "pdf" and hit.page_start:
        out["page"] = hit.page_start   # for view_page
    return {"n": n, **out} if n is not None else out


# -------------------------------------------------------------------- tools
class Tools:
    """The tool bodies, synchronous and testable without the MCP transport."""

    def __init__(self, settings: Settings, get_embedder: Callable[[], Any],
                 get_directory: Callable[[], Any] = lambda: None):
        self.settings = settings
        self.get_embedder = get_embedder
        self.get_directory = get_directory

    @property
    def base(self) -> str:
        return self.settings.public_url.rstrip("/")

    def _access(self, conn: sqlite3.Connection, caller: Caller) -> fs.Access:
        return fs.Access.for_claude(conn, caller.user_id)

    def search_documents(self, conn, caller: Caller, query: str, library: str | None = None,
                         folder: str | None = None, limit: int = 8) -> tuple[dict, list[int]]:
        query = (query or "").strip()
        if not query:
            raise ToolError("Give a question or some words to search for.")
        if len(query) > self.settings.max_question_chars:
            raise ToolError(f"Keep the search under {self.settings.max_question_chars} characters.")
        limit = max(1, min(int(limit), 20))
        access = self._access(conn, caller)
        allowed = retrieval.Scope.of_reach(access.reaches.values())
        if library:
            row = _resolve_collection(conn, access, library)
            folder_id = _resolve_folder(conn, access, row["id"], folder)
            allowed = allowed.narrow(row["id"], fs.scope_folders(conn, access, row["id"], folder_id))
        elif folder:
            raise ToolError("Name the library the folder is in, too.")
        if not allowed:
            return {"found": False, "query": query, "results": [],
                    "note": "Nothing is shared with Claude for you yet; ask a Mnemerynne admin."}, []
        result = retrieval.search(conn, self.settings, self.get_embedder(), query, allowed)
        if not result.found_anything:
            return {"found": False, "query": query, "results": [],
                    "note": "Nothing in these documents is close to this question."}, []
        names = {cid: _label(row) for cid, row in _collections(conn, access).items()}
        budget, picked = self.settings.mcp_max_tokens, []
        for hit in result.candidates:
            if len(picked) >= limit:
                break
            if hit.tokens > budget and picked:
                continue
            picked.append(hit)
            budget -= hit.tokens
        results = [_passage(h, self.base, names.get(h.collection_id, ""), n) for n, h in enumerate(picked, 1)]
        return {"found": True, "query": query, "results": results}, sorted({h.file_id for h in picked})

    def read_passage(self, conn, caller: Caller, chunk_id: int, around: int = 1) -> tuple[dict, list[int]]:
        around = max(0, min(int(around), 3))
        access = self._access(conn, caller)
        row = conn.execute("SELECT c.file_id, c.ord, c.collection_id, f.folder_id FROM chunks c "
                           "JOIN files f ON f.id = c.file_id WHERE c.id = ? AND f.deleted_at IS NULL",
                           (chunk_id,)).fetchone()
        # Chunks of a file in the bin (or under a binned folder) carry collection 0.
        if row is None or row["collection_id"] == fs.TRASH_COLLECTION \
                or not access.can_read(row["collection_id"], row["folder_id"]):
            raise ToolError("No such passage (or it isn't shared with Claude). Use a chunk_id from search_documents.")
        ids = [r[0] for r in conn.execute("SELECT id FROM chunks WHERE file_id = ? AND ord BETWEEN ? AND ? ORDER BY ord",
                                          (row["file_id"], row["ord"] - around, row["ord"] + around))]
        hits = retrieval._load_hits(conn, ids)
        library = _label(_collections(conn, access)[row["collection_id"]])
        return {"passages": [_passage(hits[i], self.base, library) for i in ids]}, [row["file_id"]]

    def _user(self, conn, caller: Caller):
        return conn.execute("SELECT id, ad_account FROM users WHERE id = ?", (caller.user_id,)).fetchone()

    def list_apis(self, conn, caller: Caller) -> tuple[dict, list[int]]:
        ops = apis.allowed_operations(conn, self._user(conn, caller), self.get_directory(), for_claude=True)
        out: dict[str, dict] = {}
        for op in ops:
            entry = out.setdefault(op["api_name"], {"api": op["api_name"], "operations": []})
            entry["operations"].append({
                "operation": op["name"], "description": op["description"],
                "parameters": [{k: v for k, v in s.items() if k in ("name", "type", "required", "options",
                                                                    "min", "max", "default", "help")}
                               for s in apis.param_specs(op)]})
        if not out:
            return {"apis": [], "note": "No internal APIs are shared with Claude for this person."}, []
        return {"apis": list(out.values())}, []

    def query_api(self, conn, caller: Caller, api: str, operation: str,
                  params: dict | None = None) -> tuple[dict, list[int]]:
        try:
            op = apis.operation_for(conn, self._user(conn, caller), self.get_directory(), api, operation,
                                    for_claude=True)
            result = apis.call(conn, self.settings, op["id"], params, user_id=caller.user_id, via="claude")
        except apis.ApiError as exc:
            raise ToolError(f"{exc} Use list_apis to see what you can query.") from None
        if not result.ok:
            raise ToolError(f"{result.api_name} didn't answer: {result.error}.")
        live = f"live, {result.at[11:16]} UTC"
        return {"api": result.api_name, "operation": result.operation_name, "at": result.at + " UTC",
                "citation": f"{result.api_name} · {result.operation_name} ({live})",
                "link": f"{self.base}/apis/calls/{result.call_id}",
                "data": apis.digest(result.data, 40_000)}, []

    def list_libraries(self, conn, caller: Caller) -> tuple[dict, list[int]]:
        access = self._access(conn, caller)
        out = []
        for cid, row in sorted(_collections(conn, access).items(),
                               key=lambda kv: (kv[1]["kind"] != "private", _label(kv[1]).lower())):
            counts = conn.execute("SELECT count(*) AS files, coalesce(sum(status = 'ready'), 0) AS searchable "
                                  "FROM files WHERE collection_id = ? AND deleted_at IS NULL", (cid,)).fetchone()
            out.append({"library": _label(row), "id": cid, "kind": "inbox" if row["kind"] == "private" else "library",
                        "files": counts["files"], "searchable_files": counts["searchable"]})
        edit = ([f"{p['library']}" + (f" / {p['folder']}" if p["folder"] else "") for p in
                 editing.editable_places(conn, caller.user_id)] if caller.can_edit else [])
        return {"libraries": out, "you_can_edit_in": edit,
                **({} if caller.can_edit else {"editing": "This connection can only search; the person can turn "
                                                          "on editing for it on the Claude page in Mnemerynne."})}, []

    def browse_folder(self, conn, caller: Caller, library: str, folder: str | None = None) -> tuple[dict, list[int]]:
        access = self._access(conn, caller)
        row = _resolve_collection(conn, access, library)
        folder_id = _resolve_folder(conn, access, row["id"], folder)
        listing = fs.browse(conn, access, row["id"], folder_id)
        return {
            "library": _label(row), "path": _path(conn, folder_id) or "/",
            "folders": [{"name": f["name"], "id": f["id"], "items": f["items"]} for f in listing["folders"]],
            "files": [{"name": f["display_name"], "file_id": f["id"], "type": f["kind"], "pages": f["unit_count"],
                       "searchable": f["status"] == "ready", "link": _file_link(self.base, f["id"], f["kind"])}
                      for f in listing["files"]],
        }, []

    def find_files(self, conn, caller: Caller, name: str) -> tuple[dict, list[int]]:
        access = self._access(conn, caller)
        found = fs.search_names(conn, access, name, limit=50)
        names = {cid: _label(row) for cid, row in _collections(conn, access).items()}
        return {
            "folders": [{"name": f["name"], "library": names[f["collection_id"]], "path": _path(conn, f["id"])}
                        for f in found["folders"]],
            "files": [{"name": f["display_name"], "file_id": f["id"], "library": names[f["collection_id"]],
                       "folder": _path(conn, f["folder_id"]) or "/", "type": f["kind"],
                       "searchable": f["status"] == "ready", "link": _file_link(self.base, f["id"], f["kind"])}
                      for f in found["files"]],
        }, []

    def view_page(self, conn, caller: Caller, file_id: int, page: int) -> tuple[list, list[int]]:
        """A PDF page as a JPEG, plus a line saying what it is."""
        access = self._access(conn, caller)
        row = fs.file_row(conn, file_id)
        if row is None or row["deleted_at"] is not None or not fs.is_live_folder(conn, row["folder_id"]) \
                or not access.can_read(row["collection_id"], row["folder_id"]):
            raise ToolError("No such file (or it isn't shared with Claude). Use a file_id from the other tools.")
        if row["kind"] != "pdf":
            raise ToolError(f"“{row['display_name']}” isn't a PDF; use read_passage for its text.")
        pages = row["unit_count"] or 0
        if not 1 <= page <= max(pages, 1):
            raise ToolError(f"“{row['display_name']}” has {pages} page(s); ask for 1-{pages}.")
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
        return ("My inbox" if coll["kind"] == "private" else coll["name"]) + (f" / {path}" if path else "")

    def open_file(self, conn, caller: Caller, file_id: int, start: int = 1, limit: int | None = None,
                  sheet: str | None = None, cells: str | None = None) -> tuple[dict, list[int]]:
        loaded = editing.load(conn, self.settings, caller.user_id, file_id)
        if loaded.fmt == "text":
            view = editing_formats.text_view(loaded.data, start, limit or 400)
        elif loaded.fmt == "docx":
            view = editing_formats.docx_view(loaded.data, start, limit or 200)
        else:
            view = editing_formats.xlsx_view(loaded.data, sheet, cells)
        try:
            editing.check_edit(conn, caller.user_id, caller.can_edit, loaded.row["collection_id"], loaded.row["folder_id"])
            can_edit, why = True, None
        except editing.EditError as exc:
            can_edit, why = False, str(exc)
        return {"file_id": file_id, "file": loaded.row["display_name"], "where": self._where(conn, loaded.row),
                "version": loaded.version, "can_edit": can_edit, **({"why_not": why} if why else {}),
                "link": _file_link(self.base, file_id, loaded.row["kind"]), **view}, [file_id]

    def _edited(self, conn, caller: Caller, file_id: int, version: str, fmt: str, apply) -> tuple[dict, list[int]]:
        loaded = editing.load(conn, self.settings, caller.user_id, file_id)
        if loaded.fmt != fmt:
            tool = {"text": "edit_text", "docx": "edit_document", "xlsx": "edit_spreadsheet"}[loaded.fmt]
            raise editing.EditError(f"“{loaded.row['display_name']}” is edited with {tool}.")
        editing.check_edit(conn, caller.user_id, caller.can_edit, loaded.row["collection_id"], loaded.row["folder_id"])
        if version != loaded.version:
            raise editing.EditConflict(f"“{loaded.row['display_name']}” changed since that version was opened. "
                                       f"Open it again and redo the change on the new version.")
        result = apply(loaded.data)
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
            raise editing.EditError("Name the file with .txt, .md, .docx or .xlsx at the end.")
        collection_id = folder_id = None
        if library:
            access = self._access(conn, caller)
            row = _resolve_collection(conn, access, library)
            collection_id, folder_id = row["id"], _resolve_folder(conn, access, row["id"], folder)
        elif folder:
            raise editing.EditError("Name the library the folder is in, too (or leave both out for the Agents folder).")
        file_id, version = editing.create(conn, self.settings, caller, name, data, collection_id, folder_id)
        row = conn.execute("SELECT * FROM files f JOIN blobs b ON b.sha256 = f.blob_sha256 WHERE f.id = ?",
                           (file_id,)).fetchone()
        return {"file_id": file_id, "file": row["display_name"], "where": self._where(conn, row), "version": version,
                "link": _file_link(self.base, file_id, row["kind"])}, [file_id]

    def list_versions(self, conn, caller: Caller, file_id: int) -> tuple[dict, list[int]]:
        loaded = editing.load(conn, self.settings, caller.user_id, file_id)
        return {"file_id": file_id, "file": loaded.row["display_name"], "current_version": loaded.version,
                "earlier_versions": editing.versions(conn, file_id),
                "note": "The person restores a version from Files → the file's info → Versions."}, [file_id]


# --------------------------------------------------------------- the server
def build_server(settings: Settings, get_embedder: Callable[[], Any],
                 get_directory: Callable[[], Any] = lambda: None) -> MCPServer:
    tools = Tools(settings, get_embedder, get_directory)
    server = MCPServer(name="mnemerynne", title="Mnemerynne", instructions=INSTRUCTIONS)

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
                except (editing.EditError, fs.FsError) as exc:
                    raise ToolError(str(exc)) from None
                ok = True
                return out
            finally:
                conn.execute("INSERT INTO mcp_calls(user_id, token_id, tool, args, file_ids, ok, ms) "
                             "VALUES (?, ?, ?, ?, ?, ?, ?)",
                             (caller.user_id, caller.token_id, name, json.dumps(args), json.dumps(file_ids),
                              int(ok), int((time.monotonic() - started) * 1000)))
                conn.close()

        return await anyio.to_thread.run_sync(work)

    @server.tool(annotations=_READ_ONLY)
    async def search_documents(ctx: Context, query: str, library: str | None = None, folder: str | None = None,
                               limit: int = 8) -> dict:
        """Search the user's Mnemerynne documents (their inbox and shared libraries) and return the best
        matching passages, each with a ready-to-use `citation` and `link`. Ask in natural language;
        hotel and system jargon works. Optionally narrow to one `library` (a name from list_libraries,
        or "inbox") and a `folder` path inside it such as "Front Office/Night Audit". `limit` is 1-20.
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
        and parameters. Read-only: nothing can be changed through them."""
        return await call(ctx, "list_apis", {}, tools.list_apis)

    @server.tool(annotations=ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True,
                                             open_world_hint=True))
    async def query_api(ctx: Context, api: str, operation: str, params: dict | None = None) -> dict:
        """Call one operation of an internal API from list_apis, e.g. api="Atera",
        operation="Tickets", or operation="Ticket" with params {"ticketId": 4812}.
        Returns live data with a `citation` and `link` to cite."""
        return await call(ctx, "query_api", {"api": api, "operation": operation, "params": params or {}},
                          tools.query_api)

    @server.tool(annotations=_READ_ONLY)
    async def list_libraries(ctx: Context) -> dict:
        """List what the user can search from Claude: their inbox and each shared library, with file counts."""
        return await call(ctx, "list_libraries", {}, tools.list_libraries)

    @server.tool(annotations=_READ_ONLY)
    async def browse_folder(ctx: Context, library: str, folder: str | None = None) -> dict:
        """List the folders and files in a library (or "inbox"), at its top level or inside `folder`
        (a path such as "Front Office/Night Audit")."""
        return await call(ctx, "browse_folder", {"library": library, "folder": folder}, tools.browse_folder)

    @server.tool(annotations=_READ_ONLY)
    async def find_files(ctx: Context, name: str) -> dict:
        """Find files and folders whose name contains `name`, across everything the user can search."""
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
                          rows: list | None = None, sheet: str | None = None, library: str | None = None,
                          folder: str | None = None) -> dict:
        """Create a new file; the extension in `name` decides the kind. .txt/.md: `content`. .docx:
        `paragraphs`, a list of strings or {"text": "...", "style": "Heading 1"} (or `content`, paragraphs
        separated by blank lines). .xlsx: `rows`, a list of rows (lists of values), optional `sheet` name.
        Goes in the person's Agents folder unless `library` (and `folder`) name another place you may edit.
        If the name is taken, it gets "(2)" added."""
        return await call(ctx, "create_file", {"name": name, "content": content, "paragraphs": paragraphs,
                                               "rows": rows, "sheet": sheet, "library": library, "folder": folder},
                          tools.create_file)

    @server.tool(annotations=_READ_ONLY)
    async def list_versions(ctx: Context, file_id: int) -> dict:
        """Earlier versions of a file kept when it was changed through Claude, newest first, with what
        each change was. The person can restore any of them from Files."""
        return await call(ctx, "list_versions", {"file_id": file_id}, tools.list_versions)

    return server


def _caller_of(ctx: Context) -> Caller:
    """The caller McpEndpoint verified for this HTTP request."""
    request = ctx.request_context.request
    caller = getattr(request, "scope", {}).get("mnemerynne.caller") if request is not None else None
    if caller is None:
        raise ToolError("Not authenticated.")
    return caller


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
        self.recent: dict[int, deque[float]] = defaultdict(deque)

    def _verify(self, token: str | None) -> Caller | None:
        conn = db.connect(self.settings.db_path)
        try:
            return api_tokens.verify(conn, token)
        finally:
            conn.close()

    def _over_limit(self, token_id: int) -> bool:
        now, window = time.monotonic(), self.recent[token_id]
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
            response = JSONResponse({"error": "invalid_token",
                                     "error_description": "Missing, expired or revoked Mnemerynne token."},
                                    status_code=401, headers={"WWW-Authenticate": 'Bearer realm="mnemerynne"'})
            return await response(scope, receive, send)
        if scope["method"] == "POST" and self._over_limit(caller.token_id):
            response = JSONResponse({"error": "rate_limited",
                                     "error_description": "Too many requests; wait a minute."},
                                    status_code=429, headers={"Retry-After": "60"})
            return await response(scope, receive, send)
        scope["mnemerynne.caller"] = caller
        await self.manager.handle_request(scope, receive, send)
