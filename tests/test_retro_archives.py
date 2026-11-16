"""Archives holding several ROMs (an original and its romhacks): the player asks "Which ROM?" (web/retro.py lists
them, from a .zip's directory or a .7z's header, media/sevenzip.py), and each ROM inside keeps saves of its own
(retro.member_key), so a hack's save never lands on the original's. A disc's zip (PS1) is never split."""

import io
import os
import shutil
import subprocess
import zipfile

import pytest

from mnemerynne import share
from mnemerynne.media import retro, sevenzip
from test_retro import _by_title, _login, _player, _put, _turn_on, client, conn, checker, directory, root, settings  # noqa: F401

SEVEN_ZIP = shutil.which("7z") or shutil.which("7za") or (r"C:\Program Files\7-Zip\7z.exe"
                                                           if os.path.exists(r"C:\Program Files\7-Zip\7z.exe") else None)


def _zip(*members) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, body in members:
            z.writestr(name, body)
    return buf.getvalue()


@pytest.mark.skipif(SEVEN_ZIP is None, reason="7-Zip isn't installed here")
def test_a_7z_s_file_names_are_read_from_its_header(tmp_path):
    """Every kind 7-Zip makes: its default (an LZMA-compressed header, LZMA2 data), a plain header, LZMA, stored,
    not solid; folders kept in the names."""
    src = tmp_path / "src"
    (src / "hacks").mkdir(parents=True)
    (src / "Metroid Fusion (USA).gba").write_bytes(b"A" * 100)
    (src / "hacks" / "Metroid Fusion (Hack).gba").write_bytes(b"B" * 200)
    (src / "readme.txt").write_text("have fun")
    for name, options in (("default", []), ("plain-header", ["-mhc=off"]), ("lzma", ["-m0=LZMA"]), ("stored", ["-mx0"]),
                          ("not-solid", ["-ms=off"])):
        subprocess.run([SEVEN_ZIP, "a", "-bd", "-y", *options, str(tmp_path / f"{name}.7z"), "*"], cwd=src,
                       check=True, capture_output=True)
        found = sevenzip.names(tmp_path / f"{name}.7z")
        assert sorted(found) == ["Metroid Fusion (USA).gba", "hacks", "hacks/Metroid Fusion (Hack).gba", "readme.txt"], name
    gba = retro.CONSOLES["gba"]
    assert retro.archive_roms(tmp_path / "default.7z", gba) == ["hacks/Metroid Fusion (Hack).gba", "Metroid Fusion (USA).gba"]
    (tmp_path / "broken.7z").write_bytes(b"7z\xbc\xaf\x27\x1c" + b"\0" * 40)
    assert sevenzip.names(tmp_path / "broken.7z") is None and retro.archive_roms(tmp_path / "broken.7z", gba) == []
    assert sevenzip.names(tmp_path / "src" / "readme.txt") is None


def test_which_rom_and_each_rom_s_own_saves(client, conn, directory, root, settings):
    _login(client, conn, directory)
    _turn_on(client)
    gba = root / "Games" / "GBA"
    _put(gba / "Metroid Pack.zip", _zip(("readme.txt", b"hi"), ("hacks/Metroid Fusion (Hack).gba", b"HACK"),
                                        ("Metroid Fusion (USA).gba", b"ORIGINAL")))
    _put(gba / "Solo.zip", _zip(("Solo (USA).gba", b"ONE"), ("manual.pdf", b"%PDF")))
    _put(root / "Games" / "PS1" / "Disc.zip", _zip(("Disc.cue", b'FILE "Disc.bin" BINARY\n'), ("Disc.bin", b"X")))
    share.sync(conn, settings)
    games = _by_title(client)
    pack = _player(client, games["Metroid Pack"]["id"])
    assert [m["name"] for m in pack["members"]] == ["hacks/Metroid Fusion (Hack).gba", "Metroid Fusion (USA).gba"]
    assert [m["title"] for m in pack["members"]] == ["Metroid Fusion", "Metroid Fusion"] and "gba" in pack["rom_exts"]
    assert "members" not in _player(client, games["Solo"]["id"])        # one ROM: nothing to choose
    assert "members" not in _player(client, games["Disc"]["id"])        # a disc's files: one game, never split

    gid = games["Metroid Pack"]["id"]
    hack, original = "hacks/Metroid Fusion (Hack).gba", "Metroid Fusion (USA).gba"
    url = f"/api/media/retro/saves/{gid}"
    assert client.put(f"{url}/sram", params={"member": hack}, content=b"hack save").status_code == 204
    assert client.put(f"{url}/sram", params={"member": original}, content=b"original save").status_code == 204
    assert client.get(f"{url}/sram", params={"member": hack}).content == b"hack save"           # each its own
    assert client.get(f"{url}/sram", params={"member": original}).content == b"original save"
    assert client.get(f"{url}/sram").status_code == 404                                        # nor the archive's
    assert client.put(f"{url}/sram", params={"member": "nope.gba"}, content=b"x").status_code == 404
    assert [s["slot"] for s in client.get(url, params={"member": hack}).json()["saves"]] == ["sram"]
    again = _player(client, gid)                                       # the page shows each ROM's saves
    assert {m["name"]: [s["slot"] for s in m["saves"]] for m in again["members"]} == {hack: ["sram"], original: ["sram"]}
    assert client.delete(f"{url}/sram", params={"member": hack}).status_code == 204
    assert client.get(f"{url}/sram", params={"member": original}).content == b"original save"   # the other kept
