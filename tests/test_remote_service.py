"""The remote service (docs/plans/services-rewrite.md, Phase 10): the LAN apps' proxy in a process of its own, with
the web app's sign-in and its websockets through the front door. The Remote page and its list stay with the web app."""

import httpx
from fastapi.testclient import TestClient

from fakes import FakeEmbedder
from mnemerynne import remote
from mnemerynne.services import nginx, registry
from mnemerynne.web.app import Services, create_role_app
from test_remote import _app, _hooks, _web, directory, people  # noqa: F401


def test_the_remote_process_proxies_the_lan_apps_for_the_web_apps_sign_in(conn, settings, people, directory):
    _app(conn, base="http://10.0.5.20:9180")
    seen = []

    async def handler(request):
        seen.append(request)
        return httpx.Response(200, headers={"content-type": "text/plain"}, content=b"balancer ok")
    remote.TRANSPORT = httpx.MockTransport(handler)
    try:
        with _web(settings, "alice", directory) as web:
            cookies = dict(web.cookies)
        service = create_role_app("remote", settings, Services(llm=None, embedder=FakeEmbedder(), directory=directory))
        with TestClient(service) as c:
            assert c.get("/healthz").json() == {"status": "ok", "role": "remote"}
            assert c.get("/remote/balancer/status", follow_redirects=False).status_code == 303   # sign in first
            c.cookies.update(cookies)
            page = c.get("/remote/balancer/status?x=1")
            assert page.status_code == 200 and page.text == "balancer ok"
            assert str(seen[-1].url) == "http://10.0.5.20:9180/status?x=1"
            assert seen[-1].headers["x-forwarded-prefix"] == "/remote/balancer"
            assert c.get("/remote/").status_code == 404 and c.get("/api/files").status_code == 404   # the page: web's
    finally:
        remote.TRANSPORT = None


def test_remote_carries_websockets_through_the_front_door():
    service = registry.SERVICES["remote"]
    assert service.websockets and service.port not in {s.port for s in registry.SERVICES.values()
                                                        if s.name != "remote"}
    conf = nginx.render("192.168.1.5:38200", running=["remote"])
    part = conf[conf.index("location /remote/ {"):]
    part = part[:part.index("}")]
    assert "proxy_pass http://mnemerynne_remote;" in part and "proxy_set_header Upgrade $http_upgrade;" in part
    assert conf.count("location /remote/ {") == 1
    assert "location = /remote {" in conf                               # the Remote page: the web app's
