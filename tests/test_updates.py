import json
import os
from pathlib import Path
import subprocess
import sys

import httpx
import pytest

from mnemerynne import updates, users
from fakes import find_bash

SHA_A = "a" * 40
SHA_B = "b" * 40


def _checkout(tmp_path, head=f"ref: refs/heads/main\n", loose=SHA_A, packed=None,
              url="https://github.com/AELovelace/erinne.git"):
    git = tmp_path / ".git"
    (git / "refs" / "heads").mkdir(parents=True)
    (git / "HEAD").write_text(head)
    if loose:
        (git / "refs" / "heads" / "main").write_text(loose + "\n")
    if packed:
        (git / "packed-refs").write_text(f"# pack-refs with: peeled\n{packed} refs/heads/main\n")
    (git / "config").write_text(f'[core]\n\tbare = false\n[remote "origin"]\n\turl = {url}\n'
                                f'\tfetch = +refs/heads/*:refs/remotes/origin/*\n[branch "main"]\n\tremote = origin\n')
    return tmp_path


@pytest.fixture(autouse=True)
def fresh_cache():
    updates._cache.update(at=0.0, key=None, value=None)


def test_installed_version_and_repo_come_from_dot_git(tmp_path):
    app = _checkout(tmp_path)
    assert updates.head_commit(app) == SHA_A
    assert updates.github_repo(app) == "AELovelace/erinne"

    packed = _checkout(tmp_path / "p", loose=None, packed=SHA_B, url="git@github.com:Some-One/repo-name.git")
    assert updates.head_commit(packed) == SHA_B and updates.github_repo(packed) == "Some-One/repo-name"
    detached = _checkout(tmp_path / "d", head=SHA_B + "\n", url="https://gitlab.com/x/y.git")
    assert updates.head_commit(detached) == SHA_B and updates.github_repo(detached) is None
    assert updates.head_commit(tmp_path / "nothing") is None


def _github(compare, calls):
    def handler(request: httpx.Request):
        calls.append(str(request.url))
        if callable(compare):
            return compare(request)
        return httpx.Response(200, json=compare)
    return httpx.Client(transport=httpx.MockTransport(handler))


def _commit(sha, message, date="2026-10-21T10:00:00Z"):
    return {"sha": sha, "commit": {"message": message, "committer": {"date": date}}}


def test_lists_newer_commits_and_what_they_touch(tmp_path, settings):
    app = _checkout(tmp_path)
    calls = []
    client = _github({"status": "ahead", "ahead_by": 2,
                      "base_commit": _commit(SHA_A, "installed one\n\nbody", "2026-10-20T09:00:00Z"),
                      "commits": [_commit("c" * 40, "folder access"), _commit(SHA_B, "settings page")],
                      "files": [{"filename": "mnemerynne/migrations/0007_folder_access.sql"},
                                {"filename": "deploy/linux/mnemerynne-generator.service"},
                                {"filename": "uv.lock"}, {"filename": "deploy/linux/update.sh"},
                                {"filename": "mnemerynne/fs.py"}]}, calls)
    out = updates.check(settings, client=client, app_dir=app)
    assert calls == [f"https://api.github.com/repos/AELovelace/erinne/compare/{SHA_A}...main"]
    assert out["ahead"] == 2 and out["error"] is None
    assert [c["message"] for c in out["commits"]] == ["settings page", "folder access"]   # newest first
    assert out["current"]["message"] == "installed one" and out["current"]["date"] == "2026-10-20"
    assert out["flags"] == {"migrations": ["mnemerynne/migrations/0007_folder_access.sql"],
                            "services": ["mnemerynne-generator.service"], "packages": True, "admin_command": True}
    assert out["command"] == "sudo mnemerynne-admin update"

    # Cached: no second request within the hour, nor for "Check now" within a minute.
    updates.check(settings, client=client, app_dir=app)
    updates.check(settings, client=client, app_dir=app, force=True)
    assert len(calls) == 1


def test_problems_are_reported_not_raised(tmp_path, settings):
    app = _checkout(tmp_path)
    out = updates.check(settings, client=_github(lambda r: httpx.Response(404, json={}), []), app_dir=app)
    assert "doesn't know the installed commit" in out["error"]
    updates._cache.update(at=0.0)
    def down(request):
        raise httpx.ConnectError("no route to host")
    assert "Couldn't reach GitHub" in updates.check(settings, client=_github(down, []), app_dir=app)["error"]
    updates._cache.update(at=0.0)
    out = updates.check(settings, client=_github({"status": "diverged", "ahead_by": 1, "commits": [], "files": []}, []),
                        app_dir=app)
    assert "commits GitHub doesn't" in out["error"]
    assert "isn't a GitHub checkout" in updates.check(
        settings, app_dir=_checkout(tmp_path / "g", url="https://gitlab.com/x/y.git"))["error"]


def test_last_update_result_is_shown(tmp_path, settings):
    (settings.data_dir / "update").mkdir(parents=True)
    (settings.data_dir / "update" / "last.json").write_text(json.dumps(
        {"result": "failed", "from": SHA_A, "to": SHA_B, "message": "failed while restart; undone", "at": "x"}))
    out = updates.check(settings, client=_github({"status": "identical", "ahead_by": 0}, []),
                        app_dir=_checkout(tmp_path))
    assert out["last_update"]["result"] == "failed" and out["ahead"] == 0


def test_update_status_is_admin_only(client, conn, monkeypatch):
    monkeypatch.setattr(updates, "check", lambda settings, force=False: {"ahead": 0, "forced": force})
    users.create_user(conn, "boss", "password123", role="admin")
    users.create_user(conn, "alice", "password123")
    client.post("/login", data={"username": "alice", "password": "password123"})
    assert client.get("/api/admin/updates").status_code == 403
    client.post("/logout")
    client.post("/login", data={"username": "boss", "password": "password123"})
    assert client.get("/api/admin/updates?refresh=1").json() == {"ahead": 0, "forced": True}
    assert "data-tab=\"settings\"" in client.get("/admin/libraries").text


@pytest.mark.parametrize("sync_exit,invalid_source", [(0, False), (9, False), (0, True)])
def test_update_prepares_bytecode_before_startup(tmp_path, sync_exit, invalid_source):
    """Run the updater's package helper with real compilation and a simulated package installer."""
    bash = find_bash()
    if not bash:
        pytest.skip("needs bash")
    app = tmp_path / "application with spaces"
    package = app / "mnemerynne"
    package.mkdir(parents=True)
    (package / "example.py").write_text("not valid Python!" if invalid_source else "VALUE = 42\n", encoding="utf-8")
    interpreter = app / ".venv" / "bin" / "python"
    interpreter.parent.mkdir(parents=True)
    # Use this test's interpreter without installing packages or touching the real environment.
    interpreter.write_text('#!/usr/bin/env bash\nexec "$MNEM_TEST_PYTHON" "$@"\n', encoding="utf-8", newline="\n")
    interpreter.chmod(0o755)
    updater = Path(__file__).resolve().parents[1] / "deploy" / "linux" / "update.sh"
    script = '''
set -e
source <(sed '$d' "$1")
APP=$(cd "$2" && pwd)
uv() { printf '%s\\n' "$@" > "$APP/uv-arguments"; return "$MNEM_TEST_SYNC_EXIT"; }
install_packages
'''
    result = subprocess.run([bash, "-c", script, "_", str(updater), str(app)], capture_output=True, text=True,
                            env={**os.environ, "MNEM_TEST_PYTHON": sys.executable,
                                 "MNEM_TEST_SYNC_EXIT": str(sync_exit)})
    assert result.returncode == (sync_exit or int(invalid_source)), result.stdout + result.stderr
    assert "--compile-bytecode" in (app / "uv-arguments").read_text().splitlines()
    # Successful preparation leaves a usable cache; failed sync or invalid source must not claim success.
    caches = list((package / "__pycache__").glob("example.*.pyc"))
    assert bool(caches) == (sync_exit == 0 and not invalid_source)
