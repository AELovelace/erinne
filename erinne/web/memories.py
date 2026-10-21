"""'Your memories': users see and delete what Erinne remembers about them."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse

from erinne import memory, users
from erinne.web.deps import api_user, page_user

router = APIRouter()


@router.get("/memories", response_class=HTMLResponse)
async def memories_page(request: Request, user=Depends(page_user)):
    items = await memory.list_memories(request.app.state.store, user["id"])
    settings = request.app.state.settings
    return request.app.state.templates.TemplateResponse(request, "memories.html", {
        "user": user, "is_admin": users.is_admin(user, settings.admin_usernames), "active": "memories",
        "memories": items, "mode": settings.memory_mode})


@router.get("/api/memories")
async def list_memories(request: Request, user=Depends(api_user)):
    return {"memories": await memory.list_memories(request.app.state.store, user["id"])}


@router.delete("/api/memories/{key}")
async def delete_memory(key: str, request: Request, user=Depends(api_user)):
    if not await memory.forget(request.app.state.store, user["id"], key):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Memory not found")
    return {"status": "deleted"}


@router.delete("/api/memories")
async def delete_all(request: Request, user=Depends(api_user)):
    return {"status": "deleted", "count": await memory.forget(request.app.state.store, user["id"])}
