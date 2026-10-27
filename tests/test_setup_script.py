"""deploy/linux/setup.sh, the one-shot setup wizard: its helpers (settings file edits, the Samba share
definition), the plan it prints on a dry run without touching anything, and the file steps it does
under a test root. Runs under bash (Git Bash on Windows); skipped where there is none."""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SETUP = REPO / "deploy" / "linux" / "setup.sh"
BASH = shutil.which("bash")

pytestmark = pytest.mark.skipif(not BASH, reason="needs bash")


def posix(path: Path) -> str:
    """The path as bash sees it (C:\\x -> /c/x under Git Bash)."""
    return subprocess.run([BASH, "-c", 'cd "$(dirname "$1")" && printf "%s/%s" "$(pwd)" "$(basename "$1")"', "_", str(path)],
                          capture_output=True, text=True, check=True).stdout.strip()


def _env(**extra) -> dict:
    # The whole environment: Windows programs expand %SystemDrive% to a literal folder without it.
    return {**os.environ, **extra}


def bash(script: str, *, root: Path | None = None, env: dict | None = None, check=True):
    full = _env(SETUP_SOURCE_ONLY="1", **(env or {}))
    if root is not None:
        full["SETUP_ROOT"] = posix(root)
    return subprocess.run([BASH, "-c", f'source "{posix(SETUP)}"; {script}'], capture_output=True, text=True,
                          env=full, check=check)


def _root(tmp_path: Path) -> Path:
    root = tmp_path / "root"
    (root / "etc").mkdir(parents=True)
    (root / "etc" / "os-release").write_text("NAME=Fedora Linux\nID=fedora\n")
    (root / "srv" / "storage").mkdir(parents=True)
    return root


def test_the_scripts_parse():
    for name in ("setup.sh", "preinstall.sh", "update.sh", "share-acl-watch.sh"):
        subprocess.run([BASH, "-n", posix(REPO / "deploy" / "linux" / name)], check=True)


def test_set_env_edits_keys_in_place(tmp_path):
    env = tmp_path / "x.env"
    env.write_text("# comment\nMNEMERYNNE_DATA_DIR=/var/lib/mnemerynne\nMNEMERYNNE_SECRET_KEY=change-me\n"
                   "#MNEMERYNNE_SHARE_ROOT=/srv/storage/share\n")
    f = posix(env)
    bash(f'set_env MNEMERYNNE_SECRET_KEY abc-DEF_123 "{f}"; set_env MNEMERYNNE_SHARE_ROOT /srv/data/share "{f}"; '
         f'set_env MNEMERYNNE_AD_DOMAIN CORP "{f}"; set_env MNEMERYNNE_DATA_DIR /srv/data/mnemerynne "{f}"')
    assert env.read_text() == ("# comment\nMNEMERYNNE_DATA_DIR=/srv/data/mnemerynne\nMNEMERYNNE_SECRET_KEY=abc-DEF_123\n"
                               "MNEMERYNNE_SHARE_ROOT=/srv/data/share\nMNEMERYNNE_AD_DOMAIN=CORP\n")
    got = bash(f'env_value MNEMERYNNE_SHARE_ROOT none "{f}"; echo; env_value MNEMERYNNE_NOPE fallback "{f}"').stdout
    assert got == "/srv/data/share\nfallback"
    # The shipped example round-trips: every key the wizard sets lands on its own active line.
    example = tmp_path / "example.env"
    example.write_text((REPO / "deploy" / "linux" / "mnemerynne.env.example").read_text())
    f = posix(example)
    bash(f'set_env MNEMERYNNE_BACKUP_DIR /srv/storage/backups/mnemerynne "{f}"; set_env MNEMERYNNE_SHARE_ROOT /srv/storage/share "{f}"')
    lines = example.read_text().splitlines()
    assert "MNEMERYNNE_BACKUP_DIR=/srv/storage/backups/mnemerynne" in lines and "MNEMERYNNE_SHARE_ROOT=/srv/storage/share" in lines
    assert not any(l.startswith("#MNEMERYNNE_SHARE_ROOT") or l.startswith("#MNEMERYNNE_BACKUP_DIR") for l in lines)


def test_the_samba_share_definition():
    out = bash("samba_conf share /srv/storage/share").stdout
    assert "[share]" in out and "path = /srv/storage/share" in out and "vfs objects = acl_xattr" in out
    assert "read only = no" in out and "hide dot files = yes" in out


def test_a_dry_run_shows_the_plan_and_changes_nothing(tmp_path):
    root = _root(tmp_path)
    before = sorted(p.relative_to(root) for p in root.rglob("*"))
    got = subprocess.run([BASH, posix(SETUP), "--dry-run", "--mount", "/srv/storage", "--hostname", "erynne.test",
                          "--no-samba", "--no-caddy", "--skip-preinstall", "--no-admin"],
                         capture_output=True, text=True, env=_env(SETUP_ROOT=posix(root)))
    assert got.returncode == 0, got.stderr
    plan = got.stdout
    assert "Use the storage at /srv/storage" in plan
    assert "Folders: /srv/storage/share" in plan and "/srv/storage/.mnemerynne-trash" in plan
    assert "/srv/storage/backups/mnemerynne" in plan and "/srv/storage/mnemerynne" in plan
    assert "MNEMERYNNE_DATA_DIR=/srv/storage/mnemerynne" in plan and "PUBLIC_URL=https://erynne.test" in plan
    assert "SHARE_ROOT=/srv/storage/share" in plan and "a new SECRET_KEY" in plan
    assert "share-acl watcher" in plan and "Dry run: nothing was changed." in plan
    assert "Samba" not in plan and "Caddy" not in plan and "WIPE" not in plan
    assert sorted(p.relative_to(root) for p in root.rglob("*")) == before

    # --data-local keeps the database where it is; --no-storage plans settings and services only.
    env = _env(SETUP_ROOT=posix(root))
    local = subprocess.run([BASH, posix(SETUP), "--dry-run", "--mount", "/srv/storage", "--data-local", "--hostname", "h",
                            "--no-samba", "--no-caddy", "--skip-preinstall", "--no-admin"],
                           capture_output=True, text=True, env=env).stdout
    assert "MNEMERYNNE_DATA_DIR=/var/lib/mnemerynne" in local
    none = subprocess.run([BASH, posix(SETUP), "--dry-run", "--no-storage", "--hostname", "h", "--no-caddy",
                           "--skip-preinstall", "--no-admin"], capture_output=True, text=True, env=env).stdout
    assert "No storage setup" in none and "Folders:" not in none
    bad = subprocess.run([BASH, posix(SETUP), "--dry-run", "--mount", "/srv/nope", "--hostname", "h"],
                         capture_output=True, text=True, env=env)
    assert bad.returncode == 1 and "/srv/nope doesn't exist" in bad.stderr
    assert subprocess.run([BASH, posix(SETUP), "--wat"], capture_output=True, text=True, env=env).returncode == 1


def test_the_file_steps_under_a_test_root(tmp_path):
    """Settings, the Samba include and the Caddyfile, written under SETUP_ROOT, twice (the second run changes nothing)."""
    root = _root(tmp_path)
    (root / "etc" / "samba").mkdir()
    (root / "etc" / "samba" / "smb.conf").write_text("[global]\n    workgroup = CORP\n    server role = active directory domain controller\n")
    (root / "etc" / "caddy").mkdir()
    (root / "etc" / "caddy" / "Caddyfile").write_text(":80 {\n    root * /usr/share/caddy\n    file_server\n}\n")
    script = ('MOUNT=/srv/storage; STORAGE=1; DATA_ON_STORAGE=1; SAMBA=1; CADDY=1; HOST_NAME=erynne.test; YES=1; '
              'gather_rest; do_settings; do_samba; do_caddy')
    first = bash(script, root=root)
    env = (root / "etc" / "mnemerynne" / "mnemerynne.env").read_text()
    assert "MNEMERYNNE_DATA_DIR=/srv/storage/mnemerynne\n" in env and "MNEMERYNNE_SHARE_ROOT=/srv/storage/share\n" in env
    assert "MNEMERYNNE_BACKUP_DIR=/srv/storage/backups/mnemerynne\n" in env and "MNEMERYNNE_PUBLIC_URL=https://erynne.test\n" in env
    key = next(l for l in env.splitlines() if l.startswith("MNEMERYNNE_SECRET_KEY="))[len("MNEMERYNNE_SECRET_KEY="):]
    assert len(key) >= 32 and key != "change-me" and "New MNEMERYNNE_SECRET_KEY: " + key in first.stdout
    assert (root / "usr" / "local" / "bin" / "mnemerynne-admin").exists()
    smb = (root / "etc" / "samba" / "smb.conf").read_text()
    assert smb.count("include = /etc/samba/mnemerynne-share.conf") == 1 and smb.startswith("[global]")
    assert "[share]\n    path = /srv/storage/share" in (root / "etc" / "samba" / "mnemerynne-share.conf").read_text()
    caddy = (root / "etc" / "caddy" / "Caddyfile").read_text()
    assert caddy.startswith("erynne.test {") or "\nerynne.test {" in caddy
    assert not any(l.strip().startswith("root * /usr/share/caddy") for l in caddy.splitlines())   # the test page is gone
    assert list((root / "etc" / "caddy").glob("Caddyfile.bak-*"))

    second = bash(script, root=root)
    assert (root / "etc" / "mnemerynne" / "mnemerynne.env").read_text() == env       # the key is kept
    assert "New MNEMERYNNE_SECRET_KEY" not in second.stdout
    assert (root / "etc" / "samba" / "smb.conf").read_text() == smb
    assert (root / "etc" / "caddy" / "Caddyfile").read_text() == caddy and "already" in second.stdout
