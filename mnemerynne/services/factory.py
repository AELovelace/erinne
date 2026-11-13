"""Builds a service's app from its row in the registry (`mnemerynne serve --service <name>`).

Every service gets the same front as the all-in-one web app (web/app.py _guard: the sign-in cookie with the
shared key, the cross-site check, limits, headers, the backup host's fence, the slow-request log, the JSON 503
for a busy database), the database pool and the share's permissions, its routers, its start-up hooks, and
/healthz with its name. The all-in-one web app still serves every path too, so a service can be started, and
the web server pointed at it, one at a time.
"""

from __future__ import annotations

import asyncio
import importlib
import logging
import os
import secrets
from contextlib import AsyncExitStack, asynccontextmanager

from fastapi import FastAPI

from mnemerynne import db, directory, events, share
from mnemerynne.config import Settings, get_settings
from mnemerynne.services import hooks as hooks_mod, registry

log = logging.getLogger("mnemerynne.services")
ENV_SERVICE = "MNEMERYNNE_SERVICE"       # which service uvicorn's worker processes build (service_app)


def _router(ref: str):
    """'module:attribute' -> the APIRouter, imported now."""
    module, _, attribute = ref.partition(":")
    return getattr(importlib.import_module(module), attribute)


def create_service(name: str, settings: Settings | None = None, services=None) -> FastAPI:
    """The app for one service. `services` is web.app.Services in tests (fakes), None in production."""
    from mnemerynne.web.app import ASSISTANT_NAME, _guard   # the web app's front, shared (imported late: it imports us)
    from mnemerynne.web import security

    service = registry.get(name)
    settings = settings or get_settings()
    secret_key = security.check_secret_key(settings) or secrets.token_urlsafe(48)   # the same key: the same cookies

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # The web app migrates the database (db.migrate_all); a service waits for it, so two processes never
        # apply the same migration.
        version = await asyncio.to_thread(db.wait_migrated, settings.db_path)
        bus = events.configure(settings.valkey_url)        # live events with the other services
        if isinstance(bus, events.LocalBus):
            log.warning("%s service: no Valkey (MNEMERYNNE_VALKEY_URL), so its live events stay in this process.",
                        name.capitalize())
        async with AsyncExitStack() as stack:
            stack.push_async_callback(bus.aclose)
            for hook in service.hooks:
                await stack.enter_async_context(hooks_mod.HOOKS[hook](app, settings, services))
            log.info("%s service ready: %s (schema v%d).", name.capitalize(), service.serves, version)
            try:
                yield
            finally:
                app.state.db_pool.close()

    app = FastAPI(title=f"{ASSISTANT_NAME} {name}", lifespan=lifespan, docs_url=None, redoc_url=None)
    app.state.settings = settings
    app.state.service = service
    app.state.role = name                                   # what older code and tests read
    app.state.db_pool = db.Pool(settings.db_path)
    app.state.directory = (services.directory if services is not None and services.directory
                           else directory.from_settings(settings))
    share.configure(settings, app.state.directory,                 # permissions; the watcher runs elsewhere
                    services.share_checker if services is not None else None)
    _guard(app, settings, secret_key)
    for ref in service.routers:
        app.include_router(_router(ref))

    @app.get("/healthz", include_in_schema=False)
    def healthz():
        return {"status": "ok", "role": name}               # update.sh, front-door.sh and monitors

    return app


def _logging() -> None:
    """A worker process uvicorn starts doesn't run the CLI's logging setup: give it the same."""
    if not logging.getLogger().handlers:
        logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")


def service_app() -> FastAPI:
    """uvicorn's factory for every worker process of a service: which one comes from MNEMERYNNE_SERVICE (the CLI
    sets it before starting them)."""
    _logging()
    return create_service(os.environ[ENV_SERVICE])
