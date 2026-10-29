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


def test_security_feeds_parse_rss_kev_and_nvd_and_keep_to_their_keywords(conn, world):
    recent = (datetime.now(timezone.utc) - timedelta(days=2))
    old = (datetime.now(timezone.utc) - timedelta(days=40))
    rss = (f'<?xml version="1.0"?><rss><channel>'
           f'<item><title>FortiOS SSL-VPN flaw</title><link>https://fortiguard.example/1</link>'
           f'<description>&lt;p&gt;Patch FortiOS now&lt;/p&gt;</description><pubDate>{recent.strftime("%a, %d %b %Y %H:%M:%S +0000")}</pubDate></item>'
           f'<item><title>Unrelated product</title><link>https://x.example/2</link><pubDate>{recent.strftime("%a, %d %b %Y %H:%M:%S +0000")}</pubDate></item>'
           f'<item><title>Old FortiOS issue</title><link>https://x.example/3</link><pubDate>{old.strftime("%a, %d %b %Y %H:%M:%S +0000")}</pubDate></item>'
           f'</channel></rss>').encode()
    kev = {"vulnerabilities": [{"cveID": "CVE-2026-0001", "vendorProject": "Microsoft", "product": "Exchange",
                                "vulnerabilityName": "RCE", "dateAdded": recent.strftime("%Y-%m-%d"),
                                "shortDescription": "Remote code execution", "requiredAction": "Patch", "dueDate": "2026-11-15"}]}
    nvd = {"vulnerabilities": [{"cve": {"id": "CVE-2026-0002", "published": recent.strftime("%Y-%m-%dT%H:%M:%S.000"),
                                        "descriptions": [{"lang": "en", "value": "Opera PMS issue"}],
                                        "metrics": {"cvssMetricV31": [{"cvssData": {"baseScore": 9.8}}]}}}]}
    calls = []

    def handler(request):
        calls.append(request.url.host)
        if request.url.host == "rss.example":
            return httpx.Response(200, content=rss)
        if request.url.host == "kev.example":
            return httpx.Response(200, json=kev)
        return httpx.Response(200, json=nvd)
    briefings.TRANSPORT = httpx.MockTransport(handler)
    for name, url, kind, words in (("Fortinet", "https://rss.example/feed", "rss", "fortios"),
                                   ("KEV", "https://kev.example/kev.json", "json_kev", "exchange, opera"),
                                   ("NVD", "https://nvd.example/rest/json/cves/2.0", "nvd", "opera")):
        conn.execute("INSERT INTO automation_feeds(name, url, kind, keywords) VALUES (?, ?, ?, ?)", (name, url, kind, words))
    out, _ = world["tools"].security_feeds(conn, world["callers"]["alice"], since_days=7)
    by = {f["feed"]: f for f in out["feeds"]}
    assert [i["title"] for i in by["Fortinet"]["items"]] == ["FortiOS SSL-VPN flaw"]
    assert by["Fortinet"]["items"][0]["summary"] == "Patch FortiOS now"
    assert by["KEV"]["items"][0]["title"].startswith("CVE-2026-0001: Microsoft Exchange")
    assert by["NVD"]["items"][0]["cvss"] == 9.8
    world["tools"].security_feeds(conn, world["callers"]["alice"])
    assert len(calls) == 3                                              # cached for an hour
    with pytest.raises(ValueError):
        briefings._parse_xml_feed(b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "b">]><rss/>')
