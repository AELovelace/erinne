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


def _automator(conn, settings, alice_id):
    """Turn Automator on for Domain Users and give alice a sample prompt file (a .md with notes around a fenced
    block), so the editor's "Use a prompt file" can be tried in the browser."""
    import io
    from mnemerynne import automations, permissions
    from mnemerynne.ingest import store

    automations.set_enabled(conn, True)                                  # off by default on a new box
    automations.set_group(conn, "Domain Users")                          # everyone in the preview may use it
    sample = ("# Morning briefing prompt\n\nThe automation follows the block below; these notes are for people.\n\n"
              "```\nBrief me for the day: unread notes, announcements and anything urgent.\n```\n")
    got = store.receive(io.BytesIO(sample.encode()), "morning-briefing-prompt.md", settings)
    store.add_file(conn, settings, got, permissions.private_collection_id(conn, alice_id), alice_id)
    _sample_runs(conn, settings, alice_id)


def _sample_runs(conn, settings, alice_id):
    """Three automations of alice's with a history, for the Timeline tab: a daily briefing (finished runs, one
    failed), an every-hour check (short runs), and a Claude routine whose run is still going (waiting for its
    report, so the preview's scheduler leaves it alone for two hours). Fake rows: nothing here really ran."""
    from datetime import timedelta
    from mnemerynne import automations as auto

    alice = users.get_user_by_id(conn, alice_id)
    now = auto.now()
    made = {}
    for name, schedule, runner in (("Morning briefing", {"every": "day", "at": "07:30"}, "erynne"),
                                   ("API health check", {"every": "hours", "n": 1}, "erynne"),
                                   ("Security digest", {"every": "day", "at": "08:00"}, "erynne")):
        made[name] = auto.create(conn, settings, alice, None, {"name": name, "prompt": name, "schedule": schedule,
                                                               "runner": runner})

    def ran(name, ago_minutes, seconds, status="done"):                 # one finished run, `ago` minutes back
        start = now - timedelta(minutes=ago_minutes)
        conn.execute("INSERT INTO automation_runs(automation_id, trigger, status, started_at, finished_at) "
                     "VALUES (?, 'schedule', ?, ?, ?)", (made[name], status, auto.stamp(start),
                                                          auto.stamp(start + timedelta(seconds=seconds))))

    for days, secs in ((0, 190), (1, 230), (2, 160)):                  # the briefing: about 3 minutes
        ran("Morning briefing", 6 * 60 + days * 1440, secs)
    ran("Morning briefing", 3 * 60, 25, "failed")
    for hours in range(1, 12):                                         # the hourly check: 30-50 seconds
        ran("API health check", hours * 60, 30 + hours * 2)
    for days in (1, 2):                                                # the digest: about 4 minutes
        ran("Security digest", 1440 * days, 240)
    conn.execute("UPDATE automations SET runner = 'claude_routine' WHERE id = ?", (made["Security digest"],))
    going = conn.execute("INSERT INTO automation_runs(automation_id, trigger, status, started_at) VALUES (?, 'manual', "
                         "'waiting', ?)", (made["Security digest"], auto.stamp(now - timedelta(minutes=2)))).lastrowid
    conn.execute("UPDATE automations SET last_run_id = ? WHERE id = ?", (going, made["Security digest"]))   # as start_run does


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
        if "automator" in sys.argv[2:]:                                   # `casino_preview.py PORT automator`: Automator on
            _automator(conn, settings, uids[0])                           # for everyone, with a prompt file in alice's box
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
