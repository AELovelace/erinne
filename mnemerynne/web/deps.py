"""Request dependencies shared by all routers."""

from __future__ import annotations

import sqlite3
from typing import Iterator

from fastapi import Depends, HTTPException, Request, status

from mnemerynne import accounts, db, users
from mnemerynne.config import Settings
from mnemerynne.web import security


class LoginRequired(Exception):
    """Raised by page dependencies; turned into a redirect to /login."""


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_db(request: Request) -> Iterator[sqlite3.Connection]:
    conn = db.connect(request.app.state.settings.db_path)
    try:
        yield conn
    finally:
        conn.close()


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
