"""Signing the app in to Mnemerynne, and the bearer token for each request.

Two ways: in the browser (OAuth with PKCE and a loopback redirect, as Claude Code does; the consent page says what
the app may do), or by pasting a sync token made on Mnemerynne's Backups page (headless machines).
"""

from __future__ import annotations

import base64
import hashlib
import html
import secrets
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx

from mnemerynne_backup import credentials
from mnemerynne_backup.config import APP


class SignInError(Exception):
    pass


class Tokens:
    """`tokens(refresh)` for Transport: the stored token, renewed through OAuth when it runs out."""

    def __init__(self, server: str, token_url: str | None = None, client: httpx.Client | None = None):
        self.server = server.rstrip("/")
        self.token_url = (token_url or self.server).rstrip("/") + "/oauth/token"
        self.http = client
        self.lock = threading.Lock()

    def __call__(self, refresh: bool = False) -> str:
        with self.lock:
            creds = credentials.load(self.server)
            if creds is None:
                raise SignInError("This computer isn't signed in to Mnemerynne.")
            if creds["kind"] == "token":
                return creds["token"]
            if refresh or creds["expires"] < time.time() + 60:
                creds = self._refresh(creds)
            return creds["access"]

    def _refresh(self, creds: dict) -> dict:
        from mnemerynne_backup.transport import SignedOut

        http = self.http or httpx.Client(timeout=30)
        try:
            r = http.post(self.token_url, data={"grant_type": "refresh_token", "refresh_token": creds["refresh"],
                                                "client_id": creds["client_id"]})
        except httpx.TransportError:
            return creds                  # offline: the request fails on its own and is tried again later
        if r.status_code >= 400:
            raise SignedOut("The sign-in ran out or was disconnected; sign in again.", 401)
        creds = _from_token_response(creds["client_id"], r.json())
        credentials.save(self.server, creds)
        return creds


def _from_token_response(client_id: str, body: dict) -> dict:
    return {"kind": "oauth", "client_id": client_id, "access": body["access_token"],
            "refresh": body["refresh_token"], "expires": time.time() + int(body.get("expires_in", 3600))}


def save_token(server: str, token: str) -> None:
    credentials.save(server.rstrip("/"), {"kind": "token", "token": token.strip()})


def browser_sign_in(server: str, computer: str, *, open_browser=webbrowser.open, timeout: float = 300,
                    client: httpx.Client | None = None) -> None:
    """Open the sign-in page, wait for the answer on this computer, and store the connection."""
    server = server.rstrip("/")
    http = client or httpx.Client(timeout=30)
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state = secrets.token_urlsafe(16)
    got: dict = {}
    done = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            parts = urlsplit(self.path)
            if parts.path != "/callback":
                self.send_error(404)
                return
            query = {k: v[0] for k, v in parse_qs(parts.query).items()}
            ok = query.get("state") == state and "code" in query
            got.update(query if ok else {"error": query.get("error_description") or query.get("error") or "refused"})
            text = (f"{APP} is signed in. You can close this tab." if ok else
                    f"Signing in didn't work: {got['error']}")
            body = f"<!doctype html><meta charset=utf-8><title>{APP}</title><p>{html.escape(text)}</p>".encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            done.set()

        def log_message(self, *args):
            pass

    httpd = HTTPServer(("127.0.0.1", 0), Handler)
    redirect = f"http://127.0.0.1:{httpd.server_port}/callback"
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        r = http.post(f"{server}/oauth/register", json={
            "redirect_uris": [redirect], "client_name": f"{APP} on {computer}", "token_endpoint_auth_method": "none",
            "grant_types": ["authorization_code", "refresh_token"]})
        if r.status_code >= 400:
            raise SignInError(f"Mnemerynne refused the app: {r.text}")
        client_id = r.json()["client_id"]
        open_browser(f"{server}/oauth/authorize?" + urlencode({
            "response_type": "code", "client_id": client_id, "redirect_uri": redirect, "state": state,
            "code_challenge": challenge, "code_challenge_method": "S256", "scope": "sync"}))
        if not done.wait(timeout):
            raise SignInError("Nobody finished signing in in the browser; try again.")
        if "error" in got:
            raise SignInError(f"Signing in didn't work: {got['error']}")
        r = http.post(f"{server}/oauth/token", data={
            "grant_type": "authorization_code", "code": got["code"], "redirect_uri": redirect, "client_id": client_id,
            "code_verifier": verifier})
        if r.status_code >= 400:
            raise SignInError(f"Mnemerynne didn't accept the sign-in: {r.text}")
        credentials.save(server, _from_token_response(client_id, r.json()))
    finally:
        httpd.shutdown()
        httpd.server_close()


def sign_out(server: str, client: httpx.Client | None = None) -> None:
    """Forget the sign-in here and end it on the server (a pasted token is revoked on the Backups page)."""
    server = server.rstrip("/")
    creds = credentials.load(server)
    if creds and creds["kind"] == "oauth":
        http = client or httpx.Client(timeout=15)
        try:
            http.post(f"{server}/oauth/revoke", data={"token": creds["refresh"], "client_id": creds["client_id"]})
        except httpx.HTTPError:
            pass
    credentials.forget(server)
