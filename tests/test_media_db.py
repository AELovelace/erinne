"""media.db (docs/plans/services-rewrite.md, Phase 4): Media's catalog, TVs, remotes and signage move out of core;
what core's references used to cascade (a deleted file, folder or user) is cleaned without them."""

from __future__ import annotations

import sqlite3

import pytest

from mnemerynne import db, permissions, users
from mnemerynne.web import admin


def _core_with_media(settings):
    """Today's box: core's migrations only, with a TV (and a remote, a guest, a show) and a catalogued file."""
    core = db.connect(settings.db_path)
    db.migrate(core, settings.embed_dim)
    owner = users.create_user(core, "boss", "password123")
    guest = users.create_user(core, "jane", "password123")
    lib = permissions.private_collection_id(core, owner)
    with db.transaction(core):
        folder = core.execute("INSERT INTO folders(collection_id, name) VALUES (?, 'Bluey')", (lib,)).lastrowid
        core.execute("INSERT INTO blobs(sha256, kind, size, rel_path) VALUES ('ab', 'other', 1, 'ab')")
        file_id = core.execute("INSERT INTO files(collection_id, folder_id, blob_sha256, display_name) "
                               "VALUES (?, ?, 'ab', 'ep1.mkv')", (lib, folder)).lastrowid
        screen = core.execute("INSERT INTO media_screens(user_id, name, token_hash) VALUES (?, 'Living room', 'h')",
                              (owner,)).lastrowid
        core.execute("INSERT INTO media_remotes(user_id, name, token_hash, screen_id) VALUES (?, 'Pink', 'r', ?)",
                     (guest, screen))
        core.execute("INSERT INTO media_screen_guests(owner_id, guest_id) VALUES (?, ?)", (owner, guest))
        core.execute("INSERT INTO media_items(file_id, section, media_type, title) VALUES (?, 'tv', 'video', 'Ep 1')",
                     (file_id,))
        core.execute("INSERT INTO media_tv_shows(folder_id) VALUES (?)", (folder,))
        core.execute("INSERT INTO media_progress(user_id, file_id, position_s) VALUES (?, ?, 30)", (guest, file_id))
    return core, owner, guest, file_id, folder, screen


@pytest.fixture
def media_box(settings):
    try:
        found = _core_with_media(settings)
    except sqlite3.Error as exc:                       # the schema moved on: show what this fixture needs to follow
        pytest.fail(f"_core_with_media needs updating for core's schema: {exc}")
    found[0].close()
    db.migrate_all(settings.db_path, settings.embed_dim)
    return found[1:]


def test_the_media_migration_has_cores_columns_exactly(settings):
    core = db.connect(settings.db_path)
    db.migrate(core, settings.embed_dim)
    feature = db.connect_feature(settings.db_path, "media")
    db.migrate_feature(feature, "media")
    for table in db.MEDIA_TABLES:
        mine = [tuple(r)[1:] for r in core.execute(f"PRAGMA main.table_info({table})")]
        theirs = [tuple(r)[1:] for r in feature.execute(f"PRAGMA main.table_info({table})")]
        assert mine == theirs, table
        refs = {r["table"] for r in feature.execute(f"PRAGMA main.foreign_key_list({table})")}
        assert not refs & {"users", "files", "folders"}, table


def test_every_media_row_moves_and_reads_across_the_files(settings, media_box):
    owner, guest, file_id, folder, screen = media_box
    core = db.connect(settings.db_path)
    assert not core.execute("SELECT 1 FROM sqlite_master WHERE name = 'media_items'").fetchone()
    media = db.connect_feature(settings.db_path, "media")
    row = media.execute("SELECT f.display_name, s.name FROM media_items i JOIN files f ON f.id = i.file_id "
                        "JOIN media_screens s ON s.user_id = ?", (owner,)).fetchone()
    assert tuple(row) == ("ep1.mkv", "Living room")                           # core's files through the attach
    pooled = db.connect(settings.db_path, features=True)                       # a request connection reads it too
    assert pooled.execute("SELECT name FROM media_remotes").fetchone()[0] == "Pink"


def test_a_deleted_user_takes_their_screens_remotes_and_progress(settings, media_box):
    owner, guest, *_ = media_box
    core = db.connect(settings.db_path)
    assert users.delete_user(core, "jane")
    media = db.connect_feature(settings.db_path, "media")
    assert media.execute("SELECT count(*) FROM media_remotes").fetchone()[0] == 0
    assert media.execute("SELECT count(*) FROM media_screen_guests").fetchone()[0] == 0
    assert media.execute("SELECT count(*) FROM media_progress").fetchone()[0] == 0
    assert media.execute("SELECT count(*) FROM media_screens").fetchone()[0] == 1          # the owner's TV stays


def test_a_deleted_file_or_folder_leaves_the_catalog_at_the_next_pass(settings, media_box):
    owner, guest, file_id, folder, screen = media_box
    core = db.connect(settings.db_path)
    core.execute("PRAGMA foreign_keys = OFF")
    core.execute("DELETE FROM files WHERE id = ?", (file_id,))
    core.execute("DELETE FROM folders WHERE id = ?", (folder,))
    media = db.connect_feature(settings.db_path, "media")
    with db.transaction(media):
        swept = db.sweep_feature(media, "media")                                 # what each catalog pass does first
    assert swept == {"media.media_items": 1, "media.media_progress": 1, "media.media_tv_shows": 1}
    assert db.sweep_feature(core, "media") == {}                                  # core's own: nothing to do there


def test_a_feature_routes_audit_row_still_lands_in_core(settings, media_box):
    owner, *_ = media_box
    media = db.connect_feature(settings.db_path, "media")
    actor = users.get_user_by_id(media, owner)                                    # read through the attach
    with db.transaction(media):
        media.execute("UPDATE media_screens SET name = 'Den'")
        admin._log(media, actor, "screen.rename", "Den")                          # core's access_log, on its own
    core = db.connect(settings.db_path)
    assert core.execute("SELECT action FROM access_log ORDER BY id DESC").fetchone()[0] == "screen.rename"


def test_moving_back_gives_older_code_media_in_core(settings, media_box):
    assert db.move_back(settings.db_path, "media")["media_items"] == 1
    core = db.connect(settings.db_path)
    assert core.execute("SELECT title FROM media_items").fetchone()[0] == "Ep 1"
