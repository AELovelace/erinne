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


HOOKS = {"balancer": balancer, "rooms": rooms}
