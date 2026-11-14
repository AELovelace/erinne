"""The web app slims down (docs/plans/services-rewrite.md, Phase 11): while services run as processes of their own
(MNEMERYNNE_SERVICES_ON), it serves pages, sign-in, admin and settings, leaves out the routers wholly behind those
services' paths (never importing them), and builds Ask's, Media's, MCP's and Remote's pieces only where it still
serves them. Every service imports the light shared front (web/front.py), not the whole web app."""

import re
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from fakes import FakeEmbedder
from mnemerynne import events
from mnemerynne.services import factory, registry
from mnemerynne.web.app import WEB_ROUTERS, Services, create_app

HEAVY = ("langgraph", "mnemerynne.agent", "mnemerynne.graph", "mcp", "anthropic")
ROOT = Path(__file__).resolve().parent.parent


def _loaded(code: str) -> list[str]:
    """Run `code` in a fresh Python (this one has imported everything already); the heavy modules it loaded."""
    probe = code + f"\nimport sys\nprint(','.join(m for m in {HEAVY!r} if m in sys.modules))"
    out = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True, cwd=ROOT, timeout=120)
    assert out.returncode == 0, out.stderr
    last = (out.stdout.strip().splitlines() or [""])[-1]
    return [m for m in last.split(",") if m]


def _paths(app) -> set[str]:
    """Every route's path, through the routers FastAPI wraps when they're included (original_router)."""
    out = set()
    for route in app.router.routes:
        inner = getattr(route, "original_router", None)
        out |= {getattr(r, "path", "") for r in inner.routes} if inner is not None else {getattr(route, "path", "")}
    return out


def test_a_slim_web_app_loads_no_model_libraries_and_neither_does_a_light_service(tmp_path):
    slim = (f"import tempfile\nfrom mnemerynne.config import Settings\nfrom mnemerynne.services import registry\n"
            f"from mnemerynne.web.app import create_app\n"
            f"create_app(Settings(_env_file=None, data_dir=r'{tmp_path}', secret_key='x' * 40, embed_dim=4,"
            f" services_on=','.join(registry.SERVICES)))")
    assert _loaded(slim) == []                                        # no LangGraph, agent, MCP SDK or Anthropic SDK
    for name in ("files", "sync", "media", "share"):                  # each service: the front and its own routers
        service = (f"from mnemerynne.services import factory, registry\n"
                   f"[factory._router(r) for r in registry.SERVICES[{name!r}].routers]")
        assert _loaded(service) == [], name


def test_the_slim_web_app_serves_the_pages_and_leaves_the_services_paths_out(settings, conn):
    every = settings.model_copy(update={"services_on": ",".join(registry.SERVICES)})
    with TestClient(create_app(every, Services(llm=None, embedder=FakeEmbedder()))) as c:
        assert c.get("/login").status_code == 200 and c.get("/healthz").json()["status"] == "ok"
        paths = _paths(c.app)
        for page in ("/files", "/media", "/rooms", "/automations", "/remote", "/preferences", "/memories"):
            assert page in paths, page                                # pages stay with the web app
        for gone in ("/api/ask", "/api/files", "/mcp", "/api/browse", "/wiki", "/edit/{file_id}"):
            assert gone not in paths, gone                            # their services serve these
        assert c.app.state.mcp is None and c.app.state.remote_proxy is None and c.app.state.media_sessions is None
        assert c.app.state.automator is None


@pytest.mark.parametrize("shared, built", [(True, False), (False, True)])
def test_the_model_is_built_only_while_something_here_still_asks_it(settings, conn, monkeypatch, shared, built):
    """Ask and Automator elsewhere: the web app still answers @Erynne in Rooms unless Valkey carries those asks to
    the assistant service, and only then builds no agent at all."""
    monkeypatch.setattr(events, "shared", lambda: shared)
    elsewhere = settings.model_copy(update={"services_on": "assistant,automations"})
    with TestClient(create_app(elsewhere, Services(llm=None, embedder=FakeEmbedder()))) as c:
        assert hasattr(c.app.state, "agent") is built
        assert (c.app.state.rooms_erynne is not None) is built
        assert c.app.state.automator is None                          # the automations service's


def test_with_valkey_and_every_service_on_signed_in_pages_still_open(settings, conn, monkeypatch):
    """The NAS: Valkey on and every service running, so the web app builds no agent. Ask's page and /api/me
    asked that agent who may use Erynne's tools and answered 500 to everyone signed in."""
    from mnemerynne import users
    monkeypatch.setattr(events, "shared", lambda: True)
    users.create_user(conn, "alice", "password123")
    every = settings.model_copy(update={"services_on": ",".join(registry.SERVICES)})
    with TestClient(create_app(every, Services(llm=None, embedder=FakeEmbedder()))) as c:
        assert not hasattr(c.app.state, "agent")                      # nothing here asks the model
        c.post("/login", data={"username": "alice", "password": "password123"})
        assert c.get("/").status_code == 200                          # Ask's page
        me = c.get("/api/me")
        assert me.status_code == 200 and me.json()["agent"] is True and me.json()["agent_full"] is False
        for page in ("/files", "/media", "/rooms", "/automations", "/remote", "/preferences", "/memories"):
            assert c.get(page).status_code < 500, page                # open, or off in these settings: never a crash


def test_the_files_service_offers_erynnes_edit_column_without_an_agent(settings, conn):
    """/api/browse's AI edit column: every active account, Claude or not. It asked app.state.agent, which the
    files service never builds, so only people with Claude connected saw it there."""
    from mnemerynne import users
    alice = users.create_user(conn, "alice", "password123")
    library = conn.execute("SELECT id FROM collections WHERE kind = 'private' AND owner_user_id = ?",
                           (alice,)).fetchone()[0]                   # her own File box
    from mnemerynne import db
    db.migrate_all(settings.db_path, settings.embed_dim)              # as the web app's start leaves it
    with TestClient(create_app(settings, Services(llm=None, embedder=FakeEmbedder()))) as web:
        web.post("/login", data={"username": "alice", "password": "password123"})   # sign-in is the web app's
        cookies = dict(web.cookies)
    with TestClient(factory.create_service("files", settings, Services(llm=None, embedder=FakeEmbedder())),
                    cookies=cookies) as c:
        browse = c.get("/api/browse", params={"collection_id": library})
        assert browse.status_code == 200, browse.text
        assert browse.json()["ai"] is True


def _covered(path: str, paths: tuple[str, ...]) -> bool:
    for p in paths:
        if p.startswith("= ") and path == p[2:]:
            return True
        if p.startswith("~ ") and re.match(p[2:], re.sub(r"\{[^}]+\}", "1", path)):
            return True
        if not p.startswith(("= ", "~ ")) and path.startswith(p):
            return True
    return False


def test_the_routers_the_web_app_drops_are_wholly_behind_their_service_and_the_ones_it_keeps_are_not():
    """registry.web_keeps can't drift: a dropped router with a page left uncovered would lose that page; a kept one
    with nothing of its own would load for nothing."""
    for service in registry.SERVICES.values():
        dropped = registry.web_drops([service.name])
        for ref in service.routers:
            routes = [getattr(r, "path", "") for r in factory._router(ref).routes]
            uncovered = [p for p in routes if not _covered(p, service.paths)]
            if ref in dropped:
                assert not uncovered, (service.name, ref, uncovered)
            else:
                assert uncovered, (service.name, ref)
        for ref in dropped:
            assert ref in WEB_ROUTERS or ref.endswith(("mcp_route:router", "remote_route:router")), ref
