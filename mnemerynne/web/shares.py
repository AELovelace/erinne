"""Files' Share button (mnemerynne/sharing.py): the share dialog's API, "Shared with me", and the public
"anyone with the link" pages at /s/<token>.

The public pages are built to give nothing away:
- every refusal (wrong, expired, removed, binned, the maker lost access, outside the shared folder) is the
  same bare 404, from one place (_gone), so a scanner can't tell a real-but-dead link from a made-up one;
- files only ever download (an attachment): nothing a person uploaded is shown as a page on this site;
- no-referrer (the token never leaves in a Referer), noindex, no caching, no framing, a CSP that allows nothing;
- /s/ is rate-limited per address (security.PUBLIC_LIMITS) on top of the 256-bit token.
"""

from __future__ import annotations

import sqlite3
from html import escape
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel, Field

from mnemerynne import db, fs, sharing
from mnemerynne.config import Settings
from mnemerynne.web.deps import api_user, get_db, get_settings
from mnemerynne.web.files import _blob_file, _file_json
from mnemerynne.web.sendfile import send_file

router = APIRouter()          # signed-in: the share dialog and "Shared with me"
links_router = APIRouter()    # public: /s/<token>

# Headers on every public link answer, the 404 included.
LINK_HEADERS = {
    "Referrer-Policy": "no-referrer",                  # the token never travels on in a Referer
    "X-Robots-Tag": "noindex, nofollow, noarchive",
    "Cache-Control": "no-store",
    "X-Frame-Options": "DENY",
}
_PAGE_CSP = ("default-src 'none'; style-src 'unsafe-inline'; img-src 'none'; base-uri 'none'; form-action 'none'; "
             "frame-ancestors 'none'")


def _refused(exc: sharing.ShareRefused) -> HTTPException:
    return HTTPException(exc.status, str(exc))


def _public_url(request: Request, settings: Settings) -> str:
    return settings.public_url or str(request.base_url).rstrip("/")


# ------------------------------------------------------------------ the share dialog
class ShareBody(BaseModel):
    type: Literal["file", "folder"]
    id: int
    kind: Literal["user", "group", "ad_group", "everyone", "link"]
    target: int | str | None = None                     # an account or group id, or a domain group's name
    can_write: bool = False                            # "Can edit" (never for links)
    expires_days: int | None | Literal["default"] = "default"   # 1, 7, 30 or null (never); links default to 7


@router.get("/api/shares")
def item_shares(type: Literal["file", "folder"], id: int, request: Request, user=Depends(api_user),
                conn: sqlite3.Connection = Depends(get_db), settings: Settings = Depends(get_settings)):
    """Who has this file or folder through shares, and its links (to copy again)."""
    try:
        return sharing.of_item(conn, user["id"], type, id, secret_key=settings.secret_key,
                               public_url=_public_url(request, settings))
    except sharing.ShareRefused as exc:
        raise _refused(exc) from None


@router.post("/api/shares", status_code=status.HTTP_201_CREATED)
def add_share(body: ShareBody, request: Request, user=Depends(api_user), conn: sqlite3.Connection = Depends(get_db),
              settings: Settings = Depends(get_settings)):
    """Share it (or change an existing share with the same person or group). Answers the dialog's new state;
    a new link's address is in `link`."""
    out_link = None
    try:
        with db.transaction(conn):                      # the share and its link in one go
            if body.kind == "link":
                _, token = sharing.create_link(conn, user["id"], body.type, body.id, settings.secret_key,
                                               expires_days=body.expires_days)
                out_link = sharing.link_url(_public_url(request, settings), token)
            else:
                sharing.create(conn, user["id"], body.type, body.id, body.kind, target=body.target,
                               can_write=body.can_write, expires_days=body.expires_days)
        state = sharing.of_item(conn, user["id"], body.type, body.id, secret_key=settings.secret_key,
                                public_url=_public_url(request, settings))
    except sharing.ShareRefused as exc:
        raise _refused(exc) from None
    return {**state, "link": out_link}


@router.delete("/api/shares/{share_id}")
def remove_share(share_id: int, user=Depends(api_user), conn: sqlite3.Connection = Depends(get_db)):
    """Take a share or link back: its maker, or someone who may change the item's place themselves."""
    try:
        with db.transaction(conn):
            sharing.remove(conn, user["id"], share_id)
    except sharing.ShareRefused as exc:
        raise _refused(exc) from None
    return {"removed": share_id}


@router.get("/api/shares/with-me")
def shared_with_me(user=Depends(api_user), conn: sqlite3.Connection = Depends(get_db)):
    """Files' "Shared with me": what people shared with this person (folders open by browsing them). Files come
    as Files' own rows (view, download and edit addresses), so the list works like any folder's."""
    access = fs.Access.of(conn, user["id"])
    out = []
    for item in sharing.with_me(conn, user["id"]):
        extra = {"shared_by": item["by"], "shared_at": item["shared_at"], "shared_until": item["expires_at"]}
        if item["type"] == "file":
            out.append({**_file_json(fs.file_row(conn, item["id"]), access), "can_delete": False, **extra})
        else:
            out.append({"type": "folder", "id": item["id"], "name": item["name"], "collection_id": item["collection_id"],
                        "can_write": item["can_write"], **extra})
    return {"items": out}


@router.get("/api/shares/targets")
def share_targets(user=Depends(api_user), conn: sqlite3.Connection = Depends(get_db)):
    """Who the dialog can pick: accounts, Mnemerynne's groups and the domain's groups."""
    return sharing.targets(conn, user["id"])


# ------------------------------------------------------------------ anyone with the link
def _gone() -> Response:
    """The one answer to every link that doesn't open: no body, no hint which reason it was."""
    return Response("Not found", status_code=404, media_type="text/plain", headers=LINK_HEADERS)


def _size(n: int | None) -> str:
    n = n or 0
    for unit in ("bytes", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "bytes" else f"{n:.1f} {unit}"
        n /= 1024
    return ""


def _page(title: str, body: str) -> HTMLResponse:
    """A small page of our own markup (names escaped): no scripts, nothing loaded from anywhere."""
    html = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><meta name="robots" content="noindex, nofollow">
<meta name="referrer" content="no-referrer"><title>{escape(title)}</title>
<style>
:root {{ color-scheme: light dark; --fg:#2b2330; --muted:#7a6f80; --bg:#fdf8fb; --card:#fff; --line:#eadde6; --accent:#c2477f; }}
@media (prefers-color-scheme: dark) {{ :root {{ --fg:#f1e9ef; --muted:#b5a8b2; --bg:#1d1720; --card:#271f2b; --line:#3a2f3f; --accent:#f08cb8; }} }}
* {{ box-sizing: border-box; }}
body {{ margin:0; font:16px/1.5 system-ui, sans-serif; color:var(--fg); background:var(--bg); }}
main {{ max-width:720px; margin:0 auto; padding:32px 16px; }}
.card {{ background:var(--card); border:1px solid var(--line); border-radius:12px; padding:20px; }}
h1 {{ font-size:1.25rem; margin:0 0 4px; overflow-wrap:anywhere; }}
.muted {{ color:var(--muted); font-size:.9rem; }}
ul {{ list-style:none; padding:0; margin:16px 0 0; }}
li {{ display:flex; justify-content:space-between; gap:12px; padding:10px 0; border-top:1px solid var(--line); }}
li a {{ color:var(--fg); overflow-wrap:anywhere; }}
a.button {{ display:inline-block; margin-top:16px; padding:10px 18px; border-radius:8px; background:var(--accent);
           color:#fff; text-decoration:none; font-weight:600; }}
</style></head><body><main><div class="card">{body}</div>
<p class="muted">Shared from Mnemerynne. Anyone with this link can open it.</p></main></body></html>"""
    return HTMLResponse(html, headers={**LINK_HEADERS, "Content-Security-Policy": _PAGE_CSP})


def _in_share(conn: sqlite3.Connection, share: sqlite3.Row, folder_id: int | None) -> bool:
    """This folder is the shared one or inside it (and not in the bin)."""
    return (folder_id is not None and folder_id in set(fs.subtree(conn, share["folder_id"]))
            and fs.is_live_folder(conn, folder_id))


def _download(settings: Settings, conn: sqlite3.Connection, file_id: int) -> Response:
    row = fs.file_row(conn, file_id)
    try:
        path = _blob_file(settings, row, prefer_ocr=False, conn=conn)
    except HTTPException:
        return _gone()
    # Always an attachment, as octet-stream: a shared file never renders as a page on this site.
    return send_file(path, media_type="application/octet-stream", filename=row["display_name"],
                     content_disposition_type="attachment", headers=LINK_HEADERS)


def _folder_page(conn: sqlite3.Connection, token: str, share: sqlite3.Row, folder_id: int) -> Response:
    folder = conn.execute("SELECT name FROM folders WHERE id = ?", (folder_id,)).fetchone()
    subfolders = conn.execute("SELECT id, name FROM folders WHERE parent_id = ? AND deleted_at IS NULL "
                              "ORDER BY name COLLATE NOCASE", (folder_id,)).fetchall()
    files = conn.execute(f"SELECT f.id, f.display_name, b.size {fs._FILE_FROM} WHERE f.folder_id = ? "
                         "AND f.deleted_at IS NULL ORDER BY f.display_name COLLATE NOCASE", (folder_id,)).fetchall()
    base = sharing.LINK_PREFIX + token
    rows = []
    if folder_id != share["folder_id"]:
        parent = conn.execute("SELECT parent_id FROM folders WHERE id = ?", (folder_id,)).fetchone()["parent_id"]
        up = base if parent == share["folder_id"] else f"{base}/folder/{parent}"
        rows.append(f'<li><a href="{up}">↑ Up</a><span></span></li>')
    rows += [f'<li><a href="{base}/folder/{f["id"]}">📁 {escape(f["name"])}</a><span class="muted">folder</span></li>'
             for f in subfolders]
    rows += [f'<li><a href="{base}/file/{f["id"]}">{escape(f["display_name"])}</a>'
             f'<span class="muted">{_size(f["size"])}</span></li>' for f in files]
    empty = '<p class="muted">This folder is empty.</p>' if not (subfolders or files) else ""
    return _page(folder["name"], f'<h1>📁 {escape(folder["name"])}</h1>'
                                 f'<div class="muted">Shared folder · files download when opened</div>'
                                 f'{empty}<ul>{"".join(rows)}</ul>')


@links_router.get("/s/{token}")
def open_link(token: str, conn: sqlite3.Connection = Depends(get_db)):
    """A link's page: a file's name and a Download button, or a shared folder's listing."""
    share = sharing.link_target(conn, token)
    if share is None:
        return _gone()
    if share["folder_id"] is not None:
        return _folder_page(conn, token, share, share["folder_id"])
    row = fs.file_row(conn, share["file_id"])
    return _page(row["display_name"], f'<h1>{escape(row["display_name"])}</h1>'
                                      f'<div class="muted">{_size(row["size"])}</div>'
                                      f'<a class="button" href="{sharing.LINK_PREFIX}{token}/download">Download</a>')


@links_router.get("/s/{token}/download")
def download_link(token: str, conn: sqlite3.Connection = Depends(get_db), settings: Settings = Depends(get_settings)):
    share = sharing.link_target(conn, token)
    if share is None or share["file_id"] is None:
        return _gone()
    return _download(settings, conn, share["file_id"])


@links_router.get("/s/{token}/folder/{folder_id}")
def link_folder(token: str, folder_id: int, conn: sqlite3.Connection = Depends(get_db)):
    share = sharing.link_target(conn, token)
    if share is None or share["folder_id"] is None or not _in_share(conn, share, folder_id):
        return _gone()
    return _folder_page(conn, token, share, folder_id)


@links_router.get("/s/{token}/file/{file_id}")
def link_file(token: str, file_id: int, conn: sqlite3.Connection = Depends(get_db),
              settings: Settings = Depends(get_settings)):
    share = sharing.link_target(conn, token)
    if share is None or share["folder_id"] is None:
        return _gone()
    row = conn.execute("SELECT folder_id, deleted_at FROM files WHERE id = ?", (file_id,)).fetchone()
    if row is None or row["deleted_at"] is not None or not _in_share(conn, share, row["folder_id"]):
        return _gone()
    return _download(settings, conn, file_id)
