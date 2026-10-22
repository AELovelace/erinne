"""FastAPI application factory."""

from __future__ import annotations

import asyncio
import logging
import secrets
import sqlite3
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import aiosqlite
from fastapi import Depends, FastAPI, Form, Request, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.store.sqlite.aio import AsyncSqliteStore
from starlette.middleware.sessions import SessionMiddleware

from mnemerynne import db, directory, mcp_server, memory, permissions, users
from mnemerynne.answer import ASSISTANT_NAME
from mnemerynne.auth import LoginThrottle
from mnemerynne.clients.embed import Embedder
from mnemerynne.clients.llm import LLM
from mnemerynne.clients.rerank import Reranker
from mnemerynne.config import Settings, get_settings
from mnemerynne.graph import Deps, build_graph
from mnemerynne.router import load_router
from mnemerynne.web import admin, chat, files, memories, tokens
from mnemerynne.web.deps import LoginRequired, api_user, get_db, is_admin, page_user

log = logging.getLogger("mnemerynne.web")

_HERE = Path(__file__).parent
_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def _cross_site(request: Request) -> bool:
    """True for a state-changing request another site's page made the browser send.

    Browsers label requests with Sec-Fetch-Site and Origin; requests without
    either (curl, scripts) can't ride on someone's session cookie anyway.
    Hostnames only: nginx forwards Host without the port.
    """
    fetch_site = request.headers.get("sec-fetch-site")
    if fetch_site and fetch_site not in ("same-origin", "none"):
        return True
    origin = request.headers.get("origin")
    if origin:
        host = (request.headers.get("host") or "").rsplit(":", 1)[0].strip("[]").lower()
        return (urlsplit(origin).hostname or "") != host
    return False


@dataclass
class Services:
    """Model clients; tests pass fakes."""
    llm: Any
    embedder: Any
    reranker: Any = None
    router: Any = None
    directory: Any = None   # the AD domain (mnemerynne.directory); None = from settings


def create_app(settings: Settings | None = None, services: Services | None = None) -> FastAPI:
    settings = settings or get_settings()
    secret_key = settings.secret_key
    if not secret_key:
        log.warning("MNEMERYNNE_SECRET_KEY is not set; using a random key, so sessions end on restart.")
        secret_key = secrets.token_urlsafe(48)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        conn = db.connect(settings.db_path)
        try:
            version = db.migrate(conn, settings.embed_dim)
        finally:
            conn.close()
        log.info("Database ready at %s (schema v%d).", settings.db_path, version)

        svc = services or Services(
            llm=LLM.from_settings(settings),
            embedder=Embedder(settings),
            reranker=Reranker(settings.rerank_url) if settings.rerank_url else None,
        )
        # LangGraph's checkpointer (conversation state) and store (long-term
        # memories) live in the same database file, on their own connections.
        # Autocommit (isolation_level=None): they issue their own BEGIN/COMMIT,
        # and Python's implicit transactions would otherwise hold the write lock.
        saver_conn = await aiosqlite.connect(settings.db_path, timeout=10, isolation_level=None)
        store_conn = await aiosqlite.connect(settings.db_path, timeout=10, isolation_level=None)
        checkpointer = AsyncSqliteSaver(saver_conn)
        await checkpointer.setup()
        store = AsyncSqliteStore(store_conn)
        await store.setup()
        app.state.checkpointer, app.state.store = checkpointer, store
        router = svc.router if services is not None else load_router(settings)
        log.info("Routing: %s.", f"{', '.join(router.labels)}" if router else "no router trained; everything is search")
        app.state.graph = build_graph(Deps(settings, svc.llm, svc.embedder, svc.reranker, router),
                                      checkpointer, store)
        app.state.background = set()
        app.state.memory_writer = memory.make_writer(settings, svc.llm, store, app.state.background)
        app.state.embedder = svc.embedder
        try:
            async with mcp.session_manager.run():
                yield
        finally:
            for task in list(app.state.background):
                task.cancel()
            await asyncio.gather(*app.state.background, return_exceptions=True)
            await saver_conn.close()
            await store_conn.close()
            if services is None:
                svc.embedder.close()
                await svc.llm.aclose()
                if svc.reranker:
                    svc.reranker.close()

    app = FastAPI(title=ASSISTANT_NAME, lifespan=lifespan, docs_url=None, redoc_url=None)
    app.state.settings = settings
    app.state.directory = (services.directory if services and services.directory
                           else directory.from_settings(settings))
    app.state.throttle = LoginThrottle(settings.login_max_failures, settings.login_lockout_seconds)
    app.add_middleware(
        SessionMiddleware,
        secret_key=secret_key,
        session_cookie="mnemerynne_session",
        max_age=settings.session_max_age,
        same_site="lax",
        https_only=settings.session_https_only,
    )

    @app.middleware("http")
    async def _csrf_guard(request: Request, call_next):
        # The session cookie is SameSite=lax, which still lets other sites'
        # top-level form POSTs through; refuse anything cross-site that changes data.
        if request.method not in _SAFE_METHODS and _cross_site(request):
            return JSONResponse({"detail": "Cross-site request refused."}, status_code=status.HTTP_403_FORBIDDEN)
        return await call_next(request)

    app.mount("/static", StaticFiles(directory=_HERE / "static"), name="static")
    templates = Jinja2Templates(directory=_HERE / "templates")
    templates.env.globals["assistant_name"] = ASSISTANT_NAME

    def static(name: str) -> str:
        # The file's mtime in the URL: browsers fetch the new copy after an update
        # instead of reusing a cached one.
        path = _HERE / "static" / name
        version = int(path.stat().st_mtime) if path.exists() else 0
        return f"/static/{name}?v={version}"

    templates.env.globals["static"] = static
    app.state.templates = templates
    app.include_router(files.router)
    app.include_router(chat.router)
    app.include_router(memories.router)
    app.include_router(admin.router)
    app.include_router(tokens.router)

    # MCP for Claude Desktop: bearer-token auth, read-only tools (mnemerynne/mcp_server.py).
    mcp = mcp_server.build_server(settings, lambda: app.state.embedder)
    app.add_route("/mcp", mcp_server.McpEndpoint(settings, mcp), methods=["GET", "POST", "DELETE"])

    @app.exception_handler(LoginRequired)
    async def _redirect_to_login(request: Request, exc: LoginRequired):
        return RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)

    @app.get("/healthz")
    def healthz(conn: sqlite3.Connection = Depends(get_db)):
        return JSONResponse({"status": "ok", "schema": db.schema_version(conn)})

    def _login(request: Request, error: str | None = None, code: int = status.HTTP_200_OK):
        return templates.TemplateResponse(request, "login.html", {"error": error}, status_code=code)

    @app.get("/login", response_class=HTMLResponse)
    def login_page(request: Request):
        return _login(request)

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
            return _login(request, "Too many failed attempts. Try again in a few minutes.",
                          status.HTTP_429_TOO_MANY_REQUESTS)
        user = users.authenticate(conn, username, password)
        if user is None:
            throttle.record_failure(username, client_ip)
            return _login(request, "Invalid username or password.", status.HTTP_401_UNAUTHORIZED)
        throttle.clear(username, client_ip)
        request.session.clear()
        request.session["user_id"] = user["id"]
        return RedirectResponse("/", status_code=status.HTTP_303_SEE_OTHER)

    @app.post("/logout")
    def logout(request: Request):
        request.session.clear()
        return RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)

    def _page(request: Request, template: str, user, conn, **extra):
        return templates.TemplateResponse(request, template, {
            "user": user, "is_admin": is_admin(request, conn, user), **extra})

    @app.get("/", response_class=HTMLResponse)
    def chat_page(request: Request, user=Depends(page_user), conn: sqlite3.Connection = Depends(get_db)):
        has_files = conn.execute(
            "SELECT 1 FROM files WHERE status = 'ready' AND collection_id IN "
            f"({','.join(str(i) for i in permissions.readable_collection_ids(conn, user['id'])) or 'NULL'}) "
            "LIMIT 1").fetchone() is not None
        return _page(request, "chat.html", user, conn, active="chat", has_files=has_files)

    @app.get("/files", response_class=HTMLResponse)
    def files_page(request: Request, user=Depends(page_user), conn: sqlite3.Connection = Depends(get_db)):
        # The file manager loads everything through the /api routes.
        return _page(request, "files.html", user, conn, active="files", trash_days=settings.trash_days)

    @app.get("/api/me")
    def me(user=Depends(api_user), conn: sqlite3.Connection = Depends(get_db)):
        return {
            "username": user["username"],
            "role": user["role"],
            "collections": [dict(c) for c in permissions.readable_collections(conn, user["id"])],
        }

    return app
