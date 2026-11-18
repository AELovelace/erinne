"""Isolated loopback preview of Automator's page, for checking its editor in a real browser.

    python python/automator_preview.py <port>

Never reads the project's .env or live data: a temporary folder holds the database. alice / preview-password may
use Automator; Rooms is on with a channel she's in (#lobby, Erynne on) and one with Erynne off (#quiet), and she has
two automations to edit: "Brief" (id 1, run by Erynne) and "Audit" (id 2, run by a Claude routine), both to her Inbox.
"""

import sys
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "tests")]

import uvicorn
from fakes import FakeDirectory, FakeEmbedder
from mnemerynne import accounts, automations, db, rooms, users
from mnemerynne.config import Settings
from mnemerynne.web.app import Services, create_app


def main():
    """Alice, Automator and Rooms on, two channels and an automation, then serve only on the loopback interface."""
    with TemporaryDirectory(prefix="automator-preview-") as directory:
        settings = Settings(_env_file=None, data_dir=Path(directory) / "data", embed_dim=4,
                            secret_key="automator-local-preview-only-secret", ad_domain="PREVIEW")
        people = FakeDirectory({"alice": {"Domain Users"}})
        db.migrate_all(settings.db_path, settings.embed_dim)           # core and every feature file, as at start
        conn = db.connect(settings.db_path)
        uid = users.create_user(conn, "alice", "preview-password")
        accounts.link(conn, uid, "alice", people)
        alice = users.get_user(conn, "alice")
        automations.set_enabled(conn, True)                             # Automator on, for Domain Users
        automations.set_group(conn, "Domain Users")
        rooms.set_enabled(conn, True)                                   # Rooms on (core's meta)
        with rooms.on(conn) as rc:                                      # Rooms' own tables (rooms.db)
            rooms.create_channel(rc, alice, "lobby")                    # Erynne on: offered by the picker
            quiet = rooms.create_channel(rc, alice, "quiet")
            rc.execute("UPDATE chat_rooms SET erynne = 0 WHERE id = ?", (quiet,))   # Erynne off: left out
        runs = automations_run_conn(settings)
        with db.transaction(runs):
            automations.create(runs, settings, alice, people, {"name": "Brief", "prompt": "Brief me",
                                                                "schedule": {"every": "day", "at": "07:30"}})
            automations.create(runs, settings, alice, people, {     # id 2: run by a Claude routine, as Doll's audit
                "name": "Audit", "prompt": "Audit Wazuh", "schedule": {"every": "day", "at": "07:30"},
                "runner": "claude_routine", "routine_url": "https://api.anthropic.com/v1/claude_code/routines/trig_01x/fire",
                "routine_token": "sk-ant-oat01-preview"})
        runs.close()
        conn.close()
        app = create_app(settings, Services(llm=None, embedder=FakeEmbedder(), directory=people))
        uvicorn.run(app, host="127.0.0.1", port=int(sys.argv[1]), log_level="warning")


def automations_run_conn(settings):
    """Automator's tables (automations.db), as the runner reaches them."""
    from mnemerynne import automations_run

    return automations_run.connect(settings)


if __name__ == "__main__":
    main()
