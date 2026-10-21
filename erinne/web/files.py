"""Uploads, file status, deletion and viewing (PDF inline, Markdown rendered)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile, status
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

from erinne import jobs, permissions
from erinne.config import Settings
from erinne.ingest import markdown, store
from erinne.web.deps import api_user, get_db, get_settings, page_user

router = APIRouter()

# The Markdown view renders user-supplied content: no scripts, no remote
# resources, no framing by other sites.
_VIEW_CSP = ("default-src 'none'; style-src 'self'; img-src 'self'; base-uri 'none'; "
             "form-action 'none'; frame-ancestors 'self'")

_FILE_SQL = """
    SELECT f.id, f.collection_id, f.display_name, f.status, f.progress, f.error, f.notes,
           f.created_at, f.updated_at, b.kind, b.size, b.unit_count, b.rel_path, b.ocr_rel_path,
           c.name AS collection_name, c.kind AS collection_kind
      FROM files f
      JOIN blobs b ON b.sha256 = f.blob_sha256
      JOIN collections c ON c.id = f.collection_id
"""


def _file_json(row: sqlite3.Row, writable: set[int]) -> dict:
    view = f"/files/{row['id']}/pdf" if row["kind"] == "pdf" else f"/files/{row['id']}/view"
    return {
        "id": row["id"],
        "collection_id": row["collection_id"],
        "collection_name": row["collection_name"],
        "name": row["display_name"],
        "kind": row["kind"],
        "size": row["size"],
        "pages": row["unit_count"] if row["kind"] == "pdf" else None,
        "status": row["status"],
        "progress": row["progress"],
        "error": row["error"],
        "notes": row["notes"],
        "created_at": row["created_at"],
        "view_url": view,
        "can_delete": row["collection_id"] in writable,
    }


def _readable_file(conn: sqlite3.Connection, user: sqlite3.Row, file_id: int) -> sqlite3.Row:
    row = conn.execute(_FILE_SQL + " WHERE f.id = ?", (file_id,)).fetchone()
    # Same 404 whether the file doesn't exist or isn't yours: no existence leaks.
    if row is None or row["collection_id"] not in permissions.readable_collection_ids(conn, user["id"]):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "File not found")
    return row


def _writable_file(conn: sqlite3.Connection, user: sqlite3.Row, file_id: int) -> sqlite3.Row:
    row = _readable_file(conn, user, file_id)
    if row["collection_id"] not in permissions.writable_collection_ids(conn, user["id"]):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You can't change files in this collection")
    return row


# ------------------------------------------------------------------------ API
@router.post("/api/files")
def upload(
    file: UploadFile = File(...),
    collection_id: int | None = Form(None),
    user=Depends(api_user),
    conn: sqlite3.Connection = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    writable = permissions.writable_collection_ids(conn, user["id"])
    target = collection_id if collection_id is not None else permissions.private_collection_id(conn, user["id"])
    if target not in writable:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You can't upload to this collection")
    try:
        received = store.receive(file.file, file.filename or "", settings)
    except store.UploadError as exc:
        code = status.HTTP_413_CONTENT_TOO_LARGE if "larger than" in str(exc) else status.HTTP_400_BAD_REQUEST
        raise HTTPException(code, str(exc)) from exc
    file_id, created = store.add_file(conn, settings, received, target, user["id"])
    row = conn.execute(_FILE_SQL + " WHERE f.id = ?", (file_id,)).fetchone()
    return JSONResponse(_file_json(row, writable),
                        status_code=status.HTTP_201_CREATED if created else status.HTTP_200_OK)


@router.get("/api/files")
def list_files(
    collection_id: int | None = Query(None),
    user=Depends(api_user),
    conn: sqlite3.Connection = Depends(get_db),
):
    readable = permissions.readable_collection_ids(conn, user["id"])
    if collection_id is not None:
        if collection_id not in readable:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Collection not found")
        readable = {collection_id}
    writable = permissions.writable_collection_ids(conn, user["id"])
    marks = ",".join("?" * len(readable)) or "NULL"
    rows = conn.execute(
        _FILE_SQL + f" WHERE f.collection_id IN ({marks}) ORDER BY f.created_at DESC, f.id DESC",
        tuple(readable),
    ).fetchall()
    return {"files": [_file_json(r, writable) for r in rows]}


@router.get("/api/files/{file_id}")
def file_status(file_id: int, user=Depends(api_user), conn: sqlite3.Connection = Depends(get_db)):
    row = _readable_file(conn, user, file_id)
    return _file_json(row, permissions.writable_collection_ids(conn, user["id"]))


@router.delete("/api/files/{file_id}")
def delete_file(file_id: int, user=Depends(api_user), conn: sqlite3.Connection = Depends(get_db),
                settings: Settings = Depends(get_settings)):
    _writable_file(conn, user, file_id)
    store.delete_file(conn, settings, file_id)
    return {"status": "deleted", "id": file_id}


@router.post("/api/files/{file_id}/retry")
def retry_file(file_id: int, user=Depends(api_user), conn: sqlite3.Connection = Depends(get_db)):
    row = _writable_file(conn, user, file_id)
    if row["status"] != "failed":
        raise HTTPException(status.HTTP_409_CONFLICT, "Only failed files can be retried")
    conn.execute("UPDATE files SET status = 'queued', error = NULL, progress = NULL WHERE id = ?", (file_id,))
    jobs.enqueue(conn, "ingest", file_id)
    return {"status": "queued", "id": file_id}


# ---------------------------------------------------------------------- views
def _blob_file(settings: Settings, row: sqlite3.Row, prefer_ocr: bool) -> Path:
    rel = row["ocr_rel_path"] if prefer_ocr and row["ocr_rel_path"] else row["rel_path"]
    path = store.blob_path(settings, rel)
    if not path.exists():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "File content is missing")
    return path


@router.get("/files/{file_id}/pdf")
def view_pdf(file_id: int, user=Depends(page_user), conn: sqlite3.Connection = Depends(get_db),
             settings: Settings = Depends(get_settings)):
    row = _readable_file(conn, user, file_id)
    if row["kind"] != "pdf":
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not a PDF")
    # Serve the OCR'd copy when there is one, so its text is selectable too.
    return FileResponse(
        _blob_file(settings, row, prefer_ocr=True), media_type="application/pdf",
        filename=row["display_name"], content_disposition_type="inline",
        headers={"X-Content-Type-Options": "nosniff"},
    )


@router.get("/files/{file_id}/view", response_class=HTMLResponse)
def view_markdown(request: Request, file_id: int, user=Depends(page_user),
                  conn: sqlite3.Connection = Depends(get_db), settings: Settings = Depends(get_settings)):
    row = _readable_file(conn, user, file_id)
    if row["kind"] != "md":
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not a Markdown file")
    html = markdown.render_html(markdown.decode(_blob_file(settings, row, prefer_ocr=False).read_bytes()))
    response = request.app.state.templates.TemplateResponse(
        request, "file_view.html", {"file": row, "content": html})
    response.headers["Content-Security-Policy"] = _VIEW_CSP
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


@router.get("/files/{file_id}/raw")
def download_raw(file_id: int, user=Depends(page_user), conn: sqlite3.Connection = Depends(get_db),
                 settings: Settings = Depends(get_settings)):
    row = _readable_file(conn, user, file_id)
    media = "application/pdf" if row["kind"] == "pdf" else "text/markdown; charset=utf-8"
    return FileResponse(_blob_file(settings, row, prefer_ocr=False), media_type=media,
                        filename=row["display_name"], headers={"X-Content-Type-Options": "nosniff"})
