"""What the briefing and security tools read (mcp_server.Tools wraps them for Erynne and Claude):

- my_briefing: what's waiting for the person inside Mnemerynne (unread notes, announcements, room
  mentions and direct messages, proposals to apply, open notes on their agent scratchpad). Mail and
  calendar have their own tools.
- mnemerynne_updates (admins): new commits upstream and what they touch (updates.check), and the
  installed packages' known vulnerabilities (pip-audit, a dependency since 2026-10-28; cached).
- m365_security (admins): open Defender alerts, risky users, the latest Secure Score and risky sign-ins
  per Microsoft 365 tenant, read with the app's own token. They need application permissions on the
  Entra app (GRAPH_PERMISSIONS); a 403 says which.
- security_feeds: the security feeds (security_feeds.py, T-21): default feeds (CISA KEV, NVD, MSRC, Mozilla,
  GitHub, nginx, Oracle) plus the admins' own, one item per CVE, joined with Wazuh's installed packages.
"""

from __future__ import annotations

import json
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

from mnemerynne import m365, messages, notices, rooms, scratchpad, updates
from mnemerynne.config import Settings

AUDIT_SECONDS = 6 * 3600
GRAPH_PERMISSIONS = {"alerts": "SecurityAlert.Read.All", "risky_users": "IdentityRiskyUser.Read.All",
                     "secure_score": "SecurityEvents.Read.All", "sign_ins": "AuditLog.Read.All"}
SECURITY_KINDS = tuple(GRAPH_PERMISSIONS)

# Tests: an httpx transport (unused since the feeds moved to security_feeds.py), and pip-audit's output.
TRANSPORT: httpx.BaseTransport | None = None
AUDIT_OUTPUT: str | None = None
_audit = {"at": 0.0, "value": None}
_audit_lock = threading.Lock()


class BriefingError(ValueError):
    """Refused or failed; the message is safe to show."""


# ---------------------------------------------------------------- briefing
def briefing(conn: sqlite3.Connection, user: sqlite3.Row, directory, claim_minutes: int = 0) -> dict:
    """claim_minutes: the scratchpad's claim timeout (settings.scratchpad_claim_minutes), so stale
    claims are released before they're reported."""
    notes = messages.inbox(conn, user["id"], unread_only=True, limit=20)
    pending = notices.pending_for(conn, user, directory)
    out: dict = {"unread_notes": [{"from": n["from"], "at": n["at"], "text": n["text"][:300]} for n in notes],
                 "announcements": [{"title": a["title"], "must_acknowledge": bool(a.get("must_ack"))} for a in pending]}
    with rooms.on(conn) as rooms_conn:
        if rooms_conn is not None and rooms.enabled(rooms_conn):
            listed = rooms.rooms_for(rooms_conn, user, directory)
            out["rooms"] = [{"room": ("#" if r["kind"] == "channel" else "") + r["name"], "unread": r["unread"],
                             "mentions": r["mentions"]} for r in listed if r["unread"] or r["mentions"]]
    waiting = conn.execute("SELECT COUNT(*) FROM agent_proposals WHERE user_id = ? AND status = 'pending'",
                           (user["id"],)).fetchone()[0] if _has_table(conn, "agent_proposals") else 0
    out["proposals_waiting"] = waiting
    if _has_table(conn, "scratchpad_notes"):                 # the agents' own notes: only when something is open
        pad = scratchpad.summary(conn, user["id"], claim_minutes)
        if pad["open"]:
            out["scratchpad"] = pad
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
        exe = pip_audit_exe()
        if exe is None:
            return {"checked": False, "note": "pip-audit isn't installed in Mnemerynne's environment (uv sync installs "
                                              "it), so the packages' known vulnerabilities weren't checked."}
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


def pip_audit_exe() -> str | None:
    """The pip-audit beside this Python (the venv's bin/ or Scripts/; systemd's PATH doesn't include it),
    else one on the PATH."""
    return shutil.which("pip-audit", path=str(Path(sys.executable).parent)) or shutil.which("pip-audit")


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
            graph = m365.Graph(m365.app_token(m365.live(conn, settings), row))   # live: the shared app as Access has it now
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
def security_feeds(conn: sqlite3.Connection, since_days: int = 7, settings: Settings | None = None, *,
                   product: str | None = None, min_cvss: float | None = None, kev_only: bool = False,
                   affects_us_only: bool = False) -> dict:
    """The security feeds (T-21): one item per CVE across every feed, joined with Wazuh's inventory, plus each
    feed's health. The work is security_feeds.query's; this keeps the briefing tools' entry point."""
    from mnemerynne import security_feeds as feeds

    return feeds.query(conn, settings, since_days, product=product, min_cvss=min_cvss, kev_only=kev_only,
                       affects_us_only=affects_us_only)
