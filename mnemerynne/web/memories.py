"""'Your memories': users see and delete what Erynne remembers about them (on Preferences)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import RedirectResponse

from mnemerynne import memory
from mnemerynne.web.deps import api_user

router = APIRouter()


@router.get("/memories")
def memories_page():
    """Old links: the memories live on the Preferences page now."""
    return RedirectResponse("/preferences#memories", status_code=status.HTTP_308_PERMANENT_REDIRECT)


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
