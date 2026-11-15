"""Isolated loopback preview for tests/tv-controllers-browser.mjs: a TV box's 🔗 Controllers and Retro on the TV.

    python python/tv_controllers_preview.py <port>

Runs two things, both on 127.0.0.1 only:
  - the app (Media on, screens for Domain Users, Retro on with the homebrew GBA game), signed in as alice;
  - the box's own helper (deploy/screen/wifi-setup.py's Server and Controllers) on its real port, 9187, talking to
    that app for the controllers pass, with a pretend bluetoothctl (tests/test_screen_bluetooth.py FakeBluetoothctl):
    an Xbox controller paired, and a DualSense, a keyboard and a phone nearby.
Never reads the project's .env or live data.
"""

import importlib.util
import sys
import threading
import types
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
from test_screen_bluetooth import FakeBluetoothctl

_spec = importlib.util.spec_from_file_location("wifi_setup", ROOT / "deploy" / "screen" / "wifi-setup.py")
helper = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(helper)


def main():
    port = int(sys.argv[1])
    with TemporaryDirectory(prefix="tv-controllers-preview-") as directory:
        root = Path(directory) / "share"
        root.mkdir()
        (root / share.MARKER).write_text("")
        settings = Settings(_env_file=None, data_dir=Path(directory) / "data", embed_dim=4, share_root=root,
                            share_settle_seconds=0, secret_key="tv-controllers-local-preview-only", ad_domain="PREVIEW")
        people = FakeDirectory({"alice": {"Domain Users"}})
        checker = FakeShareChecker(root)
        share.configure(settings, people, checker)
        conn = db.connect(settings.db_path)
        db.migrate(conn, settings.embed_dim)
        uid = users.create_user(conn, "alice", "preview-password")
        accounts.link(conn, uid, "alice", people)
        media.set_enabled(conn, True)
        media.save_settings(conn, settings, {"media_group": "Domain Users", "screens_group": "Domain Users",
                                             "radio_directory_url": "", "retro_enabled": True})
        with db.transaction(conn):
            retro.ensure_library(conn)
        name, make, _ = GAMES["gba"]
        (root / "Games" / retro.CONSOLES["gba"].folder / name).write_bytes(make())
        share.sync(conn, settings)
        conn.close()

        # The box's helper, answering the app's origin, with the pretend Bluetooth.
        box = types.SimpleNamespace(server=f"http://127.0.0.1:{port}")
        controllers = helper.Controllers(box, helper.Bluetooth(run=FakeBluetoothctl()))
        local = helper.Server(("127.0.0.1", helper.LOCAL_PORT), box, local=True, controllers=controllers)
        threading.Thread(target=local.serve_forever, daemon=True).start()

        app = create_app(settings, Services(llm=None, embedder=FakeEmbedder(), directory=people, share_checker=checker))
        uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":
    main()
