"""Security feeds (tasklist T-21): "is there a new vulnerability that is actively exploited, or critical, in something
we actually run, and on which machines?"

The pieces, in the order a poll runs them:

- **Defaults** (DEFAULT_FEEDS, DEFAULT_PRODUCTS): CISA KEV, NVD, MSRC, Mozilla, GitHub advisories for git/git and
  git-for-windows/git, nginx.org, Ubiquiti (off: no machine-readable feed) and Oracle, plus the products we run with
  their CPEs, keywords and Wazuh package names. ensure_defaults() puts each in once (security_defaults_seeded), so an
  admin's edits and deletions stick; all Edith enters is the NVD API key and which Windows Server versions we run.
- **Polling** (poll_due): each feed on its own interval (NVD 2 h, KEV 6 h, vendors daily), from Automator's scheduler
  tick and from a read. A 403/429 backs off (Retry-After, then doubling); any failure is written to the feed's row,
  so feed_status shows it and an empty answer is never a silent failure.
- **Sightings → items**: every source's view of a CVE is saved (security_sightings) and the item is re-merged from
  all of them (merge()), so one CVE is one item listing all its sources.
- **affects_us**: joined at read time with Wazuh's *Installed packages* inventory (the indexer's
  wazuh-states-inventory-packages-*), comparing each installed version with `fixed_in` (versions.py-style rules in
  version_status(): a distro build older than the fix may carry a backport, so it's "distro-backport-unknown", not a
  false positive).
- **Wazuh push**: a new item that is in KEV (or CVSS >= 9) and affects us gets one JSON line in the log Wazuh's agent
  reads (settings.security_feeds_log), once: security_items.wazuh_pushed_at.
- **Certificates**: outbound fetches trust the system's CAs (httpx verify=True); no leaf certificate is pinned, so
  routine renewals don't break a feed.

Writes go to automations.db (Phase 9) through _db(); core's apis table (the Wazuh credentials) is read through the
feature connection's read-only `core` attachment.
"""

from __future__ import annotations

import fnmatch
import json
import logging
import re
import sqlite3
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator
from urllib.parse import urljoin
from xml.etree import ElementTree

import httpx

from mnemerynne import db
from mnemerynne.config import Settings

log = logging.getLogger("mnemerynne.security_feeds")

HISTORY_DAYS = 90                 # since_days can go this far back
PURGE_DAYS = 120                  # items quiet this long (and no longer listed anywhere) are deleted
MAX_ITEMS = 200                   # per answer, most urgent first
MAX_BYTES = 16 * 1024 * 1024      # per response
NVD_MIN_CVSS = 7.0                # NVD records kept: matching a product and at least this score (or already known)
NVD_FIRST_DAYS = 14               # NVD's first modified-date window
NVD_PAGE = 2000
NVD_MAX_PAGES = 10
NVD_ENRICH = 20                   # CVEs from other sources looked up in NVD per poll (their CVSS and fixed versions)
MOZILLA_PAGES = 10                # advisory pages read per poll, for their CVEs
PUSH_RECENT_DAYS = 30             # only items this fresh are pushed to Wazuh (the first poll's backfill isn't an alert)
PUSH_MIN_CVSS = 9.0
INVENTORY_SECONDS = 900           # Wazuh inventory cached this long
INVENTORY_ROWS = 10000
TIMEOUT = 30.0
USER_AGENT = "Mnemerynne-SecurityFeeds/1.0"           # generic: no site or person is named to the vendors
KINDS = ("rss", "atom", "json_kev", "nvd", "msrc", "github", "nginx", "mozilla", "html")
INVENTORY_FEED = "Wazuh inventory (affects_us)"

# Tests: an httpx transport for every outbound fetch, and a sleep that doesn't.
TRANSPORT: httpx.BaseTransport | None = None
SLEEP = time.sleep

_poll_lock = threading.Lock()                       # one poll at a time per process
_inventory_cache: dict = {"at": 0.0, "key": None, "value": None}

CVE_RE = re.compile(r"CVE-\d{4}-\d{4,7}")
VERSION_RE = re.compile(r"\d+(?:\.\d+)+")

# ------------------------------------------------------------------ defaults
# The products we run (plan Part A, "Products and match keys"). cpes: CPE 2.3 prefixes compared part by part with *
# wildcards; keywords: whole words, case-insensitive, except ALL-CAPS ones (UXG, OPERA, IFC8), which match exactly so
# "Opera" the browser isn't Oracle's OPERA; packages: Wazuh inventory package names (* wildcards, any case).
DEFAULT_PRODUCTS = (
    {"key": "windows", "name": "Windows", "vendor": "Microsoft",
     "cpes": ["cpe:2.3:o:microsoft:windows_10*", "cpe:2.3:o:microsoft:windows_11*",
              "cpe:2.3:o:microsoft:windows_server_2012*", "cpe:2.3:o:microsoft:windows_server_2016",
              "cpe:2.3:o:microsoft:windows_server_2019", "cpe:2.3:o:microsoft:windows_server_2022"],
     "keywords": ["Microsoft Windows", "Windows 10", "Windows 11", "Windows Server 2012 R2", "Windows Server 2016",
                  "Windows Server 2019", "Windows Server 2022", "Win32k"],
     "packages": [],
     "note": "Confirm the Windows Server versions we still run (at least 2012 R2 on WAPSCO-SVR-001 and 2019 on "
             "Win2019SRVR): remove the CPEs and keywords of the ones we don't. Wazuh's package inventory doesn't "
             "list Windows itself, so affects_us says no-inventory."},
    {"key": "dotnet", "name": ".NET", "vendor": "Microsoft", "cpes": ["cpe:2.3:a:microsoft:.net*"],
     "keywords": [".NET 8.0", ".NET 9.0", ".NET 10.0", ".NET Framework"], "packages": ["Microsoft .NET*"]},
    {"key": "office", "name": "Microsoft 365 Apps / Office", "vendor": "Microsoft",
     "cpes": ["cpe:2.3:a:microsoft:365_apps*", "cpe:2.3:a:microsoft:office*"],
     "keywords": ["Microsoft 365 Apps", "Microsoft Office"], "packages": ["Microsoft 365 Apps*", "Microsoft Office*"]},
    {"key": "edge", "name": "Edge", "vendor": "Microsoft", "cpes": ["cpe:2.3:a:microsoft:edge_chromium*"],
     "keywords": ["Microsoft Edge"], "packages": ["Microsoft Edge"]},
    {"key": "firefox", "name": "Firefox", "vendor": "Mozilla",
     "cpes": ["cpe:2.3:a:mozilla:firefox", "cpe:2.3:a:mozilla:firefox_esr"],
     "keywords": ["Firefox"], "packages": ["Mozilla Firefox*", "firefox", "firefox-esr"]},
    {"key": "git", "name": "Git for Windows", "vendor": "Git",
     "cpes": ["cpe:2.3:a:git-scm:git", "cpe:2.3:a:git_for_windows_project:git_for_windows"],
     "keywords": ["Git for Windows", "Git"], "packages": ["Git", "Git version *", "git"]},
    {"key": "nginx", "name": "nginx", "vendor": "F5",
     "cpes": ["cpe:2.3:a:f5:nginx", "cpe:2.3:a:nginx:nginx", "cpe:2.3:a:f5:nginx_open_source"],
     "keywords": ["nginx"], "packages": ["nginx"]},
    {"key": "unifi", "name": "UniFi", "vendor": "Ubiquiti",
     "cpes": ["cpe:2.3:a:ui:unifi_network_application", "cpe:2.3:a:ui:unifi_network*", "cpe:2.3:o:ui:unifi_os*",
              "cpe:2.3:o:ui:uxg*"],
     "keywords": ["UniFi", "Ubiquiti", "UXG"], "packages": ["unifi", "UniFi*"]},
    {"key": "oracle-ifc8", "name": "Oracle Hospitality (OPERA 5, IFC8)", "vendor": "Oracle", "cpes": [],
     "keywords": ["Oracle Hospitality", "OPERA", "IFC8", "Hospitality Interfaces"], "packages": [],
     "note": "No reliable CPE: matched by keywords. Add the Wazuh package names of OPERA/IFC8 for affects_us."},
    # "Worth adding later" (plan Part A): seeded off; switch one on when it's worth watching.
    {"key": "wazuh", "name": "Wazuh", "vendor": "Wazuh", "cpes": ["cpe:2.3:a:wazuh:wazuh"], "keywords": ["Wazuh"],
     "packages": ["wazuh-agent", "wazuh-manager", "Wazuh Agent"], "enabled": False},
    {"key": "suricata", "name": "Suricata", "vendor": "OISF", "cpes": ["cpe:2.3:a:oisf:suricata"],
     "keywords": ["Suricata"], "packages": ["suricata"], "enabled": False},
    {"key": "opnsense", "name": "OPNsense", "vendor": "Deciso", "cpes": ["cpe:2.3:a:opnsense:opnsense"],
     "keywords": ["OPNsense"], "packages": ["opnsense"], "enabled": False},
    {"key": "proxmox-ve", "name": "Proxmox VE", "vendor": "Proxmox", "cpes": ["cpe:2.3:a:proxmox:virtual_environment"],
     "keywords": ["Proxmox"], "packages": ["pve-manager"], "enabled": False},
    {"key": "synology-dsm", "name": "Synology DSM", "vendor": "Synology",
     "cpes": ["cpe:2.3:o:synology:diskstation_manager*"], "keywords": ["Synology", "DiskStation Manager"],
     "packages": [], "enabled": False},
    {"key": "veeam", "name": "Veeam Backup & Replication", "vendor": "Veeam", "cpes": ["cpe:2.3:a:veeam:veeam_backup*"],
     "keywords": ["Veeam Backup"], "packages": ["Veeam Backup*"], "enabled": False},
    {"key": "vaultwarden", "name": "Vaultwarden", "vendor": "Vaultwarden", "cpes": ["cpe:2.3:a:dani-garcia:vaultwarden"],
     "keywords": ["Vaultwarden"], "packages": ["vaultwarden"], "enabled": False,
     "note": "Switch on once T-23 (Vaultwarden) is live."},
    {"key": "gravityzone", "name": "Bitdefender GravityZone agent", "vendor": "Bitdefender",
     "cpes": ["cpe:2.3:a:bitdefender:gravityzone*", "cpe:2.3:a:bitdefender:endpoint_security_tools*"],
     "keywords": ["GravityZone", "Bitdefender Endpoint Security Tools"],
     "packages": ["Bitdefender Endpoint Security Tools*"], "enabled": False},
    {"key": "atera", "name": "Atera agent", "vendor": "Atera", "cpes": ["cpe:2.3:a:atera:*"], "keywords": ["Atera"],
     "packages": ["AteraAgent*"], "enabled": False},
    {"key": "openssh", "name": "OpenSSH", "vendor": "OpenBSD", "cpes": ["cpe:2.3:a:openbsd:openssh"],
     "keywords": ["OpenSSH"], "packages": ["openssh-server", "openssh-client", "OpenSSH*"], "enabled": False},
)

ALL_PRODUCTS = [p["key"] for p in DEFAULT_PRODUCTS]
DAY, HOUR = 86400, 3600

# The feeds (plan Part A). products: which products the feed speaks for (an item from it that names none of them is
# dropped); an item from a vendor feed that names none, but whose feed has one product, is that product's.
DEFAULT_FEEDS = (
    {"key": "cisa-kev", "name": "CISA KEV", "kind": "json_kev", "interval": 6 * HOUR, "products": ALL_PRODUCTS,
     "url": "https://www.cisa.gov/sites/default/files/feeds/known_exploited_vulnerabilities.json",
     "note": "Known exploited vulnerabilities, matched to our products by vendor and product."},
    {"key": "nvd", "name": "NVD CVEs", "kind": "nvd", "interval": 2 * HOUR, "products": ALL_PRODUCTS,
     "url": "https://services.nvd.nist.gov/rest/json/cves/2.0",
     "note": "Enter the NVD API key (free: nvd.nist.gov/developers/request-an-api-key). Without one NVD allows "
             "far fewer requests, so polls are slower. Kept: CVEs matching our CPEs/keywords with CVSS >= 7."},
    {"key": "msrc", "name": "Microsoft (MSRC)", "kind": "msrc", "interval": DAY,
     "products": ["windows", "dotnet", "office", "edge"], "url": "https://api.msrc.microsoft.com/cvrf/v3.0/updates",
     "note": "The security update guide's CVRF documents: the latest two (monthly and out-of-band)."},
    {"key": "mozilla", "name": "Mozilla advisories", "kind": "mozilla", "interval": DAY, "products": ["firefox"],
     "url": "https://www.mozilla.org/en-US/security/advisories/",
     "note": "Mozilla has no feed: the advisories page is read, and each new Firefox advisory's page for its CVEs."},
    {"key": "github-git", "name": "GitHub advisories: git/git", "kind": "github", "interval": DAY, "products": ["git"],
     "url": "https://api.github.com/repos/git/git/security-advisories"},
    {"key": "github-git-for-windows", "name": "GitHub advisories: git-for-windows/git", "kind": "github",
     "interval": DAY, "products": ["git"], "url": "https://api.github.com/repos/git-for-windows/git/security-advisories"},
    {"key": "nginx", "name": "nginx.org advisories", "kind": "nginx", "interval": DAY, "products": ["nginx"],
     "url": "https://nginx.org/en/security_advisories.html"},
    {"key": "ubiquiti", "name": "Ubiquiti security bulletins", "kind": "html", "interval": DAY, "products": ["unifi"],
     "url": "https://community.ui.com/releases?q=Security%20Advisory%20Bulletin", "enabled": False,
     "note": "Off: Ubiquiti's community site only renders in a browser and has no RSS, so there's nothing to read "
             "here. UniFi CVEs still arrive through NVD (the ui CPEs) and CISA KEV."},
    {"key": "oracle", "name": "Oracle CPU and Security Alerts", "kind": "rss", "interval": DAY,
     "products": ["oracle-ifc8"],
     "url": "https://www.oracle.com/ocom/groups/public/@otn/documents/webcontent/rss-otn-sec.xml",
     "note": "Quarterly Critical Patch Updates and Security Alerts. Read each CPU's Hospitality section for OPERA 5 "
             "and IFC8; NVD fills in the Hospitality CVEs by keyword."},
)

ITEM_FIELDS = ("cve", "title", "product", "vendor", "sources", "published", "first_seen", "cvss", "cvss_vector",
               "severity", "kev", "kev_due_date", "kev_ransomware", "fixed_in", "link", "summary")


class FeedError(Exception):
    """A fetch that failed; status and retry_after when the server said so (403/429 back off)."""

    def __init__(self, message: str, status: int | None = None, retry_after: float | None = None):
        super().__init__(message)
        self.status, self.retry_after = status, retry_after


# ------------------------------------------------------------------- helpers
def now() -> datetime:
    return datetime.now(timezone.utc)


def iso(at: datetime) -> str:
    return at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_date(text) -> datetime | None:
    """ISO dates and times (with or without a zone), RSS dates and MSRC's; None if it isn't one."""
    if not text:
        return None
    text = str(text).strip()
    for fmt in ("%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S",
                "%Y-%m-%d", "%a, %d %b %Y %H:%M:%S %z", "%a, %d %b %Y %H:%M:%S %Z", "%B %d, %Y"):
        try:
            got = datetime.strptime(text.replace("Z", "+0000") if "%z" in fmt else text.rstrip("Z"), fmt)
            return got if got.tzinfo else got.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def iso_or_empty(text) -> str:
    found = parse_date(text)
    return iso(found) if found else ""


def _plain(html: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html or "")).replace("&nbsp;", " ").replace("&amp;", "&").strip()


def severity_of(cvss: float | None, given: str = "") -> str:
    """critical / high / medium / low from the score; the source's own word when there's no score."""
    if cvss is not None:
        return "critical" if cvss >= 9 else "high" if cvss >= 7 else "medium" if cvss >= 4 else "low" if cvss > 0 else "none"
    given = (given or "").strip().lower()
    return {"important": "high", "moderate": "medium", "major": "high", "minor": "low"}.get(given, given or "unknown")


def _float(value) -> float | None:
    try:
        return round(float(value), 1) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _jl(text) -> list:
    try:
        got = json.loads(text or "[]")
        return got if isinstance(got, list) else []
    except ValueError:
        return []


# ------------------------------------------------------------------- storage
@contextmanager
def _db(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection | None]:
    """The connection that holds the security tables (automations.db), or None before its migrations ran."""
    with db.table_connection(conn, "automations", "security_items") as found:
        yield found


def ensure_defaults(conn: sqlite3.Connection) -> None:
    """Put in each default feed and product once (on the security tables' own connection). One an admin deleted
    or changed stays as they left it: security_defaults_seeded remembers what was put in."""
    seeded = {r[0] for r in conn.execute("SELECT key FROM security_defaults_seeded")}
    todo_p = [p for p in DEFAULT_PRODUCTS if f"product:{p['key']}" not in seeded]
    todo_f = [f for f in DEFAULT_FEEDS if f"feed:{f['key']}" not in seeded]
    if not todo_p and not todo_f:
        return
    with db.transaction(conn):
        for p in todo_p:
            conn.execute("INSERT OR IGNORE INTO security_products(key, name, vendor, cpes, keywords, packages, note, "
                         "enabled) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                         (p["key"], p["name"], p["vendor"], json.dumps(p["cpes"]), json.dumps(p["keywords"]),
                          json.dumps(p["packages"]), p.get("note", ""), int(p.get("enabled", True))))
            conn.execute("INSERT OR IGNORE INTO security_defaults_seeded(key) VALUES (?)", (f"product:{p['key']}",))
        for f in todo_f:
            same = conn.execute("SELECT id, key FROM automation_feeds WHERE url = ? OR name = ?",
                                (f["url"], f["name"])).fetchone()
            if same is not None and same["key"] is None:     # an admin made it already: it becomes the default
                conn.execute("UPDATE automation_feeds SET key = ?, kind = ?, products = ?, note = ?, "
                             "interval_seconds = ? WHERE id = ?",
                             (f["key"], f["kind"], json.dumps(f["products"]), f.get("note", ""), f["interval"], same["id"]))
            elif same is None:
                conn.execute("INSERT INTO automation_feeds(key, name, url, kind, products, note, enabled, "
                             "interval_seconds) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                             (f["key"], f["name"], f["url"], f["kind"], json.dumps(f["products"]), f.get("note", ""),
                              int(f.get("enabled", True)), f["interval"]))
            conn.execute("INSERT OR IGNORE INTO security_defaults_seeded(key) VALUES (?)", (f"feed:{f['key']}",))


def products(conn: sqlite3.Connection, enabled_only: bool = True) -> list[dict]:
    rows = conn.execute("SELECT * FROM security_products" + (" WHERE enabled = 1" if enabled_only else "") +
                        " ORDER BY id").fetchall()
    return [{"id": r["id"], "key": r["key"], "name": r["name"], "vendor": r["vendor"], "cpes": _jl(r["cpes"]),
             "keywords": _jl(r["keywords"]), "packages": _jl(r["packages"]), "note": r["note"],
             "enabled": bool(r["enabled"])} for r in rows]


# ------------------------------------------------------------------ matching
def cpe_matches(pattern: str, cpe: str) -> bool:
    """A CPE 2.3 prefix pattern against a full CPE, part by part (* wildcards inside a part)."""
    want, have = pattern.lower().split(":"), cpe.lower().split(":")
    if len(have) < len(want):
        return False
    return all(fnmatch.fnmatchcase(h, w) for w, h in zip(want, have))


def keyword_in(keyword: str, text: str) -> bool:
    """A whole-word match; ALL-CAPS keywords (OPERA, UXG) are case-sensitive."""
    if not keyword:
        return False
    flags = 0 if keyword.isupper() else re.IGNORECASE
    return re.search(r"(?<![\w.])" + re.escape(keyword) + r"(?![\w])", text, flags) is not None


def product_by_text(text: str, prods: list[dict], *, strict: bool = False) -> dict | None:
    """The first product a keyword of which is in the text. strict (NVD descriptions): only keywords of two words
    or more, or six letters and up, so "Git" alone doesn't catch every description mentioning a git repository."""
    for p in prods:
        for k in p["keywords"]:
            if strict and " " not in k and len(k) < 6:
                continue
            if keyword_in(k, text):
                return p
    return None


def product_by_cpe(cpes: list[str], prods: list[dict]) -> dict | None:
    for p in prods:
        if any(cpe_matches(pat, c) for pat in p["cpes"] for c in cpes):
            return p
    return None


# ------------------------------------------------------------------ versions
_DISTRO = re.compile(r"(ubuntu|debian|deb\d|\+deb|[.~]el\d|\bel\d|fc\d|amzn|suse|sles|mga\d|\.alpine|-r\d+$)", re.I)


def version_tuple(text: str) -> tuple[int, ...] | None:
    """The upstream version's numbers: '1:1.24.0-2ubuntu7.19' -> (1, 24, 0); '2.44.0.windows.1' -> (2, 44, 0)."""
    text = re.sub(r"^\d+:", "", (text or "").strip())                # a Debian epoch
    found = re.match(r"v?(\d+(?:\.\d+)*)", text)
    return tuple(int(x) for x in found.group(1).split(".")) if found else None


def _pad(a: tuple, b: tuple) -> tuple[tuple, tuple]:
    n = max(len(a), len(b))
    return a + (0,) * (n - len(a)), b + (0,) * (n - len(b))


def _ge(a: tuple, b: tuple) -> bool:
    a, b = _pad(a, b)
    return a >= b


def fixed_versions(fixed_in: str) -> list[tuple[int, ...]]:
    return [t for t in (version_tuple(v) for v in VERSION_RE.findall(fixed_in or "")) if t]


def version_status(installed: str, fixed_in: str) -> str:
    """'fixed', 'vulnerable', 'unclear' (a distro build older than the fix: it may carry a backport) or 'unknown'
    (no fixed version to compare with, or a version that isn't one). With several fixed versions (one per release
    branch: 2.44.1, 2.45.1), an install is fixed at or above the newest one, or at or above the one of its own
    major.minor branch."""
    fixes = fixed_versions(fixed_in)
    have = version_tuple(installed)
    if not fixes or have is None:
        return "unknown"
    newest = max(fixes, key=lambda t: t + (0,) * (8 - len(t)))
    if _ge(have, newest) or any(have[:2] == f[:2] and _ge(have, f) for f in fixes):
        return "fixed"
    bare = re.sub(r"^\d+:", "", installed.strip())
    upstream = re.match(r"v?\d+(?:\.\d+)*", bare)
    revision = bare[upstream.end():] if upstream else bare            # '-2ubuntu7.19', '.windows.1', '-1~noble'
    if _DISTRO.search(revision):
        return "unclear"
    return "vulnerable"


# ----------------------------------------------------------------- fetching
def _client(headers: dict | None = None) -> httpx.Client:
    """Outbound fetches trust the system's certificate authorities (verify=True): never a pinned leaf certificate,
    which broke Gasco (2026-11-16) and Atera (2026-11-19) on routine renewals."""
    return httpx.Client(transport=TRANSPORT, timeout=TIMEOUT, follow_redirects=True, verify=True,
                        headers={"User-Agent": USER_AGENT, **(headers or {})})


def _get(client: httpx.Client, url: str, params: dict | None = None, headers: dict | None = None) -> httpx.Response:
    try:
        r = client.get(url, params=params, headers=headers)
    except httpx.TimeoutException:
        raise FeedError(f"no answer within {int(TIMEOUT)} s") from None
    except httpx.HTTPError as exc:
        text = str(exc)
        raise FeedError("couldn't verify the server's certificate (is the system's CA bundle current?)"
                        if "CERTIFICATE" in text.upper() else f"couldn't connect: {text[:200] or type(exc).__name__}") from None
    if r.status_code in (403, 429):
        retry = r.headers.get("retry-after", "")
        raise FeedError(f"the server refused (HTTP {r.status_code}); backing off", r.status_code,
                        float(retry) if retry.isdigit() else None)
    if r.status_code >= 400:
        raise FeedError(f"the server answered HTTP {r.status_code}", r.status_code)
    if len(r.content) > MAX_BYTES:
        raise FeedError("the answer was too large")
    return r


def _json(r: httpx.Response):
    try:
        return r.json()
    except ValueError:
        raise FeedError("the answer wasn't JSON") from None


# ------------------------------------------------------------------ parsers
# Each returns sightings: {"key", "cve", "title", "summary", "product", "published", "date", "cvss", "cvss_vector",
# "severity", "fixed_in", "link", and for KEV "kev_due_date", "kev_ransomware"}. product is a product key or "".
def _sighting(**kw) -> dict:
    s = {"key": "", "cve": "", "title": "", "summary": "", "product": "", "published": "", "date": "", "cvss": None,
         "cvss_vector": "", "severity": "", "fixed_in": "", "link": ""}
    s.update(kw)
    s["key"] = s["key"] or s["cve"] or ("url:" + s["link"] if s["link"] else "")
    return s


def parse_kev(data: dict, prods: list[dict]) -> list[dict]:
    out = []
    for v in data.get("vulnerabilities", []) if isinstance(data, dict) else []:
        text = f"{v.get('vendorProject', '')} {v.get('product', '')} {v.get('vulnerabilityName', '')}"
        p = product_by_text(text, prods)
        cve = v.get("cveID") or ""
        if p is None or not CVE_RE.fullmatch(cve):
            continue
        added = iso_or_empty(v.get("dateAdded"))
        out.append(_sighting(
            cve=cve, title=f"{v.get('vendorProject', '')} {v.get('product', '')}: {v.get('vulnerabilityName', '')}".strip(),
            summary=(v.get("shortDescription") or "")[:600], product=p["key"], date=added, kev=True,
            kev_due_date=v.get("dueDate") or "", kev_ransomware=v.get("knownRansomwareCampaignUse") or "",
            kev_date_added=(v.get("dateAdded") or ""), kev_action=v.get("requiredAction") or "",
            link=f"https://nvd.nist.gov/vuln/detail/{cve}"))
    return out


def _nvd_cvss(metrics: dict) -> tuple[float | None, str]:
    for name in ("cvssMetricV31", "cvssMetricV40", "cvssMetricV30", "cvssMetricV2"):
        rows = metrics.get(name) or []
        primary = next((m for m in rows if m.get("type") == "Primary"), rows[0] if rows else None)
        if primary:
            data = primary.get("cvssData") or {}
            return _float(data.get("baseScore")), data.get("vectorString") or ""
    return None, ""


def parse_nvd(data: dict, prods: list[dict], known: set[str] | None = None) -> list[dict]:
    """NVD CVE API 2.0 records matching a product (by CPE in its configurations; by keyword when NVD hasn't analysed
    it yet), kept when CVSS >= NVD_MIN_CVSS or another source already has the CVE. fixed_in: the
    versionEndExcluding of the matching ranges."""
    known = known or set()
    out = []
    for item in data.get("vulnerabilities", []) if isinstance(data, dict) else []:
        cve = item.get("cve") or {}
        cid = cve.get("id") or ""
        if not CVE_RE.fullmatch(cid):
            continue
        desc = next((d.get("value") for d in cve.get("descriptions", []) if d.get("lang") == "en"), "") or ""
        matches = [m for conf in cve.get("configurations", []) for node in conf.get("nodes", [])
                   for m in node.get("cpeMatch", []) if m.get("vulnerable", True)]
        p = product_by_cpe([m.get("criteria", "") for m in matches], prods) if matches else None
        if p is None and not matches:
            p = product_by_text(desc, prods, strict=True)
        if p is None:
            continue
        cvss, vector = _nvd_cvss(cve.get("metrics") or {})
        if cid not in known and (cvss is None or cvss < NVD_MIN_CVSS):
            continue
        fixes = sorted({m["versionEndExcluding"] for m in matches if m.get("versionEndExcluding")
                        and any(cpe_matches(pat, m.get("criteria", "")) for pat in p["cpes"])},
                       key=lambda v: version_tuple(v) or ())
        published = iso_or_empty(cve.get("published"))
        out.append(_sighting(cve=cid, title=desc[:140], summary=desc[:600], product=p["key"], published=published,
                             date=published, cvss=cvss, cvss_vector=vector, fixed_in=", ".join(fixes),
                             link=f"https://nvd.nist.gov/vuln/detail/{cid}"))
    return out


def parse_github(data, prods: list[dict], feed_products: list[str]) -> list[dict]:
    """GitHub repository security advisories (git/git, git-for-windows/git)."""
    out = []
    default = next((p for p in prods if p["key"] in feed_products), None)
    for adv in data if isinstance(data, list) else []:
        if adv.get("state") not in (None, "published"):
            continue
        text = f"{adv.get('summary', '')} {adv.get('description', '')}"
        p = product_by_text(adv.get("summary", ""), [x for x in prods if x["key"] in feed_products]) or default
        if p is None:
            continue
        patched = " ".join((v.get("patched_versions") or "") for v in adv.get("vulnerabilities") or [])
        fixes = sorted(set(VERSION_RE.findall(patched)), key=lambda v: version_tuple(v) or ())
        cvss = adv.get("cvss") or {}
        published = iso_or_empty(adv.get("published_at"))
        out.append(_sighting(cve=adv.get("cve_id") or "", key=adv.get("cve_id") or f"url:{adv.get('html_url', '')}",
                             title=(adv.get("summary") or "")[:200], summary=_plain(text)[:600], product=p["key"],
                             published=published, date=published, cvss=_float(cvss.get("score")),
                             cvss_vector=cvss.get("vector_string") or "", severity=adv.get("severity") or "",
                             fixed_in=", ".join(fixes), link=adv.get("html_url") or ""))
    return out


def parse_nginx(html: str, prods: list[dict], feed_products: list[str], dated: bool) -> list[dict]:
    """nginx.org's advisories page: one <li> per advisory, lines split by <br>: title, "Severity: …", links (the CVE's
    href carries its id even where the label has a typo), "Not vulnerable: 1.31.9+, 1.30.7+", "Vulnerable: …".
    The page has no dates: an advisory's date is when it was first seen, except on the feed's first poll (dated
    False), when the whole history is on the page and nothing of it is new."""
    p = next((x for x in prods if x["key"] in feed_products), None)
    if p is None:
        return []
    stamp = iso(now()) if dated else ""
    out = []
    for block in re.findall(r"<li>(.*?)</li>", html, re.S | re.I):
        lines = [_plain(x) for x in re.split(r"<br\s*/?>", block, flags=re.I)]
        lines = [x for x in lines if x]
        cves = list(dict.fromkeys(CVE_RE.findall(block)))
        if not lines or not cves:
            continue
        sev = next((x.split(":", 1)[1].strip() for x in lines if x.lower().startswith("severity:")), "")
        safe = next((x.split(":", 1)[1] for x in lines if x.lower().startswith("not vulnerable:")), "")
        fixes = [v for v in VERSION_RE.findall(safe)]
        for cve in cves:
            out.append(_sighting(cve=cve, title=f"nginx: {lines[0]}", product=p["key"], date=stamp, undated=True,
                                 severity=sev,
                                 fixed_in=", ".join(sorted(set(fixes), key=lambda v: version_tuple(v) or ())),
                                 link="https://nginx.org/en/security_advisories.html",
                                 summary=" · ".join(lines[:6])[:600]))
    return out


def parse_mozilla_index(html: str, base: str) -> list[dict]:
    """The advisories list: date headings, then links like 'MFSA 2026-130 Security Vulnerabilities fixed in
    Firefox 160.0.1'. [{title, link, date, fixed_in}]."""
    out, current = [], ""
    for m in re.finditer(r"<h[234][^>]*>(.*?)</h[234]>|<a[^>]+href=\"([^\"]*mfsa\d{4}-\d+/?)\"[^>]*>(.*?)</a>", html, re.S | re.I):
        if m.group(1) is not None:
            found = parse_date(_plain(m.group(1)))
            current = iso(found) if found else current
            continue
        title = _plain(m.group(3))
        fixed = re.search(r"fixed in Firefox(?: ESR)? (\d+(?:\.\d+)*)", title, re.I)
        out.append({"title": title, "link": urljoin(base, m.group(2)), "date": current,
                    "fixed_in": fixed.group(1) if fixed else ""})
    return out


def parse_mozilla_advisory(html: str) -> tuple[list[str], str]:
    """An advisory page: its CVE ids and its impact (critical, high, moderate, low)."""
    impact = re.search(r"Impact[^A-Za-z]{0,80}(critical|high|moderate|low)", _plain(html), re.I)
    return list(dict.fromkeys(CVE_RE.findall(html))), impact.group(1).lower() if impact else ""


def parse_msrc(doc: dict, prods: list[dict], feed_products: list[str]) -> list[dict]:
    """One CVRF document (JSON): each vulnerability whose affected products name one of ours."""
    mine = [p for p in prods if p["key"] in feed_products]
    names = {}
    tree = doc.get("ProductTree") or {}
    for fp in tree.get("FullProductName") or []:
        names[fp.get("ProductID")] = fp.get("Value") or ""
    released = iso_or_empty((doc.get("DocumentTracking") or {}).get("CurrentReleaseDate"))
    out = []
    for v in doc.get("Vulnerability") or []:
        cve = v.get("CVE") or ""
        if not CVE_RE.fullmatch(cve):
            continue
        affected = [names.get(pid, "") for st in v.get("ProductStatuses") or [] for pid in st.get("ProductID") or []]
        p = next((x for x in mine if any(product_by_text(n, [x]) for n in affected)), None)
        if p is None:
            continue
        threats = v.get("Threats") or []
        sev = next(((t.get("Description") or {}).get("Value", "") for t in threats if t.get("Type") == 3), "")
        exploited = any("exploited:yes" in ((t.get("Description") or {}).get("Value", "")).lower().replace(" ", "")
                        for t in threats if t.get("Type") == 1)
        scores = [(s.get("BaseScore"), s.get("Vector", "")) for s in v.get("CVSSScoreSets") or [] if s.get("BaseScore") is not None]
        best = max(scores, key=lambda s: float(s[0])) if scores else (None, "")
        builds = [r.get("FixedBuild") for r in v.get("Remediations") or [] if r.get("FixedBuild")]
        kbs = [f"KB{(r.get('Description') or {}).get('Value', '')}" for r in v.get("Remediations") or []
               if r.get("Type") == 2 and str((r.get("Description") or {}).get("Value", "")).isdigit()]
        history = v.get("RevisionHistory") or []
        published = iso_or_empty(history[0].get("Date")) if history else released
        out.append(_sighting(cve=cve, title=((v.get("Title") or {}).get("Value") or cve)[:200], product=p["key"],
                             published=published or released, date=published or released, cvss=_float(best[0]),
                             cvss_vector=best[1] or "", severity=sev,
                             fixed_in=", ".join(dict.fromkeys(builds[:3] or kbs[:3])),
                             summary=("Exploited (Microsoft). " if exploited else "") + "Affects: " +
                                     ", ".join(sorted({n for n in affected if n}))[:500],
                             link=f"https://msrc.microsoft.com/update-guide/vulnerability/{cve}"))
    return out


def parse_xml_feed(content: bytes) -> list[dict]:
    """RSS or Atom entries: [{title, link, summary, published}]. Documents with a DOCTYPE aren't read (entities)."""
    if b"<!DOCTYPE" in content[:2000].upper() or b"<!ENTITY" in content[:5000].upper():
        raise FeedError("feeds with a DOCTYPE aren't read")
    try:
        root = ElementTree.fromstring(content)
    except ElementTree.ParseError:
        raise FeedError("the feed isn't valid RSS or Atom") from None
    out = []
    atom = "{http://www.w3.org/2005/Atom}"
    for item in root.iter("item"):
        out.append({"title": (item.findtext("title") or "").strip(), "link": (item.findtext("link") or "").strip(),
                    "summary": _plain(item.findtext("description") or "")[:600],
                    "published": (item.findtext("pubDate") or "").strip()})
    for entry in root.iter(atom + "entry"):
        link = entry.find(atom + "link")
        out.append({"title": (entry.findtext(atom + "title") or "").strip(),
                    "link": link.get("href", "") if link is not None else "",
                    "summary": _plain(entry.findtext(atom + "summary") or entry.findtext(atom + "content") or "")[:600],
                    "published": (entry.findtext(atom + "updated") or entry.findtext(atom + "published") or "").strip()})
    return out[:300]


def parse_rss_items(entries: list[dict], prods: list[dict], feed_products: list[str], keywords: str) -> list[dict]:
    """Vendor RSS/Atom entries: one sighting per CVE an entry names, else one for the entry itself. An admin's own
    feed with keywords keeps only the entries naming one."""
    words = [w.strip() for w in keywords.split(",") if w.strip()]
    mine = [p for p in prods if p["key"] in feed_products]
    out = []
    for e in entries:
        text = f"{e['title']} {e['summary']}"
        if words and not any(keyword_in(w, text) for w in words):
            continue
        p = product_by_text(text, mine or prods) or (mine[0] if len(mine) == 1 else None)
        published = iso_or_empty(e["published"])
        common = dict(title=e["title"][:200], summary=e["summary"], product=p["key"] if p else "",
                      published=published, date=published, link=e["link"])
        cves = list(dict.fromkeys(CVE_RE.findall(text)))[:50]
        if cves:
            out.extend(_sighting(cve=c, **common) for c in cves)
        else:
            out.append(_sighting(**common))
    return out


def parse_html_cves(html: str, prods: list[dict], feed_products: list[str], url: str, dated: bool) -> list[dict]:
    p = next((x for x in prods if x["key"] in feed_products), None)
    stamp = iso(now()) if dated else ""
    return [_sighting(cve=c, title=c, product=p["key"] if p else "", date=stamp, undated=True, link=url)
            for c in dict.fromkeys(CVE_RE.findall(html))][:300]


# ------------------------------------------------------------------- fetch
def fetch(feed: sqlite3.Row, prods: list[dict], settings: Settings | None, known: set[str]) -> tuple[list[dict], str, str]:
    """(sightings, the source name, the NVD cursor to save) for one feed; FeedError when it fails."""
    kind, url = feed["kind"], feed["url"]
    feed_products = _jl(feed["products"]) or [p["key"] for p in prods]
    dated = bool(feed["last_success"])
    with _client() as client:
        if kind == "json_kev":
            return parse_kev(_json(_get(client, url)), prods), "kev", ""
        if kind == "nvd":
            return _fetch_nvd(client, feed, prods, settings, known)
        if kind == "github":
            got = _json(_get(client, url, params={"per_page": "100", "state": "published"},
                             headers={"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}))
            return parse_github(got, prods, feed_products), "github", ""
        if kind == "nginx":
            return parse_nginx(_get(client, url).text, prods, feed_products, dated), "nginx", ""
        if kind == "mozilla":
            return _fetch_mozilla(client, url, prods, feed_products, known), "mozilla", ""
        if kind == "msrc":
            index = _json(_get(client, url, headers={"Accept": "application/json"}))
            docs = sorted(index.get("value") or [], key=lambda d: d.get("CurrentReleaseDate") or "", reverse=True)[:2]
            out = []
            for d in docs:
                if d.get("CvrfUrl"):
                    out += parse_msrc(_json(_get(client, d["CvrfUrl"], headers={"Accept": "application/json"})),
                                      prods, feed_products)
            return out, "msrc", ""
        if kind in ("rss", "atom"):
            entries = parse_xml_feed(_get(client, url).content)
            return parse_rss_items(entries, prods, _jl(feed["products"]), feed["keywords"]), feed["key"] or kind, ""
        if kind == "html":
            return parse_html_cves(_get(client, url).text, prods, feed_products, url, dated), feed["key"] or "html", ""
    raise FeedError(f"unknown kind {kind}")


def _fetch_mozilla(client, url: str, prods, feed_products, known: set[str]) -> list[dict]:
    """The list, then the pages of new Firefox advisories (dated within HISTORY_DAYS) for their CVEs; an advisory
    whose page couldn't be read is kept as itself."""
    mine = [p for p in prods if p["key"] in feed_products]
    cutoff = iso(now() - timedelta(days=HISTORY_DAYS))
    out, pages = [], 0
    for adv in parse_mozilla_index(_get(client, url).text, url):
        p = product_by_text(adv["title"], mine)
        if p is None or (adv["date"] and adv["date"] < cutoff):
            continue
        common = dict(product=p["key"], published=adv["date"], date=adv["date"], fixed_in=adv["fixed_in"],
                      link=adv["link"])
        if f"url:{adv['link']}" in known or pages >= MOZILLA_PAGES:
            out.append(_sighting(title=adv["title"], **common))
            continue
        pages += 1
        try:
            cves, impact = parse_mozilla_advisory(_get(client, adv["link"]).text)
        except FeedError:
            cves, impact = [], ""
        if not cves:
            out.append(_sighting(title=adv["title"], **common))
        out += [_sighting(cve=c, title=f"{c} ({adv['title']})", severity=impact, **common) for c in cves]
    return out


def _fetch_nvd(client, feed, prods, settings, known: set[str]) -> tuple[list[dict], str, str]:
    """A lastModStartDate/lastModEndDate window from the last one's end (NVD_FIRST_DAYS the first time, at most 120
    days), paged; then CVEs other sources brought that NVD hasn't been asked about, for their scores."""
    from mnemerynne import apis

    key = apis.decrypt_secret(settings, feed["secret_enc"]) if settings is not None and feed["secret_enc"] else None
    headers = {"apiKey": key} if key else {}
    pause = 0.7 if key else 6.5                    # NVD: 50 requests / 30 s with a key, 5 without
    end = now()
    start = parse_date(feed["cursor"]) or end - timedelta(days=NVD_FIRST_DAYS)
    start = max(start, end - timedelta(days=119))
    fmt = "%Y-%m-%dT%H:%M:%S.000"
    out, index = [], 0
    for page in range(NVD_MAX_PAGES):
        if page:
            SLEEP(pause)
        got = _json(_get(client, feed["url"], headers=headers, params={
            "lastModStartDate": start.strftime(fmt), "lastModEndDate": end.strftime(fmt),
            "resultsPerPage": str(NVD_PAGE), "startIndex": str(index)}))
        out += parse_nvd(got, prods, known)
        index += int(got.get("resultsPerPage") or len(got.get("vulnerabilities") or []) or NVD_PAGE)
        if index >= int(got.get("totalResults") or 0):
            break
    have = {s["cve"] for s in out}
    for cve in [k for k in known if k.startswith("CVE-") and k not in have][:NVD_ENRICH if key else 3]:
        SLEEP(pause)
        try:
            out += parse_nvd(_json(_get(client, feed["url"], headers=headers, params={"cveId": cve})), prods, known)
        except FeedError as exc:
            if exc.status in (403, 429):
                raise
    return out, "nvd", iso(end)


# ------------------------------------------------------------------- merge
_PRIORITY = {"nvd": 0, "kev": 1}                    # vendor sources rank above both for titles, fixes and links


def merge(sightings: dict[str, dict], first_seen: str) -> dict:
    """One item from every source's sighting of it ({source: data})."""
    vendor = [s for name, s in sightings.items() if name not in _PRIORITY]
    kev = sightings.get("kev")
    nvd = sightings.get("nvd")
    ordered = vendor + [x for x in (kev, nvd) if x]

    def first(field, among=None):
        return next((s[field] for s in (among or ordered) if s.get(field)), "")

    cvss_from = [x for x in (nvd,) if x] + vendor
    cvss = next((s["cvss"] for s in cvss_from if s.get("cvss") is not None), None)
    vector = next((s["cvss_vector"] for s in cvss_from if s.get("cvss") is not None and s.get("cvss_vector")), "")
    published = min([s["published"] for s in ordered if s.get("published")] or [""])
    any_s = ordered[0] if ordered else {}
    return {"cve": first("cve"), "title": first("title", vendor + ([kev] if kev else []) + ([nvd] if nvd else [])),
            "product": first("product"), "vendor": "", "sources": sorted(sightings),
            "published": published, "first_seen": first_seen, "cvss": cvss, "cvss_vector": vector,
            "severity": severity_of(cvss, first("severity")), "kev": kev is not None,
            "kev_due_date": (kev or {}).get("kev_due_date", ""), "kev_ransomware": (kev or {}).get("kev_ransomware", ""),
            "fixed_in": first("fixed_in", vendor + ([nvd] if nvd else [])), "link": first("link") or any_s.get("link", ""),
            "summary": first("summary"),
            "kev_date_added": (kev or {}).get("kev_date_added", "")}


def store(fdb: sqlite3.Connection, feed_id: int, source: str, sightings: list[dict]) -> list[str]:
    """Save a poll's sightings and re-merge the items they touch; the keys of items new to us."""
    at = iso(now())
    new = []
    seen_here: set[str] = set()
    with db.transaction(fdb):
        for s in sightings:
            key = s["key"]
            if not key or key in seen_here:
                continue
            seen_here.add(key)
            row = fdb.execute("SELECT first_seen FROM security_items WHERE key = ?", (key,)).fetchone()
            if row is None:
                fdb.execute("INSERT INTO security_items(key, data, activity_at, first_seen, updated_at) "
                            "VALUES (?, '{}', '', ?, ?)", (key, at, at))
                new.append(key)
            old = fdb.execute("SELECT data FROM security_sightings WHERE item_key = ? AND source = ?",
                              (key, source)).fetchone()
            data = dict(s)
            if old is not None:
                prev = json.loads(old["data"])
                if data.get("undated"):                  # a page without dates: its date is the day it first listed
                    data["date"] = prev.get("date", "")  # the item ('' if that was its first poll's history)
                elif not data.get("date") and prev.get("date"):
                    data["date"] = prev["date"]
            fdb.execute("INSERT INTO security_sightings(item_key, source, feed_id, data, first_seen, last_seen) "
                        "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(item_key, source) DO UPDATE SET data = excluded.data, "
                        "last_seen = excluded.last_seen, feed_id = excluded.feed_id",
                        (key, source, feed_id, json.dumps(data), at, at))
            remerge(fdb, key)
    return new


def remerge(fdb: sqlite3.Connection, key: str) -> None:
    item = fdb.execute("SELECT first_seen FROM security_items WHERE key = ?", (key,)).fetchone()
    found = {r["source"]: json.loads(r["data"]) for r in
             fdb.execute("SELECT source, data FROM security_sightings WHERE item_key = ?", (key,))}
    merged = merge(found, item["first_seen"])
    dates = [s.get("date") or "" for s in found.values()] + [merged["published"]]
    activity = max(dates) if any(dates) else ""
    fdb.execute("UPDATE security_items SET data = ?, activity_at = ?, updated_at = ? WHERE key = ?",
                (json.dumps(merged), activity, iso(now()), key))


def purge(fdb: sqlite3.Connection) -> int:
    """Items quiet for PURGE_DAYS that no source lists any more."""
    cutoff = iso(now() - timedelta(days=PURGE_DAYS))
    with db.transaction(fdb):
        return fdb.execute("DELETE FROM security_items WHERE activity_at < ? AND updated_at < ? AND NOT EXISTS "
                           "(SELECT 1 FROM security_sightings s WHERE s.item_key = key AND s.last_seen >= ?)",
                           (cutoff, cutoff, cutoff)).rowcount


# ----------------------------------------------------------------- polling
def next_due(feed: sqlite3.Row) -> datetime:
    if feed["backoff_until"]:
        return parse_date(feed["backoff_until"]) or now()
    last = parse_date(feed["last_attempt"])
    return last + timedelta(seconds=feed["interval_seconds"]) if last else now() - timedelta(seconds=1)


def poll_due(conn: sqlite3.Connection, settings: Settings | None, *, force: bool = False,
             only: int | None = None) -> list[str]:
    """Poll every enabled feed that's due (force: every enabled one; only: that feed), then push to Wazuh. The names
    of the feeds polled. One poll at a time per process; a second caller returns at once."""
    if not _poll_lock.acquire(blocking=False):
        return []
    try:
        with _db(conn) as fdb:
            if fdb is None:
                return []
            ensure_defaults(fdb)
            prods = products(fdb)
            polled = []
            for feed in fdb.execute("SELECT * FROM automation_feeds WHERE enabled = 1 ORDER BY id").fetchall():
                if only is not None and feed["id"] != only:
                    continue
                if not force and only is None and next_due(feed) > now():
                    continue
                poll_one(fdb, feed, prods, settings)
                polled.append(feed["name"])
            if polled:
                purge(fdb)
                push_to_wazuh(fdb, settings)
            return polled
    finally:
        _poll_lock.release()


def poll_one(fdb: sqlite3.Connection, feed: sqlite3.Row, prods: list[dict], settings: Settings | None) -> None:
    started = iso(now())
    known = {r[0] for r in fdb.execute("SELECT key FROM security_items")}
    try:
        sightings, source, cursor = fetch(feed, prods, settings, known)
    except FeedError as exc:
        failures = feed["failures"] + 1
        if exc.status in (403, 429):
            wait = max(exc.retry_after or 0, min(DAY, 900 * 2 ** (failures - 1)))
        else:
            wait = min(feed["interval_seconds"], 600 * 2 ** (failures - 1))
        with db.transaction(fdb):
            fdb.execute("UPDATE automation_feeds SET last_attempt = ?, error = ?, failures = ?, backoff_until = ? "
                        "WHERE id = ?", (started, str(exc), failures, iso(now() + timedelta(seconds=wait)), feed["id"]))
        log.warning("Security feed %s failed: %s", feed["name"], exc)
        return
    except Exception as exc:                        # a parser surprise must still show on feed_status
        log.exception("Security feed %s couldn't be read", feed["name"])
        with db.transaction(fdb):
            fdb.execute("UPDATE automation_feeds SET last_attempt = ?, error = ?, failures = failures + 1, "
                        "backoff_until = ? WHERE id = ?",
                        (started, f"couldn't read the answer: {type(exc).__name__}",
                         iso(now() + timedelta(seconds=min(feed['interval_seconds'], 3600))), feed["id"]))
        return
    store(fdb, feed["id"], source, sightings)
    with db.transaction(fdb):
        fdb.execute("UPDATE automation_feeds SET last_attempt = ?, last_success = ?, error = '', failures = 0, "
                    "backoff_until = NULL, items_last_fetch = ?, fetched_at = ?, cursor = CASE WHEN ? = '' THEN cursor "
                    "ELSE ? END WHERE id = ?",
                    (started, iso(now()), len(sightings), started.rstrip("Z"), cursor, cursor, feed["id"]))


# ---------------------------------------------------------------- inventory
def _inventory_api(conn: sqlite3.Connection):
    """The API (apis row) whose operations read the indexer's package inventory: the Wazuh preset's
    Installed packages."""
    try:
        return conn.execute("SELECT a.* FROM apis a JOIN api_operations o ON o.api_id = a.id WHERE o.path LIKE "
                            "'%wazuh-states-inventory-packages%' ORDER BY a.id LIMIT 1").fetchone()
    except sqlite3.OperationalError:
        return None


def inventory(conn: sqlite3.Connection, settings: Settings | None, prods: list[dict]) -> dict:
    """{"status": "ok"|"none"|"error", "error": str, "by_product": {key: [{agent, package, version}]}, "at": iso}.
    Read from Wazuh's indexer with the Wazuh API's own address, credential and trusted CA (apis.py); cached
    INVENTORY_SECONDS."""
    from mnemerynne import apis

    patterns = {p["key"]: p["packages"] for p in prods if p["packages"]}
    api = _inventory_api(conn)
    if api is None:
        return {"status": "none", "error": "No Wazuh API with the Installed packages operation is set up (Access → "
                                           "APIs → add the Wazuh preset), so affects_us can't be filled in.",
                "by_product": {}, "at": ""}
    if settings is None or not patterns:
        return {"status": "none", "error": "", "by_product": {}, "at": ""}
    cache_key = (api["id"], json.dumps(patterns, sort_keys=True))
    hit = _inventory_cache
    if hit["key"] == cache_key and hit["value"] is not None and time.time() - hit["at"] < INVENTORY_SECONDS:
        return hit["value"]
    every = sorted({x for pats in patterns.values() for x in pats})
    body = json.dumps({"size": INVENTORY_ROWS, "query": {"bool": {"should": [
        {"wildcard": {"package.name": {"value": x, "case_insensitive": True}}} for x in every],
        "minimum_should_match": 1}}, "_source": ["agent.name", "package.name", "package.version"]})
    url = api["base_url"].rstrip("/") + "/wazuh-states-inventory-packages-*/_search"
    try:
        status, _, raw, truncated = apis._fetch(api, "POST", url, {}, body, apis._headers(settings, api))
        if status != 200:
            raise FeedError(apis._explain(status, raw.decode("utf-8", "replace")))
        if truncated:
            raise FeedError("the inventory answer was too large")
        hits = (json.loads(raw).get("hits") or {}).get("hits") or []
    except (FeedError, apis.ApiError, httpx.HTTPError, OSError, ValueError) as exc:
        value = {"status": "error", "error": f"Wazuh inventory: {exc}"[:300], "by_product": {}, "at": iso(now())}
        hit.update(at=time.time() - INVENTORY_SECONDS + 60, key=cache_key, value=value)   # retried within a minute
        return value
    by: dict[str, list] = {}
    for h in hits:
        src = h.get("_source") or {}
        name = (src.get("package") or {}).get("name") or ""
        row = {"agent": (src.get("agent") or {}).get("name") or "", "package": name,
               "version": (src.get("package") or {}).get("version") or ""}
        for key, pats in patterns.items():
            if any(fnmatch.fnmatch(name.lower(), x.lower()) for x in pats):
                by.setdefault(key, []).append(row)
    value = {"status": "ok", "error": "", "by_product": by, "at": iso(now())}
    hit.update(at=time.time(), key=cache_key, value=value)
    return value


def affects_us(item: dict, inv: dict, prods_by_key: dict) -> dict:
    """{machines, agents, versions, version_check, unclear_agents}: the machines with the product at a version below
    fixed_in. version_check: compared, no-fixed-version (every install counted), distro-backport-unknown (some
    distro builds older than the fix weren't counted: they may be patched), no-inventory, inventory-error."""
    out = {"machines": 0, "agents": [], "versions": [], "version_check": "no-inventory", "unclear_agents": []}
    p = prods_by_key.get(item.get("product") or "")
    if inv["status"] == "error":
        out["version_check"] = "inventory-error"
        return out
    if inv["status"] != "ok" or p is None or not p["packages"]:
        return out
    installs = inv["by_product"].get(p["key"], [])
    hit_agents, versions, unclear, compared = set(), set(), set(), False
    for row in installs:
        state = version_status(row["version"], item.get("fixed_in", ""))
        if state == "fixed":
            compared = True
            continue
        if state == "unclear":
            unclear.add(row["agent"])
            continue
        compared = compared or state == "vulnerable"
        hit_agents.add(row["agent"])
        versions.add(row["version"])
    unclear -= hit_agents
    out.update(machines=len(hit_agents), agents=sorted(hit_agents)[:100], versions=sorted(versions)[:50],
               unclear_agents=sorted(unclear)[:100],
               version_check=("distro-backport-unknown" if unclear else
                              "no-fixed-version" if not fixed_versions(item.get("fixed_in", "")) else "compared"))
    return out


# ------------------------------------------------------------------- wazuh
def log_path(settings: Settings | None) -> Path | None:
    if settings is None:
        return None
    return settings.security_feeds_log or settings.data_dir / "logs" / "security-feeds.json"


def push_to_wazuh(fdb: sqlite3.Connection, settings: Settings | None) -> int:
    """One JSON line per new urgent item that affects us (KEV, or CVSS >= PUSH_MIN_CVSS), in the log Wazuh's agent
    reads, and never again for that item (wazuh_pushed_at). Only items active in the last PUSH_RECENT_DAYS, so the
    first poll's history isn't an alarm. The rules (100350-100359) are work Lumi's."""
    path = log_path(settings)
    if path is None:
        return 0
    cutoff = iso(now() - timedelta(days=PUSH_RECENT_DAYS))
    rows = fdb.execute("SELECT key, data FROM security_items WHERE wazuh_pushed_at IS NULL AND activity_at >= ?",
                       (cutoff,)).fetchall()
    urgent = []
    for r in rows:
        item = json.loads(r["data"])
        if item.get("kev") or (item.get("cvss") or 0) >= PUSH_MIN_CVSS:
            urgent.append((r["key"], item))
    if not urgent:
        return 0
    prods = {p["key"]: p for p in products(fdb, enabled_only=False)}
    inv = inventory(fdb, settings, list(prods.values()))
    if inv["status"] != "ok":
        return 0                                     # unknown isn't "not affected": try again next poll
    lines, keys = [], []
    for key, item in urgent:
        hit = affects_us(item, inv, prods)
        if hit["machines"] <= 0:
            continue
        p = prods.get(item["product"]) or {}
        lines.append(json.dumps({
            "integration": "security-feeds", "event": "kev_affects_us" if item["kev"] else "critical_affects_us",
            "cve": item["cve"] or key, "title": item["title"], "product": p.get("name", item["product"]),
            "vendor": p.get("vendor", ""), "cvss": item["cvss"], "severity": item["severity"], "kev": item["kev"],
            "kev_due_date": item["kev_due_date"], "kev_ransomware": item["kev_ransomware"],
            "fixed_in": item["fixed_in"], "machines": hit["machines"], "agents": hit["agents"],
            "versions": hit["versions"], "version_check": hit["version_check"], "sources": item["sources"],
            "link": item["link"], "first_seen": item["first_seen"], "at": iso(now())}, ensure_ascii=False))
        keys.append(key)
    if not lines:
        return 0
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:     # one line per item: Wazuh's <log_format>json</log_format>
        fh.write("\n".join(lines) + "\n")
    with db.transaction(fdb):
        fdb.executemany("UPDATE security_items SET wazuh_pushed_at = ? WHERE key = ?", [(iso(now()), k) for k in keys])
    return len(lines)


# -------------------------------------------------------------------- read
def feed_status(fdb: sqlite3.Connection, inv: dict | None = None) -> list[dict]:
    out = [{"feed": f["name"], "kind": f["kind"], "enabled": bool(f["enabled"]), "last_success": f["last_success"] or "",
            "last_attempt": f["last_attempt"] or "", "error": f["error"] or "", "items_last_fetch": f["items_last_fetch"],
            "next_poll": iso(next_due(f)) if f["enabled"] else "", "note": f["note"] or ""}
           for f in fdb.execute("SELECT * FROM automation_feeds ORDER BY id")]
    if inv is not None:
        out.append({"feed": INVENTORY_FEED, "kind": "wazuh", "enabled": True,
                    "last_success": inv["at"] if inv["status"] == "ok" else "", "last_attempt": inv["at"],
                    "error": inv["error"], "items_last_fetch": sum(len(v) for v in inv["by_product"].values()),
                    "next_poll": "", "note": "Installed packages from Wazuh's indexer, for affects_us."})
    return out


def query(conn: sqlite3.Connection, settings: Settings | None, since_days: int = 7, product: str | None = None,
          min_cvss: float | None = None, kev_only: bool = False, affects_us_only: bool = False,
          poll: bool = True) -> dict:
    """What the security_feeds tool answers: {since_days, filters, count, items, truncated, feed_status, note}.
    Items most urgent first: in KEV and affecting us, then CVSS >= 9 affecting us, then by score."""
    try:
        since_days = max(1, min(int(since_days or 7), HISTORY_DAYS))
    except (TypeError, ValueError):
        since_days = 7
    min_cvss = _float(min_cvss)
    if poll:
        poll_due(conn, settings)
    with _db(conn) as fdb:
        if fdb is None:
            return {"since_days": since_days, "count": 0, "items": [], "feed_status": [],
                    "note": "The security feeds' tables aren't there yet: restart Mnemerynne so its migrations run."}
        ensure_defaults(fdb)
        prods = {p["key"]: p for p in products(fdb, enabled_only=False)}
        enabled = {k for k, p in prods.items() if p["enabled"]}
        inv = inventory(fdb, settings, [p for p in prods.values() if p["enabled"]])
        cutoff = iso(now() - timedelta(days=since_days))
        rows = fdb.execute("SELECT data FROM security_items WHERE activity_at >= ? ORDER BY activity_at DESC",
                           (cutoff,)).fetchall()
        want = (product or "").strip().lower()
        items = []
        for r in rows:
            item = json.loads(r["data"])
            p = prods.get(item.get("product") or "")
            if p is not None and item["product"] not in enabled:
                continue
            if want and not (p and (want in p["key"].lower() or want in p["name"].lower()
                                    or want in p["vendor"].lower())):
                continue
            if kev_only and not item["kev"]:
                continue
            if min_cvss is not None and (item["cvss"] is None or item["cvss"] < min_cvss):
                continue
            item["affects_us"] = affects_us(item, inv, prods)
            if affects_us_only and item["affects_us"]["machines"] <= 0:
                continue
            item["product"], item["vendor"] = (p["name"], p["vendor"]) if p else (item.get("product") or "", "")
            item.pop("kev_date_added", None)
            items.append({k: item.get(k) for k in ITEM_FIELDS} | {"affects_us": item["affects_us"]})
        items.sort(key=lambda i: (not (i["kev"] and i["affects_us"]["machines"] > 0),
                                  not ((i["cvss"] or 0) >= 9 and i["affects_us"]["machines"] > 0),
                                  -(i["cvss"] or 0), i["published"] or ""))
        status = feed_status(fdb, inv)
    broken = [s["feed"] for s in status if s["enabled"] and s["error"]]
    note = ("Every feed answered." if not broken else
            f"Not working: {', '.join(broken)} (see feed_status). An empty answer from them is not 'nothing new'.")
    return {"since_days": since_days,
            "filters": {"product": product or "", "min_cvss": min_cvss, "kev_only": bool(kev_only),
                        "affects_us_only": bool(affects_us_only)},
            "count": len(items), "items": items[:MAX_ITEMS], "truncated": len(items) > MAX_ITEMS,
            "feed_status": status, "note": note}


def wazuh_localfile(settings: Settings) -> str:
    """The <localfile> block for the Wazuh agent on this server (shown on the admin page and in the help)."""
    return (f"<localfile>\n  <log_format>json</log_format>\n  <location>{log_path(settings)}</location>\n"
            "</localfile>")


def clear_cache() -> None:
    """Forget the cached Wazuh inventory (tests; the admin page's Poll now)."""
    _inventory_cache.update(at=0.0, key=None, value=None)
