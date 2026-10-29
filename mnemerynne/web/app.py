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

from mnemerynne import (identity, arcade as arcade_mod, balancer as balancer_mod, bfgguf, claude_chat, db, directory, editing, feedback as feedback_mod,
                        mcp_server, media as media_mod, memory,
                        diagnostics, messages as messages_mod, permissions, remote, rooms as rooms_mod, rooms_bus, share,
                        sharepoint, theme as theme_mod,
                        users)
from mnemerynne.agent import Agent
from mnemerynne.media import screens as media_screens, transcode as media_transcode
from mnemerynne.rooms_erynne import RoomErynne
from mnemerynne import automations as automations_mod, automations_run
from mnemerynne import health
from mnemerynne.automations_run import Scheduler
from mnemerynne.answer import ASSISTANT_NAME
from mnemerynne.auth import LoginThrottle
from mnemerynne.clients.embed import Embedder
from mnemerynne.clients.llm import LLM
from mnemerynne.clients.rerank import Reranker
from mnemerynne.config import Settings, get_settings
from mnemerynne.graph import Deps, build_graph
from mnemerynne.router import load_router
from mnemerynne.web import (admin, admin_apis, admin_notices, admin_remote, agent as agent_pages, apis as api_pages,
                            arcade as arcade_pages,
                            automations as automation_pages, balancer as balancer_pages,
                            bfgguf as bfgguf_pages, chat,
                            claude_links as claude_link_pages, editor as editor_pages, files, help as help_pages,
                            kiosk as kiosk_pages,
                            m365 as m365_pages, media as media_pages, memories, messages as message_pages, oauth as oauth_pages, remote as remote_pages,
                            rooms as room_pages, scratchpad as scratchpad_pages, security,
                            settings as settings_pages, theme as theme_pages, tokens, wiki as wiki_pages)
from mnemerynne.web.deps import LoginRequired, api_user, get_db, is_admin, page_user

log = logging.getLogger("mnemerynne.web")

_HERE = Path(__file__).parent
_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
# Called by Claude's servers with OAuth credentials, never with the session cookie.
_OAUTH_API = {"/oauth/token", "/oauth/register", "/oauth/revoke"}


def _safe_return(after: str) -> bool:
    """A place sign-in may send someone back to: the page on this site they were opening (a link from
    Claude, Claude's consent page, a remote app); never another site (open redirect), the sign-in pages
    themselves, or anything with a scheme, a backslash or control characters."""
    if not after.startswith("/") or urlsplit(after).path in ("/login", "/logout"):
        return False
    parts = urlsplit(after)
    return not (after.startswith("//") or "\\" in after or parts.scheme or parts.netloc
                or any(ord(c) < 32 or c == "\x7f" for c in after))


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
    bfgguf_transport: Any = None   # the HTTP transport under BFGguf's clients (tests); None = the network


def create_app(settings: Settings | None = None, services: Services | None = None) -> FastAPI:
    settings = settings or get_settings()
    diagnostics.install_log_capture()   # the last few hundred log lines, for the diagnose tool
    secret_key = security.check_secret_key(settings) or secrets.token_urlsafe(48)
    security.parse_networks(settings.admin_networks)   # a typo fails at start, not on every admin request
    if security.exposed(settings) and not settings.public_url:
        log.warning("MNEMERYNNE_PUBLIC_URL is not set; links and OAuth addresses follow the request's Host.")
    try:
        arcade_mod.check_config(settings)   # the arcade must be a host of its own, or it isn't a wall at all
    except ValueError as exc:
        raise security.InsecureConfig(str(exc)) from None

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        conn = db.connect(settings.db_path)
        try:
            version = db.migrate(conn, settings.embed_dim)
            app.state.looks = theme_mod.load_looks(conn, settings)   # the themes, as every page draws them
            app.state.instance = identity.load(conn, settings)      # which box this is, for the connector
            mcp_server.apply_identity(app.state.mcp, app.state.instance)
            # Every inbox has an Agents folder for Claude; inboxes on an
            # unavailable share get theirs on first use.
            made = editing.ensure_all_agents(conn)
            if made:
                log.info("Made the Agents folder in %d inbox(es).", made)
            with db.transaction(conn):
                archived = feedback_mod.backfill(conn)
            if archived:
                log.info("Archived %d rating(s) given before the feedback archive.", archived)
            cfg = balancer_mod.load_config(conn)
            nodes, node_key = cfg["nodes"], balancer_mod.api_key(settings, cfg)
        finally:
            conn.close()
        log.info("Database ready at %s (schema v%d).", settings.db_path, version)

        # Balancer nodes (Settings): the answer model's requests go to the least busy one.
        balancer = (services.balancer if services and services.balancer is not None else balancer_mod.Balancer())
        balancer.configure(nodes, node_key)
        app.state.balancer = balancer
        app.state.rooms_bus = rooms_bus.Bus(settings.db_path)   # Rooms' live streams
        app.state.bfgguf_transport = services.bfgguf_transport if services else None
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
        # Erynne answering @mentions in Rooms, reading only what everyone in the room may read.
        app.state.rooms_erynne = RoomErynne(
            settings, app.state.agent, app.state.rooms_bus, app.state.background, lambda: app.state.embedder,
            lambda: app.state.directory, balancer, app.state.bfgguf_transport,
            (lambda cfg: svc.agent_llm) if svc.agent_llm is not None else None)
        app.state.memory_writer = memory.make_writer(settings, svc.llm, store, app.state.background)
        # Media's ffmpeg sessions (media/transcode.py): none survive a restart.
        app.state.media_sessions = media_transcode.Manager(settings)
        await asyncio.to_thread(app.state.media_sessions.startup)
        app.state.embedder = svc.embedder
        poller = asyncio.create_task(balancer.run())
        app.state.background.add(poller)
        # Automator: scheduled automations run here, where the agent and its model path live.
        app.state.automator = Scheduler(settings, app.state.agent, app.state.rooms_erynne, app.state.rooms_bus,
                                        lambda: app.state.directory, app.state.background)
        automations_run.ACTIVE = app.state.automator   # for the MCP tools (run now, a routine's report)
        if services is None:   # tests drive it with tick() instead
            app.state.background.add(asyncio.create_task(app.state.automator.run_forever()))
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
            await app.state.remote_proxy.aclose()
            await app.state.arcade.aclose()
            await balancer.aclose()
            await asyncio.to_thread(app.state.media_sessions.shutdown)
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
    app.state.throttle = LoginThrottle(settings.login_max_failures, settings.login_lockout_seconds,
                                       settings.login_ip_max_failures)
    app.add_middleware(
        SessionMiddleware,
        secret_key=secret_key,
        session_cookie="mnemerynne_session",
        max_age=settings.session_max_age,
        same_site="lax",
        https_only=security.https_only(settings),
    )

    @app.middleware("http")
    async def _csrf_guard(request: Request, call_next):
        # The session cookie is SameSite=lax, which still lets other sites'
        # top-level form POSTs through; refuse anything cross-site that changes data.
        if request.method not in _SAFE_METHODS and _cross_site(request) and request.url.path not in _OAUTH_API:
            return JSONResponse({"detail": "Cross-site request refused."}, status_code=status.HTTP_403_FORBIDDEN)
        return await call_next(request)

    # Added last, so they run first: refused requests get the headers too.
    app.add_middleware(security.PublicRateLimit)
    app.add_middleware(security.SecurityHeaders, settings=settings)
    # The arcade's host (arcade.py) is sent to the arcade before anything of Mnemerynne's runs: no session, no
    # pages, no API there. Added after everything else, so it runs first.
    app.state.arcade = arcade_mod.ArcadeProxy(settings, secret_key, lambda: app.state.directory)
    app.add_middleware(arcade_mod.ArcadeHost, proxy=app.state.arcade, arcade_host=arcade_mod.host(settings))

    app.mount("/static", StaticFiles(directory=_HERE / "static"), name="static")

    def _tv(request: Request, conn: sqlite3.Connection) -> dict | None:
        """This browser is a screen (the TV, by its key): its underscan and text size on every page, and on a
        screen box the small bar and the remote (base.html). Not inside a frame: the page around it has those."""
        if request.headers.get("sec-fetch-dest") == "iframe":
            return None
        screen = media_screens.by_token(conn, request.cookies.get(media_screens.COOKIE))
        if screen is None:
            return None
        return {"id": screen["id"], "name": screen["name"], "safe_x": screen["safe_x"], "safe_y": screen["safe_y"],
                "ui_scale": screen["ui_scale"], "box": bool(screen["is_box"])}

    def _nav_context(request: Request) -> dict:
        """What every page's top bar needs beyond its own context: the badge on the Messages button
        (unread notes, and with Rooms on, mentions and unread DMs), whether Rooms and Remote are there;
        and on a screen, the screen (tv)."""
        session = request.scope.get("session", {}) if "session" in request.scope else {}
        user_id = session.get("user_id")
        # The look (theme) and the brand: every page, signed in or not (the sign-in page follows the cookie).
        looks = theme_pages.looks_for(app)
        cookie = request.cookies.get(settings_pages.THEME_COOKIE)
        out = {"look": looks.resolve(None, cookie), "brand": looks.brand,
               "product_name": looks.brand.get("name") or ASSISTANT_NAME}
        if not user_id and not request.cookies.get(media_screens.COOKIE):
            return out
        conn = db.connect(settings.db_path)
        try:
            out["tv"] = _tv(request, conn)
            user = users.get_user_by_id(conn, user_id) if user_id else None
            if user is None:
                return out
            out["look"] = looks.resolve(user["theme"] or None, cookie)
            admin = is_admin(request, conn, user)
            unread_rooms = rooms_mod.unread_summary(conn, user, app.state.directory)
            return out | {"nav_unread": messages_mod.unread_count(conn, user_id) + unread_rooms["mentions"] + unread_rooms["dms"],
                    "has_rooms": rooms_mod.enabled(conn), "has_media": media_mod.enabled(conn) and media_mod.may_use(conn, user, app.state.directory),
                    "message_max_chars": messages_mod.MAX_CHARS,
                    "has_remote": bool(remote.apps_for(conn, user, app.state.directory, admin)),
                    "has_automations": automations_mod.may_use(conn, settings, user, app.state.directory),
                    # Signed in on a screen box's TV (its top bar): back to the TV page, which signs out.
                    "kiosk_back": bool(session.get("kiosk_screen"))}
        except sqlite3.Error:
            return out
        finally:
            conn.close()

    templates = Jinja2Templates(directory=_HERE / "templates", context_processors=[_nav_context])
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
    app.include_router(scratchpad_pages.router)   # the agents' scratchpad summary and Clear, on Preferences
    app.include_router(admin.router)
    app.include_router(tokens.router)
    app.include_router(api_pages.router)
    app.include_router(admin_apis.router)
    app.include_router(agent_pages.router)
    app.include_router(m365_pages.router)
    app.include_router(oauth_pages.router)
    app.include_router(balancer_pages.router)
    app.include_router(bfgguf_pages.router)
    app.include_router(settings_pages.router)
    app.include_router(theme_pages.router)
    app.include_router(theme_pages.admin_router)
    app.include_router(claude_link_pages.router)
    app.include_router(help_pages.router)
    app.include_router(editor_pages.router)
    app.include_router(wiki_pages.router)
    app.include_router(message_pages.router)
    app.include_router(room_pages.router)
    app.include_router(room_pages.admin_router)
    app.include_router(automation_pages.router)
    app.include_router(automation_pages.admin_router)
    app.include_router(media_pages.router)
    app.include_router(media_pages.admin_router)
    app.include_router(media_pages.share_router)
    app.include_router(kiosk_pages.router)
    app.include_router(admin_notices.router)
    app.include_router(remote_pages.router)
    app.include_router(admin_remote.router)
    app.include_router(arcade_pages.router)        # a ticket into the arcade (arcade.py)
    app.include_router(arcade_pages.media_router)  # Media → Games: the list, and a ticket into a game
    app.include_router(arcade_pages.admin_router)  # Settings → Entertainment → Games
    # LAN apps served at /remote/<slug>/... (mnemerynne/remote.py); after the routers, so /remote itself is the page.
    app.state.remote_proxy = remote.RemoteProxy(settings, lambda: app.state.directory)
    app.mount(remote.PREFIX, app.state.remote_proxy, name="remote")

    # MCP for Claude Desktop: bearer-token auth, read-only tools (mnemerynne/mcp_server.py).
    app.state.instance = identity.from_settings(settings)   # the brand name joins once the database is open
    mcp = mcp_server.build_server(settings, lambda: app.state.embedder, lambda: app.state.directory,
                                  instance=lambda: app.state.instance)
    app.state.mcp = mcp
    app.add_route("/mcp", mcp_server.McpEndpoint(settings, mcp), methods=["GET", "POST", "DELETE"])

    @app.exception_handler(LoginRequired)
    async def _redirect_to_login(request: Request, exc: LoginRequired):
        # Back to this page after signing in: a link opened from Claude Desktop lands where it pointed.
        if request.method == "GET":
            request.session["after_login"] = request.url.path + (f"?{request.url.query}" if request.url.query else "")
        return RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)

    @app.get("/manifest.webmanifest", include_in_schema=False)
    def manifest():
        """Makes Erynne installable (Add to Home screen), and on Android a target in the Share menu:
        a YouTube link shared to it opens Media → Screens to play on the TV (web/media.py share_link).
        Public: browsers fetch it without the session cookie."""
        looks = theme_pages.looks_for(app)
        brand = looks.brand
        if brand.get("mark"):   # the organisation's mark (Settings → Themes → Branding)
            v = brand["mark_version"]
            icons = [{"src": f"/theme/brand/{name}?v={v}", "sizes": size, "type": "image/png", "purpose": purpose}
                     for name, size, purpose in (("mark-192.png", "192x192", "any"), ("mark-512.png", "512x512", "any"),
                                                 ("mark-maskable-512.png", "512x512", "maskable"))]
        else:
            icons = [{"src": static(f"icons/{name}"), "sizes": size, "type": "image/png", "purpose": purpose}
                     for name, size, purpose in (("icon-192.png", "192x192", "any"), ("icon-512.png", "512x512", "any"),
                                                 ("maskable-512.png", "512x512", "maskable"))]
        name = brand.get("name") or ASSISTANT_NAME
        default = looks.resolve(None, None)
        return JSONResponse({
            "id": "/", "name": name, "short_name": name, "start_url": "/", "scope": "/",
            "display": "standalone", "background_color": default.page_dark if default.dark_only else default.page_light,
            "theme_color": default.page_light, "icons": icons,
            "shortcuts": [{"name": "Media", "url": "/media"}, {"name": "Screens", "url": "/media#screens"},
                          {"name": "Files", "url": "/files"}],
            "share_target": {"action": "/media/share", "method": "GET",
                             "params": {"title": "title", "text": "text", "url": "url"}},
        }, media_type="application/manifest+json", headers={"Cache-Control": "no-cache"})

    @app.get("/healthz")
    def healthz(conn: sqlite3.Connection = Depends(get_db)):
        return JSONResponse({"status": "ok", "schema": db.schema_version(conn)})

    @app.get("/healthz/full")
    def healthz_full(conn: sqlite3.Connection = Depends(get_db)):
        """For monitors (D8): the worker, its queue and the models too; 503 while any of them fails.
        /healthz above stays a liveness check (update.sh and the TV boxes rely on its 200)."""
        report = health.report(conn, settings)
        report["schema"] = db.schema_version(conn)
        code = status.HTTP_200_OK if report["status"] == "ok" else status.HTTP_503_SERVICE_UNAVAILABLE
        return JSONResponse(report, status_code=code, headers={"Cache-Control": "no-store"})

    def _kiosk_screen(request: Request, conn: sqlite3.Connection) -> sqlite3.Row | None:
        """The screen this browser is, if its top bar is on (kiosk settings): sign-ins here are the TV's."""
        screen = media_screens.by_token(conn, request.cookies.get(media_screens.COOKIE))
        return screen if screen is not None and screen["top_bar"] else None

    def _login(request: Request, error: str | None = None, code: int = status.HTTP_200_OK):
        conn = db.connect(settings.db_path)
        try:
            back = _kiosk_screen(request, conn) is not None
        except sqlite3.Error:
            back = False
        finally:
            conn.close()
        return templates.TemplateResponse(request, "login.html", {"error": error, "kiosk_back": back},
                                          status_code=code)

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
        screen = _kiosk_screen(request, conn)
        if screen is not None:     # on a TV: going back to its page signs out (web/media.py screen_page)
            request.session["kiosk_screen"] = screen["id"]
        target = after if _safe_return(after) else "/"
        return RedirectResponse(target, status_code=status.HTTP_303_SEE_OTHER)

    @app.post("/logout")
    def logout(request: Request, conn: sqlite3.Connection = Depends(get_db)):
        # Sessions are signed cookies the server doesn't keep, so clearing this browser's wouldn't stop a copy
        # of it; bumping the epoch (as a password change does) ends every session of this account.
        if (user_id := request.session.get("user_id")) is not None:
            users.sign_out_everywhere(conn, user_id)
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
                     claude=claude, bfgguf=bfgguf.available(conn, user, app.state.directory) == "ok",
                     claude_key=claude and claude_chat.status(conn, settings, user["id"])["key"] == "set")

    @app.get("/files", response_class=HTMLResponse)
    def files_page(request: Request, user=Depends(page_user), conn: sqlite3.Connection = Depends(get_db)):
        # The file manager loads everything through the /api routes.
        return _page(request, "files.html", user, conn, active="files", trash_days=settings.trash_days)

    @app.get("/api/me")
    def me(request: Request, user=Depends(api_user), conn: sqlite3.Connection = Depends(get_db)):
        return {
            "username": user["username"],
            "role": user["role"],
            "is_admin": is_admin(request, conn, user),   # local admin, bootstrap name or the admin group
            "collections": [dict(c) for c in permissions.readable_collections(conn, user["id"])],
            # Erynne's tools (everyone), and whether they may change files anywhere (the agent group);
            # the server checks again on every question.
            "agent": app.state.agent.allowed(conn, user),
            "agent_full": app.state.agent.full(conn, user),
            # The experimental group, with BFGguf set up: chat offers the BFGguf toggle.
            "bfgguf": bfgguf.available(conn, user, app.state.directory) == "ok",
        }

    return app
