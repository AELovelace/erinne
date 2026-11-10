"""Web Push: notifications to the installed web app (the "Android app" is this, a PWA) while it is closed.

How it works: a browser that says yes hands over a subscription (an address at its maker's push service, and two keys).
To notify it we encrypt a small message to those keys (RFC 8291, aes128gcm) and POST it to that address, signed with
this server's own key pair (VAPID, RFC 8292) so the push service knows who is sending. The browser's service worker
(web/static/sw.js) wakes, and shows fixed words for the kind of message: nothing we send is shown as written.

Built on `cryptography` and `httpx`, which Mnemerynne already uses, so there is nothing new to install.
Push only works over HTTPS: a phone has to install the app from the public address.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import sqlite3
import struct
import time
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

log = logging.getLogger(__name__)

# Only real push services are ever contacted: a "subscription" pointing anywhere else (an address inside the
# network, say) is refused when it is saved and again when it is used.
PUSH_HOSTS = ("fcm.googleapis.com", ".push.services.mozilla.com", ".notify.windows.com", ".push.apple.com")
MAX_PER_USER = 10                       # devices one person may subscribe
TTL = 6 * 3600                          # how long the push service keeps trying a phone that is switched off
TRANSPORT: httpx.BaseTransport | None = None   # tests put a stand-in push service here


class PushError(ValueError):
    """Refused; the message is safe to show."""


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def allowed(endpoint: str) -> bool:
    """Whether this address belongs to a push service we know."""
    parts = urlsplit(endpoint)
    host = (parts.hostname or "").lower()
    return parts.scheme == "https" and parts.port in (None, 443) and any(
        host == known or (known.startswith(".") and host.endswith(known)) for known in PUSH_HOSTS)


# --------------------------------------------------------------------------- this server's key pair
def _key(data_dir: Path) -> ec.EllipticCurvePrivateKey:
    """The server's VAPID key, made the first time it is needed and kept beside the database (never in git).
    Losing it only means phones must switch reminders on again."""
    path = data_dir / "push-vapid.pem"
    if path.exists():
        return serialization.load_pem_private_key(path.read_bytes(), password=None)
    key = ec.generate_private_key(ec.SECP256R1())
    data_dir.mkdir(parents=True, exist_ok=True)
    handle = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)   # readable by the service account only
    with os.fdopen(handle, "wb") as out:
        out.write(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    return key


def _point(key: ec.EllipticCurvePublicKey) -> bytes:
    return key.public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)


def public_key(data_dir: Path) -> str:
    """What a browser needs to subscribe to this server (applicationServerKey)."""
    return _b64(_point(_key(data_dir).public_key()))


def _vapid(key: ec.EllipticCurvePrivateKey, endpoint: str, contact: str, now: float) -> str:
    """The Authorization header: a short-lived signed note saying who is sending, for this push service only."""
    parts = urlsplit(endpoint)
    header = _b64(json.dumps({"typ": "JWT", "alg": "ES256"}, separators=(",", ":")).encode())
    claims = _b64(json.dumps({"aud": f"{parts.scheme}://{parts.netloc}", "exp": int(now) + 12 * 3600, "sub": contact},
                             separators=(",", ":")).encode())
    r, s = decode_dss_signature(key.sign(f"{header}.{claims}".encode(), ec.ECDSA(hashes.SHA256())))
    signature = _b64(r.to_bytes(32, "big") + s.to_bytes(32, "big"))
    return f"vapid t={header}.{claims}.{signature}, k={_b64(_point(key.public_key()))}"


# --------------------------------------------------------------------------- encryption (RFC 8291)
def encrypt(message: bytes, p256dh: str, auth: str) -> bytes:
    """Seal a message so only that browser can read it; the push service in the middle sees noise."""
    theirs_raw, secret = _unb64(p256dh), _unb64(auth)
    theirs = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), theirs_raw)
    ours = ec.generate_private_key(ec.SECP256R1())        # a fresh key for every message
    ours_raw = _point(ours.public_key())
    shared = ours.exchange(ec.ECDH(), theirs)
    ikm = HKDF(hashes.SHA256(), 32, secret, b"WebPush: info\x00" + theirs_raw + ours_raw).derive(shared)
    salt = os.urandom(16)
    cek = HKDF(hashes.SHA256(), 16, salt, b"Content-Encoding: aes128gcm\x00").derive(ikm)
    nonce = HKDF(hashes.SHA256(), 12, salt, b"Content-Encoding: nonce\x00").derive(ikm)
    sealed = AESGCM(cek).encrypt(nonce, message + b"\x02", None)          # \x02: this is the last (only) record
    return salt + struct.pack(">IB", 4096, len(ours_raw)) + ours_raw + sealed


# --------------------------------------------------------------------------- subscriptions
def subscribe(conn: sqlite3.Connection, user_id: int, endpoint: str, p256dh: str, auth: str, utc_offset: int) -> None:
    """Remember this browser for this person. Saying yes again on the same browser just refreshes it."""
    try:
        good = allowed(endpoint) and len(_unb64(p256dh)) == 65 and len(_unb64(auth)) == 16
        if good:
            ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), _unb64(p256dh))
    except ValueError:
        good = False
    if not good:
        raise PushError("This browser's notification details weren't recognised.")
    known = conn.execute("SELECT 1 FROM push_subscriptions WHERE endpoint = ?", (endpoint,)).fetchone()
    held = conn.execute("SELECT COUNT(*) FROM push_subscriptions WHERE user_id = ?", (user_id,)).fetchone()[0]
    if not known and held >= MAX_PER_USER:
        raise PushError("Too many devices have notifications on. Switch one off first.")
    conn.execute("INSERT INTO push_subscriptions(user_id, endpoint, p256dh, auth, utc_offset) VALUES (?, ?, ?, ?, ?) "
                 "ON CONFLICT(endpoint) DO UPDATE SET user_id = excluded.user_id, p256dh = excluded.p256dh, auth = excluded.auth, "
                 "utc_offset = excluded.utc_offset", (user_id, endpoint, p256dh, auth, utc_offset))


def unsubscribe(conn: sqlite3.Connection, user_id: int, endpoint: str) -> None:
    conn.execute("DELETE FROM push_subscriptions WHERE user_id = ? AND endpoint = ?", (user_id, endpoint))


def subscriptions(conn: sqlite3.Connection, user_id: int) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM push_subscriptions WHERE user_id = ? ORDER BY id", (user_id,)).fetchall()


# --------------------------------------------------------------------------- sending
def send(conn: sqlite3.Connection, data_dir: Path, contact: str, user_id: int, payload: dict) -> int:
    """Push one small message to every device this person subscribed. Returns how many push services took it.
    A device that has gone (uninstalled, permission withdrawn) is forgotten. Never raises for a network fault."""
    rows = subscriptions(conn, user_id)
    if not rows:
        return 0
    key, body, taken = _key(data_dir), json.dumps(payload, separators=(",", ":")).encode(), 0
    with httpx.Client(timeout=10, follow_redirects=False, transport=TRANSPORT) as client:   # never follow a redirect with our signature
        for row in rows:
            if not allowed(row["endpoint"]):
                continue
            try:
                answer = client.post(row["endpoint"], content=encrypt(body, row["p256dh"], row["auth"]), headers={
                    "Authorization": _vapid(key, row["endpoint"], contact, time.time()), "Content-Encoding": "aes128gcm",
                    "Content-Type": "application/octet-stream", "TTL": str(TTL), "Urgency": "normal"})
            except (httpx.HTTPError, ValueError):
                log.warning("A push service couldn't be reached.")
                continue
            if answer.status_code in (404, 410):           # the browser no longer wants these
                conn.execute("DELETE FROM push_subscriptions WHERE id = ?", (row["id"],))
            elif answer.is_success:
                taken += 1
            else:
                log.warning("A push service answered %s.", answer.status_code)
    return taken
