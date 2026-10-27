"""Hardening for running on the open internet: response headers, per-IP rate
limits on the endpoints anyone can reach, the secret key check, and the
optional network allow-list for admin rights."""

from __future__ import annotations

import ipaddress
import json
import logging
import threading
import time
from functools import lru_cache

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from mnemerynne.config import Settings

log = logging.getLogger("mnemerynne.web")

# The example env files' placeholders, and other values nobody should run with.
_PLACEHOLDER_KEYS = {"change-me", "changeme", "change_me", "secret", "password", "mnemerynne", "test"}
MIN_SECRET_KEY_LENGTH = 32
_LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}


class InsecureConfig(RuntimeError):
    """The server would run with settings that aren't safe where it's reachable."""


def exposed(settings: Settings) -> bool:
    """True when other machines can reach the server: it has a public address,
    or listens on more than loopback."""
    return bool(settings.public_url) or settings.host not in _LOOPBACK_HOSTS


def https_only(settings: Settings) -> bool:
    """Session cookies only over HTTPS: the setting, or, when unset, whenever the public address is https."""
    if settings.session_https_only is not None:
        return settings.session_https_only
    return settings.public_url.lower().startswith("https://")


def check_secret_key(settings: Settings) -> str | None:
    """The key to sign sessions with, or None for a throwaway one (development only).

    MNEMERYNNE_SECRET_KEY also encrypts every stored credential (API keys,
    Microsoft 365 tokens), so a server others can reach refuses to start
    without a real one rather than silently using a random key."""
    key = settings.secret_key.strip()
    if key.lower() in _PLACEHOLDER_KEYS:
        raise InsecureConfig("MNEMERYNNE_SECRET_KEY is still the example value. Generate one with: "
                             "python3 -c \"import secrets; print(secrets.token_urlsafe(48))\"")
    if not key:
        if exposed(settings):
            raise InsecureConfig("MNEMERYNNE_SECRET_KEY is not set. It signs sessions and encrypts stored "
                                 "credentials; generate one with: "
                                 "python3 -c \"import secrets; print(secrets.token_urlsafe(48))\"")
        log.warning("MNEMERYNNE_SECRET_KEY is not set; using a random key, so sessions end on restart.")
        return None
    if len(key) < MIN_SECRET_KEY_LENGTH:
        if exposed(settings):
            raise InsecureConfig(f"MNEMERYNNE_SECRET_KEY is shorter than {MIN_SECRET_KEY_LENGTH} characters.")
        log.warning("MNEMERYNNE_SECRET_KEY is shorter than %d characters.", MIN_SECRET_KEY_LENGTH)
    return key


# ------------------------------------------------------------------ headers
class SecurityHeaders:
    """Headers every response gets unless the route set its own: no framing by
    other sites, no MIME sniffing, no full URLs in Referer, and HSTS over HTTPS.

    Pure ASGI (not BaseHTTPMiddleware) so streamed answers aren't buffered."""

    def __init__(self, app: ASGIApp, settings: Settings) -> None:
        self.app = app
        self.hsts = settings.public_url.lower().startswith("https://")

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        hsts = self.hsts or scope.get("scheme") == "https"

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers.setdefault("X-Content-Type-Options", "nosniff")
                # The file manager previews files in same-origin iframes; nobody else may frame us.
                headers.setdefault("X-Frame-Options", "SAMEORIGIN")
                csp = headers.get("Content-Security-Policy")
                if csp is None:
                    headers["Content-Security-Policy"] = "frame-ancestors 'self'; base-uri 'self'; object-src 'none'"
                elif "frame-ancestors" not in csp and "sandbox" not in csp:
                    headers["Content-Security-Policy"] = csp.rstrip("; ") + "; frame-ancestors 'self'"
                headers.setdefault("Referrer-Policy", "same-origin")
                headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=()")
                if hsts:
                    headers.setdefault("Strict-Transport-Security", "max-age=31536000")
            await send(message)

        await self.app(scope, receive, send_with_headers)


# -------------------------------------------------------------- rate limits
class RateLimiter:
    """Sliding-window counter per key, in memory (the web app is one process)."""

    def __init__(self) -> None:
        self._hits: dict[str, list[float]] = {}
        self._lock = threading.Lock()
        self._last_sweep = time.monotonic()

    def allow(self, key: str, limit: int, window: float) -> bool:
        """Count one hit for `key`; False when it already had `limit` within `window` seconds."""
        now = time.monotonic()
        with self._lock:
            if now - self._last_sweep > 600:   # forget idle keys so the table can't grow forever
                self._sweep(now)
            recent = [t for t in self._hits.get(key, []) if t > now - window]
            if len(recent) >= limit:
                self._hits[key] = recent
                return False
            recent.append(now)
            self._hits[key] = recent
            return True

    def _sweep(self, now: float) -> None:
        # Windows here are at most an hour.
        self._hits = {k: v for k, v in self._hits.items() if v and v[-1] > now - 3600}
        self._last_sweep = now


# (path prefix, methods or None for all, requests, window seconds). Endpoints
# that need no sign-in; signed-in pages and /mcp have their own checks.
# claude.ai registers and refreshes for everyone from a few shared addresses,
# so its endpoints allow more than one person would ever need.
PUBLIC_LIMITS: list[tuple[str, frozenset[str] | None, int, int]] = [
    ("/oauth/register", None, 30, 3600),
    ("/oauth/token", None, 120, 60),
    ("/oauth/revoke", None, 60, 60),
    ("/oauth/authorize", None, 60, 60),
    ("/.well-known/", None, 120, 60),
    ("/c/", None, 120, 60),
    ("/login", frozenset({"POST"}), 30, 60),
    ("/media/screen/pair", None, 60, 60),     # an unpaired TV asks every 3 s
    ("/api/media/kiosk/redeem", frozenset({"POST"}), 30, 60),   # a screen box's helper (kiosk settings)
    ("/api/media/kiosk/screen", None, 120, 60),
]


class PublicRateLimit:
    """Per-client-IP limits on the endpoints anyone can reach. The client IP
    is the real one behind Caddy (uvicorn's proxy_headers + FORWARDED_ALLOW_IPS)."""

    def __init__(self, app: ASGIApp, limiter: RateLimiter | None = None,
                 limits: list[tuple[str, frozenset[str] | None, int, int]] | None = None) -> None:
        self.app = app
        self.limiter = limiter or RateLimiter()
        self.limits = PUBLIC_LIMITS if limits is None else limits

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            path, method = scope.get("path", ""), scope.get("method", "GET")
            for prefix, methods, limit, window in self.limits:
                if path.startswith(prefix) and (methods is None or method in methods):
                    ip = (scope.get("client") or ("",))[0]
                    if not self.limiter.allow(f"{prefix}|{ip}", limit, window):
                        await _too_many(send, window)
                        return
                    break
        await self.app(scope, receive, send)


async def _too_many(send: Send, retry_after: int) -> None:
    body = json.dumps({"detail": "Too many requests. Try again later.", "error": "rate_limited"}).encode()
    await send({"type": "http.response.start", "status": 429,
                "headers": [(b"content-type", b"application/json"), (b"retry-after", str(retry_after).encode()),
                            (b"content-length", str(len(body)).encode())]})
    await send({"type": "http.response.body", "body": body})


# ---------------------------------------------------------- admin networks
@lru_cache(maxsize=16)
def _networks(spec: str) -> tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...]:
    return tuple(ipaddress.ip_network(part.strip(), strict=False) for part in spec.split(",") if part.strip())


def parse_networks(spec: str) -> tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...]:
    """MNEMERYNNE_ADMIN_NETWORKS, e.g. "192.168.1.0/24, 10.8.0.0/16"; raises ValueError on a bad entry."""
    return _networks(spec)


def admin_network_ok(settings: Settings, client_ip: str) -> bool:
    """Admin rights only count from these networks (all networks when unset)."""
    if not settings.admin_networks.strip():
        return True
    try:
        addr = ipaddress.ip_address(client_ip)
    except ValueError:
        return False
    return any(addr in net for net in parse_networks(settings.admin_networks))
