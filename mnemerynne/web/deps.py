"""Request dependencies shared by all routers."""

from __future__ import annotations

import sqlite3
import threading
from typing import AsyncIterator, Callable

import anyio.to_thread
from fastapi import Depends, HTTPException, Request, status

from mnemerynne import accounts, db, users
from mnemerynne.config import Settings
from mnemerynne.web import security


class LoginRequired(Exception):
    """Raised by page dependencies; turned into a redirect to /login."""


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


async def get_db(request: Request) -> AsyncIterator[sqlite3.Connection]:
    """The request's database connection, from the pool and back to it when the request is done.

    Async on purpose. FastAPI runs a plain (sync) generator dependency through contextmanager_in_threadpool,
    whose cleanup catches only Exception: a request *cancelled* midway (the client or nginx gave up, a page
    retrying) raises CancelledError, a BaseException, so the finally below never ran and the connection was
    left for the garbage collector. A storm of cut-off requests leaked thousands that way (10,705 open on the
    box: "unable to open database file", "database is locked", nginx 502). An async generator's cleanup runs
    on cancellation too."""
    async for conn in _lend(request, request.app.state.db_pool):
        yield conn


async def _lend(request: Request, pool: db.Pool) -> AsyncIterator[sqlite3.Connection]:
    """A connection from `pool` for this request, back to it when the request is done (or at release_db)."""
    conn = await anyio.to_thread.run_sync(pool.get)   # off the event loop: a new connection may wait on a lock
    held = _held(request)
    held.append((pool, conn))              # release_db() can hand it back before the response is over
    try:
        yield conn
    finally:
        if (pool, conn) in held:           # still ours: not handed back early by release_db()
            held.remove((pool, conn))
            pool.put(conn)


_pools_lock = threading.Lock()


def feature_pool(app, feature: str) -> db.Pool:
    """The pool of connections to a feature's own file (db.connect_feature: core attached read-only), made on
    first use, so the web app and every service that serves the feature get one without setting it up."""
    pools = getattr(app.state, "feature_pools", None)
    if pools is None or feature not in pools:
        with _pools_lock:
            pools = getattr(app.state, "feature_pools", None)
            if pools is None:
                pools = app.state.feature_pools = {}
            if feature not in pools:
                core_path = app.state.settings.db_path
                pools[feature] = db.Pool(db.feature_path(core_path, feature),
                                         opener=lambda: db.connect_feature(core_path, feature))
    return pools[feature]


def close_feature_pools(app) -> None:
    """At shutdown, with the core pool."""
    for pool in (getattr(app.state, "feature_pools", None) or {}).values():
        pool.close()


def feature_db(feature: str) -> Callable[[Request], AsyncIterator[sqlite3.Connection]]:
    """A dependency like get_db for a feature's own database file: `conn=Depends(get_games_db)`. Its queries still
    find core's tables (users, groups, media_screens...) by their plain names, read-only."""
    async def get_feature_db(request: Request) -> AsyncIterator[sqlite3.Connection]:
        async for conn in _lend(request, feature_pool(request.app, feature)):
            yield conn
    get_feature_db.__name__ = f"get_{feature}_db"
    return get_feature_db


get_games_db = feature_db("games")   # the games, their coins and Lumi (docs/plans/services-rewrite.md, Phase 2)
get_rooms_db = feature_db("rooms")   # Rooms: channels, messages and their live events (Phase 3)


def _held(request: Request) -> list[tuple[db.Pool, sqlite3.Connection]]:
    """The connections get_db (and the feature dependencies) lent this request, with the pool each came from
    (request.state is shared by every dependency of one request)."""
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
    while held:
        pool, conn = held.pop()
        pool.put(conn)                     # back to its pool (or closed, if the pool already has enough spares)


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
