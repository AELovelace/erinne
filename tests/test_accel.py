"""nginx sends the file bytes (docs/plans/services-rewrite.md, Phase 1): mnemerynne/web/sendfile.py, the accel
part of mnemerynne/services/nginx.py, and `front-door.sh accel on|off` with the box stood in for."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route

from fakes import find_bash
from mnemerynne import users
from mnemerynne.config import Settings
from mnemerynne.services import nginx
from mnemerynne.web import security, sendfile
from mnemerynne.web.app import create_app

DEPLOY = Path(__file__).resolve().parents[1] / "deploy" / "linux"
ACCEL = {"X-Mnemerynne-Accel": "1"}


# --- which folders, and the internal address of a file ---------------------------------------------------------

def test_roots_are_the_static_data_and_share_folders(tmp_path):
    settings = Settings(_env_file=None, data_dir=tmp_path / "data", share_root=tmp_path / "share")
    found = sendfile.roots(settings)
    assert set(found) == {"static", "data", "share"}            # the media cache is inside the data folder
    assert found["static"] == sendfile.STATIC_DIR
    outside = Settings(_env_file=None, data_dir=tmp_path / "data", media_cache_dir=tmp_path / "cache")
    assert sendfile.roots(outside)["media"] == Path(tmp_path / "cache").resolve()


def test_a_file_maps_to_its_deepest_folder_and_is_quoted(tmp_path):
    known = {"data": tmp_path, "media": tmp_path / "media-cache"}
    (tmp_path / "media-cache" / "art").mkdir(parents=True)
    assert sendfile.accel_path(tmp_path / "blobs" / "ab" / "c d#?.bin", "plain", known) \
        == "/_accel/plain/data/blobs/ab/c%20d%23%3F.bin"
    assert sendfile.accel_path(tmp_path / "media-cache" / "art" / "1.jpg", "sandbox", known) \
        == "/_accel/sandbox/media/art/1.jpg"
    assert sendfile.accel_path(tmp_path.parent / "elsewhere.txt", "plain", known) is None
    assert sendfile.accel_path(tmp_path, "plain", known) is None                 # a folder itself, never


# --- through the real routes ------------------------------------------------------------------------------------

@pytest.fixture
def accel_client(tmp_path, conn, settings):
    on = settings.model_copy(update={"accel": True})
    users.create_user(conn, "alice", "password123")
    with TestClient(create_app(on)) as client:
        client.post("/login", data={"username": "alice", "password": "password123"})
        yield client


def _upload(client, data: bytes, name: str) -> int:
    return client.post("/api/files", files={"file": (name, data, "application/octet-stream")}).json()["id"]


def test_through_the_front_door_nginx_sends_the_bytes(accel_client):
    file_id = _upload(accel_client, b"%PDF-1.4 nothing much", "audit.bin")
    r = accel_client.get(f"/files/{file_id}/raw", headers=ACCEL)
    assert r.status_code == 200 and r.content == b""
    assert r.headers["x-accel-redirect"].startswith("/_accel/plain/data/")
    assert r.headers["content-disposition"].startswith("attachment")
    assert r.headers["content-type"] == "application/octet-stream"


def test_an_uploaded_picture_shown_inline_goes_out_sandboxed(accel_client):
    file_id = _upload(accel_client, b"\x89PNG\r\n\x1a\n" + b"\x00" * 16, "logo.png")
    r = accel_client.get(f"/files/{file_id}/inline", headers=ACCEL)
    assert r.headers["x-accel-redirect"].startswith("/_accel/sandbox/data/")
    assert r.headers["content-type"] == "image/png"
    text = _upload(accel_client, b"<script>alert(1)</script> words", "readme.txt")   # the start of a text: Python's
    assert "x-accel-redirect" not in accel_client.get(f"/files/{text}/inline", headers=ACCEL).headers


def test_without_the_front_doors_header_the_app_sends_the_file(accel_client):
    file_id = _upload(accel_client, b"some bytes", "a.bin")
    r = accel_client.get(f"/files/{file_id}/raw")
    assert r.content == b"some bytes" and "x-accel-redirect" not in r.headers


def test_with_accel_off_the_header_changes_nothing(client, conn):
    users.create_user(conn, "alice", "password123")
    client.post("/login", data={"username": "alice", "password": "password123"})
    file_id = _upload(client, b"some bytes", "a.bin")
    r = client.get(f"/files/{file_id}/raw", headers=ACCEL)
    assert r.content == b"some bytes" and "x-accel-redirect" not in r.headers


def test_a_file_outside_the_known_folders_stays_with_the_app(tmp_path):
    outside = tmp_path / "elsewhere.txt"
    outside.write_text("hi")
    token = sendfile._ROOTS.set({"data": tmp_path / "data"})
    try:
        response = sendfile.send_file(outside)
        other_policy = sendfile.send_file(outside, headers={"Content-Security-Policy": "default-src 'self'"})
    finally:
        sendfile._ROOTS.reset(token)
    assert "x-accel-redirect" not in response.headers
    assert "x-accel-redirect" not in other_policy.headers            # a policy nginx has no location for


# --- the nginx side ---------------------------------------------------------------------------------------------

def test_the_plain_policy_is_the_apps_own_default():
    app = security.SecurityHeaders(Starlette(routes=[Route("/", lambda request: PlainTextResponse("x"))]),
                                   settings=Settings(_env_file=None))
    headers = TestClient(app).get("/").headers
    assert headers["content-security-policy"] == nginx.CSP["plain"]
    assert headers["permissions-policy"] == nginx.PERMISSIONS
    assert nginx.CSP["sandbox"] == sendfile.SANDBOX_CSP


def test_the_accel_config_has_internal_locations_for_every_folder_and_kind(tmp_path):
    roots = {"static": tmp_path / "static", "data": tmp_path / "data", "share": tmp_path / "my share"}
    conf = nginx.render("192.168.1.5:38200", accel=roots)
    for key, root in roots.items():
        for kind in sendfile.KINDS:
            block = conf[conf.index(f"location ^~ /_accel/{kind}/{key}/ {{"):]
            block = block[:block.index("}")]
            assert "internal;" in block and f'alias "{root.as_posix()}/";' in block
            assert f'add_header Content-Security-Policy "{nginx.CSP[kind]}" always;' in block
            assert "add_header X-Content-Type-Options nosniff always;" in block
    assert f'location ^~ /static/ {{\n        alias "{(tmp_path / "static").as_posix()}/";' in conf
    probe = conf[conf.index("location = /_accel-probe/share {"):]
    assert "allow 192.168.1.5;" in probe and "deny all;" in probe and nginx.PROBE in probe
    assert "/_accel-probe/static" not in conf
    assert conf.count("proxy_set_header X-Mnemerynne-Accel 1;") == 2             # the server and /remote/


def test_without_accel_nobody_can_claim_the_header():
    conf = nginx.render("192.168.1.5:38200")
    assert conf.count('proxy_set_header X-Mnemerynne-Accel "";') == 2 and "/_accel/" not in conf
    remote = conf[conf.index("location /remote/"):]
    assert "proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;" in remote[:remote.index("}")]


def test_a_path_nginx_could_not_read_back_is_refused(tmp_path):
    with pytest.raises(ValueError):
        nginx.render("192.168.1.5:38200", accel={"data": Path('/srv/a"b')})


def test_the_roots_come_from_the_services_env_file(tmp_path, capsys):
    env = tmp_path / "mnemerynne.env"
    env.write_text(f"MNEMERYNNE_DATA_DIR={tmp_path / 'data'}\nMNEMERYNNE_SHARE_ROOT={tmp_path / 'share'}\nOTHER=1\n")
    nginx.main(["--roots", "--env-file", str(env)])
    lines = dict(line.split(" ", 1) for line in capsys.readouterr().out.splitlines())
    assert Path(lines["data"]) == (tmp_path / "data").resolve() and Path(lines["share"]) == (tmp_path / "share").resolve()


# --- front-door.sh accel on|off ---------------------------------------------------------------------------------

BOX = r'''
LOG=$1; ENV=$2; SAVED=$3; CONF=$4; NGINX_CONF=$5; DATA=$6; STATIC=$7; WAIT=2
sleep() { :; }
journalctl() { :; }
usermod() { echo "usermod $*" >> "$LOG"; }
runuser() { shift 3; "$@"; }                       # -u mnemerynne -- command...
systemctl() {
    echo "systemctl $*" >> "$LOG"
    case "$1" in list-units) echo "mnemerynne-web.service loaded active running x" ;; esac
}
nginx() { echo "nginx $*" >> "$LOG"; }
app_py() {
    case "$*" in
        *--roots*) printf 'static %s\ndata %s\n' "$STATIC" "$DATA" ;;
        *--accel*) echo "accel config" ;;
        *) echo "plain config" ;;
    esac
}
curl() {
    case "$*" in
        *healthz*) echo '{"status":"ok","role":"web"}' ;;
        *_accel-probe/data*) [ -n "${NGINX_CANT_READ:-}" ] || cat "$DATA/.mnemerynne-accel-probe" ;;
        *icon-192.png*) echo 200 ;;
    esac
}
'''


def _box(tmp_path: Path) -> dict[str, Path]:
    paths = {name: tmp_path / name for name in ("log", "env", "saved", "front.conf", "nginx.conf", "data", "static")}
    paths["env"].write_text("MNEMERYNNE_HOST=127.0.0.1\nMNEMERYNNE_PORT=38210\n")
    paths["saved"].write_text("MNEMERYNNE_HOST=192.168.1.5\nMNEMERYNNE_PORT=38200\n")
    paths["nginx.conf"].write_text("user nginx;\nworker_processes auto;\n")
    paths["data"].mkdir()
    paths["static"].mkdir()
    return paths


def _accel(paths: dict[str, Path], body: str) -> subprocess.CompletedProcess:
    bash = find_bash()
    if not bash:
        pytest.skip("needs bash")
    script = 'source <(sed \'$d\' "$1"); shift\n' + BOX + body
    args = [paths[k].as_posix() for k in ("log", "env", "saved", "front.conf", "nginx.conf", "data", "static")]
    return subprocess.run([bash, "-c", script, "_", str(DEPLOY / "front-door.sh"), *args],
                          capture_output=True, text=True)


def test_accel_on_probes_every_folder_before_the_app_hands_files_over(tmp_path):
    paths = _box(tmp_path)
    result = _accel(paths, "accel_on\n")
    assert result.returncode == 0, result.stderr + result.stdout
    assert "MNEMERYNNE_ACCEL=1" in paths["env"].read_text()
    assert "MNEMERYNNE_ACCEL" not in paths["saved"].read_text()          # "off" for the front door forgets it
    assert paths["front.conf"].read_text() == "accel config\n"
    assert paths["nginx.conf"].read_text().startswith("user mnemerynne;")
    assert (tmp_path / "nginx.conf.before-accel").read_text().startswith("user nginx;")
    assert not (paths["data"] / ".mnemerynne-accel-probe").exists()       # the probe is cleaned up
    asked = paths["log"].read_text().splitlines()
    assert asked.index("systemctl restart nginx") < asked.index("systemctl restart mnemerynne-web")


def test_accel_on_that_nginx_cannot_read_puts_everything_back(tmp_path):
    paths = _box(tmp_path)
    result = _accel(paths, "NGINX_CANT_READ=1\naccel_on\n")
    assert result.returncode == 1
    assert "nginx couldn't read" in result.stderr
    assert "MNEMERYNNE_ACCEL" not in paths["env"].read_text()
    assert paths["front.conf"].read_text() == "plain config\n"
    assert paths["nginx.conf"].read_text() == "user nginx;\nworker_processes auto;\n"
    assert "systemctl restart mnemerynne-web" not in paths["log"].read_text()   # the app never noticed


def test_accel_off_hands_the_files_back_to_the_app(tmp_path):
    paths = _box(tmp_path)
    assert _accel(paths, "accel_on\n").returncode == 0
    result = _accel(paths, "accel_off\n")
    assert result.returncode == 0, result.stderr
    assert "MNEMERYNNE_ACCEL" not in paths["env"].read_text()
    assert paths["front.conf"].read_text() == "plain config\n"
    assert paths["nginx.conf"].read_text().startswith("user nginx;")


def test_selinux_lets_nginx_read_exactly_the_types_found(tmp_path):
    paths = _box(tmp_path)
    result = _accel(paths, r'''
getenforce() { echo Enforcing; }
find() { printf 'system_u:object_r:samba_share_t:s0\nsystem_u:object_r:var_lib_t:s0\nsystem_u:object_r:samba_share_t:s0\n'; }
checkmodule() { cp "${@: -1}" "$LOG.te"; touch "$4"; }
semodule_package() { :; }
semodule() { echo "semodule $*" >> "$LOG"; }
selinux_allow /srv/share /var/lib/mnemerynne
''')
    assert result.returncode == 0, result.stderr
    te = Path(str(paths["log"]) + ".te").read_text()
    assert "allow httpd_t samba_share_t:file { getattr open read map ioctl };" in te
    assert "allow httpd_t var_lib_t:dir { getattr open read search };" in te
    assert te.count("type samba_share_t;") == 1
    assert "semodule -i" in paths["log"].read_text()


# --- rate limits shared through Valkey ---------------------------------------------------------------------------

def test_with_valkey_a_limit_holds_across_processes(monkeypatch):
    import fakeredis
    from mnemerynne import events
    server = fakeredis.FakeServer()

    def bus():                                     # each "process" has its own client to the one Valkey
        return events.ValkeyBus("fake", clients=(fakeredis.FakeRedis(server=server, decode_responses=True),
                                                 fakeredis.aioredis.FakeRedis(server=server, decode_responses=True)))
    one, two = security.RateLimiter(), security.RateLimiter()
    monkeypatch.setattr(events, "_bus", bus())
    assert [one.allow("/login|1.2.3.4", 3, 60) for _ in range(2)] == [True, True]
    monkeypatch.setattr(events, "_bus", bus())
    assert two.allow("/login|1.2.3.4", 3, 60) is True
    assert two.allow("/login|1.2.3.4", 3, 60) is False          # the fourth, counted in another process
    assert one.allow("/login|5.6.7.8", 3, 60) is True             # someone else


def test_a_limit_keeps_counting_when_valkey_stops_answering(monkeypatch):
    from mnemerynne import events

    class Gone(events.ValkeyBus):
        def __init__(self):
            pass

        def count(self, key, window):
            raise ConnectionError("no valkey")
    monkeypatch.setattr(events, "_bus", Gone())
    limiter = security.RateLimiter()
    assert [limiter.allow("k", 2, 60) for _ in range(3)] == [True, True, False]
