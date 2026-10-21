"""Connect Claude: people create and revoke their own API tokens, see what
Claude can search for them, and download the Claude Desktop extension."""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel, Field

from erinne import api_tokens, permissions, users
from erinne.config import Settings
from erinne.web.deps import api_user, get_db, get_settings, page_user

router = APIRouter()


def _extension(settings: Settings):
    """The packed Claude Desktop extension, dropped into the data directory by an admin."""
    path = settings.data_dir / "erinne.mcpb"
    return path if path.is_file() else None


@router.get("/connect", response_class=HTMLResponse)
def connect_page(request: Request, user=Depends(page_user), settings: Settings = Depends(get_settings)):
    return request.app.state.templates.TemplateResponse(request, "connect.html", {
        "user": user, "is_admin": users.is_admin(user, settings.admin_usernames), "active": "connect",
        "has_extension": _extension(settings) is not None})


@router.get("/connect/erinne.mcpb")
def download_extension(user=Depends(page_user), settings: Settings = Depends(get_settings)):
    path = _extension(settings)
    if path is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "The extension hasn't been published yet.")
    return FileResponse(path, media_type="application/octet-stream", filename="erinne.mcpb")


@router.get("/api/tokens")
def list_tokens(request: Request, user=Depends(api_user), conn: sqlite3.Connection = Depends(get_db),
                settings: Settings = Depends(get_settings)):
    shared = permissions.claude_collection_ids(conn, user["id"])
    collections = [{"name": "My inbox" if c["kind"] == "private" else c["name"], "kind": c["kind"]}
                   for c in permissions.readable_collections(conn, user["id"]) if c["id"] in shared]
    base = settings.public_url.rstrip("/") or str(request.base_url).rstrip("/")
    return {"tokens": api_tokens.list_for_user(conn, user["id"]), "shared": collections,
            "mcp_url": base + "/mcp", "expiry_days": list(api_tokens.EXPIRY_DAYS)}


class NewToken(BaseModel):
    name: str = Field("Claude Desktop", max_length=80)
    days: int = 90


@router.post("/api/tokens", status_code=status.HTTP_201_CREATED)
def create_token(body: NewToken, user=Depends(api_user), conn: sqlite3.Connection = Depends(get_db)):
    if body.days not in api_tokens.EXPIRY_DAYS:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT,
                            f"Expiry must be one of {', '.join(map(str, api_tokens.EXPIRY_DAYS))} days.")
    token_id, token = api_tokens.create(conn, user["id"], body.name, body.days)
    return {"id": token_id, "token": token}


@router.delete("/api/tokens/{token_id}")
def revoke_token(token_id: int, user=Depends(api_user), conn: sqlite3.Connection = Depends(get_db)):
    if not api_tokens.revoke(conn, token_id, user["id"]):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Token not found")
    return {"status": "revoked", "id": token_id}
