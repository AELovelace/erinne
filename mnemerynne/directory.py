"""The on-premises Active Directory, as the server's domain join sees it.

The box is joined with winbind, so domain accounts and groups resolve through
the system's user database (NSS): no LDAP code or credentials of our own.
Names are plain sAMAccountNames ("jsmith", "Domain Admins"); the NetBIOS domain
(MNEMERYNNE_AD_DOMAIN, e.g. ETERNALHOTELS) is added for lookups.
"""

from __future__ import annotations

import logging
import subprocess
import threading
import time
from typing import Protocol

log = logging.getLogger("mnemerynne.directory")


class Directory(Protocol):
    enabled: bool

    def account_exists(self, account: str) -> bool: ...
    def groups_of(self, account: str) -> set[str]: ...
    def ids_of(self, account: str) -> tuple[int, frozenset[int]] | None: ...
    def accounts(self) -> list[str]: ...
    def groups(self) -> list[str]: ...


def _bare(name: str) -> str:
    """'ETERNALHOTELS\\Domain Admins' -> 'Domain Admins'."""
    return name.rsplit("\\", 1)[-1]


class NoDirectory:
    """No domain configured (or not on Linux): nobody gets anything from AD."""

    enabled = False

    def account_exists(self, account: str) -> bool:
        return False

    def groups_of(self, account: str) -> set[str]:
        return set()

    def ids_of(self, account: str) -> tuple[int, frozenset[int]] | None:
        return None

    def accounts(self) -> list[str]:
        return []

    def groups(self) -> list[str]:
        return []


class WinbindDirectory:
    """Lookups through NSS (winbind). Group memberships are cached for a few
    minutes: they are checked on every admin request."""

    enabled = True

    def __init__(self, domain: str, cache_seconds: float = 300) -> None:
        self.domain = domain
        self.cache_seconds = cache_seconds
        self._cache: dict[str, tuple[float, set[str]]] = {}
        self._lock = threading.Lock()

    def _qualified(self, account: str) -> str:
        return f"{self.domain}\\{_bare(account)}"

    def account_exists(self, account: str) -> bool:
        import pwd
        try:
            pwd.getpwnam(self._qualified(account))
            return True
        except KeyError:
            return False

    def groups_of(self, account: str) -> set[str]:
        """Lower-cased names of the account's groups (nested ones included,
        as winbind resolves them); empty if the account doesn't resolve."""
        import grp
        import os
        import pwd
        key = _bare(account).lower()
        now = time.monotonic()
        with self._lock:
            hit = self._cache.get(key)
            if hit and now - hit[0] < self.cache_seconds:
                return hit[1]
        try:
            entry = pwd.getpwnam(self._qualified(account))
            gids = os.getgrouplist(entry.pw_name, entry.pw_gid)
        except (KeyError, OSError):
            names: set[str] = set()
        else:
            names = set()
            for gid in gids:
                try:
                    names.add(_bare(grp.getgrgid(gid).gr_name).lower())
                except KeyError:
                    continue
        with self._lock:
            self._cache[key] = (now, names)
        return names

    def ids_of(self, account: str) -> tuple[int, frozenset[int]] | None:
        """The account's uid and all its gids as the server sees them (for
        checking file permissions on the share); None if it doesn't resolve."""
        import os
        import pwd
        key = "ids:" + _bare(account).lower()
        now = time.monotonic()
        with self._lock:
            hit = self._cache.get(key)
            if hit and now - hit[0] < self.cache_seconds:
                return hit[1]
        try:
            entry = pwd.getpwnam(self._qualified(account))
            ids = (entry.pw_uid, frozenset(os.getgrouplist(entry.pw_name, entry.pw_gid)))
        except (KeyError, OSError):
            ids = None
        with self._lock:
            self._cache[key] = (now, ids)
        return ids

    def _wbinfo(self, flag: str) -> list[str]:
        try:
            out = subprocess.run(["wbinfo", flag], capture_output=True, text=True, timeout=15, check=True).stdout
        except (OSError, subprocess.SubprocessError) as exc:
            log.warning("wbinfo %s failed: %s", flag, exc)
            return []
        return sorted({_bare(line.strip()) for line in out.splitlines() if line.strip()}, key=str.lower)

    def accounts(self) -> list[str]:
        return self._wbinfo("-u")

    def groups(self) -> list[str]:
        return self._wbinfo("-g")


def from_settings(settings) -> Directory:
    if not settings.ad_domain:
        return NoDirectory()
    try:
        import pwd  # noqa: F401  (Linux only)
    except ImportError:
        log.warning("MNEMERYNNE_AD_DOMAIN is set, but domain lookups need Linux; AD is off.")
        return NoDirectory()
    return WinbindDirectory(settings.ad_domain)
