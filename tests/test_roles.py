"""`mnemerynne serve --role sync|games`: a busy part of Mnemerynne in a process of its own (web/app.py
create_role_app), so it gets its own core instead of queuing with Files for the web app's one."""

import argparse

import pytest
from fastapi.testclient import TestClient

from fakes import FakeEmbedder, FakeShareChecker
from mnemerynne import cli, users
from mnemerynne.web.app import Services, create_role_app
from test_sync import _put, app_auth, client, conn, directory, root, settings  # noqa: F401  (a backup user)


def _signed_in(client, conn, name="jane"):
    """Sign in on the web app; the role's process must accept the same session cookie."""
    users.create_user(conn, name, "password123")
    assert client.post("/login", data={"username": name, "password": "password123"},
                       follow_redirects=False).status_code == 303
    return client.cookies


def _role(role, settings, directory=None, root=None):
    return TestClient(create_role_app(role, settings, Services(
        llm=None, embedder=FakeEmbedder(), directory=directory,
        share_checker=FakeShareChecker(root) if root is not None else None)))


def test_the_backup_process_serves_the_backup_api_and_nothing_else(client, conn, settings, directory, root, app_auth):
    auth, root_id = app_auth                               # the backup app's own token (it never uses a cookie)
    with _role("sync", settings, directory, root) as sync:
        assert sync.get("/healthz").json() == {"status": "ok", "role": "sync"}
        assert sync.get("/api/sync/roots", headers=auth).json() == client.get("/api/sync/roots", headers=auth).json()
        assert _put(sync, auth, root_id, "notes/today.txt", b"hello from the backup process").status_code in (200, 201)
        listed = client.get(f"/api/sync/roots/{root_id}/manifest", headers=auth).json()   # the web app sees it
        assert [f["path"] for f in listed["files"]] == ["notes/today.txt"]
        assert sync.get("/api/browse?collection_id=1").status_code == 404   # Files stays with the web app
        assert sync.get("/api/media/games").status_code == 404             # and so do the games


def test_the_games_process_serves_the_games_api_and_nothing_else(client, conn, settings, directory, root):
    import json

    from mnemerynne import accounts, media
    media.set_enabled(conn, True)                          # Media on, for Domain Users (jane's account is one)
    conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)",
                 (media.SETTINGS_KEY, json.dumps({"media_group": "Domain Users"})))
    cookies = _signed_in(client, conn)
    accounts.link(conn, users.get_user(conn, "jane")["id"], "jsmith", directory)
    with _role("games", settings, directory, root) as games:
        assert games.get("/healthz").json() == {"status": "ok", "role": "games"}
        assert games.get("/api/media/games").status_code == 401   # nobody signed in: refused, as on the web app
        games.cookies.update(cookies)                      # signed in on the web app...
        mine = games.get("/api/media/games")
        assert mine.status_code == 200, mine.text          # ...is signed in here: the same key, the same cookie
        assert mine.json() == client.get("/api/media/games").json()
        assert games.get("/api/sync/roots").status_code == 404
        assert games.get("/api/locations").status_code == 404
        assert games.app.state.balancer is not None        # Lumi's live tales reach the answer model from here


def test_a_role_process_has_the_web_apps_guard(client, conn, settings):
    """Cross-site writes are refused there too, and a busy database is a JSON 503 (one _guard for every role)."""
    _signed_in(client, conn)
    with _role("sync", settings) as sync:
        sync.cookies.update(client.cookies)
        refused = sync.post("/api/sync/roots", json={"computer": "PC", "label": "x"},
                            headers={"Origin": "https://evil.example"})
        assert refused.status_code == 403


def test_there_is_no_such_service(settings):
    with pytest.raises(ValueError, match="No service 'nonsense'"):
        create_role_app("nonsense", settings)


def test_every_service_has_its_own_port_its_paths_and_routers_that_import():
    """The registry drives the factory, the CLI and the deploy tools: a mistake here breaks all three."""
    from mnemerynne.services import factory, registry
    ports = [s.port for s in registry.SERVICES.values()]
    assert len(ports) == len(set(ports)) and 38210 not in ports and 38200 not in ports   # the web app's, the front door's
    for service in registry.SERVICES.values():
        assert bool(service.paths) == bool(service.routers)        # a background service (share) has neither
        assert all(p.startswith(("/", "= /")) for p in service.paths + service.web_paths)
        for ref in service.routers:
            assert factory._router(ref).routes                    # imports, and has routes
        for hook in service.hooks:
            assert hook in factory.hooks_mod.HOOKS


def test_processes_follow_the_profile_and_single_services_stay_single(monkeypatch):
    from mnemerynne.services import registry
    sync, games = registry.get("sync"), registry.get("games")
    monkeypatch.setenv("MNEMERYNNE_PROFILE", "nas")
    assert sync.processes() == 3 and games.processes() == 1
    monkeypatch.setenv("MNEMERYNNE_PROFILE", "ryzen")
    assert sync.processes() == 6 and games.processes() == 1      # live state: one, on any box
    monkeypatch.setenv("MNEMERYNNE_PROFILE", "something-else")
    assert sync.processes() == 3                                  # an unknown profile falls back to the NAS's


def test_the_games_run_as_one_process(monkeypatch):
    monkeypatch.setattr(cli, "get_settings", lambda: None)
    with pytest.raises(SystemExit, match="one process"):
        cli.cmd_serve(argparse.Namespace(role="games", port=38202, workers=2))
