"""Isolated loopback preview for tests/connected-browser.mjs; never reads the project's .env or live data."""

import sys
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "tests")]

import uvicorn
from fakes import FakeDirectory, FakeEmbedder
from mnemerynne import accounts, db, media, users
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
        for name in ("alice", "bob"):
            uid = users.create_user(conn, name, "preview-password")
            accounts.link(conn, uid, name, people)
        media.set_enabled(conn, True)
        media.save_settings(conn, settings, {"media_group": "Domain Users", "radio_directory_url": ""})
        conn.close()
        app = create_app(settings, Services(llm=None, embedder=FakeEmbedder(), directory=people))
        uvicorn.run(app, host="127.0.0.1", port=int(sys.argv[1]), log_level="warning")


if __name__ == "__main__":
    main()
