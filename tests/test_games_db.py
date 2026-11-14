"""games.db (docs/plans/services-rewrite.md, Phase 2): the games' tables move out of core into a file of their own,
with its own write lock, and everything that reads or writes them follows."""

from __future__ import annotations

import pytest

from mnemerynne import coins, db, lumipet, retention, users


def _core_with_games(settings):
    """A core database as today's box has it: only core's migrations, the games' tables still in it, with rows."""
    core = db.connect(settings.db_path)
    db.migrate(core, settings.embed_dim)
    alice = users.create_user(core, "alice", "password123")
    bob = users.create_user(core, "bob", "password123")
    with db.transaction(core):
        coins.held(core, alice)                                   # a purse each (the starting coins)
        coins.held(core, bob)
        core.execute("INSERT INTO lumi_pets(user_id, data) VALUES (?, '{}'), (?, '{}')", (alice, bob))
        core.execute("INSERT INTO lumi_parties(code, state, updated_at) VALUES ('ABCD', '{}', 1)")
        core.execute("INSERT INTO lumi_party_members(user_id, code) VALUES (?, 'ABCD')", (alice,))
    return core, alice, bob


def test_the_games_migration_has_cores_columns_exactly(settings):
    core = db.connect(settings.db_path)
    db.migrate(core, settings.embed_dim)
    games = db.connect_feature(settings.db_path, "games")
    db.migrate_feature(games, "games")
    for table in db.GAMES_TABLES:
        mine = [(r["name"], r["type"], r["notnull"], r["dflt_value"], r["pk"]) for r in core.execute(f"PRAGMA main.table_info({table})")]
        theirs = [(r["name"], r["type"], r["notnull"], r["dflt_value"], r["pk"]) for r in games.execute(f"PRAGMA main.table_info({table})")]
        assert mine == theirs, table
        assert not [r for r in games.execute(f"PRAGMA main.foreign_key_list({table})") if r["table"] == "users"]


def test_migrate_all_moves_every_games_row_out_of_core(settings):
    core, alice, bob = _core_with_games(settings)
    before = {t: core.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in db.GAMES_TABLES}
    core.close()
    versions = db.migrate_all(settings.db_path, settings.embed_dim)
    assert versions["games"] == 1
    core = db.connect(settings.db_path)
    assert not [t for t in db.GAMES_TABLES if core.execute("SELECT 1 FROM sqlite_master WHERE name = ?", (t,)).fetchone()]
    games = db.connect_feature(settings.db_path, "games")
    assert {t: games.execute(f"SELECT count(*) FROM main.{t}").fetchone()[0] for t in db.GAMES_TABLES} == before
    assert coins.peek(games, alice) == coins.START
    named = games.execute("SELECT u.username FROM lumi_pets p JOIN users u ON u.id = p.user_id ORDER BY 1").fetchall()
    assert [r[0] for r in named] == ["alice", "bob"]                                 # core read through the attach
    assert db.migrate_all(settings.db_path, settings.embed_dim)["games"] == 1        # again: nothing left to move


def test_deleting_a_user_forgets_their_games_rows(settings):
    core, alice, bob = _core_with_games(settings)
    core.close()
    db.migrate_all(settings.db_path, settings.embed_dim)
    core = db.connect(settings.db_path)
    assert users.delete_user(core, "alice")
    games = db.connect_feature(settings.db_path, "games")
    assert games.execute("SELECT user_id FROM main.lumi_pets").fetchall()[0][0] == bob
    assert games.execute("SELECT count(*) FROM main.lumi_pets").fetchone()[0] == 1
    assert games.execute("SELECT count(*) FROM main.coin_accounts WHERE user_id = ?", (alice,)).fetchone()[0] == 0
    assert games.execute("SELECT count(*) FROM main.lumi_party_members").fetchone()[0] == 0


def test_rows_left_by_a_user_deleted_elsewhere_are_swept(settings):
    core, alice, bob = _core_with_games(settings)
    core.close()
    db.migrate_all(settings.db_path, settings.embed_dim)
    core = db.connect(settings.db_path)
    core.execute("PRAGMA foreign_keys = OFF")
    core.execute("DELETE FROM users WHERE id = ?", (bob,))                      # as a crash between the two would
    swept = db.sweep_orphans(settings.db_path)
    assert swept["games.lumi_pets"] == 1 and swept["games.coin_accounts"] == 1


def test_the_app_moves_the_games_tables_at_start_and_its_pool_reaches_them(client, settings, conn):
    from mnemerynne.web import deps
    assert not conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'lumi_pets'").fetchone()    # moved at start
    alice = users.create_user(conn, "alice", "password123")
    pool = deps.feature_pool(client.app, "games")
    assert deps.feature_pool(client.app, "games") is pool                            # one per app
    games = pool.get()
    try:
        with db.transaction(games):
            lumipet.load(games, alice, 0)                                       # what the routes do with theirs
        assert games.execute("SELECT count(*) FROM main.lumi_pets").fetchone()[0] == 1
    finally:
        pool.put(games)


def test_code_outside_a_request_finds_the_table_where_it_lives(settings):
    core, *_ = _core_with_games(settings)
    with db.table_connection(core, "games", "lumi_pets") as there:
        assert there is core                                                          # not moved yet: core's own
    core.close()
    db.migrate_all(settings.db_path, settings.embed_dim)
    core = db.connect(settings.db_path)
    with db.table_connection(core, "games", "lumi_pets") as there:
        assert there is not core and there.execute("SELECT count(*) FROM main.lumi_pets").fetchone()[0] == 2
    with db.table_connection(core, "games", "no_such_table") as there:
        assert there is None


def test_the_coin_ledger_is_pruned_in_games_db(settings):
    core, alice, _ = _core_with_games(settings)
    core.close()
    db.migrate_all(settings.db_path, settings.embed_dim)
    games = db.connect_feature(settings.db_path, "games")
    games.execute("INSERT INTO coin_transactions(to_account, amount, type, created_at) "
                  "VALUES ('user:1', 5, 'reward', datetime('now', '-400 days'))")
    core = db.connect(settings.db_path)
    out = retention.purge(core, settings.model_copy(update={"coin_ledger_days": 365}))
    assert out["coin_transactions"] == 1
    assert games.execute("SELECT count(*) FROM main.coin_transactions WHERE created_at < datetime('now', '-365 days')").fetchone()[0] == 0


def test_a_games_request_holds_no_core_write_lock(settings, conn):
    """The point of the file: while a games write is open, core takes writes (and the other way round)."""
    db.migrate_all(settings.db_path, settings.embed_dim)
    games = db.connect_feature(settings.db_path, "games")
    games.execute("BEGIN IMMEDIATE")
    try:
        games.execute("INSERT INTO lumi_parties(code, state, updated_at) VALUES ('WXYZ', '{}', 1)")
        conn.execute("BEGIN IMMEDIATE")                       # would wait out busy_timeout if games held core's lock
        conn.execute("INSERT INTO meta(key, value) VALUES ('games-db-test', '1')")
        conn.execute("COMMIT")
    finally:
        games.execute("COMMIT")


def test_moving_back_gives_older_code_its_tables_and_a_later_start_moves_them_out_again(settings):
    """B back to A on the box: the rows go back into core (counted), games.db is set aside, and the newer code's
    next start makes a fresh games.db and moves them out again, rows intact."""
    core, alice, bob = _core_with_games(settings)
    core.close()
    db.migrate_all(settings.db_path, settings.embed_dim)
    games = db.connect_feature(settings.db_path, "games")
    with db.transaction(games):
        coins.spend(games, alice, 40, "slots")                                       # played on B
    games.close()
    copied = db.move_back(settings.db_path, "games")
    assert copied["coin_accounts"] == 3 and copied["lumi_pets"] == 2                 # alice, bob and the house
    assert not db.feature_path(settings.db_path, "games").exists()
    assert list(db.feature_path(settings.db_path, "games").parent.glob("games.moved-back-*.db"))
    core = db.connect(settings.db_path)
    assert coins.peek(core, alice) == coins.START - 40                               # what A's code sees
    core.close()
    db.migrate_all(settings.db_path, settings.embed_dim)                              # B again
    games = db.connect_feature(settings.db_path, "games")
    assert coins.peek(games, alice) == coins.START - 40


def test_moving_back_refuses_when_core_still_has_the_tables(settings):
    core, *_ = _core_with_games(settings)
    games = db.connect_feature(settings.db_path, "games")
    db.migrate_feature(games, "games")
    games.close()
    core.close()
    with pytest.raises(db.SchemaError):
        db.move_back(settings.db_path, "games")
