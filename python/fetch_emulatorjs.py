"""Fetch the pinned EmulatorJS and its cores into mnemerynne/web/static/vendor/emulatorjs (Media → Retro).

    python python/fetch_emulatorjs.py            # fetch, check each package's checksum, copy what Retro uses
    python python/fetch_emulatorjs.py --check    # only say whether the folder matches the pins

EmulatorJS (GPL-3.0) is RetroArch compiled to WebAssembly. Each package comes from the npm registry, pinned to one
version and checked against the registry's sha512 before anything is copied, so the files in the repository are
exactly those packages' files. Only what the player loads is kept: the unminified scripts (web/static/retro-player.js
loads them in order), the stylesheet, the zip and 7z extractors, and per console its core (WebGL2 and legacy builds)
with the core's build report (which keys the browser's cache of the core).

To add a console: its package to CORES (version and `npm view @emulatorjs/core-<name>@<version> dist.integrity`),
run this, then its row in mnemerynne/media/retro.py CONSOLES.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import shutil
import sys
import tarfile
import urllib.request
from pathlib import Path

VERSION = "4.2.3"
FRONTEND = ("emulatorjs", "sha512-7z3qaA4LwyurhuGvdMUDF9xJpEbxC3SNy9+E9tSaOsRo8FCS2QXam/0k/lc9kqHWRFIlLKWahNjPAStyL0rFnw==")
# Core name -> the npm package's sha512 (dist.integrity), one per console in media/retro.py.
CORES = {
    "mgba": "sha512-daiHzZQKEr+P9fra7j5YoEAXiyYUEtBhFQ8EAV/SeCtrkvqtayU7GQ9LYgoSgzkKSwsbNSskApqGuA9EGARYPA==",
}
# From the frontend package: its path in the package -> its path under the vendor folder.
FRONTEND_FILES = {
    "LICENSE": "LICENSE",
    "data/version.json": "version.json",
    "data/emulator.css": "emulator.css",
    "data/compression/extract7z.js": "compression/extract7z.js",
    "data/compression/extractzip.js": "compression/extractzip.js",
    **{f"data/src/{name}": f"src/{name}" for name in (
        "emulator.js", "nipplejs.js", "shaders.js", "storage.js", "gamepad.js", "GameManager.js", "compression.js")},
}

REPO = Path(__file__).resolve().parent.parent
DEST = REPO / "mnemerynne" / "web" / "static" / "vendor" / "emulatorjs"
REGISTRY = "https://registry.npmjs.org/@emulatorjs/{name}/-/{name}-{version}.tgz"


def _core_files(core: str) -> dict[str, str]:
    """A core package's files Retro keeps: the WebGL2 and legacy (no WebGL2) builds, and the build report."""
    return {f"{core}-wasm.data": f"cores/{core}-wasm.data",
            f"{core}-legacy-wasm.data": f"cores/{core}-legacy-wasm.data",
            f"reports/{core}.json": f"cores/reports/{core}.json"}


def _download(name: str, integrity: str) -> tarfile.TarFile:
    """The package's tarball, after its bytes match the registry's sha512 (else the script stops)."""
    url = REGISTRY.format(name=name, version=VERSION)
    with urllib.request.urlopen(url, timeout=120) as response:   # noqa: S310 (a fixed https address)
        body = response.read()
    algo, _, expected = integrity.partition("-")
    got = base64.b64encode(hashlib.new(algo, body).digest()).decode()
    if got != expected:
        sys.exit(f"{name} {VERSION}: the download's {algo} doesn't match the pin; nothing was copied.")
    return tarfile.open(fileobj=io.BytesIO(body), mode="r:gz")


def _copy(archive: tarfile.TarFile, files: dict[str, str], into: Path) -> list[str]:
    """Each wanted member (under the tarball's package/) written to its place; returns what was written."""
    written = []
    for inside, outside in files.items():
        member = archive.extractfile(f"package/{inside}")
        if member is None:
            sys.exit(f"package/{inside} isn't in the package.")
        target = into / outside
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(member.read())
        written.append(outside)
    return written


def _manifest(folder: Path) -> dict[str, str]:
    """Every file under the folder (but the manifest itself) -> its sha256, to tell later whether it was changed."""
    return {p.relative_to(folder).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(folder.rglob("*")) if p.is_file() and p.name != "MANIFEST.json"}


def fetch() -> None:
    staging = DEST.with_name("emulatorjs.new")
    shutil.rmtree(staging, ignore_errors=True)
    written = _copy(_download(*FRONTEND), FRONTEND_FILES, staging)
    for core, integrity in CORES.items():
        written += _copy(_download(f"core-{core}", integrity), _core_files(core), staging)
    (staging / "MANIFEST.json").write_text(json.dumps(
        {"version": VERSION, "cores": sorted(CORES), "files": _manifest(staging)}, indent=1) + "\n", encoding="utf-8")
    readme = DEST / "README.md"            # written by hand: kept across fetches
    if readme.is_file():
        shutil.copy2(readme, staging / "README.md")
    shutil.rmtree(DEST, ignore_errors=True)
    staging.rename(DEST)
    print(f"EmulatorJS {VERSION} with {', '.join(sorted(CORES))}: {len(written)} files in {DEST.relative_to(REPO)}.")


def check() -> int:
    """0 when the folder holds the pinned version and cores, unchanged since they were fetched."""
    try:
        manifest = json.loads((DEST / "MANIFEST.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        print("No MANIFEST.json: run this script without --check.")
        return 1
    problems = []
    if manifest.get("version") != VERSION or manifest.get("cores") != sorted(CORES):
        problems.append(f"it has {manifest.get('version')} with {manifest.get('cores')}; the pins say {VERSION} "
                        f"with {sorted(CORES)}")
    on_disk = {k: v for k, v in _manifest(DEST).items() if k != "README.md"}
    if on_disk != manifest.get("files"):
        problems.append("its files differ from what was fetched")
    for problem in problems:
        print("vendor/emulatorjs: " + problem + ".")
    return 1 if problems else 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--check", action="store_true", help="only compare the folder with the pins")
    sys.exit(check() if parser.parse_args().check else fetch())
