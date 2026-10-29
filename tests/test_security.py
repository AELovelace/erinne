"""Hardening for the open internet (mnemerynne/web/security.py)."""

import pytest
from fastapi.testclient import TestClient

from mnemerynne import users
from mnemerynne.auth import LoginThrottle
from mnemerynne.config import Settings
from mnemerynne.web import security
from mnemerynne.web.app import create_app

GOOD_KEY = "k" * 48


def _settings(tmp_path, **extra):
    return Settings(_env_file=None, data_dir=tmp_path / "data", embed_dim=4, **extra)


def _login(client, username="alice", password="password123"):
    return client.post("/login", data={"username": username, "password": password}, follow_redirects=False)


# ------------------------------------------------------------------ headers
def test_pages_get_security_headers(client):
    r = client.get("/login")
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["x-frame-options"] == "SAMEORIGIN"
    assert "frame-ancestors 'self'" in r.headers["content-security-policy"]
    assert r.headers["referrer-policy"] == "same-origin"
    assert "microphone=()" in r.headers["permissions-policy"]
    assert "strict-transport-security" not in r.headers   # plain http, no https public address


def test_hsts_when_the_public_address_is_https(tmp_path):
    settings = _settings(tmp_path, secret_key=GOOD_KEY, public_url="https://erynne.example.org")
    with TestClient(create_app(settings)) as client:
        assert client.get("/login").headers["strict-transport-security"] == "max-age=31536000"


def test_routes_own_csp_is_kept_and_sandboxed_ones_untouched():
    async def inner(scope, receive, send):
        await send({"type": "http.response.start", "status": 200,
                    "headers": [(b"content-security-policy", b"default-src 'none'; sandbox")]})
        await send({"type": "http.response.body", "body": b""})

    sent = []

    async def capture(message):
        sent.append(message)

    import asyncio
    app = security.SecurityHeaders(inner, Settings(_env_file=None))
    asyncio.run(app({"type": "http", "scheme": "http", "headers": []}, None, capture))
    headers = dict((k.decode(), v.decode()) for k, v in sent[0]["headers"])
    assert headers["content-security-policy"] == "default-src 'none'; sandbox"
    assert headers["x-content-type-options"] == "nosniff"


# --------------------------------------------------------------- secret key
@pytest.mark.parametrize("key", ["change-me", "CHANGE-ME", " changeme "])
def test_placeholder_keys_are_refused_even_in_development(tmp_path, key):
    with pytest.raises(security.InsecureConfig):
        security.check_secret_key(_settings(tmp_path, secret_key=key))


def test_missing_or_short_key_refused_when_reachable(tmp_path):
    for extra in ({"public_url": "https://erynne.example.org"}, {"host": "0.0.0.0"}):
        with pytest.raises(security.InsecureConfig):
            security.check_secret_key(_settings(tmp_path, **extra))
        with pytest.raises(security.InsecureConfig):
            security.check_secret_key(_settings(tmp_path, secret_key="short-key", **extra))
        assert security.check_secret_key(_settings(tmp_path, secret_key=GOOD_KEY, **extra)) == GOOD_KEY


def test_development_on_loopback_may_run_without_a_key(tmp_path):
    assert security.check_secret_key(_settings(tmp_path)) is None
    assert security.check_secret_key(_settings(tmp_path, secret_key="short-key")) == "short-key"


def test_server_refuses_to_start_without_a_key_when_public(tmp_path):
    with pytest.raises(security.InsecureConfig):
        create_app(_settings(tmp_path, public_url="https://erynne.example.org"))


def test_bad_admin_networks_fail_at_start(tmp_path):
    with pytest.raises(ValueError):
        create_app(_settings(tmp_path, secret_key=GOOD_KEY, admin_networks="192.168.1.0/24, not-a-net"))


# ----------------------------------------------------------- https cookies
def test_session_cookie_https_only_follows_the_public_address(tmp_path):
    assert security.https_only(_settings(tmp_path, public_url="https://erynne.example.org"))
    assert not security.https_only(_settings(tmp_path, public_url="http://erynne.lan"))
    assert not security.https_only(_settings(tmp_path))
    assert not security.https_only(_settings(tmp_path, public_url="https://x.org", session_https_only=False))
    assert security.https_only(_settings(tmp_path, session_https_only=True))


# -------------------------------------------------------------- rate limits
def _register_limit():
    return next(limit for prefix, _, limit, _ in security.PUBLIC_LIMITS if prefix == "/oauth/register")


def test_oauth_registration_is_rate_limited_per_ip(client):
    body = {"redirect_uris": ["http://127.0.0.1:5000/callback"], "client_name": "x"}
    limit = _register_limit()
    codes = [client.post("/oauth/register", json=body).status_code for _ in range(limit + 1)]
    assert codes[-1] == 429 and 429 not in codes[:limit]
    r = client.post("/oauth/register", json=body)
    assert r.json()["error"] == "rate_limited" and r.headers["retry-after"] == "3600"
    assert r.headers["x-content-type-options"] == "nosniff"


def test_limits_are_per_client_ip(settings, conn):
    app = create_app(settings)
    body = {"redirect_uris": ["http://127.0.0.1:5000/callback"]}
    # One client runs the app's startup; the second shares it (startup can't run twice).
    with TestClient(app, client=("203.0.113.5", 1)) as a:
        b = TestClient(app, client=("203.0.113.6", 1))
        for _ in range(_register_limit()):
            a.post("/oauth/register", json=body)
        assert a.post("/oauth/register", json=body).status_code == 429
        assert b.post("/oauth/register", json=body).status_code != 429


def test_rate_limiter_window_expires(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(security.time, "monotonic", lambda: now[0])
    limiter = security.RateLimiter()
    assert all(limiter.allow("k", 2, 60) for _ in range(2))
    assert not limiter.allow("k", 2, 60)
    now[0] += 61
    assert limiter.allow("k", 2, 60)


# ---------------------------------------------------------- login spraying
def test_one_ip_spraying_many_usernames_gets_locked_out(client, conn):
    users.create_user(conn, "alice", "password123")
    for i in range(20):
        assert _login(client, username=f"user{i}", password="wrong").status_code == 401
    assert _login(client).status_code == 429   # alice's right password, from the spraying IP


def test_ip_count_survives_a_successful_login(monkeypatch):
    throttle = LoginThrottle(5, 300, ip_max_failures=3)
    for name in ("a", "b", "c"):
        throttle.record_failure(name, "203.0.113.5")
    throttle.clear("alice", "203.0.113.5")
    assert throttle.locked_out("alice", "203.0.113.5")
    assert not throttle.locked_out("alice", "203.0.113.6")


# ---------------------------------------------------------- admin networks
def test_admin_rights_only_from_admin_networks(tmp_path):
    settings = _settings(tmp_path, secret_key=GOOD_KEY, admin_networks="192.168.1.0/24")
    app = create_app(settings)
    with TestClient(app, client=("192.168.1.20", 1)) as lan:
        outside = TestClient(app, client=("203.0.113.5", 1))   # shares lan's startup
        from mnemerynne import db
        conn = db.connect(settings.db_path)
        users.create_user(conn, "boss", "password123", role="admin")
        conn.close()
        for c in (lan, outside):
            assert _login(c, "boss").status_code == 303
        assert lan.get("/api/admin/libraries").status_code == 200
        assert outside.get("/api/admin/libraries").status_code == 403
        # Still an ordinary user from outside: their own things work.
        assert outside.get("/api/me").status_code == 200


def test_admin_networks_unset_means_anywhere(tmp_path):
    settings = _settings(tmp_path)
    assert security.admin_network_ok(settings, "203.0.113.5")
    limited = _settings(tmp_path, admin_networks="10.0.0.0/8, fd00::/8")
    assert security.admin_network_ok(limited, "10.1.2.3")
    assert security.admin_network_ok(limited, "fd00::1")
    assert not security.admin_network_ok(limited, "203.0.113.5")
    assert not security.admin_network_ok(limited, "testclient")


def test_signing_out_ends_the_session_everywhere(client, conn):
    """Sessions are signed cookies the server doesn't keep, so Sign out bumps the account's session epoch:
    a cookie copied from a shared PC stops working the moment the person signs out anywhere."""
    users.create_user(conn, "alice", "password123")
    client.post("/login", data={"username": "alice", "password": "password123"})
    stolen = client.cookies.get("mnemerynne_session")
    assert client.get("/api/me").status_code == 200
    client.post("/logout")
    client.cookies.set("mnemerynne_session", stolen)
    assert client.get("/api/me").status_code == 401
