"""The briefing and security tools: what's waiting, Mnemerynne updates and package vulnerabilities (admins),
Microsoft 365 security (admins, the app's own token) and the admins' security feeds."""

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest

from fakes import FakeDirectory, FakeEmbedder
from mnemerynne import api_tokens, briefings, m365, messages, updates, users
from mnemerynne.mcp_server import Tools

TENANT = "11111111-2222-3333-4444-555555555555"


@pytest.fixture(autouse=True)
def _hooks(monkeypatch):
    monkeypatch.setattr(briefings, "_audit", {"at": 0.0, "value": None})
    yield
    briefings.TRANSPORT = None
    briefings.AUDIT_OUTPUT = None


@pytest.fixture
def world(conn, settings):
    ids = {"alice": users.create_user(conn, "alice", "password123"),
           "boss": users.create_user(conn, "boss", "password123", role="admin")}
    directory = FakeDirectory({}, groups=[])
    tools = Tools(settings, lambda: FakeEmbedder(), lambda: directory)
    callers = {n: api_tokens.verify(conn, api_tokens.create(conn, uid, "t")[1]) for n, uid in ids.items()}
    return {"ids": ids, "tools": tools, "callers": callers}


def test_my_briefing_lists_what_is_waiting(conn, world):
    messages.system(conn, world["ids"]["alice"], "Your proposal was applied.")
    out, _ = world["tools"].my_briefing(conn, world["callers"]["alice"])
    assert [n["text"] for n in out["unread_notes"]] == ["Your proposal was applied."]
    assert out["announcements"] == [] and out["proposals_waiting"] == 0


def test_updates_and_package_vulnerabilities_are_for_admins(conn, world, monkeypatch):
    monkeypatch.setattr(updates, "check", lambda settings: {
        "current": {"short": "49dfe22"}, "status": "ahead", "ahead": 2, "error": None, "command": "update",
        "checked_at": "now", "commits": [{"short": "abc1234", "message": "Fix"}],
        "flags": {"migrations": ["mnemerynne/migrations/0051_x.sql"], "services": [], "packages": True,
                  "admin_command": False}})
    briefings.AUDIT_OUTPUT = json.dumps({"dependencies": [
        {"name": "httpx", "version": "0.28.1", "vulns": []},
        {"name": "lxml", "version": "6.1.3", "vulns": [{"id": "GHSA-xxxx", "fix_versions": ["6.1.4"],
                                                         "aliases": ["CVE-2026-1"], "description": "XXE"}]}]})
    with pytest.raises(Exception, match="Only Mnemerynne's admins"):
        world["tools"].mnemerynne_updates(conn, world["callers"]["alice"])
    out, _ = world["tools"].mnemerynne_updates(conn, world["callers"]["boss"])
    assert out["new_commits"] == 2 and out["touches"]["packages"] is True
    assert out["packages"]["vulnerable"] == [{"package": "lxml", "version": "6.1.3", "id": "GHSA-xxxx",
                                              "fixed_in": ["6.1.4"], "aliases": ["CVE-2026-1"], "summary": "XXE"}]


def test_pip_audit_is_found_beside_the_apps_python(tmp_path, monkeypatch):
    # systemd starts the app from .venv/bin without putting it on the PATH, so look next to the interpreter.
    name = "pip-audit.exe" if sys.platform == "win32" else "pip-audit"
    (tmp_path / name).write_text("")
    (tmp_path / name).chmod(0o755)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "python"))
    monkeypatch.setenv("PATH", "")
    assert Path(briefings.pip_audit_exe()).parent == tmp_path


def test_m365_security_reads_with_the_apps_token_and_names_a_missing_permission(conn, settings, world, monkeypatch):
    settings.m365_client_id, settings.m365_client_secret = "app-id", "app-secret"
    conn.execute("INSERT INTO m365_tenants(tenant_id, label, mode) VALUES (?, 'Gasco', 'cloud')", (TENANT,))
    seen = []

    def handler(request):
        seen.append(request)
        if request.url.host == "login.microsoftonline.com":
            return httpx.Response(200, json={"access_token": "app-token", "expires_in": 3600})
        if request.url.path.endswith("/security/alerts_v2"):
            return httpx.Response(200, json={"value": [{"title": "Impossible travel", "severity": "high",
                                                        "status": "new", "createdDateTime": "2026-10-28T08:00:00Z"}]})
        return httpx.Response(403, json={"error": {"code": "Authorization_RequestDenied", "message": "Insufficient privileges"}})
    monkeypatch.setattr(m365, "TRANSPORT", httpx.MockTransport(handler))
    m365._app_tokens.clear()
    with pytest.raises(Exception, match="Only Mnemerynne's admins"):
        world["tools"].m365_security(conn, world["callers"]["alice"])
    out, _ = world["tools"].m365_security(conn, world["callers"]["boss"], "alerts")
    assert out["tenants"][0]["open_alerts"][0]["title"] == "Impossible travel"
    assert seen[-1].headers["authorization"] == "Bearer app-token"
    out, _ = world["tools"].m365_security(conn, world["callers"]["boss"], "risky_users")
    assert "IdentityRiskyUser.Read.All" in out["tenants"][0]["error"]
    with pytest.raises(Exception, match="kind is one of"):
        world["tools"].m365_security(conn, world["callers"]["boss"], "everything")


def test_an_admins_own_rss_feed_keeps_to_its_keywords_beside_the_defaults(conn, world, automations_conn, settings):
    """An admin's own feed (the defaults are tests/test_security_feeds.py's): RSS entries kept to its keywords, one
    item per CVE they name, cached until the feed's next poll; a DOCTYPE is refused."""
    from mnemerynne import security_feeds

    recent = (datetime.now(timezone.utc) - timedelta(days=2))
    rss = (f'<?xml version="1.0"?><rss><channel>'
           f'<item><title>FortiOS SSL-VPN flaw CVE-2026-0003</title><link>https://fortiguard.example/1</link>'
           f'<description>&lt;p&gt;Patch FortiOS now&lt;/p&gt;</description><pubDate>{recent.strftime("%a, %d %b %Y %H:%M:%S +0000")}</pubDate></item>'
           f'<item><title>Unrelated product</title><link>https://x.example/2</link><pubDate>{recent.strftime("%a, %d %b %Y %H:%M:%S +0000")}</pubDate></item>'
           f'</channel></rss>').encode()
    calls = []

    def handler(request):
        calls.append(request.url.host)
        return httpx.Response(200, content=rss) if request.url.host == "rss.example" else httpx.Response(503)
    security_feeds.TRANSPORT = httpx.MockTransport(handler)
    security_feeds.ensure_defaults(automations_conn)
    automations_conn.execute("UPDATE automation_feeds SET enabled = 0")              # only ours, here
    automations_conn.execute("INSERT INTO automation_feeds(name, url, kind, keywords) VALUES "
                             "('Fortinet', 'https://rss.example/feed', 'rss', 'fortios')")
    out, _ = world["tools"].security_feeds(conn, world["callers"]["alice"], since_days=7)
    assert [(i["cve"], i["title"]) for i in out["items"]] == [("CVE-2026-0003", "FortiOS SSL-VPN flaw CVE-2026-0003")]
    assert out["items"][0]["summary"] == "Patch FortiOS now" and out["items"][0]["sources"] == ["rss"]
    world["tools"].security_feeds(conn, world["callers"]["alice"])
    assert calls == ["rss.example"]                                                  # not due again for a day
    with pytest.raises(security_feeds.FeedError):
        security_feeds.parse_xml_feed(b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "b">]><rss/>')
