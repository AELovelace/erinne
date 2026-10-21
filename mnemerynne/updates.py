"""Is a newer Mnemerynne on GitHub? Read-only: for the Settings page.

The installed version comes from the checkout's .git directory (read as
files: the web app can't run git there, and doesn't need to). What's newer
comes from GitHub's public API, at most once an hour. Installing an update is
`sudo mnemerynne-admin update` (deploy/linux/update.sh), never the web app.
"""

from __future__ import annotations

import json
import re
import threading
import time
from pathlib import Path

import httpx

from mnemerynne.config import Settings

APP_DIR = Path(__file__).resolve().parent.parent
CACHE_SECONDS = 3600
FORCE_SECONDS = 60          # "Check now" can't hit GitHub more often than this
_API = "https://api.github.com"
_lock = threading.Lock()
_cache: dict = {"at": 0.0, "key": None, "value": None}


def head_commit(app_dir: Path = APP_DIR) -> str | None:
    """The checked-out commit, from .git/HEAD (following a branch ref, loose or packed)."""
    git = app_dir / ".git"
    try:
        head = (git / "HEAD").read_text().strip()
    except OSError:
        return None
    if not head.startswith("ref: "):
        return head or None
    ref = head[5:].strip()
    try:
        return (git / ref).read_text().strip()
    except OSError:
        pass
    try:
        for line in (git / "packed-refs").read_text().splitlines():
            parts = line.split()
            if len(parts) == 2 and parts[1] == ref:
                return parts[0]
    except OSError:
        pass
    return None


_GITHUB_URL = re.compile(r"github\.com[:/]+([^/\s]+)/([^/\s]+?)(?:\.git)?/?$")


def github_repo(app_dir: Path = APP_DIR) -> str | None:
    """owner/name of the checkout's `origin`, if it is on GitHub."""
    try:
        config = (app_dir / ".git" / "config").read_text()
    except OSError:
        return None
    section = re.search(r'\[remote "origin"\](.*?)(?:\n\[|\Z)', config, re.DOTALL)
    url = re.search(r"^\s*url\s*=\s*(\S+)", section.group(1), re.MULTILINE) if section else None
    match = _GITHUB_URL.search(url.group(1)) if url else None
    return f"{match.group(1)}/{match.group(2)}" if match else None


def last_update(settings: Settings) -> dict | None:
    """What the last `mnemerynne-admin update` did (written by update.sh)."""
    try:
        return json.loads((settings.data_dir / "update" / "last.json").read_text())
    except (OSError, ValueError):
        return None


def _flags(files: list[str]) -> dict:
    return {
        "migrations": [f for f in files if re.fullmatch(r"mnemerynne/migrations/\d+_.*\.sql", f)],
        "services": [f.rsplit("/", 1)[-1] for f in files if re.fullmatch(r"deploy/linux/[^/]+\.(service|timer)", f)],
        "packages": any(f in ("uv.lock", "pyproject.toml") for f in files),
        "admin_command": "deploy/linux/mnemerynne-admin" in files or "deploy/linux/update.sh" in files,
    }


def _commit_view(c: dict) -> dict:
    info = c.get("commit") or {}
    return {"sha": c.get("sha", ""), "short": c.get("sha", "")[:7],
            "message": (info.get("message") or "").split("\n", 1)[0],
            "date": ((info.get("committer") or {}).get("date") or "")[:10]}


def check(settings: Settings, *, force: bool = False, client: httpx.Client | None = None,
          app_dir: Path = APP_DIR) -> dict:
    """Current version, newer commits on GitHub and what they touch; cached."""
    repo = settings.update_repo or github_repo(app_dir)
    branch = settings.update_branch
    current = head_commit(app_dir)
    key = (repo, branch, current)
    now = time.time()
    with _lock:
        age = now - _cache["at"]
        if _cache["key"] == key and _cache["value"] and (age < CACHE_SECONDS and not force or age < FORCE_SECONDS):
            return {**_cache["value"], "last_update": last_update(settings)}

    out: dict = {"repo": repo, "branch": branch, "current": {"sha": current or "", "short": (current or "")[:7]},
                 "commits": [], "ahead": 0, "status": "unknown", "flags": _flags([]), "error": None,
                 "checked_at": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(now)),
                 "command": "sudo mnemerynne-admin update"}
    if not current:
        out["error"] = f"Couldn't read the installed version from {app_dir / '.git'}."
    elif not repo:
        out["error"] = "This copy isn't a GitHub checkout; set MNEMERYNNE_UPDATE_REPO (owner/name)."
    else:
        own = client is None
        client = client or httpx.Client(timeout=10, headers={"Accept": "application/vnd.github+json",
                                                              "User-Agent": "mnemerynne-update-check"})
        try:
            r = client.get(f"{_API}/repos/{repo}/compare/{current}...{branch}")
            if r.status_code == 404:
                out["error"] = (f"GitHub doesn't know the installed commit {current[:7]} or branch {branch} "
                                f"in {repo} (a private repository, or a local-only commit).")
            else:
                r.raise_for_status()
                data = r.json()
                out["status"] = data.get("status", "unknown")
                out["ahead"] = int(data.get("ahead_by", 0))
                out["commits"] = [_commit_view(c) for c in reversed(data.get("commits", []))]
                out["flags"] = _flags([f.get("filename", "") for f in data.get("files", [])])
                base = data.get("base_commit") or {}
                if base:
                    out["current"] = _commit_view(base)
                if out["status"] in ("behind", "diverged"):
                    out["error"] = ("This copy has commits GitHub doesn't, so `mnemerynne-admin update` "
                                    "will refuse until that's sorted out on the box.")
        except (httpx.HTTPError, ValueError) as exc:
            out["error"] = f"Couldn't reach GitHub: {exc}"
        finally:
            if own:
                client.close()
    with _lock:
        _cache.update(at=now, key=key, value=out)
    return {**out, "last_update": last_update(settings)}
