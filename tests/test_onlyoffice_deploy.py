"""deploy/linux/onlyoffice-setup.sh: which nginx layout a box has, and where the container calls the app (MN-24).

The script is sourced without its last line (`main "$@"`), as tests/test_updates.py does with update.sh.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from fakes import find_bash

SETUP = Path(__file__).resolve().parents[1] / "deploy" / "linux" / "onlyoffice-setup.sh"


def _run(body: str, *args: str) -> subprocess.CompletedProcess:
    bash = find_bash()
    if not bash:
        pytest.skip("needs bash")
    script = 'source <(sed \'$d\' "$1"); shift\n' + body
    return subprocess.run([bash, "-c", script, "_", str(SETUP), *args], capture_output=True, text=True, timeout=15)


@pytest.mark.parametrize("edge,front,saved,expected", [
    (True, True, True, "edge"),        # nginx holds HTTPS here (mnemerynne-server): the edge snippet
    (False, True, True, "front"),      # the front door behind another reverse proxy
    (False, True, False, "none"),      # a front door config left behind, but front-door.sh isn't on
    (False, False, False, "none"),     # the app on its own: no nginx to hang /onlyoffice/ on
])
def test_the_layout_is_read_from_the_nginx_files(tmp_path, edge, front, saved, expected):
    for name, there in (("edge.conf", edge), ("front.conf", front), ("saved.env", saved)):
        if there:
            (tmp_path / name).write_text("x\n")
    d = tmp_path.as_posix()
    result = _run(f'EDGE_CONF={d}/edge.conf; FRONT_CONF={d}/front.conf; SAVED={d}/saved.env\nlayout\n')
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == expected


@pytest.mark.parametrize("host,expected", [
    ("192.168.1.5", "192.168.1.5:38200"),
    ("0.0.0.0", "10.0.0.7:38200"),        # every address: the box's first one
    ("fd00::5", "[fd00::5]:38200"),
])
def test_the_container_calls_the_front_doors_own_address(tmp_path, host, expected):
    saved = tmp_path / "saved.env"
    saved.write_text(f"MNEMERYNNE_HOST={host}\nMNEMERYNNE_PORT=38200\n")
    result = _run(f'SAVED={saved.as_posix()}\nhostname() {{ echo "10.0.0.7 172.17.0.1"; }}\nfront_address\n')
    assert result.returncode == 0, result.stderr
    assert result.stdout == expected


def test_install_refuses_a_box_without_either_nginx_layout(tmp_path):
    (tmp_path / "mnemerynne.env").write_text("MNEMERYNNE_PUBLIC_URL=https://erynne.example\n")
    d = tmp_path.as_posix()
    result = _run(f'''
ENV_FILE={d}/mnemerynne.env; EDGE_CONF={d}/edge.conf; FRONT_CONF={d}/front.conf; SAVED={d}/saved.env
QUADLET_SRC={d}/mnemerynne.env; SNIPPET_SRC={d}/mnemerynne.env
dnf() {{ echo "dnf ran" >&2; return 1; }}
podman() {{ echo "podman ran" >&2; return 1; }}
oo_install
''')
    assert result.returncode == 1
    assert "neither" in result.stderr and "front-door.sh on" in result.stderr
    assert "ran" not in result.stderr                            # stopped before touching anything
