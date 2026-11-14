"""Start-up pieces a service may need (registry.Service.hooks), each an async context manager:
it gets the app, the settings and the test Services, sets up what it provides on app.state, and tears it down
when the service stops. The all-in-one web app sets the same things up itself (web/app.py create_app)."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

from mnemerynne import balancer as balancer_mod, db


@asynccontextmanager
async def balancer(app, settings, services):
    """app.state.balancer: the answer model's requests go to the least busy balancer node (Settings → Balancer).
    For a service that asks the model (the games' live tales)."""
    conn = db.connect(settings.db_path)
    try:
        cfg = balancer_mod.load_config(conn)
        nodes, node_key = cfg["nodes"], balancer_mod.api_key(settings, cfg)
    finally:
        conn.close()
    found = services.balancer if services is not None and services.balancer is not None else balancer_mod.Balancer()
    found.configure(nodes, node_key)
    app.state.balancer = found
    poller = asyncio.create_task(found.run())      # keeps the nodes' health current
    try:
        yield
    finally:
        poller.cancel()
        await asyncio.gather(poller, return_exceptions=True)
        await found.aclose()


@asynccontextmanager
async def rooms(app, settings, services):
    """app.state.rooms_bus: Rooms' live streams in this process, hearing the other processes' new messages, typing
    and presence (rooms_bus.Bus.listen). No Erynne here: a message that asks her goes to the web app ("rooms:ask")."""
    from mnemerynne import rooms_bus

    app.state.rooms_bus = rooms_bus.Bus(settings.db_path)
    listener = asyncio.create_task(app.state.rooms_bus.listen())
    try:
        yield
    finally:
        listener.cancel()
        await asyncio.gather(listener, return_exceptions=True)


@asynccontextmanager
async def pages(app, settings, services):
    """app.state.templates: the web app's pages with their top bar (web/app.make_templates), for a service that
    serves pages. A theme saved in the web app ("theme:changed") is picked up here at the next page."""
    from mnemerynne import events
    from mnemerynne.web.app import make_templates

    app.state.templates = make_templates(app)

    async def follow_themes():
        while True:
            try:
                async for _ in events.subscribe("theme:changed"):
                    app.state.looks = None                  # reloaded by the next page (theme.looks_for)
            except asyncio.CancelledError:
                raise
            except Exception:
                await asyncio.sleep(5)

    follower = asyncio.create_task(follow_themes())
    try:
        yield
    finally:
        follower.cancel()
        await asyncio.gather(follower, return_exceptions=True)


@asynccontextmanager
async def media(app, settings, services):
    """app.state.media_sessions: this process's transcoding sessions (its own folder, cleared at start)."""
    from mnemerynne.media import transcode

    app.state.media_sessions = transcode.Manager(settings, "media")
    await asyncio.to_thread(app.state.media_sessions.startup)
    try:
        yield
    finally:
        await asyncio.to_thread(app.state.media_sessions.shutdown)


@asynccontextmanager
async def share(app, settings, services):
    """The share service: this process walks the share's permissions for everyone (share.serve_walks) and runs the
    share watcher, which the web app leaves to it."""
    from mnemerynne import share as share_mod

    share_mod.configure(settings, app.state.directory, services.share_checker if services else None, owner=True)
    share_mod.start_watching()
    walker = asyncio.create_task(share_mod.serve_walks())
    try:
        yield
    finally:
        walker.cancel()
        await asyncio.gather(walker, return_exceptions=True)
        share_mod.stop_watching()


@asynccontextmanager
async def _answering(app, settings, services, *, answer_rooms: bool):
    """The model clients, then the same Ask pieces the web app builds (web/app.py assistant_parts: LangGraph on
    assistant.db, the agent, the graph, memories, Erynne for Rooms), for a service that asks the model. Needs the
    balancer and rooms hooks first. Yields Erynne for Rooms."""
    from mnemerynne.web.app import assistant_parts, model_clients

    app.state.background = set()                   # answers being written, memories being saved, runs
    app.state.bfgguf_transport = services.bfgguf_transport if services is not None else None
    svc = services if services is not None else model_clients(settings, app.state.balancer)
    app.state.embedder = svc.embedder
    try:
        async with assistant_parts(app, settings, svc, app.state.balancer, testing=services is not None,
                                   answer_rooms=answer_rooms) as erynne:
            try:
                yield erynne
            finally:                               # stopped before the agent and LangGraph's connections close
                for task in list(app.state.background):
                    task.cancel()
                await asyncio.gather(*app.state.background, return_exceptions=True)
    finally:
        if services is None:
            svc.embedder.close()
            await svc.llm.aclose()
            if svc.reranker:
                svc.reranker.close()


@asynccontextmanager
async def mcp(app, settings, services):
    """Claude's MCP endpoint (web/mcp_route.py): the embedder for searching, which box this is (its name for Claude,
    reloaded when an admin changes the brand: "theme:changed"), the server and its stateless session manager. No
    model: the tools search, read and propose; Automator's runs are started where the scheduler runs."""
    from mnemerynne import db, events, identity, mcp_server
    from mnemerynne.clients.embed import Embedder

    embedder = services.embedder if services is not None else Embedder(settings)
    app.state.embedder = embedder

    def load_identity() -> None:
        conn = db.connect(settings.db_path)
        try:
            app.state.instance = identity.load(conn, settings)
        finally:
            conn.close()

    load_identity()
    server = mcp_server.build_server(settings, lambda: app.state.embedder, lambda: app.state.directory,
                                     instance=lambda: app.state.instance)
    app.state.mcp = server
    app.state.mcp_endpoint = mcp_server.McpEndpoint(settings, server)

    async def follow_brand():
        while True:
            try:
                async for _ in events.subscribe("theme:changed"):
                    await asyncio.to_thread(load_identity)
                    mcp_server.apply_identity(server, app.state.instance)
            except asyncio.CancelledError:
                raise
            except Exception:
                await asyncio.sleep(5)

    follower = asyncio.create_task(follow_brand())
    try:
        async with server.session_manager.run():
            yield
    finally:
        follower.cancel()
        await asyncio.gather(follower, return_exceptions=True)
        if services is None:
            embedder.close()


@asynccontextmanager
async def assistant(app, settings, services):
    """Ask in a process of its own (_answering). With Valkey it answers @Erynne for every process's Rooms
    ("rooms:ask"), and the web app stops (front-door.sh tells it, MNEMERYNNE_SERVICES_ON); without, the web app
    keeps answering Rooms."""
    from mnemerynne import events

    async with _answering(app, settings, services, answer_rooms=events.shared()):
        yield


@asynccontextmanager
async def automations(app, settings, services):
    """Automator's scheduler in its own process (exactly one: it ticks), with Erynne's agent to run automations
    and Erynne for Rooms to post reports. Runs other processes start, and routine reports they save, are queued
    in automations.db and claimed here ("automations:wake", else the next tick). The web app leaves its own
    scheduler off while this runs (MNEMERYNNE_SERVICES_ON)."""
    from mnemerynne import automations_run

    async with _answering(app, settings, services, answer_rooms=False) as erynne:
        scheduler = automations_run.Scheduler(settings, app.state.agent, erynne, app.state.rooms_bus,
                                              lambda: app.state.directory, app.state.background)
        app.state.automator = scheduler
        automations_run.ACTIVE = scheduler
        if services is None:                       # tests drive it with tick()
            app.state.background.add(asyncio.create_task(scheduler.run_forever()))
        try:
            yield
        finally:
            if automations_run.ACTIVE is scheduler:
                automations_run.ACTIVE = None


HOOKS = {"balancer": balancer, "rooms": rooms, "pages": pages, "media": media, "share": share,
         "assistant": assistant, "mcp": mcp, "automations": automations}
