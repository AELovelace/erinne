"""StickBob Runner's coins (mnemerynne/runner.py): tickets, the time check, paying once, and the daily cap setting."""

import pytest

from mnemerynne import coins, media, runner, users
from mnemerynne.db import transaction
from test_media import _login, _no_network, _turn_on, checker, client, conn, directory, root, settings  # noqa: F401


@pytest.fixture
def alice(conn):
    return users.create_user(conn, "alice", "password123")


def _set_cap(conn, settings, cap):
    with transaction(conn):
        media.save_settings(conn, settings, {"runner_daily_cap": cap})


def _ledger(conn, uid):
    return conn.execute("SELECT amount, type, game, ref FROM coin_transactions WHERE to_account = ? AND type = 'reward'",
                        (f"user:{uid}",)).fetchall()


def test_a_run_pays_its_distance_and_kills_once(conn, alice):
    run = runner.start(conn, alice, now=1000.0)
    out = runner.finish(conn, alice, run, meters=257, kills=4, now=1030.0)   # 257 m in 30 s is well within top speed
    assert out == {"earned": 25 + 4, "paid": 29, "capped": False, "refused": None, "coins": coins.START + 29}
    assert [tuple(r) for r in _ledger(conn, alice)] == [(29, "reward", "stickbob-runner", f"stickbob-runner:{alice}:{run}")]
    again = runner.finish(conn, alice, run, meters=257, kills=4, now=1031.0)
    assert again["paid"] == 0 and again["refused"] and again["coins"] == coins.START + 29


def test_the_cap_is_off_by_default(conn, settings, alice):
    assert media.load_settings(conn)["runner_daily_cap"] == 0 and runner.daily_cap(conn) == 0
    for i in range(3):                                       # three huge, plausible runs, all paid in full
        run = runner.start(conn, alice, now=0.0)
        assert runner.finish(conn, alice, run, meters=20000, kills=0, now=1000.0)["paid"] == 2000


def test_a_run_faster_than_stickbob_can_go_pays_nothing(conn, alice):
    run = runner.start(conn, alice, now=0.0)
    out = runner.finish(conn, alice, run, meters=900, kills=0, now=10.0)      # 90 m/s: impossible
    assert out["paid"] == 0 and "add up" in out["refused"]
    assert runner.finish(conn, alice, run, meters=10, kills=0, now=11.0)["refused"]   # and the ticket is used up
    run = runner.start(conn, alice, now=0.0)
    assert runner.finish(conn, alice, run, meters=0, kills=200, now=10.0)["paid"] == 0   # nor more kills than could fit


def test_starting_again_drops_the_old_ticket(conn, alice):
    first = runner.start(conn, alice, now=0.0)
    second = runner.start(conn, alice, now=5.0)
    assert runner.finish(conn, alice, first, meters=50, kills=0, now=20.0)["refused"]
    assert runner.finish(conn, alice, second, meters=50, kills=0, now=20.0)["paid"] == 5
    assert runner.start(conn, alice) > second                # run numbers are never reused, so each pays its own ref


def test_someone_elses_ticket_pays_nothing(conn, alice):
    bob = users.create_user(conn, "bob", "password123")
    run = runner.start(conn, alice, now=0.0)
    assert runner.finish(conn, bob, run, meters=100, kills=0, now=60.0)["refused"]
    assert runner.finish(conn, alice, run, meters=100, kills=0, now=60.0)["paid"] == 10


def test_a_daily_cap_limits_a_day_of_runs(conn, settings, alice):
    _set_cap(conn, settings, 30)
    paid = []
    for _ in range(3):
        run = runner.start(conn, alice, now=0.0)
        paid.append(runner.finish(conn, alice, run, meters=200, kills=0, now=60.0))
    assert [p["paid"] for p in paid] == [20, 10, 0] and [p["capped"] for p in paid] == [False, True, True]


def test_the_cap_setting_is_checked(conn, settings):
    for bad in (-1, 1_000_001, "50", True, 2.5):
        with pytest.raises(media.MediaError):
            media.save_settings(conn, settings, {"runner_daily_cap": bad})
    _set_cap(conn, settings, 0)
    assert media.load_settings(conn)["runner_daily_cap"] == 0


def test_the_page_starts_and_finishes_a_run(client, conn, directory):
    uid = _login(client, conn, directory)
    _turn_on(client)
    run = client.post("/api/media/games/stickbob-runner/start").json()["run"]
    assert client.post("/api/media/games/stickbob-runner/finish", json={"run": run, "meters": 15, "kills": 2, "coins": 999}).status_code == 422
    out = client.post("/api/media/games/stickbob-runner/finish", json={"run": run, "meters": 15, "kills": 2}).json()
    assert out["paid"] == 3 and out["coins"] == coins.START + 3
    assert client.post("/api/media/games/stickbob-runner/finish", json={"run": run, "meters": "15", "kills": 2}).status_code == 422
    assert client.put("/api/admin/settings/media", json={"runner_daily_cap": 40}).status_code == 200   # boss is an admin
    assert client.get("/api/admin/settings/media").json()["settings"]["runner_daily_cap"] == 40
    assert client.put("/api/admin/settings/media", json={"runner_daily_cap": -5}).status_code == 422
    assert uid


def test_signed_out_people_get_no_ticket(client, conn, directory):
    _login(client, conn, directory)
    _turn_on(client)
    client.cookies.clear()
    assert client.post("/api/media/games/stickbob-runner/start").status_code == 401
