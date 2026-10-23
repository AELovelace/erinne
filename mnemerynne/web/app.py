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

from mnemerynne import (balancer as balancer_mod, claude_chat, db, directory, editing, mcp_server, memory,
                        permissions, share, sharepoint, users)
from mnemerynne.agent import Agent
from mnemerynne.answer import ASSISTANT_NAME
from mnemerynne.auth import LoginThrottle
from mnemerynne.clients.embed import Embedder
from mnemerynne.clients.llm import LLM
from mnemerynne.clients.rerank import Reranker
from mnemerynne.config import Settings, get_settings
from mnemerynne.graph import Deps, build_graph
from mnemerynne.router import load_router
from mnemerynne.web import (admin, admin_apis, agent as agent_pages, apis as api_pages, balancer as balancer_pages, chat,
                            claude_links as claude_link_pages, files, m365 as m365_pages, memories,
                            oauth as oauth_pages, settings as settings_pages, tokens)
from mnemerynne.web.deps import LoginRequired, api_user, get_db, is_admin, page_user

log = logging.getLogger("mnemerynne.web")

_HERE = Path(__file__).parent
_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
# Called by Claude's servers with OAuth credentials, never with the session cookie.
_OAUTH_API = {"/oauth/token", "/oauth/register", "/oauth/revoke"}


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
    share_checker: Any = None   # share permission checks (share.Checker); None = the real POSIX ACLs
    agent_llm: Any = None   # the agent's model client; None = the balancer nodes, then MNEMERYNNE_LLM_URL
    claude_llm: Any = None  # Claude's client in chat (tests); None = the Anthropic API with each person's key
    balancer: Any = None    # balancer.Balancer (tests pass one with a fake transport); None = a real one


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
            # Every inbox has an Agents folder for Claude; inboxes on an
            # unavailable share get theirs on first use.
            made = editing.ensure_all_agents(conn)
            if made:
                log.info("Made the Agents folder in %d inbox(es).", made)
            cfg = balancer_mod.load_config(conn)
            nodes, node_key = cfg["nodes"], balancer_mod.api_key(settings, cfg)
        finally:
            conn.close()
        log.info("Database ready at %s (schema v%d).", settings.db_path, version)

        # Balancer nodes (Settings): the answer model's requests go to the least busy one.
        balancer = (services.balancer if services and services.balancer is not None else balancer_mod.Balancer())
        balancer.configure(nodes, node_key)
        app.state.balancer = balancer
        if nodes:
            log.info("Answer model: %d balancer node(s), %s when none answers.", len(nodes), settings.llm_url)
        svc = services or Services(
            llm=LLM.from_settings(settings, transport=balancer_mod.BalancedTransport(balancer, settings.llm_url)),
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
        # Erynne with tools, for the agent group (agent.py); same model path as the answers.
        app.state.agent = Agent(settings, lambda: app.state.embedder, lambda: app.state.directory,
                                (lambda cfg: svc.agent_llm) if svc.agent_llm is not None else None, balancer,
                                (lambda key, model, timeout: svc.claude_llm) if svc.claude_llm is not None else None)
        app.state.graph = build_graph(Deps(settings, svc.llm, svc.embedder, svc.reranker, router,
                                           app.state.directory, app.state.agent), checkpointer, store)
        app.state.background = set()
        app.state.memory_writer = memory.make_writer(settings, svc.llm, store, app.state.background)
        app.state.embedder = svc.embedder
        poller = asyncio.create_task(balancer.run())
        app.state.background.add(poller)
        try:
            async with mcp.session_manager.run():
                yield
        finally:
            for task in list(app.state.background):
                task.cancel()
            await asyncio.gather(*app.state.background, return_exceptions=True)
            await saver_conn.close()
            await store_conn.close()
            await app.state.agent.aclose()
            await balancer.aclose()
            if services is None:
                svc.embedder.close()
                await svc.llm.aclose()
                if svc.reranker:
                    svc.reranker.close()

    app = FastAPI(title=ASSISTANT_NAME, lifespan=lifespan, docs_url=None, redoc_url=None)
    app.state.settings = settings
    app.state.directory = (services.directory if services and services.directory
                           else directory.from_settings(settings))
    share.configure(settings, app.state.directory, services.share_checker if services else None)
    sharepoint.configure(settings)
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
        if request.method not in _SAFE_METHODS and _cross_site(request) and request.url.path not in _OAUTH_API:
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
    app.include_router(api_pages.router)
    app.include_router(admin_apis.router)
    app.include_router(agent_pages.router)
    app.include_router(m365_pages.router)
    app.include_router(oauth_pages.router)
    app.include_router(balancer_pages.router)
    app.include_router(settings_pages.router)
    app.include_router(claude_link_pages.router)

    # MCP for Claude Desktop: bearer-token auth, read-only tools (mnemerynne/mcp_server.py).
    mcp = mcp_server.build_server(settings, lambda: app.state.embedder, lambda: app.state.directory)
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
        # Back to where signing in was needed (only Claude's connect page asks for that).
        after = request.session.get("after_login") or ""
        request.session.clear()
        request.session["user_id"] = user["id"]
        request.session["epoch"] = user["session_epoch"]
        target = after if after.startswith("/oauth/authorize?") else "/"
        return RedirectResponse(target, status_code=status.HTTP_303_SEE_OTHER)

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
        claude = claude_chat.is_claude_user(conn, user, app.state.directory)
        return _page(request, "chat.html", user, conn, active="chat", has_files=has_files,
                     agent=app.state.agent.allowed(conn, user), agent_full=app.state.agent.full(conn, user),
                     claude=claude,
                     claude_key=claude and claude_chat.status(conn, settings, user["id"])["key"] == "set")

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
            # Erynne's tools (everyone), and whether they may change files anywhere (the agent group);
            # the server checks again on every question.
            "agent": app.state.agent.allowed(conn, user),
            "agent_full": app.state.agent.full(conn, user),
        }

    return app
