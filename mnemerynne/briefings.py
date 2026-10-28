"""What the briefing and security tools read (mcp_server.Tools wraps them for Erynne and Claude):

- my_briefing: what's waiting for the person inside Mnemerynne (unread notes, announcements, room
  mentions and direct messages, proposals to apply). Mail and calendar have their own tools.
- mnemerynne_updates (admins): new commits upstream and what they touch (updates.check), and the
  installed packages' known vulnerabilities (pip-audit, when it's installed; cached).
- m365_security (admins): open Defender alerts, risky users, the latest Secure Score and risky sign-ins
  per Microsoft 365 tenant, read with the app's own token. They need application permissions on the
  Entra app (GRAPH_PERMISSIONS); a 403 says which.
- security_feeds: the admin's outside feeds (RSS/Atom advisories, CISA KEV, NVD), fetched at most
  hourly, kept to the feed's keywords.
"""

from __future__ import annotations

import json
import re
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from xml.etree import ElementTree

import httpx

from mnemerynne import m365, messages, notices, rooms, updates
from mnemerynne.config import Settings

FEED_SECONDS = 3600
AUDIT_SECONDS = 6 * 3600
FEED_MAX_BYTES = 8 * 1024 * 1024
FEED_ITEMS = 40
GRAPH_PERMISSIONS = {"alerts": "SecurityAlert.Read.All", "risky_users": "IdentityRiskyUser.Read.All",
                     "secure_score": "SecurityEvents.Read.All", "sign_ins": "AuditLog.Read.All"}
SECURITY_KINDS = tuple(GRAPH_PERMISSIONS)

# Tests: an httpx transport for the feeds, and pip-audit's output in place of running it.
TRANSPORT: httpx.BaseTransport | None = None
AUDIT_OUTPUT: str | None = None
_audit = {"at": 0.0, "value": None}
_audit_lock = threading.Lock()


class BriefingError(ValueError):
    """Refused or failed; the message is safe to show."""


# ---------------------------------------------------------------- briefing
def briefing(conn: sqlite3.Connection, user: sqlite3.Row, directory) -> dict:
    notes = messages.inbox(conn, user["id"], unread_only=True, limit=20)
    pending = notices.pending_for(conn, user, directory)
    out: dict = {"unread_notes": [{"from": n["from"], "at": n["at"], "text": n["text"][:300]} for n in notes],
                 "announcements": [{"title": a["title"], "must_acknowledge": bool(a.get("must_ack"))} for a in pending]}
    if rooms.enabled(conn):
        listed = rooms.rooms_for(conn, user, directory)
        out["rooms"] = [{"room": ("#" if r["kind"] == "channel" else "") + r["name"], "unread": r["unread"],
                         "mentions": r["mentions"]} for r in listed if r["unread"] or r["mentions"]]
    waiting = conn.execute("SELECT COUNT(*) FROM agent_proposals WHERE user_id = ? AND status = 'pending'",
                           (user["id"],)).fetchone()[0] if _has_table(conn, "agent_proposals") else 0
    out["proposals_waiting"] = waiting
    out["note"] = ("Mail and calendar come from their own tools (recent_mail, calendar) when the person has "
                   "Microsoft 365 linked.")
    return out


def _has_table(conn: sqlite3.Connection, name: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)).fetchone() is not None


# ----------------------------------------------------------------- updates
def mnemerynne_updates(settings: Settings) -> dict:
    found = updates.check(settings)
    out = {"installed": found["current"].get("short"), "status": found["status"], "new_commits": found["ahead"],
           "commits": found["commits"][:30], "touches": found["flags"], "error": found["error"],
           "how_to_update": found["command"], "checked_at": found["checked_at"]}
    out["packages"] = package_audit()
    return out


def package_audit() -> dict:
    """pip-audit over the installed environment, cached AUDIT_SECONDS; says so when it isn't installed."""
    with _audit_lock:
        if _audit["value"] is not None and time.time() - _audit["at"] < AUDIT_SECONDS:
            return _audit["value"]
    raw = AUDIT_OUTPUT
    if raw is None:
        exe = shutil.which("pip-audit")
        if exe is None:
            return {"checked": False, "note": "pip-audit isn't installed on the server (uv tool install pip-audit), "
                                              "so the packages' known vulnerabilities weren't checked."}
        try:
            done = subprocess.run([exe, "--format", "json", "--progress-spinner", "off", "--path",
                                   str(_site_packages())], capture_output=True, text=True, timeout=300)
            raw = done.stdout
        except (OSError, subprocess.TimeoutExpired) as exc:
            return {"checked": False, "note": f"pip-audit didn't finish: {type(exc).__name__}."}
    try:
        data = json.loads(raw or "{}")
    except ValueError:
        return {"checked": False, "note": "pip-audit's output couldn't be read."}
    found = []
    for dep in data.get("dependencies", []) if isinstance(data, dict) else data:
        for v in dep.get("vulns", []):
            found.append({"package": dep.get("name"), "version": dep.get("version"), "id": v.get("id"),
                          "fixed_in": v.get("fix_versions") or [], "aliases": v.get("aliases") or [],
                          "summary": (v.get("description") or "")[:300]})
    value = {"checked": True, "vulnerable": found, "checked_at": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime())}
    with _audit_lock:
        _audit.update(at=time.time(), value=value)
    return value


def _site_packages() -> str:
    for p in sys.path:
        if p.endswith("site-packages"):
            return p
    return sys.prefix


# ------------------------------------------------------------ 365 security
def m365_security(conn: sqlite3.Connection, settings: Settings, kind: str, tenant: str | None = None) -> dict:
    if kind not in SECURITY_KINDS:
        raise BriefingError(f"kind is one of {', '.join(SECURITY_KINDS)}.")
    rows = [t for t in m365.tenants(conn, enabled_only=True)
            if not tenant or tenant.lower() in (t["label"].lower(), t["tenant_id"].lower())]
    if not rows:
        raise BriefingError("No Microsoft 365 tenant is set up" + (f" called {tenant}." if tenant else "."))
    out = []
    for row in rows:
        entry: dict = {"tenant": row["label"]}
        try:
            graph = m365.Graph(m365.app_token(settings, row))
            entry.update(_security(graph, kind))
        except m365.GraphError as exc:
            entry["error"] = (f"Microsoft refused: grant the Entra app the application permission "
                              f"{GRAPH_PERMISSIONS[kind]} (with admin consent)." if exc.status in (401, 403)
                              else f"Microsoft answered {exc.status}: {exc}")
        except (m365.M365Error, httpx.HTTPError) as exc:
            entry["error"] = str(exc) or type(exc).__name__
        out.append(entry)
    return {"kind": kind, "tenants": out}


def _security(graph: "m365.Graph", kind: str) -> dict:
    if kind == "alerts":
        got = graph.get("/security/alerts_v2", {"$filter": "status ne 'resolved'", "$top": "50",
                                                "$orderby": "createdDateTime desc"})
        return {"open_alerts": [{"title": a.get("title"), "severity": a.get("severity"), "status": a.get("status"),
                                 "category": a.get("category"), "created": a.get("createdDateTime"),
                                 "service": a.get("serviceSource"), "link": a.get("alertWebUrl")}
                                for a in got.get("value", [])]}
    if kind == "risky_users":
        got = graph.get("/identityProtection/riskyUsers", {"$filter": "riskState eq 'atRisk'", "$top": "50"})
        return {"risky_users": [{"user": u.get("userPrincipalName"), "risk": u.get("riskLevel"),
                                 "detail": u.get("riskDetail"), "updated": u.get("riskLastUpdatedDateTime")}
                                for u in got.get("value", [])]}
    if kind == "secure_score":
        got = graph.get("/security/secureScores", {"$top": "1"})
        latest = (got.get("value") or [{}])[0]
        return {"secure_score": {"current": latest.get("currentScore"), "max": latest.get("maxScore"),
                                 "date": latest.get("createdDateTime")}}
    since = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    got = graph.get("/auditLogs/signIns", {"$filter": f"createdDateTime ge {since} and riskLevelDuringSignIn ne 'none'",
                                           "$top": "50"})
    return {"risky_sign_ins": [{"user": s.get("userPrincipalName"), "risk": s.get("riskLevelDuringSignIn"),
                                "at": s.get("createdDateTime"), "app": s.get("appDisplayName"),
                                "where": ", ".join(filter(None, [(s.get("location") or {}).get("city"),
                                                                 (s.get("location") or {}).get("countryOrRegion")])),
                                "ip": s.get("ipAddress")} for s in got.get("value", [])]}


# ------------------------------------------------------------------- feeds
def security_feeds(conn: sqlite3.Connection, since_days: int = 7) -> dict:
    since_days = max(1, min(int(since_days or 7), 90))
    cutoff = datetime.now(timezone.utc) - timedelta(days=since_days)
    rows = conn.execute("SELECT * FROM automation_feeds WHERE enabled = 1 ORDER BY name").fetchall()
    if not rows:
        return {"feeds": [], "note": "No security feeds are set up (Access → Automator → Security feeds)."}
    out = []
    for row in rows:
        try:
            items = _cached_items(conn, row)
        except (httpx.HTTPError, ValueError, ElementTree.ParseError) as exc:
            out.append({"feed": row["name"], "error": f"Couldn't read it: {type(exc).__name__}."})
            continue
        words = [w.strip().lower() for w in row["keywords"].split(",") if w.strip()]
        kept = [i for i in items if (_parsed(i.get("published")) or cutoff) >= cutoff
                and (not words or any(w in (i["title"] + " " + i.get("summary", "")).lower() for w in words))]
        out.append({"feed": row["name"], "items": kept[:FEED_ITEMS], "matched": len(kept)})
    return {"since_days": since_days, "feeds": out}


def _cached_items(conn: sqlite3.Connection, row: sqlite3.Row) -> list[dict]:
    fresh = row["fetched_at"] and (datetime.now(timezone.utc) - _parsed(row["fetched_at"] + "Z")).total_seconds() < FEED_SECONDS
    if fresh and row["cache"]:
        return json.loads(row["cache"])
    items = fetch_feed(row["url"], row["kind"], row["keywords"])
    conn.execute("UPDATE automation_feeds SET cache = ?, fetched_at = strftime('%Y-%m-%dT%H:%M:%S', 'now') WHERE id = ?",
                 (json.dumps(items), row["id"]))
    return items


def fetch_feed(url: str, kind: str, keywords: str = "") -> list[dict]:
    params = None
    if kind == "nvd":
        words = [w.strip() for w in keywords.split(",") if w.strip()]
        start = (datetime.now(timezone.utc) - timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%S.000")
        params = {"pubStartDate": start, "pubEndDate": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000"),
                  "resultsPerPage": "200", **({"keywordSearch": words[0]} if len(words) == 1 else {})}
    with httpx.Client(transport=TRANSPORT, timeout=20.0, follow_redirects=True,
                      headers={"User-Agent": "Mnemerynne-Automator"}) as client:
        r = client.get(url, params=params)
        r.raise_for_status()
        if len(r.content) > FEED_MAX_BYTES:
            raise ValueError("feed too large")
    if kind == "json_kev":
        data = r.json()
        return [{"title": f"{v.get('cveID')}: {v.get('vendorProject')} {v.get('product')} — {v.get('vulnerabilityName')}",
                 "summary": (v.get("shortDescription") or "")[:400], "published": v.get("dateAdded"),
                 "action": v.get("requiredAction"), "due": v.get("dueDate"),
                 "link": f"https://nvd.nist.gov/vuln/detail/{v.get('cveID')}"}
                for v in sorted(data.get("vulnerabilities", []), key=lambda v: v.get("dateAdded") or "", reverse=True)][:500]
    if kind == "nvd":
        data = r.json()
        out = []
        for item in data.get("vulnerabilities", []):
            cve = item.get("cve") or {}
            desc = next((d.get("value") for d in cve.get("descriptions", []) if d.get("lang") == "en"), "")
            metrics = cve.get("metrics") or {}
            score = next((m[0].get("cvssData", {}).get("baseScore") for k, m in metrics.items() if m), None)
            out.append({"title": cve.get("id", ""), "summary": desc[:400], "published": cve.get("published"),
                        "cvss": score, "link": f"https://nvd.nist.gov/vuln/detail/{cve.get('id')}"})
        return out
    return _parse_xml_feed(r.content)


def _parse_xml_feed(content: bytes) -> list[dict]:
    if b"<!DOCTYPE" in content[:2000].upper() or b"<!ENTITY" in content[:5000].upper():
        raise ValueError("feeds with a DOCTYPE aren't read")
    root = ElementTree.fromstring(content)
    out = []
    atom = "{http://www.w3.org/2005/Atom}"
    for item in root.iter("item"):
        out.append({"title": (item.findtext("title") or "").strip(), "link": (item.findtext("link") or "").strip(),
                    "summary": _plain(item.findtext("description") or "")[:400],
                    "published": (item.findtext("pubDate") or "").strip()})
    for entry in root.iter(atom + "entry"):
        link = entry.find(atom + "link")
        out.append({"title": (entry.findtext(atom + "title") or "").strip(),
                    "link": link.get("href", "") if link is not None else "",
                    "summary": _plain(entry.findtext(atom + "summary") or entry.findtext(atom + "content") or "")[:400],
                    "published": (entry.findtext(atom + "updated") or entry.findtext(atom + "published") or "").strip()})
    return out[:200]


def _plain(html: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html)).strip()


def _parsed(text: str | None) -> datetime | None:
    if not text:
        return None
    text = text.strip()
    for fmt in ("%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S",
                "%Y-%m-%d", "%a, %d %b %Y %H:%M:%S %z", "%a, %d %b %Y %H:%M:%S %Z"):
        try:
            got = datetime.strptime(text.replace("Z", "+0000") if "%z" in fmt else text.rstrip("Z"), fmt)
            return got if got.tzinfo else got.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None
