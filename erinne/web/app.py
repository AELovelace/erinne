"""FastAPI application factory."""

from __future__ import annotations

import logging
import secrets
import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Iterator

from fastapi import Depends, FastAPI, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from erinne import db, permissions, users
from erinne.auth import LoginThrottle
from erinne.config import Settings, get_settings

log = logging.getLogger("erinne.web")

_HERE = Path(__file__).parent
ASSISTANT_NAME = "Erinne"


class LoginRequired(Exception):
    """Raised by page dependencies; turned into a redirect to /login."""


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    secret_key = settings.secret_key
    if not secret_key:
        log.warning("ERINNE_SECRET_KEY is not set; using a random key, so sessions end on restart.")
        secret_key = secrets.token_urlsafe(48)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        conn = db.connect(settings.db_path)
        try:
            version = db.migrate(conn, settings.embed_dim)
        finally:
            conn.close()
        log.info("Database ready at %s (schema v%d).", settings.db_path, version)
        yield

    app = FastAPI(title="Erinne", lifespan=lifespan, docs_url=None, redoc_url=None)
    app.state.settings = settings
    app.state.throttle = LoginThrottle(settings.login_max_failures, settings.login_lockout_seconds)
    app.add_middleware(
        SessionMiddleware,
        secret_key=secret_key,
        session_cookie="erinne_session",
        max_age=settings.session_max_age,
        same_site="lax",
        https_only=settings.session_https_only,
    )
    app.mount("/static", StaticFiles(directory=_HERE / "static"), name="static")
    templates = Jinja2Templates(directory=_HERE / "templates")
    templates.env.globals["assistant_name"] = ASSISTANT_NAME

    # ------------------------------------------------------------ dependencies
    def get_db() -> Iterator[sqlite3.Connection]:
        conn = db.connect(settings.db_path)
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

    app.state.api_user = api_user  # for routers added in later phases

    @app.exception_handler(LoginRequired)
    async def _redirect_to_login(request: Request, exc: LoginRequired):
        return RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)

    # ------------------------------------------------------------------ routes
    @app.get("/healthz")
    def healthz(conn: sqlite3.Connection = Depends(get_db)):
        return JSONResponse({"status": "ok", "schema": db.schema_version(conn)})

    @app.get("/login", response_class=HTMLResponse)
    def login_page(request: Request):
        return templates.TemplateResponse(request, "login.html", {"error": None})

    @app.post("/login", response_class=HTMLResponse)
    def login_submit(
        request: Request,
        username: str = Form(...),
        password: str = Form(...),
        conn: sqlite3.Connection = Depends(get_db),
    ):
        throttle: LoginThrottle = app.state.throttle
        client_ip = request.client.host if request.client else ""
        if throttle.locked_out(username, client_ip):
            return templates.TemplateResponse(
                request, "login.html",
                {"error": "Too many failed attempts. Try again in a few minutes."},
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            )
        user = users.authenticate(conn, username, password)
        if user is None:
            throttle.record_failure(username, client_ip)
            return templates.TemplateResponse(
                request, "login.html",
                {"error": "Invalid username or password."},
                status_code=status.HTTP_401_UNAUTHORIZED,
            )
        throttle.clear(username, client_ip)
        request.session.clear()
        request.session["user_id"] = user["id"]
        return RedirectResponse("/", status_code=status.HTTP_303_SEE_OTHER)

    @app.post("/logout")
    def logout(request: Request):
        request.session.clear()
        return RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)

    @app.get("/", response_class=HTMLResponse)
    def inbox(request: Request, user=Depends(page_user), conn: sqlite3.Connection = Depends(get_db)):
        return templates.TemplateResponse(
            request, "inbox.html",
            {
                "user": user,
                "is_admin": users.is_admin(user, settings.admin_usernames),
                "collections": permissions.readable_collections(conn, user["id"]),
            },
        )

    @app.get("/api/me")
    def me(user=Depends(api_user), conn: sqlite3.Connection = Depends(get_db)):
        return {
            "username": user["username"],
            "role": user["role"],
            "collections": [dict(c) for c in permissions.readable_collections(conn, user["id"])],
        }

    return app
