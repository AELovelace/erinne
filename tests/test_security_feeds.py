"""Security feeds (T-21, mnemerynne/security_feeds.py): the default feeds and products that ship with Mnemerynne, one
item per CVE across sources, the affects_us join with Wazuh's installed packages, feed_status, the Wazuh push, and
the security_feeds tool's parameters. Everything outside is a fake: the feeds (security_feeds.TRANSPORT) and Wazuh's
indexer (apis.TRANSPORT). These cover the plan's acceptance checks 2-6."""

import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from fastapi.testclient import TestClient

from fakes import FakeDirectory, FakeEmbedder
from mnemerynne import accounts, api_tokens, apis, security_feeds as sf, users
from mnemerynne.mcp_server import Tools
from mnemerynne.web.app import Services, create_app

RECENT = datetime.now(timezone.utc) - timedelta(days=2)
ISO = RECENT.strftime("%Y-%m-%dT%H:%M:%S.000")

# ------------------------------------------------------------------ fixtures: the outside world
KEV = {"vulnerabilities": [
    {"cveID": "CVE-2026-50001", "vendorProject": "Mozilla", "product": "Firefox",
     "vulnerabilityName": "Mozilla Firefox Use-After-Free", "dateAdded": RECENT.strftime("%Y-%m-%d"),
     "shortDescription": "Use-after-free in the DOM.", "requiredAction": "Update.", "dueDate": "2026-12-10",
     "knownRansomwareCampaignUse": "Known"},
    {"cveID": "CVE-2026-50002", "vendorProject": "Acme", "product": "Widget", "vulnerabilityName": "Acme RCE",
     "dateAdded": RECENT.strftime("%Y-%m-%d"), "dueDate": "2026-12-10", "knownRansomwareCampaignUse": "Unknown"}]}


def _nvd_cve(cve, criteria, fixed, score, published=ISO):
    return {"cve": {"id": cve, "published": published, "lastModified": ISO,
                    "descriptions": [{"lang": "en", "value": f"{cve} description"}],
                    "metrics": {"cvssMetricV31": [{"type": "Primary", "cvssData": {
                        "baseScore": score, "vectorString": "CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H"}}]},
                    "configurations": [{"nodes": [{"cpeMatch": [{"vulnerable": True, "criteria": criteria,
                                                                 "versionEndExcluding": fixed}]}]}]}}


NVD = {"vulnerabilities": [
    _nvd_cve("CVE-2026-42945", "cpe:2.3:a:f5:nginx:*:*:*:*:open_source:*:*:*", "1.30.1", 8.1),
    _nvd_cve("CVE-2026-50001", "cpe:2.3:a:mozilla:firefox:*:*:*:*:*:*:*:*", "147.0", 8.8),
    _nvd_cve("CVE-2024-32002", "cpe:2.3:a:git-scm:git:*:*:*:*:*:*:*:*", "2.45.1", 9.0, "2024-05-14T19:15:10.773"),
    _nvd_cve("CVE-2026-70000", "cpe:2.3:a:acme:widget:*:*:*:*:*:*:*:*", "2.0", 9.8),     # not ours
    _nvd_cve("CVE-2026-70001", "cpe:2.3:a:f5:nginx:*:*:*:*:*:*:*:*", "1.31.0", 5.0)],   # ours, but below 7
    "totalResults": 5, "resultsPerPage": 5}

GITHUB_GIT = [{"ghsa_id": "GHSA-8h77-4q3w-gfgv", "cve_id": "CVE-2024-32002", "state": "published",
               "summary": "Recursive clones on case-insensitive filesystems can run code",
               "description": "Git for Windows and macOS are affected.", "severity": "critical",
               "published_at": RECENT.strftime("%Y-%m-%dT%H:%M:%SZ"),
               "html_url": "https://github.com/git/git/security/advisories/GHSA-8h77-4q3w-gfgv",
               "cvss": {"score": 9.0, "vector_string": "CVSS:3.1/AV:N/AC:H/PR:N/UI:R/S:C/C:H/I:H/A:H"},
               "vulnerabilities": [{"package": {"name": "git"}, "vulnerable_version_range": "<= 2.45.0",
                                    "patched_versions": "2.39.4, 2.40.2, 2.41.1, 2.42.2, 2.43.4, 2.44.1, 2.45.1"}]}]

NGINX = """<html><body><ul>
<li><p>Memory corruption in the HTTP/3 module<br>Severity: major<br>
<a href="https://my.f5.com/manage/s/article/K000000001">Advisory</a><br>
<a href="https://www.cve.org/CVERecord?id=CVE-2026-42945">CVE-2026-42945</a><br>
Not vulnerable: 1.31.0+, 1.30.1+<br>Vulnerable: 1.25.0-1.30.0</p></li>
</ul></body></html>"""

MOZILLA_INDEX = f"""<html><body>
<h2>{RECENT.strftime('%B %d, %Y')}</h2><ul>
<li class="level-critical"><a href="/en-US/security/advisories/mfsa2026-130/">MFSA 2026-130 Security Vulnerabilities fixed in Firefox 147.0</a></li>
<li><a href="/en-US/security/advisories/mfsa2026-131/">MFSA 2026-131 Security Vulnerabilities fixed in Thunderbird 147</a></li>
</ul></body></html>"""
MOZILLA_ADVISORY = """<html><body><h1>MFSA 2026-130</h1><dl><dt>Impact</dt><dd><span class="level critical">critical</span></dd></dl>
<h4 id="CVE-2026-50001">CVE-2026-50001: Use-after-free in the DOM</h4></body></html>"""

MSRC_INDEX = {"value": [{"ID": "2026-Nov", "CurrentReleaseDate": ISO + "Z",
                         "CvrfUrl": "https://api.msrc.microsoft.com/cvrf/v3.0/cvrf/2026-Nov"}]}
MSRC_DOC = {"DocumentTracking": {"CurrentReleaseDate": ISO + "Z"},
            "ProductTree": {"FullProductName": [{"ProductID": "11", "Value": "Windows Server 2019"},
                                                {"ProductID": "12", "Value": "Windows Server 2025"}]},
            "Vulnerability": [
                {"CVE": "CVE-2026-60001", "Title": {"Value": "Windows Kerberos Remote Code Execution"},
                 "ProductStatuses": [{"ProductID": ["11"], "Type": 3}],
                 "Threats": [{"Type": 3, "Description": {"Value": "Critical"}},
                             {"Type": 1, "Description": {"Value": "Publicly Disclosed:No;Exploited:Yes"}}],
                 "CVSSScoreSets": [{"BaseScore": 9.8, "Vector": "CVSS:3.1/AV:N/AC:L"}],
                 "Remediations": [{"Type": 2, "Description": {"Value": "5066586"}, "FixedBuild": "10.0.17763.8000"}]},
                {"CVE": "CVE-2026-60002", "Title": {"Value": "Only on a Server we don't run"},
                 "ProductStatuses": [{"ProductID": ["12"], "Type": 3}],
                 "CVSSScoreSets": [{"BaseScore": 9.8, "Vector": "x"}]}]}

ORACLE = f"""<?xml version="1.0"?><rss><channel><item><title>Oracle Critical Patch Update Advisory - October 2026</title>
<link>https://www.oracle.com/security-alerts/cpuoct2026.html</link><description>Oracle Hospitality OPERA 5 fixes</description>
<pubDate>{RECENT.strftime('%a, %d %b %Y %H:%M:%S +0000')}</pubDate></item></channel></rss>"""

# Wazuh's Installed packages: 13 PCs on an old Git for Windows (T-11) and one updated; 12 on Firefox 146;
# mnemerynne-server on nginx.org's 1.30.6 and ticketgal-prod on Ubuntu's patched 1.24.0 build.
INVENTORY = ([("EH-AC-%d" % n, "Git", "2.44.0.windows.1") for n in range(1, 14)] +
             [("EH-AC-14", "Git", "2.45.1.windows.1")] +
             [("EH-PC-%d" % n, "Mozilla Firefox (x64 en-US)", "146.0.1") for n in range(1, 13)] +
             [("mnemerynne-server", "nginx", "1.30.6-1~noble"), ("ticketgal-prod", "nginx", "1.24.0-2ubuntu7.19")])


class World:
    """The fake internet: answers by host, counts requests, and can break one host."""

    def __init__(self):
        self.calls: list[httpx.Request] = []
        self.broken: dict[str, httpx.Response] = {}

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        host, path = request.url.host, request.url.path
        if host in self.broken:
            return self.broken[host]
        if host == "www.cisa.gov":
            return httpx.Response(200, json=KEV)
        if host == "services.nvd.nist.gov":
            if "cveId" in request.url.params:
                return httpx.Response(200, json={"vulnerabilities": [], "totalResults": 0})
            return httpx.Response(200, json=NVD)
        if host == "api.msrc.microsoft.com":
            return httpx.Response(200, json=MSRC_INDEX if path.endswith("/updates") else MSRC_DOC)
        if host == "www.mozilla.org":
            return httpx.Response(200, text=MOZILLA_ADVISORY if "mfsa2026-130" in path else MOZILLA_INDEX)
        if host == "api.github.com":
            return httpx.Response(200, json=GITHUB_GIT if path.startswith("/repos/git/git/") else [])
        if host == "nginx.org":
            return httpx.Response(200, text=NGINX)
        if host == "www.oracle.com":
            return httpx.Response(200, content=ORACLE.encode())
        return httpx.Response(404)

    def hits(self, host: str) -> int:
        return sum(1 for r in self.calls if r.url.host == host)


@pytest.fixture
def world():
    w = World()
    sf.TRANSPORT = httpx.MockTransport(w)
    sf.SLEEP = lambda seconds: None
    yield w
    sf.SLEEP = __import__("time").sleep


@pytest.fixture
def wazuh(conn, settings):
    """The Wazuh preset's indexer API with its Installed packages operation, answering from INVENTORY."""
    api_id = conn.execute("INSERT INTO apis(name, base_url, auth_type, auth_user, ad_group, claude_enabled, secret_enc) "
                          "VALUES ('Wazuh', 'https://wazuh-indexer.example:9200', 'basic', 'reader', 'IT', 1, ?)",
                          (apis.encrypt_secret(settings, "pw"),)).lastrowid
    conn.execute("INSERT INTO api_operations(api_id, name, method, path, params, body) VALUES (?, 'Installed packages', "
                 "'POST', '/wazuh-states-inventory-packages-*/_search', '[]', '{}')", (api_id,))
    seen = []

    def indexer(request):
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={"hits": {"hits": [
            {"_source": {"agent": {"name": a}, "package": {"name": p, "version": v}}} for a, p, v in INVENTORY]}})
    apis.TRANSPORT = httpx.MockTransport(indexer)
    yield seen
    apis.TRANSPORT = None


def _items(conn, settings, **kw):
    return sf.query(conn, settings, poll=False, **{"since_days": 7, **kw})


def _by_cve(out) -> dict:
    return {i["cve"]: i for i in out["items"] if i["cve"]}


# ------------------------------------------------------------------ the defaults
def test_the_default_feeds_and_products_ship_and_a_deleted_one_stays_deleted(conn, automations_conn):
    sf.query(conn, None, poll=False)                                   # the first look puts them in
    feeds = {r["key"]: r for r in automations_conn.execute("SELECT * FROM automation_feeds")}
    assert set(feeds) == {"cisa-kev", "nvd", "msrc", "mozilla", "github-git", "github-git-for-windows", "nginx",
                          "ubiquiti", "oracle"}
    assert feeds["ubiquiti"]["enabled"] == 0 and feeds["cisa-kev"]["enabled"] == 1
    assert feeds["nvd"]["interval_seconds"] == 7200 and feeds["cisa-kev"]["interval_seconds"] == 6 * 3600
    assert feeds["github-git"]["interval_seconds"] == 86400
    prods = {p["key"]: p for p in sf.products(automations_conn, enabled_only=False)}
    assert "cpe:2.3:o:microsoft:windows_server_2019" in prods["windows"]["cpes"]
    assert prods["oracle-ifc8"]["cpes"] == [] and "IFC8" in prods["oracle-ifc8"]["keywords"]
    assert not prods["vaultwarden"]["enabled"] and not prods["openssh"]["enabled"] and prods["nginx"]["enabled"]
    automations_conn.execute("DELETE FROM automation_feeds WHERE key = 'oracle'")
    sf.ensure_defaults(automations_conn)
    assert automations_conn.execute("SELECT 1 FROM automation_feeds WHERE key = 'oracle'").fetchone() is None


def test_the_admin_page_lists_the_default_feeds_and_takes_the_nvd_key(settings, conn):
    directory = FakeDirectory({"boss": {"Domain Admins"}}, groups=["Domain Admins"])
    with TestClient(create_app(settings, Services(llm=None, embedder=FakeEmbedder(), directory=directory))) as c:
        uid = users.create_user(conn, "boss", "password123")
        accounts.link(conn, uid, "boss", directory)
        c.post("/login", data={"username": "boss", "password": "password123"})
        data = c.get("/api/admin/automations/security-feeds").json()
        names = [f["name"] for f in data["feeds"]]
        assert "CISA KEV" in names and "NVD CVEs" in names and "nginx.org advisories" in names
        assert all(f["default"] for f in data["feeds"]) and "localfile" in data
        nvd = next(f for f in data["feeds"] if f["key"] == "nvd")
        assert nvd["has_key"] is False
        after = c.patch(f"/api/admin/automations/feeds/{nvd['id']}", json={"api_key": "nvd-key-123"}).json()
        assert next(f for f in after["feeds"] if f["key"] == "nvd")["has_key"] is True
        assert "nvd-key-123" not in json.dumps(after)                  # never shown again
        win = next(p for p in after["products"] if p["key"] == "windows")
        kept = [c_ for c_ in win["cpes"] if "2016" not in c_]           # "we don't run 2016 any more"
        after = c.patch(f"/api/admin/automations/products/{win['id']}", json={"cpes": kept}).json()
        assert not any("2016" in x for x in next(p for p in after["products"] if p["key"] == "windows")["cpes"])
        assert any(f["name"] == "CISA KEV" for f in c.get("/api/admin/automations").json()["feeds"])


# ------------------------------------------------------------------ acceptance checks
def test_git_cve_2024_32002_counts_the_pcs_on_the_old_git_for_windows(conn, settings, world, wazuh):
    """Check 2: the inventory join."""
    sf.poll_due(conn, settings, force=True)
    item = _by_cve(_items(conn, settings))["CVE-2024-32002"]
    assert item["product"] == "Git for Windows" and item["vendor"] == "Git"
    assert item["sources"] == ["github", "nvd"] and item["cvss"] == 9.0 and item["severity"] == "critical"
    assert item["fixed_in"].endswith("2.45.1") and "2.44.1" in item["fixed_in"]   # the vendor's per-branch fixes
    hit = item["affects_us"]
    assert hit["machines"] == 13 and "EH-AC-14" not in hit["agents"]          # 2.45.1 is fixed
    assert hit["versions"] == ["2.44.0.windows.1"] and hit["version_check"] == "compared"


def test_firefox_is_one_item_from_kev_mozilla_and_nvd_with_the_pcs_on_146(conn, settings, world, wazuh):
    """Check 3, and gap 1: one item per CVE, listing every source."""
    sf.poll_due(conn, settings, force=True)
    out = _items(conn, settings)
    assert [i["cve"] for i in out["items"]].count("CVE-2026-50001") == 1
    item = _by_cve(out)["CVE-2026-50001"]
    assert item["sources"] == ["kev", "mozilla", "nvd"]
    assert item["kev"] is True and item["kev_due_date"] == "2026-12-10" and item["kev_ransomware"] == "Known"
    assert item["fixed_in"] == "147.0" and item["cvss"] == 8.8
    assert item["link"].endswith("/mfsa2026-130/")                       # the vendor's page first
    assert item["affects_us"]["machines"] == 12
    assert "CVE-2026-50002" not in _by_cve(out)                          # a KEV entry for something we don't run


def test_nginx_42945_counts_neither_the_new_build_nor_ubuntus_patched_one(conn, settings, world, wazuh):
    """Check 4: version comparison, and distro backports marked instead of a false positive."""
    sf.poll_due(conn, settings, force=True)
    item = _by_cve(_items(conn, settings))["CVE-2026-42945"]
    assert item["sources"] == ["nginx", "nvd"] and item["fixed_in"] == "1.30.1, 1.31.0"
    hit = item["affects_us"]
    assert hit["machines"] == 0 and hit["agents"] == []
    assert hit["version_check"] == "distro-backport-unknown" and hit["unclear_agents"] == ["ticketgal-prod"]
    assert "CVE-2026-70001" not in _by_cve(_items(conn, settings))      # CVSS 5: NVD's isn't kept


def test_a_broken_feed_shows_in_feed_status_and_the_others_still_answer(conn, settings, world, wazuh):
    """Check 5, and gap 5: never silent."""
    world.broken["nginx.org"] = httpx.Response(500)
    sf.poll_due(conn, settings, force=True)
    out = _items(conn, settings)
    status = {s["feed"]: s for s in out["feed_status"]}
    assert "HTTP 500" in status["nginx.org advisories"]["error"]
    assert status["nginx.org advisories"]["last_success"] == ""
    assert status["CISA KEV"]["error"] == "" and status["CISA KEV"]["last_success"]
    assert status["CISA KEV"]["items_last_fetch"] == 1
    assert status["Wazuh inventory (affects_us)"]["error"] == ""
    assert "nginx.org advisories" in out["note"]
    assert "CVE-2026-50001" in _by_cve(out) and "CVE-2026-42945" in _by_cve(out)   # NVD still has nginx's


def test_403_and_429_back_off_and_a_fixed_feed_recovers(conn, settings, world, automations_conn):
    world.broken["services.nvd.nist.gov"] = httpx.Response(429, headers={"Retry-After": "3600"})
    sf.poll_due(conn, settings, force=True)
    row = automations_conn.execute("SELECT * FROM automation_feeds WHERE key = 'nvd'").fetchone()
    assert row["failures"] == 1 and "429" in row["error"]
    assert sf.next_due(row) > sf.now() + timedelta(minutes=59)          # Retry-After respected
    before = world.hits("services.nvd.nist.gov")
    sf.poll_due(conn, settings)                                          # not due: nothing asked
    assert world.hits("services.nvd.nist.gov") == before
    del world.broken["services.nvd.nist.gov"]
    sf.poll_due(conn, settings, only=row["id"])
    row = automations_conn.execute("SELECT * FROM automation_feeds WHERE key = 'nvd'").fetchone()
    assert row["error"] == "" and row["failures"] == 0 and row["backoff_until"] is None and row["cursor"]


def test_an_urgent_item_that_affects_us_reaches_wazuh_once(conn, settings, world, wazuh):
    """Check 6: one JSON line per new KEV (or CVSS >= 9) item affecting us, not one per poll."""
    sf.poll_due(conn, settings, force=True)
    sf.poll_due(conn, settings, force=True)
    sf.poll_due(conn, settings, force=True)
    lines = [json.loads(x) for x in sf.log_path(settings).read_text(encoding="utf-8").splitlines()]
    assert all(x["integration"] == "security-feeds" for x in lines)
    by = {x["cve"]: x for x in lines}
    assert len(lines) == len(by) == 2
    assert by["CVE-2026-50001"]["event"] == "kev_affects_us" and by["CVE-2026-50001"]["machines"] == 12
    assert by["CVE-2024-32002"]["event"] == "critical_affects_us" and by["CVE-2024-32002"]["machines"] == 13
    assert "CVE-2026-60001" not in by                                    # Windows: no inventory, so not "affects us"


def test_no_inventory_is_not_affects_us_and_nothing_is_pushed(conn, settings, world):
    sf.poll_due(conn, settings, force=True)
    out = _items(conn, settings)
    assert _by_cve(out)["CVE-2026-50001"]["affects_us"]["version_check"] == "no-inventory"
    assert "Wazuh preset" in {s["feed"]: s for s in out["feed_status"]}["Wazuh inventory (affects_us)"]["error"]
    assert not sf.log_path(settings).exists()


# ------------------------------------------------------------------ the tool
def test_the_tool_takes_the_morning_briefs_parameters(conn, settings, world, wazuh):
    """Gap 4: since_days, product, min_cvss, kev_only, affects_us_only."""
    tools = Tools(settings, lambda: FakeEmbedder(), lambda: FakeDirectory({}, groups=[]))
    caller = api_tokens.verify(conn, api_tokens.create(conn, users.create_user(conn, "alice", "password123"), "t")[1])
    sf.poll_due(conn, settings, force=True)
    out, _ = tools.security_feeds(conn, caller, since_days=7, kev_only=True)
    assert [i["cve"] for i in out["items"]] == ["CVE-2026-50001"]
    out, _ = tools.security_feeds(conn, caller, since_days=7, affects_us_only=True, min_cvss=7)
    assert [i["cve"] for i in out["items"]] == ["CVE-2026-50001", "CVE-2024-32002"]   # KEV first, then CVSS 9
    out, _ = tools.security_feeds(conn, caller, product="nginx")
    assert [i["cve"] for i in out["items"]] == ["CVE-2026-42945"]
    out, _ = tools.security_feeds(conn, caller, since_days=90)
    win = _by_cve(out)["CVE-2026-60001"]
    assert win["product"] == "Windows" and win["fixed_in"] == "10.0.17763.8000" and win["severity"] == "critical"
    assert "CVE-2026-60002" not in _by_cve(out)                          # Server 2025: not one we watch
    assert any(i["product"].startswith("Oracle Hospitality") and i["cve"] == "" for i in out["items"])
    assert out["filters"] == {"product": "", "min_cvss": None, "kev_only": False, "affects_us_only": False}


def test_every_item_has_the_same_fields_and_types(conn, settings, world, wazuh):
    """Gap 2: numbers are numbers, lists are lists, strings never null."""
    sf.poll_due(conn, settings, force=True)
    items = sf.query(conn, settings, since_days=90, poll=False)["items"]
    assert items
    for i in items:
        assert list(i) == list(sf.ITEM_FIELDS) + ["affects_us"]
        for f in ("cve", "title", "product", "vendor", "published", "first_seen", "cvss_vector", "severity",
                  "kev_due_date", "kev_ransomware", "fixed_in", "link", "summary"):
            assert isinstance(i[f], str), f
        assert isinstance(i["sources"], list) and isinstance(i["kev"], bool)
        assert i["cvss"] is None or isinstance(i["cvss"], float)
        a = i["affects_us"]
        assert isinstance(a["machines"], int) and isinstance(a["agents"], list) and isinstance(a["versions"], list)
        assert isinstance(a["unclear_agents"], list) and isinstance(a["version_check"], str)


def test_the_first_poll_of_an_undated_page_isnt_news_but_a_new_advisory_is(conn, settings, world, automations_conn):
    """nginx.org has no dates: what's on the page at the first poll is history; one added later is new."""
    sf.ensure_defaults(automations_conn)
    automations_conn.execute("UPDATE automation_feeds SET enabled = 0 WHERE key != 'nginx'")
    sf.poll_due(conn, settings, force=True)
    assert _items(conn, settings, since_days=90)["items"] == []
    global NGINX
    old = NGINX
    NGINX = NGINX.replace("CVE-2026-42945", "CVE-2026-42945</a><br><a href='x'>CVE-2026-49999")
    try:
        sf.poll_due(conn, settings, force=True)
    finally:
        NGINX = old
    assert [i["cve"] for i in _items(conn, settings)["items"]] == ["CVE-2026-49999"]


def test_history_is_kept_for_90_days(conn, settings, world, automations_conn):
    sf.poll_due(conn, settings, force=True)
    old = (sf.now() - timedelta(days=80)).strftime("%Y-%m-%dT%H:%M:%SZ")
    automations_conn.execute("UPDATE security_items SET activity_at = ? WHERE key = 'CVE-2026-50001'", (old,))
    assert "CVE-2026-50001" not in _by_cve(_items(conn, settings, since_days=30))
    assert "CVE-2026-50001" in _by_cve(_items(conn, settings, since_days=90))
    sf.purge(automations_conn)                                           # 80 days and still listed: kept
    assert automations_conn.execute("SELECT 1 FROM security_items WHERE key = 'CVE-2026-50001'").fetchone()


# ------------------------------------------------------------------ the pieces
@pytest.mark.parametrize("installed, fixed, expected", [
    ("1.30.6", "1.30.1", "fixed"),
    ("1.30.6-1~noble", "1.30.1", "fixed"),                     # nginx.org's own package
    ("1.24.0-2ubuntu7.19", "1.30.1", "unclear"),               # Ubuntu may have backported the fix
    ("1.29.0-1~noble", "1.30.1", "vulnerable"),
    ("2.44.0.windows.1", "2.39.4, 2.44.1, 2.45.1", "vulnerable"),
    ("2.44.1.windows.1", "2.39.4, 2.44.1, 2.45.1", "fixed"),   # its own branch's fix
    ("2.39.4", "2.39.4, 2.44.1, 2.45.1", "fixed"),
    ("146.0.1", "147.0", "vulnerable"),
    ("1:9.6p1-3ubuntu13.5", "9.8", "unclear"),                 # an epoch and a distro revision
    ("146.0.1", "", "unknown"),
])
def test_version_status(installed, fixed, expected):
    assert sf.version_status(installed, fixed) == expected


def test_cpe_and_keyword_matching():
    assert sf.cpe_matches("cpe:2.3:a:git-scm:git", "cpe:2.3:a:git-scm:git:*:*:*:*:*:*:*:*")
    assert not sf.cpe_matches("cpe:2.3:a:git-scm:git", "cpe:2.3:a:git-scm:git_lfs:*:*:*:*:*:*:*:*")
    assert sf.cpe_matches("cpe:2.3:o:microsoft:windows_server_2012*", "cpe:2.3:o:microsoft:windows_server_2012:r2:*")
    assert sf.keyword_in("OPERA", "Oracle Hospitality OPERA 5") and not sf.keyword_in("OPERA", "Opera browser flaw")
    assert sf.keyword_in("Git", "Git for Windows") and not sf.keyword_in("Git", "GitLab CE")


def test_outbound_fetches_trust_the_ca_and_pin_nothing(monkeypatch):
    """Gap 7: verify=True (the system's CAs), never a pinned leaf certificate."""
    seen = {}
    real = httpx.Client

    def spy(*args, **kwargs):
        seen.update(kwargs)
        return real(*args, **kwargs)
    monkeypatch.setattr(sf.httpx, "Client", spy)
    sf._client().close()
    assert seen["verify"] is True


def test_the_nvd_key_goes_in_its_header_and_the_window_moves_on(conn, settings, world, automations_conn):
    sf.ensure_defaults(automations_conn)
    automations_conn.execute("UPDATE automation_feeds SET secret_enc = ? WHERE key = 'nvd'",
                             (apis.encrypt_secret(settings, "nvd-key"),))
    sf.poll_due(conn, settings, force=True)
    asked = [r for r in world.calls if r.url.host == "services.nvd.nist.gov"]
    assert asked[0].headers["apiKey"] == "nvd-key"
    assert "lastModStartDate" in asked[0].url.params and "lastModEndDate" in asked[0].url.params
    cursor = automations_conn.execute("SELECT cursor FROM automation_feeds WHERE key = 'nvd'").fetchone()[0]
    sf.poll_due(conn, settings, force=True)
    again = [r for r in world.calls if r.url.host == "services.nvd.nist.gov" and "lastModStartDate" in r.url.params]
    assert again[-1].url.params["lastModStartDate"] == cursor.rstrip("Z") + ".000"
