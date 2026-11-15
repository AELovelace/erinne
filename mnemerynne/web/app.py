"""FastAPI application factory."""

from __future__ import annotations

import asyncio
import logging
import secrets
import sqlite3
from contextlib import AsyncExitStack, asynccontextmanager, nullcontext
from pathlib import Path

import importlib

import aiosqlite
from fastapi import Depends, FastAPI, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from mnemerynne import (identity, sync as sync_mod, balancer as balancer_mod, bfgguf, claude_chat, db, directory, editing, events, feedback as feedback_mod,
                        diagnostics, permissions, remote, rooms_bus, share,
                        sharepoint, theme as theme_mod,
                        users)
from mnemerynne.media import screens as media_screens, transcode as media_transcode
from mnemerynne import accounts, assistant_db, automations_run
from mnemerynne import games   # the built-in games' registry: Lumi's three apps each get a manifest (lumi_manifest)
from mnemerynne import health
from mnemerynne.automations_run import Scheduler
from mnemerynne.answer import ASSISTANT_NAME
from mnemerynne.auth import LoginThrottle
from mnemerynne.clients.embed import Embedder
from mnemerynne.clients.llm import LLM
from mnemerynne.clients.rerank import Reranker
from mnemerynne.config import Settings, get_settings
from mnemerynne.services import registry
from mnemerynne.web import security, theme as theme_pages
from mnemerynne.web.deps import api_user, get_db, is_admin, page_user
from mnemerynne.web import deps                      # close_feature_pools at shutdown
from mnemerynne.web.front import (_OAUTH_API, _SAFE_METHODS, Services, _cross_site, _guard,  # noqa: F401  (the
                                  _safe_return, make_templates)                             # shared front)

log = logging.getLogger("mnemerynne.web")

# The web app's routers, in the order they're mounted ("module:attribute", imported when mounted). While a service
# runs as its own process (MNEMERYNNE_SERVICES_ON) the routers wholly behind its paths are left out and never
# imported (registry.web_drops): the web app slims down to pages, sign-in, admin and settings (Phase 11).
WEB_ROUTERS = (
    "mnemerynne.web.files:router", "mnemerynne.web.chat:router", "mnemerynne.web.memories:router",
    "mnemerynne.web.scratchpad:router",           # the Scratchpad page: the agents' boards, and directions to them
    "mnemerynne.web.admin:router", "mnemerynne.web.tokens:router", "mnemerynne.web.apis:router",
    "mnemerynne.web.admin_apis:router", "mnemerynne.web.agent:router", "mnemerynne.web.m365:router",
    "mnemerynne.web.oauth:router", "mnemerynne.web.balancer:router", "mnemerynne.web.bfgguf:router",
    "mnemerynne.web.settings:router", "mnemerynne.web.theme:router", "mnemerynne.web.theme:admin_router",
    "mnemerynne.web.claude_links:router", "mnemerynne.web.help:router", "mnemerynne.web.editor:router",
    "mnemerynne.web.wiki:router", "mnemerynne.web.messages:router", "mnemerynne.web.rooms:router",
    "mnemerynne.web.rooms:admin_router", "mnemerynne.web.automations:router", "mnemerynne.web.automations:admin_router",
    "mnemerynne.web.media:router", "mnemerynne.web.media:admin_router", "mnemerynne.web.media:share_router",
    "mnemerynne.web.kiosk:router", "mnemerynne.web.soundcloud:router",   # Media → SoundCloud
    "mnemerynne.web.retro:router",                # Media → Retro: console games in the browser (media/retro.py)
    "mnemerynne.web.admin_notices:router", "mnemerynne.web.remote:router", "mnemerynne.web.admin_remote:router",
    "mnemerynne.web.sync:router", "mnemerynne.web.sync:admin_router",   # the desktop backup app (sync.py)
    "mnemerynne.web.games:router",                # Media → Games: the built-in games (games.py)
)


def _router(ref: str):
    module, _, attribute = ref.partition(":")
    return getattr(importlib.import_module(module), attribute)

_HERE = Path(__file__).parent
async def _answer_rooms_asks(app: FastAPI) -> None:
    """Erynne's answers for messages the rooms service took (web/rooms.py publishes "rooms:ask"): for as long as
    the web app runs. Only another process's asks come this way; the web app's own posts ask her directly."""
    while True:
        try:
            async for _, asked in events.subscribe("rooms:ask"):
                try:
                    app.state.rooms_erynne.ask(int(asked["user_id"]), asked["message"], bfgguf=bool(asked.get("bfgguf")))
                except Exception:
                    log.exception("Rooms: couldn't start Erynne's answer for another process's message")
        except asyncio.CancelledError:
            raise
        except Exception:
            log.warning("Rooms: lost the event bus for Erynne's asks; trying again.", exc_info=True)
            await asyncio.sleep(5)


def model_clients(settings: Settings, balancer) -> Services:
    """The model clients for a process that answers (the web app, the assistant service): the answer model through
    the balancer nodes (then MNEMERYNNE_LLM_URL), the embedder on this box, and the reranker if there is one."""
    return Services(
        llm=LLM.from_settings(settings, transport=balancer_mod.BalancedTransport(balancer, settings.llm_url)),
        embedder=Embedder(settings),
        reranker=Reranker(settings.rerank_url) if settings.rerank_url else None,
    )


@asynccontextmanager
async def assistant_parts(app: FastAPI, settings: Settings, svc: Services, balancer, *, testing: bool,
                          answer_rooms: bool):
    """Ask's pieces on app.state, for the web app and the assistant service alike: LangGraph's checkpointer and
    store (assistant.db), the agent, the graph, the memory writer, and Erynne for Rooms. Needs app.state.rooms_bus,
    .background, .embedder and .directory set first. `answer_rooms`: this process answers @Erynne in Rooms (its own
    posts directly, other processes' through "rooms:ask"); otherwise app.state.rooms_erynne is None and Rooms hands
    its asks on. Yields Erynne for Rooms either way: the scheduler posts automations' reports with her."""
    # Imported here: LangGraph and the agent are the heaviest of the app, and a slim web app (Phase 11) or a service
    # without Ask never loads them.
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
    from langgraph.store.sqlite.aio import AsyncSqliteStore

    from mnemerynne import memory
    from mnemerynne.agent import Agent
    from mnemerynne.graph import Deps, build_graph
    from mnemerynne.rooms_erynne import RoomErynne
    from mnemerynne.router import load_router

    # LangGraph's checkpointer (conversation state) and store (long-term memories) live in assistant.db with the
    # conversations, on their own connections. Autocommit (isolation_level=None): they issue their own
    # BEGIN/COMMIT, and Python's implicit transactions would otherwise hold the write lock.
    graph_file = assistant_db.path(settings.db_path)
    saver_conn = await aiosqlite.connect(graph_file, timeout=10, isolation_level=None)
    store_conn = await aiosqlite.connect(graph_file, timeout=10, isolation_level=None)
    try:
        checkpointer = AsyncSqliteSaver(saver_conn)
        await checkpointer.setup()                     # nothing to do on assistant.db: its migration made them
        store = AsyncSqliteStore(store_conn)
        await store.setup()
        app.state.checkpointer, app.state.store = checkpointer, store
        router = svc.router if testing else load_router(settings)
        log.info("Routing: %s.", f"{', '.join(router.labels)}" if router else "no router trained; everything is search")
        # Erynne with tools, for the agent group (agent.py); same model path as the answers.
        app.state.agent = Agent(settings, lambda: app.state.embedder, lambda: app.state.directory,
                                (lambda cfg: svc.agent_llm) if svc.agent_llm is not None else None, balancer,
                                (lambda key, model, timeout: svc.claude_llm) if svc.claude_llm is not None else None)
        try:
            app.state.graph = build_graph(Deps(settings, svc.llm, svc.embedder, svc.reranker, router,
                                               app.state.directory, app.state.agent), checkpointer, store)
            # Erynne answering @mentions in Rooms, reading only what everyone in the room may read.
            erynne = RoomErynne(
                settings, app.state.agent, app.state.rooms_bus, app.state.background, lambda: app.state.embedder,
                lambda: app.state.directory, balancer, getattr(app.state, "bfgguf_transport", None),
                (lambda cfg: svc.agent_llm) if svc.agent_llm is not None else None)
            app.state.rooms_erynne = erynne if answer_rooms else None
            app.state.memory_writer = memory.make_writer(settings, svc.llm, store, app.state.background)
            if answer_rooms:                           # the messages asking Erynne that another process took
                app.state.background.add(asyncio.create_task(_answer_rooms_asks(app)))
            yield erynne
        finally:
            await app.state.agent.aclose()
    finally:
        await saver_conn.close()
        await store_conn.close()


def create_role_app(role: str, settings: Settings | None = None, services: Services | None = None) -> FastAPI:
    """One service's own app (`mnemerynne serve --service sync|games`): see mnemerynne/services/. Kept under
    its first name for the code and tests that build it this way."""
    from mnemerynne.services.factory import create_service   # imported late: the factory uses _guard from here

    return create_service(role, settings, services)


def create_app(settings: Settings | None = None, services: Services | None = None) -> FastAPI:
    settings = settings or get_settings()
    diagnostics.install_log_capture()   # the last few hundred log lines, for the diagnose tool
    secret_key = security.check_secret_key(settings) or secrets.token_urlsafe(48)
    security.parse_networks(settings.admin_networks)   # a typo fails at start, not on every admin request
    if security.exposed(settings) and not settings.public_url:
        log.warning("MNEMERYNNE_PUBLIC_URL is not set; links and OAuth addresses follow the request's Host.")
    try:
        sync_mod.check_config(settings)     # the backup host must be a host of its own: its big-upload proxy settings stay off the site
    except ValueError as exc:
        raise security.InsecureConfig(str(exc)) from None

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        conn = db.connect(settings.db_path)
        try:
            version = db.migrate_all(settings.db_path, settings.embed_dim)["core"]   # core and every feature file
            app.state.looks = theme_mod.load_looks(conn, settings)   # the themes, as every page draws them
            app.state.instance = identity.load(conn, settings)      # which box this is, for the connector
            if app.state.mcp is not None:
                from mnemerynne import mcp_server
                mcp_server.apply_identity(app.state.mcp, app.state.instance)
            # Every inbox has an Agents folder for Claude; inboxes on an
            # unavailable share get theirs on first use.
            made = editing.ensure_all_agents(conn)
            if made:
                log.info("Made the Agents folder in %d inbox(es).", made)
            with assistant_db.on(conn) as chats, db.transaction(chats):   # ratings live in assistant.db
                archived = feedback_mod.backfill(chats)
            if archived:
                log.info("Archived %d rating(s) given before the feedback archive.", archived)
            cfg = balancer_mod.load_config(conn)
            nodes, node_key = cfg["nodes"], balancer_mod.api_key(settings, cfg)
        finally:
            conn.close()
        log.info("Database ready at %s (schema v%d).", settings.db_path, version)
        events.configure(settings.valkey_url)   # live events: Valkey when set (services), else inside this process
        swept = await asyncio.to_thread(db.sweep_orphans, settings.db_path)   # feature rows whose user/file went
        if swept:
            log.info("Removed rows whose user or file is gone: %s.", ", ".join(f"{k} ({n})" for k, n in swept.items()))

        # Balancer nodes (Settings): the answer model's requests go to the least busy one.
        balancer = (services.balancer if services and services.balancer is not None else balancer_mod.Balancer())
        balancer.configure(nodes, node_key)
        app.state.balancer = balancer
        app.state.rooms_bus = rooms_bus.Bus(settings.db_path)   # Rooms' live streams
        app.state.bfgguf_transport = services.bfgguf_transport if services else None
        if nodes:
            log.info("Answer model: %d balancer node(s), %s when none answers.", len(nodes), settings.llm_url)
        svc = services or model_clients(settings, balancer)
        app.state.background = set()
        app.state.embedder = svc.embedder
        # Ask (LangGraph, the agent, the graph, Erynne in Rooms, memories): the same pieces the assistant service
        # builds. While that service runs and Valkey carries Rooms' asks to it, Erynne answers Rooms from there.
        rooms_here = not (settings.runs_elsewhere("assistant") and events.shared())
        stack = AsyncExitStack()
        erynne = app.state.rooms_erynne = None
        if rooms_here or not settings.runs_elsewhere("assistant") or not settings.runs_elsewhere("automations"):
            erynne = await stack.enter_async_context(
                assistant_parts(app, settings, svc, balancer, testing=services is not None, answer_rooms=rooms_here))
        else:                                  # Ask, Automator and Rooms' answers all run elsewhere: no model here
            log.info("Ask, Automator and Erynne's Rooms answers run in their services: none of them loaded here.")
        # Media's ffmpeg sessions (media/transcode.py): none survive a restart. The media service has its own.
        app.state.media_sessions = None
        if not settings.runs_elsewhere("media"):
            app.state.media_sessions = media_transcode.Manager(settings)
            await asyncio.to_thread(app.state.media_sessions.startup)
        poller = asyncio.create_task(balancer.run())
        app.state.background.add(poller)
        # Rooms: other processes' new messages, typing and presence, to this process's streams (rooms_bus.listen).
        app.state.background.add(asyncio.create_task(app.state.rooms_bus.listen()))
        # Automator: scheduled automations run here, where the agent and its model path live, unless the
        # automations service runs them (Phase 9): then runs started here are queued for it (automations_run.start).
        app.state.automator = None
        if erynne is not None and not settings.runs_elsewhere("automations"):
            app.state.automator = Scheduler(settings, app.state.agent, erynne, app.state.rooms_bus,
                                            lambda: app.state.directory, app.state.background)
        automations_run.ACTIVE = app.state.automator   # for start() and the MCP tools (run now, a routine's report)
        if services is None:   # tests drive it with tick() instead
            if app.state.automator is not None:
                app.state.background.add(asyncio.create_task(app.state.automator.run_forever()))
            share.start_watching()   # permission changes on the share, as they happen (does nothing without one)
        if (rt := share.current()) is not None and not rt.owner:   # the share service walks: hear its changes
            app.state.background.add(asyncio.create_task(share.follow_changes()))
        try:
            async with (app.state.mcp.session_manager.run() if app.state.mcp is not None else nullcontext()):
                yield
        finally:
            share.stop_watching()
            if automations_run.ACTIVE is app.state.automator:
                automations_run.ACTIVE = None      # nothing reaches this scheduler once it's stopped
            for task in list(app.state.background):
                task.cancel()
            await asyncio.gather(*app.state.background, return_exceptions=True)
            await stack.aclose()                   # LangGraph's connections, the agent's clients
            app.state.db_pool.close()
            deps.close_feature_pools(app)          # games.db's and the other feature files'
            if app.state.remote_proxy is not None:
                await app.state.remote_proxy.aclose()
            await balancer.aclose()
            if app.state.media_sessions is not None:
                await asyncio.to_thread(app.state.media_sessions.shutdown)
            if services is None:
                svc.embedder.close()
                await svc.llm.aclose()
                if svc.reranker:
                    svc.reranker.close()

    app = FastAPI(title=ASSISTANT_NAME, lifespan=lifespan, docs_url=None, redoc_url=None)
    app.state.settings = settings
    app.state.db_pool = db.Pool(settings.db_path)   # the connections requests reuse (web/deps.py get_db)
    app.state.directory = (services.directory if services and services.directory
                           else directory.from_settings(settings))
    share.configure(settings, app.state.directory, services.share_checker if services else None)
    sharepoint.configure(settings)
    app.state.throttle = LoginThrottle(settings.login_max_failures, settings.login_lockout_seconds,
                                       settings.login_ip_max_failures)
    _guard(app, settings, secret_key)   # sign-in cookies, CSRF, limits, headers, error answers: every role's

    app.mount("/static", StaticFiles(directory=_HERE / "static"), name="static")

    app.state.templates = templates = make_templates(app)   # pages, and every page's top bar
    static = templates.env.globals["static"]                # versioned /static/ addresses (the manifests use it)
    dropped = registry.web_drops(settings.services_on.split(","))   # behind services running elsewhere
    for ref in WEB_ROUTERS:
        if ref not in dropped:
            app.include_router(_router(ref))
    # LAN apps served at /remote/<slug>/... (mnemerynne/remote.py); after the routers, so /remote itself is the page.
    app.state.remote_proxy = None
    if not settings.runs_elsewhere("remote"):      # the remote service proxies them while it runs
        app.state.remote_proxy = remote.RemoteProxy(settings, lambda: app.state.directory)
        app.mount(remote.PREFIX, app.state.remote_proxy, name="remote")

    # MCP for Claude Desktop: bearer-token auth, read-only tools (mnemerynne/mcp_server.py).
    app.state.instance = identity.from_settings(settings)   # the brand name joins once the database is open
    app.state.mcp = None
    if not settings.runs_elsewhere("mcp"):         # the mcp service answers Claude while it runs
        from mnemerynne import mcp_server          # the MCP SDK only where /mcp is served
        mcp = mcp_server.build_server(settings, lambda: app.state.embedder, lambda: app.state.directory,
                                      instance=lambda: app.state.instance)
        app.state.mcp = mcp
        app.add_route("/mcp", mcp_server.McpEndpoint(settings, mcp), methods=["GET", "POST", "DELETE"])

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

    @app.get("/media/games/{slug}/manifest.webmanifest", include_in_schema=False)
    def lumi_manifest(slug: str):
        """Makes each of Lumi's three apps (games.LUMI_APPS: Lumi Pet, Lumi Dungeons, Lumi's Garden) an app of its
        own, installed beside Erynne with its own icon and opening straight to it: its own id, and a scope covering
        only its page. Public, like the main manifest. Lumi Pet's icon files keep their first names (lumi-*.png);
        the other two are named after their slug (python/make_lumi_icons.py draws those)."""
        game = next((g for g in games.LUMI_APPS if g.slug == slug), None)
        if game is None:                                      # no other game is an app of its own
            raise HTTPException(status.HTTP_404_NOT_FOUND, "No such app")
        stem = "lumi" if slug == "lumi-pet" else slug         # the icon files' first name
        icons = [{"src": static(f"icons/{stem}-{file}"), "sizes": size, "type": "image/png", "purpose": purpose}
                 for file, size, purpose in (("192.png", "192x192", "any"), ("512.png", "512x512", "any"),
                                             ("maskable-512.png", "512x512", "maskable"))]
        page = f"/media/games/{slug}"
        return JSONResponse({
            "id": page, "name": game.name, "short_name": game.name, "start_url": page, "scope": page, "display": "standalone",
            "description": game.description,
            "background_color": "#0D0614", "theme_color": "#1a0f2e", "icons": icons,      # her room's night plum
            "shortcuts": [{"name": other.name, "url": f"/media/games/{other.slug}"} for other in games.LUMI_APPS if other is not game],
        }, media_type="application/manifest+json", headers={"Cache-Control": "no-cache"})

    @app.get("/media/panel/manifest.webmanifest", include_in_schema=False)
    def remote_manifest():
        """Makes the phone's remote (/media/panel) an app of its own, installed beside Erynne with its own
        icon: its own id and a scope covering only its page. Public, like the main manifest."""
        name = theme_pages.looks_for(app).brand.get("name") or ASSISTANT_NAME
        icons = [{"src": static(f"icons/{file}"), "sizes": size, "type": "image/png", "purpose": purpose}
                 for file, size, purpose in (("remote-192.png", "192x192", "any"), ("remote-512.png", "512x512", "any"),
                                             ("remote-maskable-512.png", "512x512", "maskable"))]
        return JSONResponse({
            "id": "/media/panel", "name": f"{name} Remote", "short_name": "Remote", "start_url": "/media/panel",
            "scope": "/media/panel", "display": "standalone", "orientation": "portrait",
            "background_color": "#0D0614", "theme_color": "#0D0614", "icons": icons,   # the remote panel's night plum
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
        conn = app.state.db_pool.get()
        try:
            back = _kiosk_screen(request, conn) is not None
        except sqlite3.Error:
            back = False
        finally:
            app.state.db_pool.put(conn)
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

    def _agent_allowed(user) -> bool:
        """Erynne's tools: every active account (Agent.allowed). Asked without the agent, which the web app doesn't
        build while Ask, Automator and Rooms' answers all run in services of their own (Valkey on)."""
        return bool(user["active"])

    def _agent_full(conn: sqlite3.Connection, user) -> bool:
        """May change files anywhere they may edit (the agent group; Agent.full), asked without the agent."""
        return accounts.is_agent_user(conn, user, app.state.directory)

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
                     agent=_agent_allowed(user), agent_full=_agent_full(conn, user),
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
            "agent": _agent_allowed(user),
            "agent_full": _agent_full(conn, user),
            # The experimental group, with BFGguf set up: chat offers the BFGguf toggle.
            "bfgguf": bfgguf.available(conn, user, app.state.directory) == "ok",
        }

    return app
