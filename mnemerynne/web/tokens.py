"""Connect Claude: people create and revoke their own API tokens, see what
Claude can search for them, and download the Claude Desktop extension. The
page itself is the Claude half of Settings now; /connect sends people there."""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import FileResponse, RedirectResponse
from pydantic import BaseModel, Field

from mnemerynne import api_tokens, editing, fs, permissions
from mnemerynne.config import Settings
from mnemerynne.web.deps import api_user, get_db, get_settings, page_user

router = APIRouter()


def extension_path(settings: Settings):
    """The packed Claude Desktop extension, dropped into the data directory by an admin."""
    path = settings.data_dir / "mnemerynne.mcpb"
    return path if path.is_file() else None


@router.get("/connect")
def connect_page(user=Depends(page_user)):
    """Old links and bookmarks: the Claude half of Settings."""
    return RedirectResponse("/settings#claude", status_code=status.HTTP_308_PERMANENT_REDIRECT)


@router.get("/connect/mnemerynne.mcpb")
def download_extension(user=Depends(page_user), settings: Settings = Depends(get_settings)):
    path = extension_path(settings)
    if path is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "The extension hasn't been published yet.")
    return FileResponse(path, media_type="application/octet-stream", filename="mnemerynne.mcpb")


@router.get("/api/tokens")
def list_tokens(request: Request, user=Depends(api_user), conn: sqlite3.Connection = Depends(get_db),
                settings: Settings = Depends(get_settings)):
    shared = permissions.claude_collection_ids(conn, user["id"])
    collections = [{"name": "My inbox" if c["kind"] == "private" else c["name"], "kind": c["kind"]}
                   for c in permissions.readable_collections(conn, user["id"]) if c["id"] in shared]
    base = settings.public_url.rstrip("/") or str(request.base_url).rstrip("/")
    try:
        editing.agents_folder(conn, user["id"])
    except (fs.FsError, OSError):
        pass
    return {"tokens": api_tokens.list_for_user(conn, user["id"]), "shared": collections,
            "editable": editing.editable_places(conn, user["id"]),
            "mcp_url": base + "/mcp", "expiry_days": list(api_tokens.EXPIRY_DAYS)}


class NewToken(BaseModel):
    name: str = Field("Claude Desktop", max_length=80)
    days: int = 90
    can_edit: bool = True    # Claude may change files where that's allowed (at least the Agents folder)


@router.post("/api/tokens", status_code=status.HTTP_201_CREATED)
def create_token(body: NewToken, user=Depends(api_user), conn: sqlite3.Connection = Depends(get_db)):
    if body.days not in api_tokens.EXPIRY_DAYS:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT,
                            f"Expiry must be one of {', '.join(map(str, api_tokens.EXPIRY_DAYS))} days.")
    token_id, token = api_tokens.create(conn, user["id"], body.name, body.days, can_edit=body.can_edit)
    return {"id": token_id, "token": token}


class TokenChange(BaseModel):
    can_edit: bool


@router.patch("/api/tokens/{token_id}")
def change_token(token_id: int, body: TokenChange, user=Depends(api_user), conn: sqlite3.Connection = Depends(get_db)):
    """Let a token change files, or make it search-only again."""
    if not api_tokens.set_can_edit(conn, token_id, user["id"], body.can_edit):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Token not found")
    return {"id": token_id, "can_edit": body.can_edit}


@router.delete("/api/tokens/{token_id}")
def revoke_token(token_id: int, user=Depends(api_user), conn: sqlite3.Connection = Depends(get_db)):
    if not api_tokens.revoke(conn, token_id, user["id"]):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Token not found")
    return {"status": "revoked", "id": token_id}
