"""Erinne's MCP endpoint: lets Claude Desktop (through the Erinne extension)
and other MCP clients search people's documents.

Everything is read-only and acts as the token's user, limited to the
collections that user may search from Claude (permissions.claude_collection_ids):
an fs.Access built from that set makes every file helper respect the Claude
switches with no extra checks. Claude writes the answers; Erinne only finds
and returns passages with their citations. Every call is logged in mcp_calls.
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
import time
from collections import defaultdict, deque
from typing import Any, Callable

import anyio
from mcp.server import MCPServer
from mcp.server.mcpserver import Context
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from starlette.responses import JSONResponse
from starlette.types import Receive, Scope, Send

from erinne import api_tokens, db, fs, permissions, retrieval
from erinne.api_tokens import Caller
from erinne.config import Settings

log = logging.getLogger("erinne.mcp")

INSTRUCTIONS = """\
Erinne holds this organisation's documents: each person's private inbox and the shared libraries they
have been given (brand standards, system manuals and so on). Use these tools whenever a question could
be answered from those documents.

- Answer only from the passages the tools return, and cite every claim with the passage's `citation`
  and `link`, e.g. "Run End of Day before 2 am ([OPERA Cloud Manual · p. 412](link))".
- If search_documents returns found: false, try once more with different wording (jargon, the system's
  own terms). If it is still not found, say plainly that the documents don't cover it; don't fill the
  gap from general knowledge without saying so.
- Use read_passage to see the text around a hit before relying on a fragment.
- Use list_libraries, browse_folder and find_files to see what exists or to narrow a search."""

_READ_ONLY = ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True,
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


def _resolve_folder(conn: sqlite3.Connection, collection_id: int, ref: str | None) -> int | None:
    """A folder id from an id or a path like 'Front Office/Night Audit' (case-insensitive)."""
    ref = (ref or "").strip().strip("/")
    if not ref:
        return None
    live = fs.live_folder_ids(conn, collection_id)
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
    if kind in ("md", "txt"):
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
    return {"n": n, **out} if n is not None else out


# -------------------------------------------------------------------- tools
class Tools:
    """The tool bodies, synchronous and testable without the MCP transport."""

    def __init__(self, settings: Settings, get_embedder: Callable[[], Any]):
        self.settings = settings
        self.get_embedder = get_embedder

    @property
    def base(self) -> str:
        return self.settings.public_url.rstrip("/")

    def _access(self, conn: sqlite3.Connection, caller: Caller) -> fs.Access:
        return fs.Access(caller.user_id, permissions.claude_collection_ids(conn, caller.user_id), set())

    def search_documents(self, conn, caller: Caller, query: str, library: str | None = None,
                         folder: str | None = None, limit: int = 8) -> tuple[dict, list[int]]:
        query = (query or "").strip()
        if not query:
            raise ToolError("Give a question or some words to search for.")
        if len(query) > self.settings.max_question_chars:
            raise ToolError(f"Keep the search under {self.settings.max_question_chars} characters.")
        limit = max(1, min(int(limit), 20))
        access = self._access(conn, caller)
        allowed, folders = set(access.readable), None
        if library:
            row = _resolve_collection(conn, access, library)
            allowed = {row["id"]}
            folder_id = _resolve_folder(conn, row["id"], folder)
            folders = fs.scope_folders(conn, access, row["id"], folder_id)
        elif folder:
            raise ToolError("Name the library the folder is in, too.")
        if not allowed:
            return {"found": False, "query": query, "results": [],
                    "note": "Nothing is shared with Claude for you yet; ask an Erinne admin."}, []
        result = retrieval.search(conn, self.settings, self.get_embedder(), query, allowed, folders=folders)
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
        row = conn.execute("SELECT c.file_id, c.ord, c.collection_id FROM chunks c JOIN files f ON f.id = c.file_id "
                           "WHERE c.id = ? AND f.deleted_at IS NULL", (chunk_id,)).fetchone()
        if row is None or row["collection_id"] not in access.readable:
            raise ToolError("No such passage (or it isn't shared with Claude). Use a chunk_id from search_documents.")
        ids = [r[0] for r in conn.execute("SELECT id FROM chunks WHERE file_id = ? AND ord BETWEEN ? AND ? ORDER BY ord",
                                          (row["file_id"], row["ord"] - around, row["ord"] + around))]
        hits = retrieval._load_hits(conn, ids)
        library = _label(_collections(conn, access)[row["collection_id"]])
        return {"passages": [_passage(hits[i], self.base, library) for i in ids]}, [row["file_id"]]

    def list_libraries(self, conn, caller: Caller) -> tuple[dict, list[int]]:
        access = self._access(conn, caller)
        out = []
        for cid, row in sorted(_collections(conn, access).items(),
                               key=lambda kv: (kv[1]["kind"] != "private", _label(kv[1]).lower())):
            counts = conn.execute("SELECT count(*) AS files, coalesce(sum(status = 'ready'), 0) AS searchable "
                                  "FROM files WHERE collection_id = ? AND deleted_at IS NULL", (cid,)).fetchone()
            out.append({"library": _label(row), "id": cid, "kind": "inbox" if row["kind"] == "private" else "library",
                        "files": counts["files"], "searchable_files": counts["searchable"]})
        return {"libraries": out}, []

    def browse_folder(self, conn, caller: Caller, library: str, folder: str | None = None) -> tuple[dict, list[int]]:
        access = self._access(conn, caller)
        row = _resolve_collection(conn, access, library)
        folder_id = _resolve_folder(conn, row["id"], folder)
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


# --------------------------------------------------------------- the server
def build_server(settings: Settings, get_embedder: Callable[[], Any]) -> MCPServer:
    tools = Tools(settings, get_embedder)
    server = MCPServer(name="erinne", title="Erinne", instructions=INSTRUCTIONS)

    async def call(ctx: Context, name: str, args: dict, fn) -> dict:
        caller = _caller_of(ctx)
        started = time.monotonic()

        def work():
            conn = db.connect(settings.db_path)
            ok, file_ids = False, []
            try:
                out, file_ids = fn(conn, caller, **args)
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
        """Search the user's Erinne documents (their inbox and shared libraries) and return the best
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

    return server


def _caller_of(ctx: Context) -> Caller:
    """The caller McpEndpoint verified for this HTTP request."""
    request = ctx.request_context.request
    caller = getattr(request, "scope", {}).get("erinne.caller") if request is not None else None
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
                                     "error_description": "Missing, expired or revoked Erinne token."},
                                    status_code=401, headers={"WWW-Authenticate": 'Bearer realm="erinne"'})
            return await response(scope, receive, send)
        if scope["method"] == "POST" and self._over_limit(caller.token_id):
            response = JSONResponse({"error": "rate_limited",
                                     "error_description": "Too many requests; wait a minute."},
                                    status_code=429, headers={"Retry-After": "60"})
            return await response(scope, receive, send)
        scope["erinne.caller"] = caller
        await self.manager.handle_request(scope, receive, send)
