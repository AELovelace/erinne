"""Microsoft 365: the tenants Mnemerynne works with, the Microsoft accounts
people link to their Mnemerynne user, their tokens, and a small Graph client.

People sign in to Mnemerynne with their Mnemerynne account, as always. On the
Connect page they then *link* one or more Microsoft 365 accounts (one per
tenant, e.g. Eternal Hotels and Gasco). Linking is a normal Microsoft sign-in
(MFA and Conditional Access apply) whose result is a refresh token for that
account, kept encrypted (apis.encrypt_secret). SharePoint and mail use it to
act as that person, so Microsoft's own permissions apply.

Tenants are added by an admin (Settings → Microsoft 365). One multitenant app
registration serves them all (MNEMERYNNE_M365_CLIENT_ID/SECRET), consented
by each tenant's admin; a tenant may instead have its own app. A tenant's
mode decides who may link there: 'ad' tenants are synced from our AD, so the
account must be the Mnemerynne user's own domain account; in 'cloud' tenants
any account may be linked by whoever can sign in to it.

The ID token comes straight from Microsoft's token endpoint over TLS (code
flow, confidential client), so its signature isn't checked separately (OpenID
Connect Core 3.1.3.7); its audience, tenant, issuer, nonce and expiry are.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import re
import secrets
import sqlite3
import threading
import time
from typing import Any
from urllib.parse import urlencode

import httpx

from mnemerynne import apis
from mnemerynne.config import Settings

log = logging.getLogger("mnemerynne.m365")

AUTHORITY = "https://login.microsoftonline.com"
GRAPH = "https://graph.microsoft.com/v1.0"
# Everything Mnemerynne may ever do as the person, asked for once when linking
# so nobody has to link again as features arrive. Never Mail.Send.
SCOPES = ("openid profile email offline_access User.Read Files.Read.All "
          "Mail.Read Mail.Read.Shared Calendars.Read Mail.ReadWrite")
LINK_SECONDS = 600            # a started link must finish within this
TRANSPORT: httpx.BaseTransport | None = None   # tests replace Microsoft with a fake

_GUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)


class M365Error(ValueError):
    """Refused or failed; the message is safe to show."""
    status = 400


class NeedsRelink(M365Error):
    """Microsoft no longer accepts this linked account's token."""
    status = 409


class GraphError(M365Error):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def configured(settings: Settings) -> bool:
    return bool(settings.m365_client_id and settings.m365_client_secret)


def _http() -> httpx.Client:
    return httpx.Client(transport=TRANSPORT, timeout=30.0, follow_redirects=False)


# ------------------------------------------------------------------ tenants
def resolve_tenant(ref: str) -> str:
    """The directory GUID for a tenant GUID or one of its domains."""
    ref = (ref or "").strip().lower()
    if _GUID.match(ref):
        return ref
    if not re.fullmatch(r"[a-z0-9.-]+\.[a-z]{2,}", ref):
        raise M365Error("Enter the tenant's ID or one of its domains, e.g. eternalhotels.com.")
    try:
        with _http() as client:
            r = client.get(f"{AUTHORITY}/{ref}/v2.0/.well-known/openid-configuration")
    except httpx.HTTPError as exc:
        raise M365Error(f"Couldn't reach Microsoft: {exc}") from None
    issuer = r.json().get("issuer", "") if r.status_code == 200 else ""
    match = re.search(r"/([0-9a-f-]{36})/", issuer)
    if not match:
        raise M365Error(f"Microsoft doesn't know a Microsoft 365 tenant for {ref}.")
    return match.group(1).lower()


def tenants(conn: sqlite3.Connection, enabled_only: bool = False) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM m365_tenants" + (" WHERE enabled = 1" if enabled_only else "")
                        + " ORDER BY label COLLATE NOCASE").fetchall()


def tenant(conn: sqlite3.Connection, row_id: int) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM m365_tenants WHERE id = ?", (row_id,)).fetchone()
    if row is None:
        raise M365Error("No such tenant.")
    return row


def by_tid(conn: sqlite3.Connection, tid: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM m365_tenants WHERE tenant_id = ?", (tid,)).fetchone()


def _check_mode(mode: str) -> str:
    if mode not in ("ad", "cloud"):
        raise M365Error("The mode is 'ad' (synced from our AD) or 'cloud' (separate staff).")
    return mode


def add_tenant(conn: sqlite3.Connection, settings: Settings, ref: str, label: str, mode: str = "cloud",
               client_id: str = "", client_secret: str = "") -> int:
    tid = resolve_tenant(ref)
    label = (label or "").strip()[:80] or ref.strip()
    if by_tid(conn, tid) is not None:
        raise M365Error("That tenant is already added.")
    secret = apis.encrypt_secret(settings, client_secret) if client_secret else None
    return conn.execute(
        "INSERT INTO m365_tenants(tenant_id, label, domain, mode, client_id, client_secret_enc) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (tid, label, "" if _GUID.match(ref.strip()) else ref.strip().lower(), _check_mode(mode),
         (client_id or "").strip(), secret)).lastrowid


def update_tenant(conn: sqlite3.Connection, settings: Settings, row_id: int, *, label: str | None = None,
                  mode: str | None = None, enabled: bool | None = None, client_id: str | None = None,
                  client_secret: str | None = None) -> None:
    tenant(conn, row_id)
    if label is not None:
        conn.execute("UPDATE m365_tenants SET label = ? WHERE id = ?", ((label.strip() or "Tenant")[:80], row_id))
    if mode is not None:
        conn.execute("UPDATE m365_tenants SET mode = ? WHERE id = ?", (_check_mode(mode), row_id))
    if enabled is not None:
        conn.execute("UPDATE m365_tenants SET enabled = ? WHERE id = ?", (int(enabled), row_id))
    if client_id is not None:
        conn.execute("UPDATE m365_tenants SET client_id = ? WHERE id = ?", (client_id.strip(), row_id))
    if client_secret is not None:   # '' removes it
        conn.execute("UPDATE m365_tenants SET client_secret_enc = ? WHERE id = ?",
                     (apis.encrypt_secret(settings, client_secret) if client_secret else None, row_id))


def delete_tenant(conn: sqlite3.Connection, row_id: int) -> None:
    """Remove a tenant and every account linked in it."""
    conn.execute("DELETE FROM m365_tenants WHERE id = ?", (row_id,))


def credentials(settings: Settings, row: sqlite3.Row | None) -> tuple[str, str]:
    """(client id, secret): the tenant's own app, or the shared one."""
    if row is not None and row["client_id"]:
        secret = apis.decrypt_secret(settings, row["client_secret_enc"])
        if not secret:
            raise M365Error(f"{row['label']} has its own app, but its secret is missing or unreadable.")
        return row["client_id"], secret
    if not configured(settings):
        raise M365Error("Microsoft 365 isn't set up on this server (MNEMERYNNE_M365_CLIENT_ID and _SECRET).")
    return settings.m365_client_id, settings.m365_client_secret


def consent_url(settings: Settings, row: sqlite3.Row) -> str:
    """Where that tenant's admin grants the app its permissions, once."""
    client_id, _ = credentials(settings, row)
    return f"{AUTHORITY}/{row['tenant_id']}/adminconsent?" + urlencode({"client_id": client_id})


_app_tokens: dict[str, tuple[str, float]] = {}
_lock = threading.Lock()


def app_token(settings: Settings, row: sqlite3.Row) -> str:
    """An app-only token for that tenant (SharePoint indexing, Entra groups)."""
    with _lock:
        hit = _app_tokens.get(row["tenant_id"])
        if hit and hit[1] > time.time() + 60:
            return hit[0]
    client_id, secret = credentials(settings, row)
    with _http() as client:
        r = client.post(f"{AUTHORITY}/{row['tenant_id']}/oauth2/v2.0/token", data={
            "grant_type": "client_credentials", "client_id": client_id, "client_secret": secret,
            "scope": "https://graph.microsoft.com/.default"})
    body = _json(r)
    if r.status_code != 200:
        raise M365Error(_why(body, f"Microsoft refused the app's sign-in to {row['label']}"))
    with _lock:
        _app_tokens[row["tenant_id"]] = (body["access_token"], time.time() + int(body.get("expires_in", 3600)))
    return body["access_token"]


def check_consent(conn: sqlite3.Connection, settings: Settings, row_id: int) -> dict:
    """Try the app's own sign-in to that tenant and record whether it worked."""
    row = tenant(conn, row_id)
    with _lock:
        _app_tokens.pop(row["tenant_id"], None)
    try:
        app_token(settings, row)
    except (M365Error, httpx.HTTPError) as exc:
        conn.execute("UPDATE m365_tenants SET consent_error = ? WHERE id = ?", (str(exc)[:500], row_id))
        return {"ok": False, "error": str(exc)}
    conn.execute("UPDATE m365_tenants SET consent_ok_at = datetime('now'), consent_error = NULL WHERE id = ?",
                 (row_id,))
    return {"ok": True}


def _json(r: httpx.Response) -> dict:
    try:
        body = r.json()
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}


def _why(body: dict, prefix: str) -> str:
    detail = body.get("error_description") or body.get("error") or ""
    # Microsoft's descriptions start with an AADSTS code; the first line is the useful part.
    return prefix + (f": {str(detail).splitlines()[0][:300]}" if detail else ".")


# ------------------------------------------------------------------ linking
def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def begin_link(conn: sqlite3.Connection, settings: Settings, session: dict, user_id: int, redirect_uri: str,
               tenant_row: int | None = None) -> str:
    """Start linking: remember the attempt in the session, return Microsoft's sign-in URL."""
    row = tenant(conn, tenant_row) if tenant_row else None
    if row is not None and not row["enabled"]:
        raise M365Error(f"{row['label']} is switched off.")
    if row is None and not tenants(conn, enabled_only=True):
        raise M365Error("No Microsoft 365 tenants are set up yet; ask an admin.")
    client_id, _ = credentials(settings, row)
    verifier = secrets.token_urlsafe(48)
    pending = {"state": secrets.token_urlsafe(24), "nonce": secrets.token_urlsafe(24), "verifier": verifier,
               "user_id": user_id, "tenant_row": tenant_row, "at": time.time()}
    session["m365_link"] = pending
    authority = row["tenant_id"] if row is not None else "organizations"
    return f"{AUTHORITY}/{authority}/oauth2/v2.0/authorize?" + urlencode({
        "client_id": client_id, "response_type": "code", "redirect_uri": redirect_uri, "response_mode": "query",
        "scope": SCOPES, "state": pending["state"], "nonce": pending["nonce"], "prompt": "select_account",
        "code_challenge": _b64(hashlib.sha256(verifier.encode()).digest()), "code_challenge_method": "S256"})


def _claims(id_token: str) -> dict:
    try:
        payload = id_token.split(".")[1]
        return json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    except (IndexError, ValueError):
        raise M365Error("Microsoft's answer was unreadable; try again.") from None


def finish_link(conn: sqlite3.Connection, settings: Settings, session: dict, user: sqlite3.Row, code: str,
                state: str, redirect_uri: str) -> dict:
    """Finish linking: check everything, store the account and its token. Returns the identity."""
    pending = session.pop("m365_link", None)
    if not pending or not secrets.compare_digest(str(pending.get("state", "")), state or ""):
        raise M365Error("That Microsoft sign-in didn't match one started here; start again from the Connect page.")
    if pending["user_id"] != user["id"] or time.time() - pending["at"] > LINK_SECONDS:
        raise M365Error("That Microsoft sign-in took too long or belongs to another session; start again.")
    chosen = tenant(conn, pending["tenant_row"]) if pending.get("tenant_row") else None
    client_id, secret = credentials(settings, chosen)
    authority = chosen["tenant_id"] if chosen is not None else "organizations"
    with _http() as client:
        r = client.post(f"{AUTHORITY}/{authority}/oauth2/v2.0/token", data={
            "grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri, "client_id": client_id,
            "client_secret": secret, "code_verifier": pending["verifier"], "scope": SCOPES})
    body = _json(r)
    if r.status_code != 200 or not body.get("id_token"):
        raise M365Error(_why(body, "Microsoft didn't complete the sign-in"))
    claims = _claims(body["id_token"])
    tid = str(claims.get("tid", "")).lower()
    if claims.get("aud") != client_id or claims.get("nonce") != pending["nonce"] \
            or float(claims.get("exp", 0)) < time.time() - 300 or claims.get("iss") != f"{AUTHORITY}/{tid}/v2.0":
        raise M365Error("Microsoft's answer didn't check out; start again from the Connect page.")
    row = by_tid(conn, tid)
    if row is None or not row["enabled"]:
        raise M365Error("That Microsoft account belongs to a directory Mnemerynne doesn't work with. "
                        "Pick your work account, or ask an admin to add your organisation.")
    if chosen is not None and row["id"] != chosen["id"]:
        raise M365Error(f"That account isn't in {chosen['label']}; pick your {chosen['label']} account.")
    if row["client_id"] and chosen is None:
        raise M365Error(f"{row['label']} uses its own sign-in; use its button on the Connect page.")
    me = Graph(body["access_token"]).get("/me", {"$select": "id,userPrincipalName,displayName,"
                                                          "onPremisesSamAccountName,onPremisesDomainName"})
    oid = str(claims.get("oid") or me.get("id") or "").lower()
    if not oid or oid != str(me.get("id", "")).lower():
        raise M365Error("Microsoft's answer didn't check out; start again from the Connect page.")
    if row["mode"] == "ad":
        sam = (me.get("onPremisesSamAccountName") or "").lower()
        if not user["ad_account"] or sam != user["ad_account"].lower():
            raise M365Error(f"In {row['label']} you can only link your own account"
                            + (f" ({user['ad_account']})." if user["ad_account"]
                               else "; ask an admin to link your Mnemerynne user to your domain account first."))
    other = conn.execute("SELECT i.user_id, u.username FROM m365_identities i JOIN users u ON u.id = i.user_id "
                         "WHERE i.tenant_row = ? AND i.oid = ?", (row["id"], oid)).fetchone()
    if other is not None and other["user_id"] != user["id"]:
        raise M365Error("That Microsoft account is already linked to another Mnemerynne user; ask an admin.")
    refresh = body.get("refresh_token")
    if not refresh:
        raise M365Error("Microsoft didn't allow offline access; ask an admin to grant consent for the app.")
    values = (me.get("userPrincipalName") or claims.get("preferred_username") or "", me.get("displayName") or "",
              apis.encrypt_secret(settings, refresh), body.get("scope", SCOPES))
    if other is None:
        identity_id = conn.execute(
            "INSERT INTO m365_identities(upn, display_name, refresh_enc, scopes, user_id, tenant_row, oid) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)", (*values, user["id"], row["id"], oid)).lastrowid
    else:
        identity_id = conn.execute(
            "UPDATE m365_identities SET upn = ?, display_name = ?, refresh_enc = ?, scopes = ?, status = 'ok', "
            "linked_at = datetime('now') WHERE tenant_row = ? AND oid = ? RETURNING id",
            (*values, row["id"], oid)).fetchone()[0]
    with _lock:
        _user_tokens[identity_id] = (body["access_token"], time.time() + int(body.get("expires_in", 3600)))
    return identity(conn, identity_id)


# ------------------------------------------------------------------- tokens
_user_tokens: dict[int, tuple[str, float]] = {}
_refreshing: dict[int, threading.Lock] = {}


def access_token(conn: sqlite3.Connection, settings: Settings, identity_id: int) -> str:
    """A delegated token for a linked account, refreshed (and the refresh token
    rotated) when needed. NeedsRelink when Microsoft no longer accepts it."""
    with _lock:
        hit = _user_tokens.get(identity_id)
        if hit and hit[1] > time.time() + 60:
            return hit[0]
        lock = _refreshing.setdefault(identity_id, threading.Lock())
    with lock:   # one refresh at a time: a rotated refresh token is single-use
        with _lock:
            hit = _user_tokens.get(identity_id)
            if hit and hit[1] > time.time() + 60:
                return hit[0]
        row = conn.execute("SELECT i.*, t.label FROM m365_identities i JOIN m365_tenants t ON t.id = i.tenant_row "
                           "WHERE i.id = ?", (identity_id,)).fetchone()
        if row is None:
            raise NeedsRelink("That Microsoft account isn't linked any more.")
        refresh = apis.decrypt_secret(settings, row["refresh_enc"]) if row["status"] == "ok" else None
        if not refresh:
            raise NeedsRelink(f"Your {row['label']} Microsoft account needs linking again (Connect page).")
        t = tenant(conn, row["tenant_row"])
        client_id, secret = credentials(settings, t)
        with _http() as client:
            r = client.post(f"{AUTHORITY}/{t['tenant_id']}/oauth2/v2.0/token", data={
                "grant_type": "refresh_token", "refresh_token": refresh, "client_id": client_id,
                "client_secret": secret, "scope": SCOPES})
        body = _json(r)
        if r.status_code != 200:
            if body.get("error") in ("invalid_grant", "interaction_required", "consent_required"):
                conn.execute("UPDATE m365_identities SET status = 'relink', refresh_enc = NULL WHERE id = ?",
                             (identity_id,))
                log.info("Microsoft refused %s's token (%s); it needs linking again.", row["upn"], body.get("error"))
                raise NeedsRelink(f"Your {row['label']} Microsoft account needs linking again (Connect page).")
            raise M365Error(_why(body, "Microsoft didn't renew the sign-in"))
        conn.execute("UPDATE m365_identities SET refresh_enc = COALESCE(?, refresh_enc), "
                     "last_used_at = datetime('now') WHERE id = ?",
                     (apis.encrypt_secret(settings, body["refresh_token"]) if body.get("refresh_token") else None,
                      identity_id))
        with _lock:
            _user_tokens[identity_id] = (body["access_token"], time.time() + int(body.get("expires_in", 3600)))
        return body["access_token"]


def graph_for(conn: sqlite3.Connection, settings: Settings, identity_id: int) -> "Graph":
    return Graph(access_token(conn, settings, identity_id))


# -------------------------------------------------------------- identities
def identity(conn: sqlite3.Connection, identity_id: int) -> dict:
    row = conn.execute("SELECT i.*, t.label, t.tenant_id, t.mode FROM m365_identities i "
                       "JOIN m365_tenants t ON t.id = i.tenant_row WHERE i.id = ?", (identity_id,)).fetchone()
    return _identity_view(row)


def _identity_view(row: sqlite3.Row) -> dict:
    return {"id": row["id"], "tenant": row["label"], "tenant_row": row["tenant_row"], "upn": row["upn"],
            "name": row["display_name"], "status": row["status"], "linked_at": row["linked_at"],
            "last_used_at": row["last_used_at"]}


def identities(conn: sqlite3.Connection, user_id: int, usable_only: bool = False) -> list[dict]:
    rows = conn.execute("SELECT i.*, t.label FROM m365_identities i JOIN m365_tenants t ON t.id = i.tenant_row "
                        "WHERE i.user_id = ?" + (" AND i.status = 'ok' AND t.enabled = 1" if usable_only else "")
                        + " ORDER BY t.label COLLATE NOCASE", (user_id,)).fetchall()
    return [_identity_view(r) for r in rows]


def unlink(conn: sqlite3.Connection, identity_id: int, user_id: int | None = None) -> bool:
    """Forget a linked account and its token (only the user's own when user_id is given)."""
    sql, params = "DELETE FROM m365_identities WHERE id = ?", (identity_id,)
    if user_id is not None:
        sql, params = sql + " AND user_id = ?", params + (user_id,)
    gone = conn.execute(sql, params).rowcount > 0
    with _lock:
        _user_tokens.pop(identity_id, None)
    return gone


# -------------------------------------------------------------------- Graph
class Graph:
    """Microsoft Graph as one person (or as the app), read requests mostly."""

    def __init__(self, token: str):
        self.token = token

    def _send(self, method: str, path: str, *, params: dict | None = None, body: Any = None,
              headers: dict | None = None) -> httpx.Response:
        url = path if path.startswith("https://") else GRAPH + path
        hdrs = {"Authorization": f"Bearer {self.token}", **(headers or {})}
        with _http() as client:
            for attempt in range(2):
                r = client.request(method, url, params=params, json=body, headers=hdrs)
                if r.status_code == 429 and attempt == 0:
                    time.sleep(min(float(r.headers.get("retry-after", "2") or 2), 10))
                    continue
                return r
        return r

    def _ok(self, r: httpx.Response) -> dict:
        body = _json(r)
        if r.status_code >= 400:
            message = (body.get("error") or {}).get("message") if isinstance(body.get("error"), dict) else None
            raise GraphError(r.status_code, message or f"Microsoft answered {r.status_code}.")
        return body

    def get(self, path: str, params: dict | None = None, headers: dict | None = None) -> dict:
        return self._ok(self._send("GET", path, params=params, headers=headers))

    def post(self, path: str, body: Any = None, headers: dict | None = None) -> dict:
        return self._ok(self._send("POST", path, body=body, headers=headers))

    def patch(self, path: str, body: Any = None) -> dict:
        return self._ok(self._send("PATCH", path, body=body))

    def batch(self, requests: list[dict]) -> dict[str, dict]:
        """Up to 20 GET requests ({"id", "url"}) in one call; responses by id."""
        out: dict[str, dict] = {}
        for i in range(0, len(requests), 20):
            part = [{"id": q["id"], "method": "GET", "url": q["url"]} for q in requests[i:i + 20]]
            body = self.post("/$batch", {"requests": part})
            out.update({str(resp["id"]): resp for resp in body.get("responses", [])})
        return out
