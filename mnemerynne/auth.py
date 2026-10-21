"""Password hashing, username rules and login throttling.

Hashes use passlib's pbkdf2_sha256, the same scheme MicroJames used, so
migrated users keep their passwords.
"""

from __future__ import annotations

import re
import threading
import time

from passlib.context import CryptContext

_pwd_ctx = CryptContext(schemes=["pbkdf2_sha256"], deprecated="auto")

# Used to keep login timing similar whether or not the username exists.
_DUMMY_HASH = _pwd_ctx.hash("erinne-auth-dummy")

# Usernames appear in URLs and logs; keep them to a safe, case-insensitive set.
_USERNAME_RE = re.compile(r"^[a-z0-9](?:[a-z0-9._-]{0,62}[a-z0-9])?$")

MIN_PASSWORD_LENGTH = 8


def hash_password(plain: str) -> str:
    return _pwd_ctx.hash(plain)


def verify_password(plain: str, hashed: str | None) -> bool:
    """Constant-ish time check; also burns a hash when there is no user."""
    if not hashed:
        _pwd_ctx.verify(plain, _DUMMY_HASH)
        return False
    try:
        return _pwd_ctx.verify(plain, hashed)
    except (ValueError, TypeError):
        return False


def validate_username(username: str) -> str:
    """Return the normalised username or raise ValueError."""
    name = (username or "").strip().lower()
    if not _USERNAME_RE.match(name):
        raise ValueError(
            "Username must be 1-64 characters of a-z, 0-9, '.', '_' or '-', "
            "starting and ending with a letter or digit."
        )
    return name


def validate_password(password: str) -> str:
    if len(password or "") < MIN_PASSWORD_LENGTH:
        raise ValueError(f"Password must be at least {MIN_PASSWORD_LENGTH} characters.")
    return password


class LoginThrottle:
    """In-memory failure counter per (username, client IP)."""

    def __init__(self, max_failures: int, window_seconds: int) -> None:
        self.max_failures = max_failures
        self.window_seconds = window_seconds
        self._failures: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    @staticmethod
    def _key(username: str, client_ip: str) -> str:
        return f"{(username or '').strip().lower()}|{client_ip}"

    def _recent(self, key: str, now: float) -> list[float]:
        cutoff = now - self.window_seconds
        recent = [t for t in self._failures.get(key, []) if t > cutoff]
        if recent:
            self._failures[key] = recent
        else:
            self._failures.pop(key, None)
        return recent

    def locked_out(self, username: str, client_ip: str) -> bool:
        with self._lock:
            return len(self._recent(self._key(username, client_ip), time.monotonic())) >= self.max_failures

    def record_failure(self, username: str, client_ip: str) -> None:
        key = self._key(username, client_ip)
        with self._lock:
            now = time.monotonic()
            self._failures[key] = self._recent(key, now) + [now]

    def clear(self, username: str, client_ip: str) -> None:
        with self._lock:
            self._failures.pop(self._key(username, client_ip), None)
