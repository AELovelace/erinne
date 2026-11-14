"""The box's front door and the services' units (docs/plans/services-rewrite.md, Phase 0.5).

The nginx config written from the registry (mnemerynne/services/nginx.py), and the deploy scripts
deploy/linux/front-door.sh and deploy/linux/valkey-setup.sh driven with systemctl, curl and the app's Python
stood in for (each script is sourced without its last line, `main "$@"`, as tests/test_updates.py does).
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

from fakes import find_bash
from mnemerynne.services import nginx, registry

DEPLOY = Path(__file__).resolve().parents[1] / "deploy" / "linux"


# --- the nginx config from the registry ----------------------------------------------------------------------

def _location(conf: str, path: str) -> str:
    """The upstream a `location <path>` block proxies to ('' when there's no such block)."""
    found = re.search(r"location " + re.escape(path) + r" \{\s*proxy_pass http://(\w+);", conf)
    return found.group(1) if found else ""


def test_each_service_path_goes_to_its_own_upstream_on_its_port(monkeypatch):
    monkeypatch.delenv("MNEMERYNNE_PROFILE", raising=False)
    conf = nginx.render("192.168.1.5:38200")
    assert "listen 192.168.1.5:38200;" in conf
    for service in registry.SERVICES.values():
        upstream = "mnemerynne_" + service.name.replace("-", "_")
        assert re.search(rf"upstream {upstream} \{{ server 127\.0\.0\.1:{service.port}; keepalive \d+; \}}", conf)
        for path in service.paths:
            assert _location(conf, path) == upstream, path


def test_the_web_app_gets_websockets_and_everything_else():
    conf = nginx.render("192.168.1.5:38200", web="127.0.0.1:39999")
    assert "upstream mnemerynne_web { server 127.0.0.1:39999; keepalive 32; }" in conf
    assert _location(conf, "/") == "mnemerynne_web"
    assert _location(conf, "/remote/") == "mnemerynne_web"
    remote = conf[conf.index("location /remote/"):]
    remote = remote[:remote.index("}")]
    assert "proxy_set_header Upgrade $http_upgrade;" in remote and "$front_connection_upgrade" in remote
    assert conf.index("location /remote/") < conf.index("location / {")      # the catch-all comes last
    assert conf.count("location / {") == 1


def test_keepalive_follows_how_many_processes_a_service_runs(monkeypatch):
    def keepalive(conf: str, name: str) -> int:
        return int(re.search(rf"upstream mnemerynne_{name} .*?keepalive (\d+);", conf).group(1))

    monkeypatch.setenv("MNEMERYNNE_PROFILE", "nas")
    nas = nginx.render("10.0.0.1:38200")
    monkeypatch.setenv("MNEMERYNNE_PROFILE", "ryzen")
    ryzen = nginx.render("10.0.0.1:38200")
    assert keepalive(nas, "sync") == 16 * 3 and keepalive(ryzen, "sync") == 16 * 6
    assert keepalive(nas, "games") == keepalive(ryzen, "games") == 16                 # single: one process


def test_a_service_added_to_the_registry_gets_its_route(monkeypatch):
    rooms = registry.Service(name="live-rooms", serves="rooms", routers=(), paths=("/api/rooms/",), port=38203)
    monkeypatch.setitem(registry.SERVICES, rooms.name, rooms)
    conf = nginx.render("192.168.1.5:38200")
    assert "upstream mnemerynne_live_rooms { server 127.0.0.1:38203;" in conf
    assert _location(conf, "/api/rooms/") == "mnemerynne_live_rooms"


def test_the_command_prints_the_config(capsys):
    nginx.main(["--listen", "192.168.1.5:38200", "--web", "127.0.0.1:38210"])
    assert capsys.readouterr().out == nginx.render("192.168.1.5:38200", "127.0.0.1:38210")


# --- front-door.sh and valkey-setup.sh, with the box stood in for ---------------------------------------------

def _run(script_name: str, body: str, *args: str) -> subprocess.CompletedProcess:
    bash = find_bash()
    if not bash:
        pytest.skip("needs bash")
    script = 'source <(sed \'$d\' "$1"); shift\nsleep() { :; }\n' + body
    return subprocess.run([bash, "-c", script, "_", str(DEPLOY / script_name), *args],
                          capture_output=True, text=True)


def _posix(path: Path) -> str:
    return path.as_posix()


SYSTEMCTL_LOG = '''
systemctl() {                       # records what was asked; $ENABLED lists the units enabled at the start
    echo "systemctl $*" >> "$LOG"
    case "$1" in
        is-enabled) [[ " $ENABLED " == *" ${@: -1} "* ]] ;;
        is-active) [[ " $ACTIVE " == *" ${@: -1} "* ]] ;;
        enable) ENABLED="$ENABLED ${@: -1}"; ACTIVE="$ACTIVE ${@: -1}" ;;
        disable) ENABLED="${ENABLED//${@: -1}/}"; ACTIVE="${ACTIVE//${@: -1}/}" ;;
        *) return 0 ;;
    esac
}
journalctl() { :; }
'''


def test_running_lists_template_instances_too():
    result = _run("front-door.sh", '''
systemctl() { printf 'mnemerynne-web.service loaded active running x\\nmnemerynne@sync.service loaded active running x\\n'; }
running
''')
    assert result.returncode == 0, result.stderr
    assert result.stdout.split() == ["mnemerynne-web", "mnemerynne@sync"]


def test_a_service_that_does_not_answer_gets_its_old_unit_back(tmp_path):
    log = tmp_path / "log"
    result = _run("front-door.sh", SYSTEMCTL_LOG + '''
LOG=$1; ENV=$2; WAIT=2; ENABLED="mnemerynne-sync"; ACTIVE="mnemerynne-sync"
curl() { :; }                      # nobody answers
move_service sync 38201
''', _posix(log), _posix(_env(tmp_path)))
    assert result.returncode == 1
    asked = [line for line in log.read_text().splitlines() if line.split()[1] in ("enable", "disable")]
    assert asked == ["systemctl disable --now mnemerynne-sync", "systemctl enable --now mnemerynne@sync",
                     "systemctl disable --now mnemerynne@sync", "systemctl enable --now mnemerynne-sync"]
    assert "mnemerynne-sync is back on" in result.stderr


def test_a_different_process_answering_on_the_port_is_not_the_service(tmp_path):
    log = tmp_path / "log"
    result = _run("front-door.sh", SYSTEMCTL_LOG + '''
LOG=$1; ENV=$2; WAIT=2; ENABLED=""; ACTIVE=""
curl() { echo '{"status":"ok","role":"games"}'; }
move_service sync 38201
''', _posix(log), _posix(_env(tmp_path)))
    assert result.returncode == 1                               # and it had no old unit, so none is turned on
    assert "systemctl enable --now mnemerynne-sync" not in log.read_text()


def _env(tmp_path: Path, **settings: str) -> Path:
    env = tmp_path / "mnemerynne.env"
    values = {"MNEMERYNNE_HOST": "127.0.0.1", "MNEMERYNNE_PORT": "38210", **settings}
    env.write_text("".join(f"{k}={v}\n" for k, v in values.items()))
    return env


def test_services_moves_each_service_and_rewrites_nginx_for_the_saved_address(tmp_path):
    """The template and the web app's drop-ins are installed, each registry service moves to mnemerynne@<name>,
    and nginx listens where the reverse proxy sends: the address in the .env saved before the front door."""
    units = tmp_path / "units"
    (units / "mnemerynne-web.service.d").mkdir(parents=True)
    (units / "mnemerynne-web.service.d" / "home-nas.conf").write_text("[Service]\nReadWritePaths=/srv/share\n")
    template = tmp_path / "mnemerynne@.service"
    template.write_text("[Unit]\n")
    env = _env(tmp_path)
    (tmp_path / "saved").mkdir()
    _env(tmp_path / "saved", MNEMERYNNE_HOST="192.168.1.5", MNEMERYNNE_PORT="38200")
    saved = tmp_path / "saved" / "mnemerynne.env"
    conf = tmp_path / "front.conf"
    log = tmp_path / "log"
    result = _run("front-door.sh", SYSTEMCTL_LOG + '''
LOG=$1; ENV=$2; SAVED=$3; UNIT_DIR=$4; TEMPLATE=$5; CONF=$6; WAIT=2
ENABLED="mnemerynne-sync mnemerynne-games"; ACTIVE="$ENABLED"
app_py() {                          # the registry, and the generator echoing where it was told to listen
    if [ "$1" = -c ]; then printf 'sync 38201\\ngames 38202\\n'; else echo "listen $4 web $6"; fi
}
curl() { case "$*" in *38201*) echo '{"status":"ok","role":"sync"}' ;; *38202*) echo '{"status":"ok","role":"games"}' ;; esac; }
nginx() { echo "nginx $*" >> "$LOG"; }
services
''', *(_posix(p) for p in (log, env, saved, units, template, conf)))
    assert result.returncode == 0, result.stderr + result.stdout
    assert (units / "mnemerynne@.service").read_text() == "[Unit]\n"
    assert (units / "mnemerynne@.service.d" / "home-nas.conf").read_text().endswith("ReadWritePaths=/srv/share\n")
    asked = log.read_text().splitlines()
    for name in ("sync", "games"):
        assert f"systemctl disable --now mnemerynne-{name}" in asked
        assert f"systemctl enable --now mnemerynne@{name}" in asked
    assert asked.index("systemctl daemon-reload") < asked.index("systemctl enable --now mnemerynne@sync")
    assert conf.read_text() == "listen 192.168.1.5:38200 web 127.0.0.1:38210\n"     # not the live 127.0.0.1:38210
    assert "nginx -t" in asked and "systemctl reload nginx" in asked


def test_services_keeps_a_drop_in_already_there(tmp_path):
    units = tmp_path / "units"
    for d in ("mnemerynne-web.service.d", "mnemerynne@.service.d"):
        (units / d).mkdir(parents=True)
    (units / "mnemerynne-web.service.d" / "memory.conf").write_text("web's\n")
    (units / "mnemerynne@.service.d" / "memory.conf").write_text("the services' own\n")
    template = tmp_path / "mnemerynne@.service"
    template.write_text("[Unit]\n")
    result = _run("front-door.sh", SYSTEMCTL_LOG + '''
LOG=$1; ENV=$2; UNIT_DIR=$3; TEMPLATE=$4; SAVED=$1.none; ENABLED=""; ACTIVE=""
app_py() { :; }                     # no services in the registry
services
''', _posix(tmp_path / "log"), _posix(_env(tmp_path)), _posix(units), _posix(template))
    assert result.returncode == 0, result.stderr
    assert (units / "mnemerynne@.service.d" / "memory.conf").read_text() == "the services' own\n"
    assert "Kept the existing" in result.stdout


def test_a_bad_nginx_config_puts_the_old_one_back(tmp_path):
    conf = tmp_path / "front.conf"
    conf.write_text("the one that worked\n")
    result = _run("front-door.sh", '''
CONF=$1
app_py() { echo "a new one"; }
nginx() { return 1; }               # nginx -t refuses it
write_conf 192.168.1.5:38200
''', _posix(conf))
    assert result.returncode == 1
    assert conf.read_text() == "the one that worked\n"


VALKEY_BOX = '''
valkey-server() { :; }              # installed
getent() { :; }
usermod() { echo "usermod $*" >> "$LOG"; }
systemd-tmpfiles() { :; }
has_socket() { :; }
systemctl() {
    echo "systemctl $*" >> "$LOG"
    case "$1" in
        show) echo valkey ;;        # the unit makes /run/valkey itself
        list-units) printf 'mnemerynne-web.service loaded active running x\\nmnemerynne@sync.service loaded active running x\\n' ;;
    esac
}
'''


def _valkey_box(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    conf = tmp_path / "valkey.conf"
    conf.write_text("bind 127.0.0.1\nport 6379\n# unixsocket /run/valkey/valkey.sock\n")
    env = _env(tmp_path)
    saved = tmp_path / "saved.env"
    saved.write_text("MNEMERYNNE_HOST=192.168.1.5\n")
    return conf, env, saved, tmp_path / "log"


def test_valkey_on_listens_on_a_socket_only_and_tells_every_process(tmp_path):
    conf, env, saved, log = _valkey_box(tmp_path)
    result = _run("valkey-setup.sh", VALKEY_BOX + '''
CONF_CANDIDATES=("$1"); ENV=$2; SAVED=$3; LOG=$4
ping_as_app() { echo True; }
valkey_on
''', *(_posix(p) for p in (conf, env, saved, log)))
    assert result.returncode == 0, result.stderr + result.stdout
    text = conf.read_text()
    assert "\nport 0\n" in text and "port 6379" not in text
    assert "unixsocket /run/valkey/valkey.sock\n" in text and "unixsocketperm 770\n" in text
    assert (tmp_path / "valkey.conf.before-mnemerynne").read_text().startswith("bind 127.0.0.1\nport 6379\n")
    assert "MNEMERYNNE_VALKEY_URL=unix:///run/valkey/valkey.sock" in env.read_text()
    assert "MNEMERYNNE_VALKEY_URL=unix:///run/valkey/valkey.sock" in saved.read_text()   # front-door off keeps it
    asked = log.read_text().splitlines()
    assert "usermod -aG valkey mnemerynne" in asked
    assert "systemctl restart mnemerynne-web.service mnemerynne@sync.service" in asked


def test_valkey_the_app_cannot_reach_leaves_the_app_as_it_was(tmp_path):
    conf, env, saved, log = _valkey_box(tmp_path)
    before = env.read_text()
    result = _run("valkey-setup.sh", VALKEY_BOX + '''
CONF_CANDIDATES=("$1"); ENV=$2; SAVED=$3; LOG=$4
ping_as_app() { echo "PermissionError: [Errno 13]"; return 1; }
valkey_on
''', *(_posix(p) for p in (conf, env, saved, log)))
    assert result.returncode == 1
    assert "can't reach" in result.stderr
    assert env.read_text() == before
    assert "systemctl restart mnemerynne" not in log.read_text()


def test_valkey_off_takes_the_url_out_of_both_env_files(tmp_path):
    _, env, saved, log = _valkey_box(tmp_path)
    for f in (env, saved):
        f.write_text(f.read_text() + "MNEMERYNNE_VALKEY_URL=unix:///run/valkey/valkey.sock\n")
    result = _run("valkey-setup.sh", VALKEY_BOX + '''
ENV=$1; SAVED=$2; LOG=$3
valkey_off
''', *(_posix(p) for p in (env, saved, log)))
    assert result.returncode == 0, result.stderr
    assert "VALKEY" not in env.read_text() and "VALKEY" not in saved.read_text()
    assert "MNEMERYNNE_HOST=127.0.0.1" in env.read_text()


def test_valkey_without_a_config_file_changes_nothing(tmp_path):
    _, env, saved, log = _valkey_box(tmp_path)
    result = _run("valkey-setup.sh", VALKEY_BOX + '''
CONF_CANDIDATES=("$1/nope.conf"); ENV=$2; LOG=$3
valkey_on
''', _posix(tmp_path), _posix(env), _posix(log))
    assert result.returncode == 1
    assert "No Valkey config" in result.stderr
    assert "VALKEY" not in env.read_text()
