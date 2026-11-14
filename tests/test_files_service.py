"""The files service (docs/plans/services-rewrite.md, Phase 6b): Files, the editor and the wiki in processes of their
own (three on the NAS), sharing core's files and folders with the web app. Nothing lives in one process's memory,
so any of them can answer any request: a zip link made by one downloads from another."""

from __future__ import annotations

from fastapi.testclient import TestClient

from fakes import FakeEmbedder
from mnemerynne.services import nginx, registry
from mnemerynne.web.app import Services, create_role_app
from test_files_api import MD, PDF, _login, _upload, accounts  # noqa: F401


def _files(settings):
    return TestClient(create_role_app("files", settings, Services(llm=None, embedder=FakeEmbedder())))


def test_the_files_process_serves_files_the_editor_and_the_wiki_and_nothing_else(client, accounts, settings):
    _login(client, "alice")
    cookies = dict(client.cookies)
    with _files(settings) as service:
        assert service.get("/healthz").json() == {"status": "ok", "role": "files"}
        service.cookies.update(cookies)                                   # signed in on the web app: here too
        made = _upload(service, MD, "checkout.md")
        assert made.status_code == 201, made.text
        file_id = made.json()["id"]
        assert [f["name"] for f in service.get("/api/files").json()["files"]] == ["checkout.md"]
        assert client.get("/api/files").json()["files"][0]["id"] == file_id   # the web app sees it too: one core
        viewer = service.get(f"/files/{file_id}/view")
        assert viewer.status_code == 200 and "<html" in viewer.text.lower()   # its pages, with the web app's top bar
        editor = service.get(f"/edit/{file_id}")
        assert editor.status_code == 200 and "<html" in editor.text.lower()
        assert service.get(f"/api/files/{file_id}/text").json()["content"].startswith("# Checkout")
        assert service.get("/wiki").status_code == 200
        assert service.get("/files").status_code == 404                   # the Files page stays with the web app
        assert service.get("/api/rooms").status_code == 404               # and Rooms with theirs
        assert service.get("/media").status_code == 404


def test_a_zip_link_from_one_process_downloads_from_another(client, accounts, settings):
    _login(client, "alice")
    _upload(client, MD, "checkout.md")
    _upload(client, PDF, "audit.pdf")
    ids = [f["id"] for f in client.get("/api/files").json()["files"]]
    cookies = dict(client.cookies)
    with _files(settings) as service:
        service.cookies.update(cookies)
        asked = service.post("/api/downloads", json={"items": [{"type": "file", "id": i} for i in ids]})
        assert asked.status_code == 200, asked.text
        url = asked.json()["url"]
    got = client.get(url)                                                 # signed, not remembered: any process
    assert got.status_code == 200 and got.headers["content-type"] == "application/zip"
    assert got.content[:2] == b"PK"


def test_files_runs_as_several_processes_and_nginx_sends_files_paths_to_it():
    files = registry.SERVICES["files"]
    assert not files.single and files.processes("nas") == 3 and files.processes("ryzen") == 6
    assert files.port not in {s.port for s in registry.SERVICES.values() if s.name != "files"}
    conf = nginx.render("192.168.1.5:38200", running=["files"])

    def upstream(path):
        part = conf[conf.index(f"location {path} {{"):]
        return part[part.index("proxy_pass http://") + 18:part.index(";")]
    for path in ("/api/files", "/api/browse", "/api/items/", "/files/", "/edit/", "/wiki", "/api/wiki"):
        assert upstream(path) == "mnemerynne_files", path
    assert "location /files {" not in conf and "location = /files {" not in conf   # the Files page: the web app's
    stopped = nginx.render("192.168.1.5:38200", running=[])
    assert "mnemerynne_files;" not in stopped                             # off: every path back to the web app
