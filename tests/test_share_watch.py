"""Share permissions kept current by watching the share, and a big library's first look answered at once.

On the box, a library of 28,789 folders had every folder's permissions read from the disk (a "walk") every 30 s,
and a first look waited for a whole walk. Now permission changes on the share re-read just the folders they touch,
walks are only a safety net, and a big library's first look reads only what the page shows."""

import threading
import time

from mnemerynne import fs, permissions, share
from test_performance import (CountingChecker, _background_walks_done, _write, checker, conn, jane,  # noqa: F401
                              root, rt, settings)


def _library(conn, settings, root):
    """The "Docs" library: Guides (One, Two inside) and Plans (Three inside)."""
    for place in ("Guides/One", "Guides/Two", "Plans/Three"):
        _write(root / "Docs" / place / "x.md")
    share.sync(conn, settings)
    return conn.execute("SELECT id FROM collections WHERE kind = 'library' AND share_path = 'Docs'").fetchone()[0]


def _fid(conn, name):
    return conn.execute("SELECT id FROM folders WHERE name = ?", (name,)).fetchone()[0]


def _key(rt, library):
    return next(k for k in rt.rights if k[0] == library)


# ---------------------------------------------------------------- the watcher
def test_a_permission_change_reads_just_that_folder_again_and_saves_it(conn, settings, root, checker, rt, jane):
    library = _library(conn, settings, root)
    guides = _fid(conn, "Guides")
    assert permissions.reach(conn, jane)[library].can_read(guides)
    asked = len(checker.asked)

    checker.rules["Docs/Guides"] = (False, False)          # someone takes Jane's access away, on the share
    assert share.apply_changes(rt, conn, [root / "Docs" / "Guides"]) == 1
    assert len(checker.asked) == asked + 1                  # that one folder read again: no walk
    assert not permissions.reach(conn, jane)[library].can_read(guides)
    assert share._load_rights(rt, _key(rt, library)).folders[guides] == (False, False)   # and saved


def test_a_change_also_updates_walks_saved_for_people_not_here_since_the_restart(conn, settings, root, checker, rt,
                                                                                 jane):
    library = _library(conn, settings, root)
    guides = _fid(conn, "Guides")
    permissions.reach(conn, jane)                           # walked and saved
    key = _key(rt, library)
    fresh = share.configure(settings, rt.directory, CountingChecker(root, checker.rules))   # a restart
    fresh.checker.rules["Docs/Guides"] = (False, False)
    share.apply_changes(fresh, conn, [root / "Docs" / "Guides"])
    assert key not in fresh.rights                          # Jane hasn't looked since the restart...
    assert share._load_rights(fresh, key).folders[guides] == (False, False)   # ...but her saved walk is current


def test_watcher_lines_folders_count_files_dont_and_an_overflow_makes_every_walk_due(conn, settings, root, rt, jane,
                                                                                    monkeypatch):
    library = _library(conn, settings, root)
    permissions.reach(conn, jane)
    seen = []
    monkeypatch.setattr(share, "apply_changes", lambda rt_, conn_, paths: seen.append(set(paths)) or 0)
    guides = root / "Docs" / "Guides"
    share.apply_events(rt, [f"ATTRIB,ISDIR\t{guides}\n", f"ATTRIB\t{guides / 'One' / 'x.md'}\n"])
    assert seen == [{guides}]                               # a file's own permissions don't change a folder's

    share.apply_events(rt, ["Q_OVERFLOW\t\n"])              # the kernel dropped events: trust nothing
    assert time.monotonic() - rt.rights[_key(rt, library)].at > 10 ** 8


def test_too_many_changes_at_once_mean_a_whole_walk_instead(conn, settings, root, rt, jane, monkeypatch):
    library = _library(conn, settings, root)
    permissions.reach(conn, jane)
    monkeypatch.setattr(share, "_BATCH_LIMIT", 1)
    share.apply_changes(rt, conn, [root / "Docs" / "Guides", root / "Docs" / "Plans"])
    assert time.monotonic() - rt.rights[_key(rt, library)].at > 10 ** 8   # due: the next look walks


def test_watcher_events_are_applied_in_batches(rt, monkeypatch):
    batches = []
    monkeypatch.setattr(share, "apply_events", lambda rt_, lines: batches.append(list(lines)))
    monkeypatch.setattr(share, "_BATCH_SECONDS", 0.3)
    share._watch_events(rt, iter(["a\n", "b\n", "c\n"]))    # a recursive change: many lines at once
    assert batches == [["a\n", "b\n", "c\n"]]               # one pass, not one per line


def test_with_the_watcher_up_whole_walks_are_only_a_safety_net(rt, settings):
    assert share._due_after(rt) == settings.share_rights_seconds
    rt.watching = True
    try:
        assert share._due_after(rt) == share.WATCHED_WALK_SECONDS > settings.share_rights_seconds
    finally:
        rt.watching = False


def test_the_watcher_does_nothing_without_inotifywait(rt, monkeypatch):
    monkeypatch.setattr(share.shutil, "which", lambda name: None)   # Windows, or inotify-tools not installed
    share.start_watching()
    assert rt.watcher is None and not rt.watching


# ---------------------------------------------------------- a big library's first look
class SlowWalkChecker(CountingChecker):
    """A share whose whole walk takes "forever" (the box's 28,789 folders), while a single look is quick."""

    def __init__(self, root, rules=None) -> None:
        super().__init__(root, rules)
        self.go = threading.Event()

    def rights(self, path, uid, gids):
        if threading.current_thread().name == "share-rights-walk":
            self.go.wait(10)                                # only the background walk is slow
        return super().rights(path, uid, gids)


def test_a_big_librarys_first_look_answers_at_once_and_never_shows_more(conn, settings, root, rt, jane, monkeypatch):
    monkeypatch.setattr(share, "LAZY_FROM", 2)              # "big" for this test: more than 2 folders
    library = _library(conn, settings, root)
    slow = SlowWalkChecker(root, {"Docs/Plans/Three": (False, False)})   # Jane may not open Three
    rt.checker = slow
    began = time.monotonic()
    reach = permissions.reach(conn, jane)[library]
    assert time.monotonic() - began < 2                     # didn't wait for the walk
    one, two, three = _fid(conn, "One"), _fid(conn, "Two"), _fid(conn, "Three")
    assert reach.can_read(_fid(conn, "Guides"))             # the top-level folders: read straight away
    assert not reach.can_read(one) and one in reach.unknown   # deeper: closed until read

    access = fs.Access.of(conn, jane)
    shown = {f["name"] for f in fs.browse(conn, access, library, _fid(conn, "Guides"))["folders"]}
    assert shown == {"One", "Two"}                          # opening Guides read what it shows, now
    assert not fs.browse(conn, access, library, _fid(conn, "Plans"))["folders"]   # Three: the share says no
    assert two not in access.reaches[library].unknown

    slow.go.set()                                           # the walk finishes
    _background_walks_done()
    after = permissions.reach(conn, jane)[library]
    assert not after.unknown and not rt.provisional         # the whole walk replaced the first look
    assert after.can_read(one) and not after.can_read(three)


def test_a_first_look_never_shows_a_library_on_a_guess(conn, settings, root, rt, jane, monkeypatch):
    """Jane may only open a folder deep down: until the walk finds it, the library stays out of her list rather
    than showing a name she might have no right to see."""
    monkeypatch.setattr(share, "LAZY_FROM", 2)
    library = _library(conn, settings, root)
    slow = SlowWalkChecker(root, {"Docs": (False, False), "Docs/Guides/One": (True, False)})
    rt.checker = slow
    assert library not in permissions.reach(conn, jane)     # nothing known yet that she may open
    slow.go.set()
    _background_walks_done()
    after = permissions.reach(conn, jane)[library]          # the walk found One
    assert after.can_read(_fid(conn, "One")) and not after.can_read(_fid(conn, "Guides"))
