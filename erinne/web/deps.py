"""Request dependencies shared by all routers."""

from __future__ import annotations

import sqlite3
from typing import Iterator

from fastapi import Depends, HTTPException, Request, status

from erinne import db, users
from erinne.config import Settings


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
    if user is None or not user["active"]:
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
