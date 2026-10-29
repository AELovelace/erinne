"""OAuth 2.1 for /mcp: Claude on the web and phone (claude.ai custom
connectors, added by a Team/Enterprise owner or by a person for themselves)
and Claude Code connect by signing the person in to Mnemerynne and asking
their consent, instead of a pasted personal token. Claude Desktop's
extension and personal tokens keep working as before. ChatGPT connects the
same way when an admin allows it (Settings → ChatGPT, off by default): it
sees what is shared with Claude, and switching it off ends its connections.

How a connection is made (MCP's authorization spec):
  1. /mcp answers 401 with WWW-Authenticate pointing at
     /.well-known/oauth-protected-resource, which names this server as the
     authorization server (/.well-known/oauth-authorization-server).
  2. The app registers itself (/oauth/register). Only Claude's own redirect
     addresses, this computer's (Claude Code), and ChatGPT's two exact
     callbacks (while allowed) are accepted: nothing else can register and
     then ask staff for their documents.
  3. The person signs in and allows it (/oauth/authorize), choosing whether
     it may edit files. PKCE (S256) is required.
  4. The app swaps the code for an access token (an hour) and a refresh token
     (90 days, replaced on each use; one used twice ends the connection,
     since it was copied) at /oauth/token.
Every call then acts as the person, inside what they share with Claude, the
same as a personal token. Connections show on the Connect page, where people
(and admins, per user) can disconnect them.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import secrets
import sqlite3
from urllib.parse import urlsplit

from mnemerynne.api_tokens import Caller

ACCESS_SECONDS = 3600
REFRESH_DAYS = 90
CODE_SECONDS = 300
CLAUDE_HOSTS = ("claude.ai", "claude.com")
LOOPBACK = ("localhost", "127.0.0.1", "::1")
# ChatGPT's OAuth callbacks (developers.openai.com/plugins/build/auth): the stable one, used when the
# authorization server supports RFC 9207 (it does: metadata and `iss` on every redirect), and the
# per-connector one it falls back to. Exact matches only, nothing else on chatgpt.com.
CHATGPT_REDIRECT = "https://chatgpt.com/connector_platform_oauth_redirect"
CHATGPT_CALLBACK = re.compile(r"https://chatgpt\.com/connector/oauth/[A-Za-z0-9_-]{1,128}")
CHATGPT_KEY = "chatgpt_enabled"
ACCESS_PREFIX = "mnemerynne_at_"
REFRESH_PREFIX = "mnemerynne_rt_"


class OAuthError(Exception):
    """An OAuth error response: `error` per RFC 6749, a readable description."""

    def __init__(self, error: str, description: str, status: int = 400):
        super().__init__(description)
        self.error, self.description, self.status = error, description, status

    def body(self) -> dict:
        return {"error": self.error, "error_description": self.description}


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


# ------------------------------------------------------------------ ChatGPT
def chatgpt_enabled(conn: sqlite3.Connection) -> bool:
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (CHATGPT_KEY,)).fetchone()
    return bool(row and row["value"] == "1")


def set_chatgpt(conn: sqlite3.Connection, enabled: bool) -> int:
    """Allow or stop ChatGPT connections. Stopping ends the ones there are (returns how many), so turning it
    back on means people connect again rather than old connections quietly coming back."""
    conn.execute("INSERT INTO meta(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                 (CHATGPT_KEY, "1" if enabled else "0"))
    if enabled:
        return 0
    ended = 0
    for row in conn.execute("SELECT g.id, c.redirect_uris FROM oauth_grants g JOIN oauth_clients c "
                            "ON c.id = g.client_row WHERE g.revoked_at IS NULL").fetchall():
        if kind(json.loads(row["redirect_uris"])) == "chatgpt":
            ended += revoke_grant(conn, row["id"])
    return ended


def _is_chatgpt(uri: str) -> bool:
    return uri == CHATGPT_REDIRECT or CHATGPT_CALLBACK.fullmatch(uri) is not None


def kind(redirect_uris: list[str]) -> str:
    """Which app a client is, from where it is sent back: "chatgpt", "claude" or "local" (Claude Code)."""
    if any(_is_chatgpt(u) for u in redirect_uris):
        return "chatgpt"
    hosts = {(urlsplit(u).hostname or "").lower() for u in redirect_uris}
    if any(any(h == c or h.endswith("." + c) for c in CLAUDE_HOSTS) for h in hosts):
        return "claude"
    return "local"


# What the consent page calls each app, and who the passages it reads go to.
APPS = {"claude": ("Claude", "Anthropic"), "chatgpt": ("ChatGPT", "OpenAI"),
        "local": ("An app on your computer (such as Claude Code)", "the app's AI provider")}


def _refuse_if_off(conn: sqlite3.Connection, row: sqlite3.Row, error: str = "access_denied") -> None:
    if kind(json.loads(row["redirect_uris"])) == "chatgpt" and not chatgpt_enabled(conn):
        raise OAuthError(error, "ChatGPT connections aren't allowed here; ask a Mnemerynne admin.")


# ------------------------------------------------------------------ clients
def redirect_allowed(uri: str) -> bool:
    """Claude's own addresses over HTTPS, this computer (Claude Code's local callback), or ChatGPT's callbacks."""
    if _is_chatgpt(uri):
        return True
    try:
        parts = urlsplit(uri)
    except ValueError:
        return False
    host = (parts.hostname or "").lower()
    if parts.fragment or not host:
        return False
    if parts.scheme == "https" and any(host == h or host.endswith("." + h) for h in CLAUDE_HOSTS):
        return True
    return parts.scheme == "http" and host in LOOPBACK


def who(redirect_uris: list[str]) -> str:
    """Who is asking, as the consent page says it."""
    return APPS[kind(redirect_uris)][0]


def register(conn: sqlite3.Connection, body: dict) -> dict:
    """Dynamic client registration (RFC 7591), for Claude, and ChatGPT while an admin allows it."""
    uris = body.get("redirect_uris")
    if not isinstance(uris, list) or not uris or not all(isinstance(u, str) for u in uris):
        raise OAuthError("invalid_redirect_uri", "Give the redirect_uris.")
    if len(uris) > 10 or not all(redirect_allowed(u) for u in uris):
        raise OAuthError("invalid_redirect_uri",
                         "Mnemerynne only works with Claude (claude.ai, Claude Code) and, where allowed, ChatGPT.")
    if kind(uris) == "chatgpt" and not all(_is_chatgpt(u) for u in uris):
        raise OAuthError("invalid_redirect_uri", "Register ChatGPT's callbacks on their own.")
    if kind(uris) == "chatgpt" and not chatgpt_enabled(conn):
        raise OAuthError("invalid_redirect_uri", "ChatGPT connections aren't allowed here; ask a Mnemerynne admin.")
    method = body.get("token_endpoint_auth_method") or "none"
    if method not in ("none", "client_secret_post", "client_secret_basic"):
        raise OAuthError("invalid_client_metadata", "Unsupported token_endpoint_auth_method.")
    grant_types = body.get("grant_types") or ["authorization_code", "refresh_token"]
    if not set(grant_types) <= {"authorization_code", "refresh_token"}:
        raise OAuthError("invalid_client_metadata", "Only authorization_code and refresh_token are supported.")
    client_id = "mcp_" + secrets.token_urlsafe(18)
    secret = secrets.token_urlsafe(32) if method != "none" else None
    name = str(body.get("client_name") or who(uris))[:80]
    conn.execute("INSERT INTO oauth_clients(client_id, secret_hash, name, redirect_uris) VALUES (?, ?, ?, ?)",
                 (client_id, _hash(secret) if secret else None, name, json.dumps(uris)))
    out = {"client_id": client_id, "client_name": name, "redirect_uris": uris, "grant_types": grant_types,
           "response_types": ["code"], "token_endpoint_auth_method": method,
           "client_id_issued_at": int(conn.execute("SELECT strftime('%s', 'now')").fetchone()[0])}
    if secret:
        out.update({"client_secret": secret, "client_secret_expires_at": 0})
    return out


def client(conn: sqlite3.Connection, client_id: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM oauth_clients WHERE client_id = ?", (client_id or "",)).fetchone()


def _check_secret(row: sqlite3.Row, secret: str | None) -> None:
    if row["secret_hash"] is not None and not (secret and secrets.compare_digest(_hash(secret), row["secret_hash"])):
        raise OAuthError("invalid_client", "Wrong or missing client secret.", 401)


# ---------------------------------------------------------------- authorize
def check_request(conn: sqlite3.Connection, params: dict, resource: str) -> tuple[sqlite3.Row, str]:
    """Validate an authorization request; returns (client, redirect_uri).
    Raises OAuthError; the caller shows it on a page (never redirects to an unchecked address)."""
    row = client(conn, params.get("client_id", ""))
    if row is None:
        raise OAuthError("invalid_client", "This app isn't registered here; remove and add the connector again.")
    redirect_uri = params.get("redirect_uri") or ""
    if redirect_uri not in json.loads(row["redirect_uris"]):
        raise OAuthError("invalid_request", "The app's return address doesn't match its registration.")
    if params.get("response_type") != "code":
        raise OAuthError("unsupported_response_type", "Only the authorization code flow is supported.")
    if params.get("code_challenge_method") != "S256" or not params.get("code_challenge"):
        raise OAuthError("invalid_request", "PKCE (S256) is required.")
    if params.get("resource") and params["resource"].rstrip("/") not in (resource, resource.rsplit("/mcp", 1)[0]):
        raise OAuthError("invalid_target", "That isn't this Mnemerynne's MCP address.")
    _refuse_if_off(conn, row)
    return row, redirect_uri


def issue_code(conn: sqlite3.Connection, row: sqlite3.Row, user_id: int, redirect_uri: str, challenge: str,
               can_edit: bool, scope: str = "mcp") -> str:
    code = secrets.token_urlsafe(32)
    conn.execute("INSERT INTO oauth_codes(code_hash, client_row, user_id, redirect_uri, code_challenge, can_edit, "
                 "expires_at, scope) VALUES (?, ?, ?, ?, ?, ?, datetime('now', ?), ?)",
                 (_hash(code), row["id"], user_id, redirect_uri, challenge, int(can_edit), f"+{CODE_SECONDS} seconds",
                  scope))
    return code


# -------------------------------------------------------------------- token
def _tokens(conn: sqlite3.Connection, grant_id: int) -> dict:
    scope = conn.execute("SELECT scope FROM oauth_grants WHERE id = ?", (grant_id,)).fetchone()["scope"]
    access, refresh = ACCESS_PREFIX + secrets.token_urlsafe(32), REFRESH_PREFIX + secrets.token_urlsafe(32)
    conn.executemany("INSERT INTO oauth_tokens(token_hash, grant_id, kind, expires_at) VALUES (?, ?, ?, datetime('now', ?))",
                     [(_hash(access), grant_id, "access", f"+{ACCESS_SECONDS} seconds"),
                      (_hash(refresh), grant_id, "refresh", f"+{REFRESH_DAYS} days")])
    conn.execute("DELETE FROM oauth_tokens WHERE grant_id = ? AND expires_at < datetime('now', '-1 day')", (grant_id,))
    return {"access_token": access, "token_type": "Bearer", "expires_in": ACCESS_SECONDS, "refresh_token": refresh,
            "scope": scope}


def _client_auth(conn: sqlite3.Connection, form: dict, basic: str | None) -> tuple[sqlite3.Row, str | None]:
    client_id, secret = form.get("client_id"), form.get("client_secret")
    if basic:
        try:
            client_id, secret = base64.b64decode(basic).decode().split(":", 1)
        except (ValueError, UnicodeDecodeError):
            raise OAuthError("invalid_client", "Bad client credentials.", 401) from None
    row = client(conn, client_id or "")
    if row is None:
        raise OAuthError("invalid_client", "Unknown client.", 401)
    _check_secret(row, secret)
    return row, client_id


def token(conn: sqlite3.Connection, form: dict, basic: str | None = None) -> dict:
    """The token endpoint: authorization_code (with PKCE) and refresh_token grants."""
    row, _ = _client_auth(conn, form, basic)
    _refuse_if_off(conn, row, "invalid_grant")
    grant_type = form.get("grant_type")
    if grant_type == "authorization_code":
        code = conn.execute("SELECT * FROM oauth_codes WHERE code_hash = ?", (_hash(form.get("code") or ""),)).fetchone()
        if code is None or code["client_row"] != row["id"]:
            raise OAuthError("invalid_grant", "Unknown code.")
        if code["used_at"] is not None:
            # A code used twice was intercepted: end the connection it made.
            if code["grant_id"]:
                revoke_grant(conn, code["grant_id"])
            raise OAuthError("invalid_grant", "That code was already used.")
        if conn.execute("SELECT expires_at < datetime('now') FROM oauth_codes WHERE code_hash = ?",
                        (code["code_hash"],)).fetchone()[0]:
            raise OAuthError("invalid_grant", "That code expired; connect again.")
        if form.get("redirect_uri") != code["redirect_uri"]:
            raise OAuthError("invalid_grant", "The redirect_uri doesn't match.")
        verifier = form.get("code_verifier") or ""
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        if not verifier or not secrets.compare_digest(challenge, code["code_challenge"]):
            raise OAuthError("invalid_grant", "PKCE verification failed.")
        grant_id = conn.execute("INSERT INTO oauth_grants(user_id, client_row, can_edit, scope) VALUES (?, ?, ?, ?)",
                                (code["user_id"], row["id"], code["can_edit"], code["scope"])).lastrowid
        conn.execute("UPDATE oauth_codes SET used_at = datetime('now'), grant_id = ? WHERE code_hash = ?",
                     (grant_id, code["code_hash"]))
        conn.execute("UPDATE oauth_clients SET last_used_at = datetime('now') WHERE id = ?", (row["id"],))
        return _tokens(conn, grant_id)
    if grant_type == "refresh_token":
        t = conn.execute("SELECT t.*, g.revoked_at, g.client_row FROM oauth_tokens t JOIN oauth_grants g "
                         "ON g.id = t.grant_id WHERE t.token_hash = ? AND t.kind = 'refresh'",
                         (_hash(form.get("refresh_token") or ""),)).fetchone()
        if t is None or t["client_row"] != row["id"] or t["revoked_at"] is not None:
            raise OAuthError("invalid_grant", "Unknown or disconnected; connect again.")
        if t["used_at"] is not None:
            revoke_grant(conn, t["grant_id"])          # a refresh token used twice was copied
            raise OAuthError("invalid_grant", "That refresh token was already used; the connection was ended.")
        if conn.execute("SELECT ? < datetime('now')", (t["expires_at"],)).fetchone()[0]:
            raise OAuthError("invalid_grant", "The connection expired; connect again.")
        conn.execute("UPDATE oauth_tokens SET used_at = datetime('now') WHERE token_hash = ?", (t["token_hash"],))
        return _tokens(conn, t["grant_id"])
    raise OAuthError("unsupported_grant_type", "Use authorization_code or refresh_token.")


def verify(conn: sqlite3.Connection, value: str | None, *, scope: str = "mcp") -> Caller | None:
    """The person behind an access token, or None (unknown, expired, disconnected,
    made for another scope (api_tokens.SCOPES), or the user was deactivated)."""
    if not value or not value.startswith(ACCESS_PREFIX):
        return None
    row = conn.execute(
        "SELECT g.id AS grant_id, g.user_id, g.can_edit, u.username, c.redirect_uris FROM oauth_tokens t "
        "JOIN oauth_grants g ON g.id = t.grant_id JOIN users u ON u.id = g.user_id "
        "JOIN oauth_clients c ON c.id = g.client_row "
        "WHERE t.token_hash = ? AND t.kind = 'access' AND t.expires_at > datetime('now') "
        "AND g.scope = ? AND g.revoked_at IS NULL AND u.active = 1", (_hash(value), scope)).fetchone()
    if row is None:
        return None
    if kind(json.loads(row["redirect_uris"])) == "chatgpt" and not chatgpt_enabled(conn):
        return None
    conn.execute("UPDATE oauth_grants SET last_used_at = datetime('now') WHERE id = ?", (row["grant_id"],))
    return Caller(row["user_id"], row["username"], None, bool(row["can_edit"]), grant_id=row["grant_id"],
                  token_scope=scope)


def revoke_token(conn: sqlite3.Connection, value: str) -> None:
    """RFC 7009: revoking either token of a connection ends the connection."""
    row = conn.execute("SELECT grant_id FROM oauth_tokens WHERE token_hash = ?", (_hash(value or ""),)).fetchone()
    if row is not None:
        revoke_grant(conn, row["grant_id"])


# ---------------------------------------------------------------- connections
def revoke_grant(conn: sqlite3.Connection, grant_id: int, user_id: int | None = None) -> bool:
    sql, params = "UPDATE oauth_grants SET revoked_at = datetime('now') WHERE id = ? AND revoked_at IS NULL", (grant_id,)
    if user_id is not None:
        sql, params = sql + " AND user_id = ?", params + (user_id,)
    gone = conn.execute(sql, params).rowcount > 0
    conn.execute("DELETE FROM oauth_tokens WHERE grant_id = ?", (grant_id,))
    return gone


def revoke_all(conn: sqlite3.Connection, user_id: int) -> int:
    ids = [r[0] for r in conn.execute("SELECT id FROM oauth_grants WHERE user_id = ? AND revoked_at IS NULL",
                                      (user_id,))]
    for grant_id in ids:
        revoke_grant(conn, grant_id)
    return len(ids)


def set_can_edit(conn: sqlite3.Connection, grant_id: int, user_id: int, can_edit: bool) -> bool:
    return conn.execute("UPDATE oauth_grants SET can_edit = ? WHERE id = ? AND user_id = ? AND scope = 'mcp' "
                        "AND revoked_at IS NULL",
                        (int(can_edit), grant_id, user_id)).rowcount > 0


def connections(conn: sqlite3.Connection, user_id: int, scope: str = "mcp") -> list[dict]:
    """The person's live connections of one scope: AI apps ('mcp', the Connect page) or backup apps ('sync')."""
    rows = conn.execute("SELECT g.*, c.name, c.redirect_uris FROM oauth_grants g JOIN oauth_clients c "
                        "ON c.id = g.client_row WHERE g.user_id = ? AND g.scope = ? AND g.revoked_at IS NULL "
                        "ORDER BY g.id DESC", (user_id, scope)).fetchall()
    return [{"id": r["id"], "app": who(json.loads(r["redirect_uris"])), "name": r["name"],
             "can_edit": bool(r["can_edit"]), "created_at": r["created_at"], "last_used_at": r["last_used_at"]}
            for r in rows]
