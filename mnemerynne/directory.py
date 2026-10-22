"""The on-premises Active Directory, as the server's domain join sees it.

The box is joined with winbind, so domain accounts and groups resolve through
the system's user database (NSS): no LDAP code or credentials of our own.
Names are plain sAMAccountNames ("jsmith", "Domain Admins"); the NetBIOS domain
(MNEMERYNNE_AD_DOMAIN, e.g. ETERNALHOTELS) is added for lookups.

Winbind answers group memberships from what it learned at the person's last
sign-in to this box, which for most people is never, so changes in AD don't
show. A root timer (mnemerynne-adsync, deploy/linux/ad-snapshot.sh) therefore
reads every account's and group's memberships straight from a domain
controller every few minutes into a snapshot file; memberships come from that
while it is fresh, and from winbind otherwise.
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

log = logging.getLogger("mnemerynne.directory")

# Older than this, the snapshot is ignored (the timer has stopped).
SNAPSHOT_MAX_AGE = 30 * 60

# sAMAccountType values.
_USER_TYPES = {805306368}
_GROUP_TYPES = {268435456, 268435457, 536870912, 536870913}


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


# ------------------------------------------------------------------ snapshot
@dataclass
class _Entry:
    name: str
    sid: str
    kind: str                      # "user" | "group"
    member_of: list[str]           # lower-cased DNs
    primary_group: str | None = None


@dataclass
class Snapshot:
    """Accounts and groups as `net ads search` printed them."""

    by_name: dict[str, _Entry] = field(default_factory=dict)     # lower name -> account or group
    by_dn: dict[str, _Entry] = field(default_factory=dict)       # lower DN -> group
    by_sid: dict[str, _Entry] = field(default_factory=dict)      # SID -> group

    def account(self, name: str) -> _Entry | None:
        e = self.by_name.get(_bare(name).lower())
        return e if e is not None and e.kind == "user" else None

    def group_closure(self, user: _Entry) -> list[_Entry]:
        """The user's groups: direct, the primary group, and everything those
        are nested in."""
        start = [self.by_dn[dn] for dn in user.member_of if dn in self.by_dn]
        if user.primary_group and user.primary_group in self.by_sid:
            start.append(self.by_sid[user.primary_group])
        seen: dict[str, _Entry] = {}
        while start:
            g = start.pop()
            if g.sid in seen:
                continue
            seen[g.sid] = g
            start.extend(self.by_dn[dn] for dn in g.member_of if dn in self.by_dn)
        return list(seen.values())


def parse_snapshot(text: str) -> Snapshot:
    """Parse `net ads search` output: "attribute: value" lines, one blank line
    between objects, attributes repeated for each value."""
    snap = Snapshot()
    for block in re.split(r"\n\s*\n", text):
        attrs: dict[str, list[str]] = {}
        for line in block.splitlines():
            key, sep, value = line.partition(": ")
            if sep:
                attrs.setdefault(key.strip().lower(), []).append(value.strip())
        name = (attrs.get("samaccountname") or [""])[0]
        sid = (attrs.get("objectsid") or [""])[0]
        dn = (attrs.get("distinguishedname") or [""])[0].lower()
        try:
            account_type = int((attrs.get("samaccounttype") or ["0"])[0])
        except ValueError:
            continue
        if not name or not sid.startswith("S-1-"):
            continue
        member_of = [m.lower() for m in attrs.get("memberof", [])]
        if account_type in _USER_TYPES:
            primary = None
            rid = (attrs.get("primarygroupid") or [""])[0]
            if rid.isdigit():
                primary = sid.rsplit("-", 1)[0] + "-" + rid
            snap.by_name[name.lower()] = _Entry(name, sid, "user", member_of, primary)
        elif account_type in _GROUP_TYPES:
            entry = _Entry(name, sid, "group", member_of)
            snap.by_name.setdefault(name.lower(), entry)
            snap.by_sid[sid] = entry
            if dn:
                snap.by_dn[dn] = entry
    return snap


_UNIX_ID = re.compile(r"^(S-[\d-]+) -> (?:uid|gid|uid/gid) (\d+)$")


class WinbindDirectory:
    """Lookups through NSS (winbind), with memberships from the AD snapshot
    when there is a fresh one. Winbind memberships are cached for a few
    minutes: they are checked on every admin request."""

    enabled = True

    def __init__(self, domain: str, cache_seconds: float = 300, snapshot_path: Path | None = None) -> None:
        self.domain = domain
        self.cache_seconds = cache_seconds
        self.snapshot_path = snapshot_path
        self._cache: dict[str, tuple[float, object]] = {}
        self._lock = threading.Lock()
        self._snapshot: Snapshot | None = None
        self._snapshot_mtime: float | None = None
        self._stale_warned = 0.0
        self._unix_ids: dict[str, int] = {}     # SID -> uid/gid; idmap never changes them

    def _qualified(self, account: str) -> str:
        return f"{self.domain}\\{_bare(account)}"

    # -------------------------------------------------------------- snapshot
    def snapshot(self) -> Snapshot | None:
        """The AD snapshot, or None when there is none or it is stale."""
        if self.snapshot_path is None:
            return None
        try:
            mtime = self.snapshot_path.stat().st_mtime
        except OSError:
            return None
        if time.time() - mtime > SNAPSHOT_MAX_AGE:
            if time.monotonic() - self._stale_warned > 600:
                self._stale_warned = time.monotonic()
                log.warning("The AD snapshot %s is over %d minutes old (is mnemerynne-adsync.timer running?); "
                            "using winbind's memberships, which can be out of date.",
                            self.snapshot_path, SNAPSHOT_MAX_AGE // 60)
            return None
        with self._lock:
            if mtime == self._snapshot_mtime:
                return self._snapshot
        try:
            snap = parse_snapshot(self.snapshot_path.read_text(encoding="utf-8", errors="replace"))
        except OSError as exc:
            log.warning("Couldn't read the AD snapshot %s: %s", self.snapshot_path, exc)
            return None
        if not snap.by_name:
            return None
        with self._lock:
            self._snapshot, self._snapshot_mtime = snap, mtime
        return snap

    def _sids_to_unix_ids(self, sids: list[str]) -> dict[str, int]:
        with self._lock:
            missing = [s for s in sids if s not in self._unix_ids]
        if missing:
            try:
                out = subprocess.run(["wbinfo", "--sids-to-unix-ids=" + ",".join(missing)], capture_output=True,
                                     text=True, timeout=15, check=True).stdout
            except (OSError, subprocess.SubprocessError) as exc:
                log.warning("wbinfo --sids-to-unix-ids failed: %s", exc)
                out = ""
            found = {m.group(1): int(m.group(2)) for m in map(_UNIX_ID.match, out.splitlines()) if m}
            with self._lock:
                self._unix_ids.update(found)
        with self._lock:
            return {s: self._unix_ids[s] for s in sids if s in self._unix_ids}

    # ---------------------------------------------------------------- lookups
    def account_exists(self, account: str) -> bool:
        snap = self.snapshot()
        if snap is not None and snap.account(account) is not None:
            return True
        import pwd
        try:
            pwd.getpwnam(self._qualified(account))
            return True
        except KeyError:
            return False

    def groups_of(self, account: str) -> set[str]:
        """Lower-cased names of the account's groups (nested ones included);
        empty if the account doesn't resolve."""
        snap = self.snapshot()
        if snap is not None:
            user = snap.account(account)
            if user is not None:
                return {g.name.lower() for g in snap.group_closure(user)}
        return self._nss_groups_of(account)

    def ids_of(self, account: str) -> tuple[int, frozenset[int]] | None:
        """The account's uid and all its gids as the server sees them (for
        checking file permissions on the share); None if it doesn't resolve."""
        snap = self.snapshot()
        if snap is not None:
            user = snap.account(account)
            if user is not None:
                # idmap gives the user's SID a gid too (as getgrouplist does).
                sids = [user.sid] + [g.sid for g in snap.group_closure(user)]
                ids = self._sids_to_unix_ids(sids)
                if user.sid in ids:
                    return ids[user.sid], frozenset(ids.values())
        return self._nss_ids_of(account)

    def _nss_groups_of(self, account: str) -> set[str]:
        import grp
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

    def _nss_ids_of(self, account: str) -> tuple[int, frozenset[int]] | None:
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

    def _listed(self, kind: str) -> list[str] | None:
        snap = self.snapshot()
        if snap is None:
            return None
        return sorted({e.name for e in snap.by_name.values() if e.kind == kind}, key=str.lower)

    def accounts(self) -> list[str]:
        listed = self._listed("user")
        return listed if listed is not None else self._wbinfo("-u")

    def groups(self) -> list[str]:
        listed = self._listed("group")
        return listed if listed is not None else self._wbinfo("-g")


def from_settings(settings) -> Directory:
    if not settings.ad_domain:
        return NoDirectory()
    try:
        import pwd  # noqa: F401  (Linux only)
    except ImportError:
        log.warning("MNEMERYNNE_AD_DOMAIN is set, but domain lookups need Linux; AD is off.")
        return NoDirectory()
    return WinbindDirectory(settings.ad_domain, snapshot_path=settings.ad_snapshot)
