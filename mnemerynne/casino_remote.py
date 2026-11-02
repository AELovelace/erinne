"""Short-lived proof that a casino input came from a particular paired remote, for a particular TV."""

import hashlib
import secrets

from itsdangerous import BadData, URLSafeTimedSerializer

MAX_AGE = 90  # The TV forwards inputs promptly; old stream messages must not become fresh bets.


def pairing(device):
    """Use a one-way pairing fingerprint so deleting/re-pairing a device invalidates its old inputs."""
    return hashlib.sha256(device["token_hash"].encode()).hexdigest()


def actor(kind, device):
    """A private seat/display identity that cannot be inherited when a database ID is reused."""
    return f"{kind}:{device['id']}:{pairing(device)}"


def issue(secret, remote, screen, action):
    """Bind the authenticated remote, destination TV and exact key to a unique, signed input."""
    return URLSafeTimedSerializer(secret, salt="casino-tv-input-v1").dumps({
        "remote": remote["id"], "pairing": pairing(remote), "screen": actor("screen", screen),
        "action": action, "id": secrets.token_hex(16),
    })


def read(secret, token):
    """Verify integrity and expiry before the web layer rechecks the remote's live pairing."""
    return URLSafeTimedSerializer(secret, salt="casino-tv-input-v1").loads(token, max_age=MAX_AGE)
