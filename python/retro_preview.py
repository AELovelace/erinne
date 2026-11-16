"""Isolated loopback preview for tests/retro-browser.mjs: Media → Retro with homebrew games on a disposable share.

    python python/retro_preview.py <port>

Never reads the project's .env or live data: a temporary folder holds the database and a share with
a homebrew game per console (python/make_retro_test_rom.py). Signs in as alice / preview-password (and bob and
carol, the same password: more players for netplay, tests/retro-netplay-browser.mjs).
"""

import os
import sys
import zipfile
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "tests"), str(ROOT / "python")]

import uvicorn
from fakes import FakeDirectory, FakeEmbedder, FakeShareChecker
from make_retro_test_rom import GAMES
from mnemerynne import accounts, db, media, share, users
from mnemerynne.config import Settings
from mnemerynne.media import retro
from mnemerynne.web.app import Services, create_app


def main():
    """The share and the game, Media and Retro on for alice, then serve only on the loopback interface."""
    with TemporaryDirectory(prefix="retro-preview-") as directory:
        root = Path(directory) / "share"
        root.mkdir()
        (root / share.MARKER).write_text("")
        settings = Settings(_env_file=None, data_dir=Path(directory) / "data", embed_dim=4, share_root=root,
                            share_settle_seconds=0, secret_key="retro-local-preview-only-secret", ad_domain="PREVIEW")
        people = FakeDirectory({"alice": {"Domain Users"}, "bob": {"Domain Users"}, "carol": {"Domain Users"}})
        checker = FakeShareChecker(root)
        share.configure(settings, people, checker)
        conn = db.connect(settings.db_path)
        db.migrate(conn, settings.embed_dim)
        for name in ("alice", "bob", "carol"):               # bob, carol: players 2 and 3 in the netplay test
            uid = users.create_user(conn, name, "preview-password")
            accounts.link(conn, uid, name, people)
        media.set_enabled(conn, True)
        media.save_settings(conn, settings, {"media_group": "Domain Users", "radio_directory_url": "",
                                             "retro_enabled": True})
        with db.transaction(conn):
            retro.ensure_library(conn)                       # Games, with a folder per console in it
        for key, (name, make, _) in GAMES.items():           # one homebrew game in each console's folder
            (root / "Games" / retro.CONSOLES[key].folder / name).write_bytes(make())
        if os.environ.get("RETRO_PREVIEW_PACK"):            # tests/retro-which-rom-browser.mjs: a zip of two ROMs
            hack = bytearray(GAMES["gba"][1]())
            hack[0xA0:0xA9] = b"LUMI HACK"                   # another title: another game, as a romhack is
            with zipfile.ZipFile(root / "Games" / "GBA" / "Lumi Pack.zip", "w") as pack:
                pack.writestr("Lumi Test.gba", GAMES["gba"][1]())
                pack.writestr("hacks/Lumi Test (Hack).gba", bytes(hack))
        share.sync(conn, settings)                           # the share scan: the game is a file like any other
        conn.close()
        app = create_app(settings, Services(llm=None, embedder=FakeEmbedder(), directory=people, share_checker=checker))
        uvicorn.run(app, host="127.0.0.1", port=int(sys.argv[1]), log_level="warning")


if __name__ == "__main__":
    main()
