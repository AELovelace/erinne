"""Request dependencies shared by all routers."""

from __future__ import annotations

import sqlite3
from typing import Iterator

from fastapi import Depends, HTTPException, Request, status

from mnemerynne import accounts, users
from mnemerynne.config import Settings
from mnemerynne.web import security


class LoginRequired(Exception):
    """Raised by page dependencies; turned into a redirect to /login."""


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_db(request: Request) -> Iterator[sqlite3.Connection]:
    pool = request.app.state.db_pool
    conn = pool.get()
    held = _held(request)
    held.append(conn)                      # release_db() can hand it back before the response is over
    try:
        yield conn
    finally:
        if conn in held:                   # still ours: not handed back early by release_db()
            held.remove(conn)
            pool.put(conn)


def _held(request: Request) -> list[sqlite3.Connection]:
    """The connections get_db lent this request (request.state is shared by every dependency of one request)."""
    held = getattr(request.state, "db_held", None)
    if held is None:
        held = request.state.db_held = []
    return held


def release_db(request: Request) -> None:
    """Hand this request's connection(s) back to the pool now, not when the response ends.

    FastAPI keeps a `Depends(get_db)` connection until the response is fully sent, and every guard (page_user,
    _media_on, _rooms_on...) shares it. A live stream (server-sent events, a radio relay) would hold it, and its
    3 open files (the database, -wal and -shm), for as long as the tab stays open; enough tabs and the app runs out
    of open files ("unable to open database file", nginx 502). A stream calls this just before returning its
    StreamingResponse, and must not touch that `conn` afterwards: its generator borrows its own from the pool."""
    held = _held(request)
    pool = request.app.state.db_pool
    while held:
        pool.put(held.pop())               # back to the pool (or closed, if the pool already has enough spares)


def _session_user(request: Request, conn: sqlite3.Connection) -> sqlite3.Row | None:
    """The logged-in user, re-checked against the database on every request."""
    user_id = request.session.get("user_id")
    user = users.get_user_by_id(conn, user_id) if user_id else None
    # A password reset bumps session_epoch, ending sessions signed in before it.
    if user is None or not user["active"] or request.session.get("epoch", 0) != user["session_epoch"]:
        request.session.clear()
        return None
    return user


def page_user(request: Request, conn: sqlite3.Connection = Depends(get_db)) -> sqlite3.Row:
    user = _session_user(request, conn)
    if user is None:
        raise LoginRequired()
    return user


def api_user(request: Request, conn: sqlite3.Connection = Depends(get_db)) -> sqlite3.Row:
    user = _session_user(request, conn)
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated")
    return user


def api_user_or_token(request: Request, conn: sqlite3.Connection = Depends(get_db)) -> sqlite3.Row:
    """The signed-in user, or for scripts the owner of a personal access key sent as
    `Authorization: Bearer mnemerynne_…` (the same keys as Claude's connector)."""
    from mnemerynne import api_tokens

    user = _session_user(request, conn)
    if user is not None:
        return user
    auth = request.headers.get("authorization", "")
    caller = api_tokens.verify(conn, auth[7:].strip()) if auth.lower().startswith("bearer ") else None
    user = users.get_user_by_id(conn, caller.user_id) if caller else None
    if user is None or not user["active"]:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated",
                            headers={"WWW-Authenticate": "Bearer"})
    return user


def is_admin(request: Request, conn: sqlite3.Connection, user: sqlite3.Row) -> bool:
    """Local admin, bootstrap username, or a domain user in the admin group,
    signed in from a network admin rights count from (MNEMERYNNE_ADMIN_NETWORKS)."""
    state = request.app.state
    if not security.admin_network_ok(state.settings, request.client.host if request.client else ""):
        return False
    return accounts.is_admin(conn, user, state.directory, state.settings.admin_usernames)


def admin_user(request: Request, user=Depends(api_user), conn: sqlite3.Connection = Depends(get_db)) -> sqlite3.Row:
    if not is_admin(request, conn, user):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Admins only")
    return user
