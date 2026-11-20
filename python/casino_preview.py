"""Isolated loopback preview for tests/connected-browser.mjs; never reads the project's .env or live data."""

import os
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "tests")]

import uvicorn
from fakes import FakeDirectory, FakeEmbedder
from mnemerynne import accounts, coins, db, media, users
from mnemerynne.config import Settings
from mnemerynne.web.app import Services, create_app


def main():
    """Seed disposable users and Media access, then serve only on the loopback interface."""
    with TemporaryDirectory(prefix="casino-preview-") as directory:
        settings = Settings(_env_file=None, data_dir=Path(directory), embed_dim=4,
                            secret_key="casino-local-preview-only-secret", ad_domain="PREVIEW")
        people = FakeDirectory({"alice": {"Domain Users"}, "bob": {"Domain Users"}})
        conn = db.connect(settings.db_path)
        db.migrate(conn, settings.embed_dim)
        uids = []
        for name in ("alice", "bob"):
            uid = users.create_user(conn, name, "preview-password")
            accounts.link(conn, uid, name, people)
            uids.append(uid)
        media.set_enabled(conn, True)
        media.save_settings(conn, settings, {"media_group": "Domain Users", "screens_group": "Domain Users", "radio_directory_url": ""})
        conn.close()
        app = create_app(settings, Services(llm=None, embedder=FakeEmbedder(), directory=people))
        if os.environ.get("PREVIEW_COINS"):                               # Lumi City's check (LUMI_CITY_ONLY): plots cost more than a new purse holds,
            db.migrate_all(settings.db_path, settings.embed_dim)          # so this disposable preview gives each player plenty to buy, sell and build with
            games = db.connect_feature(settings.db_path, "games")        # (the app migrates at start too; done here so the games file exists first)
            with db.transaction(games):
                for uid in uids:
                    coins.reward(games, uid, int(os.environ["PREVIEW_COINS"]), "preview", int(os.environ["PREVIEW_COINS"]))
            games.close()
        uvicorn.run(app, host="127.0.0.1", port=int(sys.argv[1]), log_level="warning")


if __name__ == "__main__":
    main()
