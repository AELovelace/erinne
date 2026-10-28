"""The file manager API (folders, move/copy, recycle bin, uploads) and file viewing."""

from __future__ import annotations

import sqlite3
from html import escape
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile, status
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel, Field

from mnemerynne import api_tokens, comments, editing, fs, jobs, permissions, share
from mnemerynne.config import Settings
from mnemerynne.ingest import markdown, store
from mnemerynne.media import screens as media_screens
from mnemerynne.web.deps import api_user, get_db, get_settings, is_admin, page_user

router = APIRouter()

# The Markdown view renders user-supplied content: no scripts, no remote
# resources, no framing by other sites.
_VIEW_CSP = ("default-src 'none'; style-src 'self'; img-src 'self'; base-uri 'none'; "
             "form-action 'none'; frame-ancestors 'self'")
# Images that are safe to show inline. Never SVG: it can carry scripts.
_INLINE_IMAGES = {"image/png", "image/jpeg", "image/gif", "image/webp"}
_TEXT_PREVIEW_BYTES = 64 * 1024


def _fs_errors(fn):
    """Run fn(), turning file-manager refusals into HTTP errors."""
    try:
        return fn()
    except fs.FsError as exc:
        raise HTTPException(exc.status, str(exc)) from exc
    except share.ShareError as exc:   # e.g. the share isn't mounted
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc


def _preview(row: sqlite3.Row) -> str | None:
    if row["kind"] == "pdf":
        return "pdf"
    if row["kind"] in ("md", "doc", "code"):
        return "md"   # the /view page: rendered Markdown, or the text read from the file
    if row["kind"] == "txt" or (row["mime"] or "") == "text/plain":
        return "text"
    if row["mime"] in _INLINE_IMAGES:
        return "image"
    return None


def _file_json(row: sqlite3.Row, access: fs.Access) -> dict:
    preview = _preview(row)
    view = {"pdf": f"/files/{row['id']}/pdf", "md": f"/files/{row['id']}/view",
            "image": f"/files/{row['id']}/inline", "text": f"/files/{row['id']}/inline"}.get(preview)
    return {
        "type": "file",
        "id": row["id"],
        "collection_id": row["collection_id"],
        "folder_id": row["folder_id"],
        "name": row["display_name"],
        "kind": row["kind"],
        "mime": row["mime"],
        "size": row["size"],
        "pages": row["unit_count"] if row["kind"] == "pdf" else None,
        "status": row["status"],
        "progress": row["progress"],
        "error": row["error"],
        "notes": row["notes"],
        "uploaded_by": row["uploaded_by_name"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "preview": preview,
        "view_url": view,
        "download_url": f"/files/{row['id']}/raw",
        "can_delete": access.can_write(row["collection_id"], row["folder_id"]),
        # Text and Markdown files the person may change open in the browser editor (web/editor.py).
        "edit_url": (f"/edit/{row['id']}" if editing.format_of(row["display_name"]) == "text"
                     and row["deleted_at"] is None and access.can_write(row["collection_id"], row["folder_id"])
                     else None),
        # "claude:alice" / "erynne:alice": last changed by Claude, or by Erynne's
        # proposal that alice applied (Versions can undo it)
        "edited_via": row["edited_via"] if "edited_via" in row.keys() else None,
        "web_url": row["web_url"] if "web_url" in row.keys() else None,   # SharePoint files: "Open in SharePoint"
    }


def _folder_json(row: sqlite3.Row) -> dict:
    out = {"type": "folder", "id": row["id"], "name": row["name"]}
    for key in ("collection_id", "parent_id", "created_at", "items", "role"):
        if key in row.keys():
            out[key] = row[key]
    return out


def _readable_file(conn: sqlite3.Connection, access: fs.Access, file_id: int) -> sqlite3.Row:
    row = fs.file_row(conn, file_id)
    # Same 404 whether the file doesn't exist or isn't yours: no existence leaks.
    if row is None or not access.can_read_file(row):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "File not found")
    return row


def _writable_file(conn: sqlite3.Connection, access: fs.Access, file_id: int) -> sqlite3.Row:
    row = _readable_file(conn, access, file_id)
    if not access.can_write(row["collection_id"], row["folder_id"]):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You can't change files here")
    return row


# ------------------------------------------------------------------ browsing
@router.get("/api/locations")
def locations(user=Depends(api_user), conn: sqlite3.Connection = Depends(get_db)):
    """The inbox and libraries the user can open, for the tree's top level.
    `writable`: may change the top level (folders carry their own `can_write`);
    `editable`: may change something somewhere in it (so it has a bin to show)."""
    try:
        editing.agents_folder(conn, user["id"])   # every inbox has one; made on first use
    except (fs.FsError, OSError):
        pass   # the share is away; it's made next time
    access = fs.Access.of(conn, user["id"])
    out = []
    for c in permissions.readable_collections(conn, user["id"]):
        editable = c["id"] in access.writable
        out.append({"id": c["id"], "kind": c["kind"],
                    "name": permissions.PRIVATE_LABEL if c["kind"] == "private" else c["name"],
                    "writable": access.can_write(c["id"], None), "editable": editable,
                    "trash": fs.trash_count(conn, c["id"]) if editable else None})
    return {"locations": out}


@router.get("/api/collections/{collection_id}/folders")
def folder_tree(collection_id: int, user=Depends(api_user), conn: sqlite3.Connection = Depends(get_db)):
    access = fs.Access.of(conn, user["id"])
    return {"folders": _fs_errors(lambda: fs.folder_tree(conn, access, collection_id))}


def _claude_json(on_and_allowed: tuple[bool, bool], writable: bool) -> dict:
    """The Claude checkbox: ticked when Claude sees it; changeable by people who can change the place, as
    long as admins share it with Claude and no folder above is hidden."""
    on, allowed = on_and_allowed
    return {"on": on, "can_change": allowed and writable, "shared": allowed}


@router.get("/api/browse")
def browse(request: Request, collection_id: int, folder_id: int | None = None, user=Depends(api_user),
           conn: sqlite3.Connection = Depends(get_db)):
    access = fs.Access.of(conn, user["id"])
    listing = _fs_errors(lambda: fs.browse(conn, access, collection_id, folder_id))
    folders = [_folder_json(r) for r in listing["folders"]]
    files = [_file_json(r, access) for r in listing["files"]]
    # The Claude column, for people who have Claude connected (Claude page).
    claude = api_tokens.has_active(conn, user["id"])
    agent = getattr(request.app.state, "agent", None)
    ai = claude or (agent is not None and agent.allowed(conn, user))
    reach = access.reaches.get(collection_id)
    if claude and reach is not None:
        for out, row in zip(folders, listing["folders"]):
            out["claude"] = _claude_json(reach.claude_state(row["id"]), access.can_write(collection_id, row["id"]))
        for out, row in zip(files, listing["files"]):
            out["claude"] = _claude_json(reach.claude_state(row["folder_id"], own=row["claude_hidden"]),
                                         out["can_delete"])
    if ai and reach is not None:
        flags = {r["id"]: r for r in conn.execute(
            f"SELECT id, ai_edit, ai_no_edit FROM folders WHERE id IN ({','.join('?' * len(folders)) or 'NULL'})",
            [f["id"] for f in folders])}
        for out in folders:
            f = flags[out["id"]]
            out["ai_edit"] = editing.edit_state(conn, user["id"], collection_id, out["id"], folder_id,
                                                f["ai_edit"], f["ai_no_edit"])
        for out, row in zip(files, listing["files"]):
            out["ai_edit"] = editing.edit_state(conn, user["id"], collection_id, row["folder_id"], row["folder_id"],
                                                row["ai_edit"], row["ai_no_edit"])
    return {"folders": folders, "files": files, "path": listing["path"], "can_write": listing["can_write"],
            "claude": claude, "ai": ai, "can_reembed": _can_reembed(request, conn, user, collection_id)}


def _can_reembed(request: Request, conn: sqlite3.Connection, user, collection_id: int) -> bool:
    """Re-embedding: people in their own inbox; admins anywhere."""
    row = conn.execute("SELECT kind, owner_user_id FROM collections WHERE id = ?", (collection_id,)).fetchone()
    if row is None:
        return False
    return (row["kind"] == "private" and row["owner_user_id"] == user["id"]) or is_admin(request, conn, user)


def _where(conn: sqlite3.Connection, collection_id: int, folder_id: int | None) -> str:
    coll = conn.execute("SELECT kind, name FROM collections WHERE id = ?", (collection_id,)).fetchone()
    parts = [permissions.PRIVATE_LABEL if coll and coll["kind"] == "private" else (coll["name"] if coll else "?")]
    return " / ".join(parts + [p["name"] for p in fs.folder_path(conn, folder_id)])


@router.get("/api/properties")
def properties(request: Request, type: Literal["file", "folder"], id: int, user=Depends(api_user),
               conn: sqlite3.Connection = Depends(get_db)):
    """Everything the Properties dialog shows: details, search (embedding) state, and Claude's access."""
    access = fs.Access.of(conn, user["id"])
    connected = api_tokens.has_active(conn, user["id"])
    if type == "file":
        row = _readable_file(conn, access, id)
        if row["deleted_at"] is not None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "File not found")
        cid, fid = row["collection_id"], row["folder_id"]
        reach = access.reaches[cid]
        chunks = conn.execute("SELECT count(*) FROM chunks WHERE file_id = ?", (id,)).fetchone()[0]
        vectors = conn.execute("SELECT count(*) FROM chunk_vec WHERE rowid IN (SELECT id FROM chunks WHERE file_id = ?)",
                               (id,)).fetchone()[0]
        return {**_file_json(row, access), "where": _where(conn, cid, fid),
                "search": {"status": row["status"], "progress": row["progress"], "error": row["error"],
                           "chunks": chunks, "embedded": vectors},
                "claude_connected": connected,
                "claude_see": _claude_json(reach.claude_state(fid, own=row["claude_hidden"]),
                                           access.can_write(cid, fid)),
                "ai_edit": editing.edit_state(conn, user["id"], cid, fid, fid, row["ai_edit"], row["ai_no_edit"]),
                "can_reembed": _can_reembed(request, conn, user, cid) and row["status"] not in fs.REEMBED_SKIP}
    folder = conn.execute("SELECT * FROM folders WHERE id = ? AND deleted_at IS NULL", (id,)).fetchone()
    if folder is None or not access.can_read(folder["collection_id"], id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Folder not found")
    cid = folder["collection_id"]
    live = fs.live_folder_ids(conn, cid)
    if id not in live:   # inside a folder in the bin
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Folder not found")
    inside = [f for f in fs.subtree(conn, id) if f in live]
    marks = ",".join("?" * len(inside))
    counts = {r["status"]: r["n"] for r in conn.execute(
        f"SELECT status, count(*) AS n FROM files WHERE folder_id IN ({marks}) AND deleted_at IS NULL GROUP BY status",
        inside)}
    chunks = conn.execute(f"SELECT count(*) FROM chunks c JOIN files f ON f.id = c.file_id "
                          f"WHERE f.folder_id IN ({marks}) AND f.deleted_at IS NULL", inside).fetchone()[0]
    return {**_folder_json(folder), "where": _where(conn, cid, folder["parent_id"]),
            "files": sum(counts.values()), "folders": len(inside) - 1,
            "search": {"by_status": counts, "chunks": chunks},
            "claude_connected": connected,
            "claude_see": _claude_json(access.reaches[cid].claude_state(id), access.can_write(cid, id)),
            "ai_edit": editing.edit_state(conn, user["id"], cid, id, folder["parent_id"], folder["ai_edit"],
                                          folder["ai_no_edit"]),
            "can_reembed": _can_reembed(request, conn, user, cid)}


class AiEdit(BaseModel):
    allowed: bool


@router.put("/api/files/{file_id}/ai-edit")
def set_file_ai_edit(file_id: int, body: AiEdit, user=Depends(api_user), conn: sqlite3.Connection = Depends(get_db)):
    access = fs.Access.of(conn, user["id"])
    _fs_errors(lambda: fs.set_ai_edit(conn, access, fs.Item("file", file_id), body.allowed))
    return {"id": file_id, "allowed": body.allowed}


@router.put("/api/folders/{folder_id}/ai-edit")
def set_folder_ai_edit(folder_id: int, body: AiEdit, user=Depends(api_user),
                       conn: sqlite3.Connection = Depends(get_db)):
    access = fs.Access.of(conn, user["id"])
    _fs_errors(lambda: fs.set_ai_edit(conn, access, fs.Item("folder", folder_id), body.allowed))
    return {"id": folder_id, "allowed": body.allowed}


class ItemRef(BaseModel):
    type: Literal["file", "folder"]
    id: int


class Reembed(BaseModel):
    collection_id: int
    items: list[ItemRef] = Field(default_factory=list, max_length=1000)   # none: the whole library


@router.post("/api/reembed")
def reembed(body: Reembed, request: Request, user=Depends(api_user), conn: sqlite3.Connection = Depends(get_db)):
    """Read and embed files again: the chosen files and folders, or a whole library. In your own inbox, or as
    an admin anywhere you can read."""
    access = fs.Access.of(conn, user["id"])
    if body.collection_id not in access.readable:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such library.")
    if not _can_reembed(request, conn, user, body.collection_id):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only admins can re-embed files outside your own inbox.")
    ids = fs.reembed_targets(conn, body.collection_id, [fs.Item(i.type, i.id) for i in body.items] or None)
    rows = [fs.file_row(conn, i) for i in ids]
    ids = [r["id"] for r in rows if r is not None and access.can_read(r["collection_id"], r["folder_id"])]
    return {"queued": fs.queue_reembed(conn, ids)}


class ClaudeVisible(BaseModel):
    visible: bool


@router.put("/api/files/{file_id}/claude")
def set_file_claude(file_id: int, body: ClaudeVisible, user=Depends(api_user),
                    conn: sqlite3.Connection = Depends(get_db)):
    """Show a file to Claude, or take it out of Claude's view (Erynne in chat still sees it)."""
    access = fs.Access.of(conn, user["id"])
    _fs_errors(lambda: fs.set_file_claude(conn, access, file_id, body.visible))
    return {"id": file_id, "visible": body.visible}


@router.put("/api/folders/{folder_id}/claude")
def set_folder_claude(folder_id: int, body: ClaudeVisible, user=Depends(api_user),
                      conn: sqlite3.Connection = Depends(get_db)):
    """Show a folder and what's in it to Claude, or take it out of Claude's view."""
    access = fs.Access.of(conn, user["id"])
    row = conn.execute("SELECT collection_id FROM folders WHERE id = ?", (folder_id,)).fetchone()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such folder.")
    _fs_errors(lambda: fs.set_folder_claude(conn, access, row["collection_id"], folder_id, body.visible))
    return {"id": folder_id, "visible": body.visible}


@router.get("/api/search-names")
def search_names(q: str = Query("", max_length=200), user=Depends(api_user),
                 conn: sqlite3.Connection = Depends(get_db)):
    access = fs.Access.of(conn, user["id"])
    found = fs.search_names(conn, access, q)
    files = []
    for r in found["files"]:
        item = _file_json(r, access)
        item["path"] = fs.folder_path(conn, r["folder_id"])
        files.append(item)
    folders = [{**_folder_json(r), "path": fs.folder_path(conn, r["parent_id"])} for r in found["folders"]]
    return {"folders": folders, "files": files}


# ------------------------------------------------------------------- changes
class NewFolder(BaseModel):
    collection_id: int
    parent_id: int | None = None
    name: str = Field(..., max_length=200)


class Rename(BaseModel):
    name: str = Field(..., max_length=200)


class ItemRef(BaseModel):
    type: Literal["file", "folder"]
    id: int


class Target(BaseModel):
    collection_id: int
    folder_id: int | None = None


class Batch(BaseModel):
    items: list[ItemRef] = Field(..., min_length=1, max_length=1000)
    target: Target | None = None


def _items(batch: Batch) -> list[fs.Item]:
    return [fs.Item(i.type, i.id) for i in batch.items]


def _batch_response(results: list[fs.Result]) -> dict:
    return {"results": [r.json() for r in results], "ok": all(r.ok for r in results)}


@router.post("/api/folders", status_code=status.HTTP_201_CREATED)
def new_folder(body: NewFolder, user=Depends(api_user), conn: sqlite3.Connection = Depends(get_db)):
    access = fs.Access.of(conn, user["id"])
    folder_id = _fs_errors(lambda: fs.create_folder(conn, access, body.collection_id, body.parent_id, body.name))
    return {"type": "folder", "id": folder_id, "name": fs.clean_name(body.name)}


@router.patch("/api/folders/{folder_id}")
def rename_folder(folder_id: int, body: Rename, user=Depends(api_user), conn: sqlite3.Connection = Depends(get_db)):
    access = fs.Access.of(conn, user["id"])
    return {"id": folder_id, "name": _fs_errors(lambda: fs.rename(conn, access, fs.Item("folder", folder_id), body.name))}


@router.patch("/api/files/{file_id}")
def rename_file(file_id: int, body: Rename, user=Depends(api_user), conn: sqlite3.Connection = Depends(get_db)):
    access = fs.Access.of(conn, user["id"])
    return {"id": file_id, "name": _fs_errors(lambda: fs.rename(conn, access, fs.Item("file", file_id), body.name))}


def _need_target(batch: Batch) -> Target:
    if batch.target is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Choose where to put them.")
    return batch.target


@router.post("/api/items/move")
def move_items(batch: Batch, user=Depends(api_user), conn: sqlite3.Connection = Depends(get_db)):
    target, access = _need_target(batch), fs.Access.of(conn, user["id"])
    return _batch_response(_fs_errors(lambda: fs.move(conn, access, _items(batch), target.collection_id,
                                                      target.folder_id)))


@router.post("/api/items/copy")
def copy_items(batch: Batch, user=Depends(api_user), conn: sqlite3.Connection = Depends(get_db)):
    target, access = _need_target(batch), fs.Access.of(conn, user["id"])
    return _batch_response(_fs_errors(lambda: fs.copy(conn, access, _items(batch), target.collection_id,
                                                      target.folder_id)))


@router.post("/api/items/trash")
def trash_items(batch: Batch, user=Depends(api_user), conn: sqlite3.Connection = Depends(get_db)):
    return _batch_response(fs.trash(conn, fs.Access.of(conn, user["id"]), _items(batch)))


@router.post("/api/items/restore")
def restore_items(batch: Batch, user=Depends(api_user), conn: sqlite3.Connection = Depends(get_db)):
    return _batch_response(fs.restore(conn, fs.Access.of(conn, user["id"]), _items(batch)))


@router.post("/api/items/purge")
def purge_items(batch: Batch, user=Depends(api_user), conn: sqlite3.Connection = Depends(get_db),
                settings: Settings = Depends(get_settings)):
    return _batch_response(fs.purge(conn, settings, fs.Access.of(conn, user["id"]), _items(batch)))


@router.get("/api/trash")
def trash_list(collection_id: int, user=Depends(api_user), conn: sqlite3.Connection = Depends(get_db),
               settings: Settings = Depends(get_settings)):
    access = fs.Access.of(conn, user["id"])
    listing = _fs_errors(lambda: fs.list_trash(conn, access, collection_id))
    return {**listing, "keep_days": settings.trash_days}


# ------------------------------------------------------------------- uploads
@router.post("/api/files")
def upload(
    file: UploadFile = File(...),
    collection_id: int | None = Form(None),
    folder_id: int | None = Form(None),
    relative_path: str | None = Form(None, max_length=1024),
    user=Depends(api_user),
    conn: sqlite3.Connection = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    """Upload one file into a collection (default: your inbox) and folder.

    `relative_path` comes from a folder upload ("Reports/2026/q1.pdf"): the
    folders in it are created under `folder_id` as needed.
    """
    access = fs.Access.of(conn, user["id"])
    target = collection_id if collection_id is not None else permissions.private_collection_id(conn, user["id"])
    if target not in access.writable:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You can't upload to this collection")
    _fs_errors(lambda: fs.require_folder(conn, target, folder_id))
    if not access.can_write(target, folder_id):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You can't upload to this folder")
    try:
        received = store.receive(file.file, file.filename or "", settings)
    except store.UploadError as exc:
        code = status.HTTP_413_CONTENT_TOO_LARGE if isinstance(exc, store.TooLarge) else status.HTTP_400_BAD_REQUEST
        raise HTTPException(code, str(exc)) from exc
    parts = fs.split_relative_path(relative_path) if relative_path else None
    file_id, created = _fs_errors(lambda: store.add_file(conn, settings, received, target, user["id"],
                                                         folder_id=folder_id, folder_parts=parts))
    return JSONResponse(_file_json(fs.file_row(conn, file_id), access),
                        status_code=status.HTTP_201_CREATED if created else status.HTTP_200_OK)


@router.get("/api/files")
def list_files(
    collection_id: int | None = Query(None),
    user=Depends(api_user),
    conn: sqlite3.Connection = Depends(get_db),
):
    """Every live file the user can read (flat), newest first."""
    access = fs.Access.of(conn, user["id"])
    readable = access.readable
    if collection_id is not None:
        if collection_id not in readable:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Collection not found")
        readable = {collection_id}
    marks = ",".join("?" * len(readable)) or "NULL"
    rows = conn.execute(
        f"SELECT {fs._FILE_COLUMNS} {fs._FILE_FROM} WHERE f.collection_id IN ({marks}) AND f.deleted_at IS NULL "
        "ORDER BY f.created_at DESC, f.id DESC", tuple(readable)).fetchall()
    live = {cid: fs.live_folder_ids(conn, cid) for cid in readable}
    rows = [r for r in rows if r["folder_id"] is None or r["folder_id"] in live[r["collection_id"]]]
    access.prefetch(rows)
    return {"files": [_file_json(r, access) for r in rows if access.can_read_file(r)]}


@router.get("/api/files/{file_id}")
def file_status(file_id: int, user=Depends(api_user), conn: sqlite3.Connection = Depends(get_db)):
    access = fs.Access.of(conn, user["id"])
    row = _readable_file(conn, access, file_id)
    out = _file_json(row, access)
    out["path"] = fs.folder_path(conn, row["folder_id"]) if fs.is_live_folder(conn, row["folder_id"]) else []
    out["chunks"] = conn.execute("SELECT count(*) FROM chunks WHERE file_id = ?", (file_id,)).fetchone()[0]
    out["in_trash"] = row["deleted_at"] is not None
    return out


@router.delete("/api/files/{file_id}")
def delete_file(file_id: int, user=Depends(api_user), conn: sqlite3.Connection = Depends(get_db)):
    """Move to the recycle bin (purge from there to delete for good)."""
    result = fs.trash(conn, fs.Access.of(conn, user["id"]), [fs.Item("file", file_id)])[0]
    if not result.ok:
        raise HTTPException(result.status, result.error)
    return {"status": "trashed", "id": file_id}


@router.post("/api/files/{file_id}/retry")
def retry_file(file_id: int, user=Depends(api_user), conn: sqlite3.Connection = Depends(get_db)):
    row = _writable_file(conn, fs.Access.of(conn, user["id"]), file_id)
    if row["status"] != "failed":
        raise HTTPException(status.HTTP_409_CONFLICT, "Only failed files can be retried")
    conn.execute("UPDATE files SET status = 'queued', error = NULL, progress = NULL WHERE id = ?", (file_id,))
    jobs.enqueue(conn, "ingest", file_id)
    return {"status": "queued", "id": file_id}


# ------------------------------------------------------------------ versions
@router.get("/api/files/{file_id}/versions")
def file_versions(file_id: int, user=Depends(api_user), conn: sqlite3.Connection = Depends(get_db)):
    """Earlier versions kept when the file was changed through Claude (or restored)."""
    access = fs.Access.of(conn, user["id"])
    row = _readable_file(conn, access, file_id)
    return {"versions": [{**v, "download_url": f"/files/{file_id}/versions/{v['id']}"}
                         for v in editing.versions(conn, file_id)],
            "can_restore": access.can_write(row["collection_id"], row["folder_id"]) and row["deleted_at"] is None}


@router.get("/files/{file_id}/versions/{version_id}")
def download_version(file_id: int, version_id: int, user=Depends(page_user),
                     conn: sqlite3.Connection = Depends(get_db), settings: Settings = Depends(get_settings)):
    row = _readable_file(conn, fs.Access.of(conn, user["id"]), file_id)
    try:
        v = editing.version_row(conn, file_id, version_id)
    except editing.EditError as exc:
        raise HTTPException(exc.status, str(exc)) from None
    path = editing.version_path(settings, v["rel_path"])
    if not path.is_file():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "That version's content is gone (it may have expired).")
    stem, dot, ext = row["display_name"].rpartition(".")
    name = f"{stem or ext} (before {v['created_at'][:16].replace(':', '.')}){dot}{ext if stem else ''}"
    return FileResponse(path, media_type="application/octet-stream", filename=name,
                        content_disposition_type="attachment", headers={"X-Content-Type-Options": "nosniff"})


class CommentBody(BaseModel):
    text: str = Field(..., max_length=comments.MAX_CHARS)
    reply_to: int | None = None


@router.get("/api/files/{file_id}/comments")
def file_comments(file_id: int, user=Depends(api_user), conn: sqlite3.Connection = Depends(get_db)):
    """Comments people (and Claude, through the connector) left on the file."""
    try:
        return {"comments": comments.listing(conn, fs.Access.of(conn, user["id"]), file_id)}
    except comments.CommentError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from None


@router.post("/api/files/{file_id}/comments", status_code=status.HTTP_201_CREATED)
def add_file_comment(file_id: int, body: CommentBody, user=Depends(api_user),
                     conn: sqlite3.Connection = Depends(get_db)):
    access = fs.Access.of(conn, user["id"])
    try:
        comments.add(conn, access, file_id, body.text, via="web", reply_to=body.reply_to)
        return {"comments": comments.listing(conn, access, file_id)}
    except comments.CommentError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from None


@router.post("/api/files/{file_id}/versions/{version_id}/restore")
def restore_version(file_id: int, version_id: int, user=Depends(api_user),
                    conn: sqlite3.Connection = Depends(get_db), settings: Settings = Depends(get_settings)):
    """Put an earlier version back; what's there now becomes a version too, so
    a restore can itself be undone."""
    access = fs.Access.of(conn, user["id"])
    row = _writable_file(conn, access, file_id)
    if row["deleted_at"] is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Restore the file from the recycle bin first.")
    try:
        version = editing.restore(conn, settings, file_id, version_id, user["id"])
    except editing.EditError as exc:
        raise HTTPException(exc.status, str(exc)) from None
    out = _file_json(fs.file_row(conn, file_id), access)
    return {**out, "version": version}


# ---------------------------------------------------------------------- views
def _blob_file(settings: Settings, row: sqlite3.Row, prefer_ocr: bool, conn: sqlite3.Connection) -> Path:
    path = store.content_path(conn, settings, row["id"], prefer_ocr=prefer_ocr)
    if not path.exists():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "File content is missing")
    return path


# A screen box's TV (deploy/screen) has no browser buttons: a document opened there as the page itself
# (not in a frame) goes to one with the small bar and the remote (base.html), the document framed inside.
def _tv_frame(request: Request, conn: sqlite3.Connection, file_id: int, how: str) -> RedirectResponse | None:
    if request.headers.get("sec-fetch-dest") != "document":
        return None
    screen = media_screens.by_token(conn, request.cookies.get(media_screens.COOKIE))
    if screen is None or not screen["is_box"]:
        return None
    return RedirectResponse(f"/files/{file_id}/tv/{how}", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/files/{file_id}/tv/{how}", response_class=HTMLResponse)
def view_on_tv(request: Request, file_id: int, how: Literal["view", "pdf", "inline"], user=Depends(page_user),
               conn: sqlite3.Connection = Depends(get_db)):
    """The view, PDF or picture in a frame, on the TV's page (_tv_frame)."""
    row = _readable_file(conn, fs.Access.of(conn, user["id"]), file_id)
    return request.app.state.templates.TemplateResponse(request, "file_tv.html", {
        "user": user, "file": row, "src": f"/files/{file_id}/{how}"})


@router.get("/files/{file_id}/pdf")
def view_pdf(request: Request, file_id: int, user=Depends(page_user), conn: sqlite3.Connection = Depends(get_db),
             settings: Settings = Depends(get_settings)):
    row = _readable_file(conn, fs.Access.of(conn, user["id"]), file_id)
    if row["kind"] != "pdf":
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not a PDF")
    if (tv := _tv_frame(request, conn, file_id, "pdf")) is not None:
        return tv
    # Serve the OCR'd copy when there is one, so its text is selectable too.
    return FileResponse(
        _blob_file(settings, row, prefer_ocr=True, conn=conn), media_type="application/pdf",
        filename=row["display_name"], content_disposition_type="inline",
        headers={"X-Content-Type-Options": "nosniff"},
    )


def _text_view(conn: sqlite3.Connection, settings: Settings, row: sqlite3.Row) -> str:
    """The text read from a document, code or text file, one section per unit,
    each with the anchor its citations link to. Everything is escaped."""
    units = conn.execute("SELECT u.* FROM blob_units u JOIN files f ON f.blob_sha256 = u.sha256 "
                         "WHERE f.id = ? ORDER BY u.unit_no", (row["id"],)).fetchall()
    code = row["kind"] == "code"
    if not units:
        if row["kind"] == "doc":
            return "<p class='muted'>This file hasn't been read yet; its text appears here once it is searchable.</p>"
        text = _blob_file(settings, row, prefer_ocr=False, conn=conn).read_bytes()[:2 * 1024 * 1024]
        return f"<pre>{escape(text.decode('utf-8', errors='replace'))}</pre>"
    parts, last = [], None
    for u in units:
        anchor = escape(u["anchor"] or f"s{u['unit_no'] + 1}")
        if u["heading_path"] and u["heading_path"] != last:
            parts.append(f'<h3 id="{anchor}">{escape(u["heading_path"])}</h3>')
        else:
            parts.append(f'<span id="{anchor}"></span>')
        last = u["heading_path"]
        if code:
            parts.append(f"<pre>{escape(u['text'])}</pre>")
        else:
            parts += [f"<p>{escape(line)}</p>" for line in u["text"].splitlines() if line.strip()]
    return "\n".join(parts)


@router.get("/files/{file_id}/view", response_class=HTMLResponse)
def view_markdown(request: Request, file_id: int, user=Depends(page_user),
                  conn: sqlite3.Connection = Depends(get_db), settings: Settings = Depends(get_settings)):
    """Markdown rendered, or the text of a document, code or text file."""
    row = _readable_file(conn, fs.Access.of(conn, user["id"]), file_id)
    if row["kind"] in ("md", "txt", "doc", "code") and (tv := _tv_frame(request, conn, file_id, "view")) is not None:
        return tv
    if row["kind"] == "md":
        html = markdown.render_html(markdown.decode(_blob_file(settings, row, prefer_ocr=False, conn=conn).read_bytes()))
    elif row["kind"] in ("txt", "doc", "code"):
        html = _text_view(conn, settings, row)
    else:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No text view for this kind of file")
    response = request.app.state.templates.TemplateResponse(
        request, "file_view.html", {"file": row, "content": html})
    response.headers["Content-Security-Policy"] = _VIEW_CSP
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


@router.get("/files/{file_id}/inline")
def view_inline(request: Request, file_id: int, user=Depends(page_user), conn: sqlite3.Connection = Depends(get_db),
                settings: Settings = Depends(get_settings)):
    """Preview an image (raster only) or the start of a text file."""
    row = _readable_file(conn, fs.Access.of(conn, user["id"]), file_id)
    preview = _preview(row)
    if preview in ("image", "text") and (tv := _tv_frame(request, conn, file_id, "inline")) is not None:
        return tv
    headers = {"X-Content-Type-Options": "nosniff", "Content-Security-Policy": "default-src 'none'; sandbox"}
    if preview == "image":
        return FileResponse(_blob_file(settings, row, prefer_ocr=False, conn=conn), media_type=row["mime"],
                            content_disposition_type="inline", filename=row["display_name"], headers=headers)
    if preview == "text":
        with open(_blob_file(settings, row, prefer_ocr=False, conn=conn), "rb") as fh:
            data = fh.read(_TEXT_PREVIEW_BYTES)
        return HTMLResponse(data.decode("utf-8", errors="replace"), media_type="text/plain; charset=utf-8",
                            headers=headers)
    raise HTTPException(status.HTTP_404_NOT_FOUND, "No preview for this file type")


@router.get("/files/{file_id}/raw")
def download_raw(file_id: int, user=Depends(page_user), conn: sqlite3.Connection = Depends(get_db),
                 settings: Settings = Depends(get_settings)):
    """Download the original. Always an attachment, never rendered by the browser."""
    row = _readable_file(conn, fs.Access.of(conn, user["id"]), file_id)
    media = {"pdf": "application/pdf", "md": "text/markdown; charset=utf-8",
             "txt": "text/plain; charset=utf-8"}.get(row["kind"], "application/octet-stream")
    return FileResponse(_blob_file(settings, row, prefer_ocr=False, conn=conn), media_type=media,
                        filename=row["display_name"], content_disposition_type="attachment",
                        headers={"X-Content-Type-Options": "nosniff"})
