"""The media service (docs/plans/services-rewrite.md, Phase 4b): all of Media in a process of its own, whose TV hub,
pairing codes and transcoding sessions are that process's memory, with its pages drawn like the web app's."""

from __future__ import annotations

from fastapi.testclient import TestClient

from fakes import FakeEmbedder, FakeShareChecker
from mnemerynne.media import transcode
from mnemerynne.services import nginx, registry
from mnemerynne.web.app import Services, create_role_app
from test_media import _login, _screens_for, _turn_on, checker, client, conn, directory, root, settings  # noqa: F401


def _media(settings, directory, root):
    return TestClient(create_role_app("media", settings, Services(
        llm=None, embedder=FakeEmbedder(), directory=directory, share_checker=FakeShareChecker(root))))


def test_the_media_process_serves_media_pages_and_api_and_nothing_else(client, conn, settings, directory, root):
    _login(client, conn, directory)
    _turn_on(client)
    cookies = dict(client.cookies)
    with _media(settings, directory, root) as service:
        assert service.get("/healthz").json() == {"status": "ok", "role": "media"}
        service.cookies.update(cookies)                                   # signed in on the web app: here too
        page = service.get("/media")
        assert page.status_code == 200 and "<html" in page.text.lower()    # its pages, with the web app's top bar
        assert service.get("/api/admin/settings/media").json()["enabled"] is True   # Media's admin, here too
        assert service.get("/files").status_code == 404                  # Files stays with the web app
        assert service.get("/api/media/games").status_code == 404         # and the games with theirs
        assert isinstance(service.app.state.media_sessions, transcode.Manager)
        assert service.app.state.media_sessions.root.name == "media"      # its own sessions folder


def test_a_tv_pairs_and_takes_a_command_all_in_the_media_process(client, conn, settings, directory, root):
    """Pairing, the screen's command stream and a command sent to it touch the hub: one process holds it."""
    _login(client, conn, directory)
    _turn_on(client)
    _screens_for(client)
    cookies = dict(client.cookies)
    with _media(settings, directory, root) as service:
        service.cookies.update(cookies)
        made = service.post("/api/media/screens", json={"name": "Living room"})
        assert made.status_code == 201, made.text
        screen_id = made.json()["id"]
        hub = service.app.state.media_screens                             # the service's own hub
        queue = hub.listen(screen_id)
        sent = service.post(f"/api/media/screens/{screen_id}/command", json={"action": "toggle"})
        assert sent.status_code in (200, 202), sent.text
        assert queue.qsize() == 1
        hub.unlisten(screen_id, queue)
    assert getattr(client.app.state, "media_screens", None) is None or not client.app.state.media_screens.online(screen_id)


def test_each_process_keeps_its_own_transcoding_sessions(settings):
    web, service = transcode.Manager(settings), transcode.Manager(settings, "media")
    assert web.root != service.root and web.root.parent == service.root.parent
    service.startup()
    (service.root / "live-session").mkdir()
    web.startup()                                                         # the web app restarting
    assert (service.root / "live-session").exists()                       # leaves the media service's alone


def test_nginx_keeps_the_games_pages_with_the_web_app_and_stopped_services_too():
    media = registry.SERVICES["media"]
    assert media.single and "/media/" in media.paths and "/media/games/" in media.web_paths
    conf = nginx.render("192.168.1.5:38200", running=["sync", "media"])
    def upstream(path):
        part = conf[conf.index(f"location {path} {{"):]
        return part[part.index("proxy_pass http://") + 18:part.index(";")]
    assert upstream("/media/") == "mnemerynne_media" and upstream("/api/media/") == "mnemerynne_media"
    assert upstream("/media/games/") == "mnemerynne_web"                  # Lumi's pages stay with the web app
    assert upstream("= /media/panel/manifest.webmanifest") == "mnemerynne_web"
    assert upstream("/api/media/games") == "mnemerynne_web"               # games off: /api/media/ mustn't take it
    assert upstream("/api/rooms") == "mnemerynne_web"


def test_services_off_keeps_a_service_with_the_web_app(tmp_path):
    """MNEMERYNNE_SERVICES_OFF=media: front-door.sh services leaves (or turns) it off: the A/B switch for a service."""
    import subprocess
    from pathlib import Path

    from fakes import find_bash
    bash = find_bash()
    if not bash:
        import pytest
        pytest.skip("needs bash")
    units = tmp_path / "units"
    (units / "mnemerynne-web.service.d").mkdir(parents=True)
    template = tmp_path / "mnemerynne@.service"
    template.write_text("[Unit]\n")
    env = tmp_path / "env"
    env.write_text("MNEMERYNNE_HOST=127.0.0.1\nMNEMERYNNE_SERVICES_OFF=media, rooms\n")
    log = tmp_path / "log"
    script = r'''
source <(sed '$d' "$1"); shift
LOG=$1; ENV=$2; UNIT_DIR=$3; TEMPLATE=$4; SAVED=$1.none; WAIT=2
sleep() { :; }
journalctl() { :; }
ENABLED="mnemerynne@media"
systemctl() {
    echo "systemctl $*" >> "$LOG"
    case "$1" in
        is-enabled|is-active) [[ " $ENABLED " == *" ${@: -1} "* ]] ;;
        enable) ENABLED="$ENABLED ${@: -1}" ;;
        disable) ENABLED="${ENABLED//${@: -1}/}" ;;
    esac
}
app_py() { printf 'sync 38201 0\nmedia 38207 0\n'; }
curl() { echo '{"status":"ok","role":"sync"}'; }
services
'''
    result = subprocess.run([bash, "-c", script, "_", str(Path(__file__).resolve().parents[1] / "deploy" / "linux" / "front-door.sh"),
                             log.as_posix(), env.as_posix(), units.as_posix(), template.as_posix()],
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr + result.stdout
    asked = log.read_text().splitlines()
    assert "systemctl enable --now mnemerynne@sync" in asked
    assert "systemctl disable --now mnemerynne@media" in asked and "systemctl enable --now mnemerynne@media" not in asked
    assert "off (MNEMERYNNE_SERVICES_OFF)" in result.stdout
