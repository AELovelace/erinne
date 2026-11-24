"""Automator: schedules, who may use it, runs by Erynne, a Claude routine reporting back, and delivery to
the Inbox, a room and webhooks."""

import asyncio
import hashlib
import hmac
import json
from contextlib import contextmanager
from datetime import datetime, timezone

import httpx
import pytest
from fastapi.testclient import TestClient

from fakes import FakeDirectory, FakeEmbedder
from mnemerynne import api_tokens, apis, automations as auto, automations_run, messages, rooms, users
from mnemerynne.clients.llm import ToolCall, ToolTurn
from mnemerynne.mcp_server import Tools
from mnemerynne.web.app import Services, create_app
from test_agent import AgentLLM

ROUTINE = "https://api.anthropic.com/v1/claude_code/routines/trig_01abc/fire"


@pytest.fixture(autouse=True)
def _hooks():
    yield
    automations_run.TRANSPORT = None
    automations_run.ACTIVE = None


@pytest.fixture
def people(conn):
    ids = {name: users.create_user(conn, name, "password123") for name in ("alice", "bob")}
    ids["boss"] = users.create_user(conn, "boss", "password123", role="admin")
    conn.execute("UPDATE users SET ad_account = username")
    auto.set_enabled(conn, True)
    auto.set_group(conn, "Ops")
    return ids


@pytest.fixture
def directory(people):
    return FakeDirectory({"alice": {"Ops"}, "bob": {"Staff"}, "boss": {"Domain Admins"}},
                         groups=["Ops", "Staff", "Domain Admins"])


@contextmanager
def _web(settings, name, directory, agent_llm=None):
    with TestClient(create_app(settings, Services(llm=None, embedder=FakeEmbedder(), directory=directory,
                                                  agent_llm=agent_llm))) as c:
        if name:
            c.post("/login", data={"username": name, "password": "password123"})
        yield c


def _wait(c, run_id, done=("done", "quiet", "failed", "timed_out", "waiting")):
    grace = 40                                     # a run says "done" a moment before its deliveries are saved
    for _ in range(200):
        run = c.get(f"/api/automations/runs/{run_id}").json()
        if run["status"] in done:
            if run["status"] != "done" or run["delivered"] or grace == 0:
                return run
            grace -= 1
        c.portal.call(asyncio.sleep, 0.05)
    raise AssertionError(f"run {run_id} stayed {run['status']}")


def _has_zones():
    """A time zone database (Linux has one; Windows needs the tzdata package)."""
    try:
        auto.zone("America/Los_Angeles")
        return True
    except auto.AutomationError:
        return False


def _at(text):
    return datetime.strptime(text, "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)


# --------------------------------------------------------------- schedules
def test_schedules_follow_the_clock_and_the_days():
    weekdays = auto.check_schedule({"every": "day", "at": "7:30", "days": ["mon", "TUE", "wed", "thu", "fri"]})
    assert weekdays == {"every": "day", "at": "07:30", "days": ["mon", "tue", "wed", "thu", "fri"]}
    # Friday 08:00 UTC: next is Monday 07:30.
    assert auto.next_run(weekdays, "UTC", _at("2026-10-30 08:00")) == _at("2026-11-02 07:30")
    assert auto.next_run(weekdays, "UTC", _at("2026-10-29 07:00")) == _at("2026-10-29 07:30")
    # 07:30 in Los Angeles across the end of daylight saving (1 November 2026): 14:30 UTC, then 15:30.
    daily = auto.check_schedule({"every": "day", "at": "07:30"})
    if _has_zones():
        assert auto.next_run(daily, "America/Los_Angeles", _at("2026-10-31 15:00")) == _at("2026-11-01 15:30")
        assert auto.next_run(daily, "America/Los_Angeles", _at("2026-10-30 15:00")) == _at("2026-10-31 14:30")
    assert auto.check_schedule({"every": "week", "at": "09:00", "day": "monday"})["days"] == ["mon"]
    assert auto.check_schedule({"every": "hours", "n": 4}) == {"every": "minutes", "n": 240}
    assert auto.next_run({"every": "minutes", "n": 240}, "UTC", _at("2026-10-30 08:00")) == _at("2026-10-30 12:00")
    assert auto.describe(weekdays, "UTC") == "weekdays at 07:30 (UTC)"
    for bad in ({"every": "minutes", "n": 5}, {"every": "day", "at": "25:00"}, {"every": "day", "at": "07:00", "days": ["xyz"]},
                {"every": "fortnight"}, "daily"):
        with pytest.raises(auto.AutomationError):
            auto.check_schedule(bad)
    with pytest.raises(auto.AutomationError):
        auto.zone("Mars/Olympus")


def test_every_so_often_can_keep_to_business_hours():
    hourly = auto.check_schedule({"every": "hours", "n": 1, "between": ["8:00", "17:00"],
                                  "days": ["mon", "tue", "wed", "thu", "fri"]})
    assert hourly == {"every": "minutes", "n": 60, "between": ["08:00", "17:00"],
                      "days": ["mon", "tue", "wed", "thu", "fri"]}
    assert auto.describe(hourly, "UTC") == "every hour between 08:00 and 17:00, weekdays (UTC)"
    # Tuesday 2026-11-24: before opening → 08:00; mid-morning → the next hour on the window's beat; after 17:00
    # → Wednesday 08:00; 17:00 itself still runs.
    assert auto.next_run(hourly, "UTC", _at("2026-11-24 06:10")) == _at("2026-11-24 08:00")
    assert auto.next_run(hourly, "UTC", _at("2026-11-24 10:20")) == _at("2026-11-24 11:00")
    assert auto.next_run(hourly, "UTC", _at("2026-11-24 16:30")) == _at("2026-11-24 17:00")
    assert auto.next_run(hourly, "UTC", _at("2026-11-24 17:00")) == _at("2026-11-25 08:00")
    # Friday evening → Monday morning: the weekend is skipped.
    assert auto.next_run(hourly, "UTC", _at("2026-11-27 17:30")) == _at("2026-11-30 08:00")
    # The Timeline's planned slots for that Tuesday: ten, 08:00 to 17:00.
    row = {"enabled": 1, "next_run_at": "2026-11-24 08:00:00", "schedule": json.dumps(hourly), "tz": "UTC"}
    assert [t.strftime("%H") for t in auto.planned(row, _at("2026-11-24 00:00"), _at("2026-11-25 00:00"))] == \
        ["08", "09", "10", "11", "12", "13", "14", "15", "16", "17"]
    # Overnight, every 90 minutes from 22:00 to 02:00, every day.
    night = auto.check_schedule({"every": "minutes", "n": 90, "between": ["22:00", "02:00"]})
    assert night["days"] == list(auto.DAYS) and auto.describe(night, "UTC") == "every 90 minutes between 22:00 and 02:00 (UTC)"
    assert auto.next_run(night, "UTC", _at("2026-11-24 23:45")) == _at("2026-11-25 01:00")
    assert auto.next_run(night, "UTC", _at("2026-11-25 01:00")) == _at("2026-11-25 22:00")
    # Without a window it's as before: every N from now.
    assert auto.next_run({"every": "minutes", "n": 60}, "UTC", _at("2026-11-24 10:20")) == _at("2026-11-24 11:20")
    if _has_zones():                                                    # the window keeps its wall-clock hours
        la = dict(hourly)
        assert auto.next_run(la, "America/Los_Angeles", _at("2026-11-24 10:00")) == _at("2026-11-24 16:00")   # 08:00 PST
    for bad in ({"every": "hours", "n": 1, "between": ["08:00", "08:00"]}, {"every": "hours", "n": 1, "between": ["8am", "5pm"]},
                {"every": "hours", "n": 1, "between": ["08:00"]}, {"every": "hours", "n": 48, "between": ["08:00", "17:00"]},
                {"every": "hours", "n": 1, "between": ["08:00", "17:00"], "days": ["xyz"]}):
        with pytest.raises(auto.AutomationError):
            auto.check_schedule(bad)


def test_a_due_automation_is_claimed_once_and_long_missed_runs_are_skipped(conn, settings, people, directory, automations_conn):
    runs = automations_conn                     # automations.db (Phase 9)
    alice = users.get_user(conn, "alice")
    a = auto.create(runs, settings, alice, directory, {"name": "Brief", "prompt": "Brief me",
                                                       "schedule": {"every": "hours", "n": 1}})
    runs.execute("UPDATE automations SET next_run_at = '2026-10-30 08:00:00' WHERE id = ?", (a,))
    row = auto.get(runs, a)
    assert [r["id"] for r in auto.due(runs, _at("2026-10-30 08:10"))] == [a]
    assert auto.claim(runs, row, _at("2026-10-30 08:10")) is True
    assert auto.claim(runs, row, _at("2026-10-30 08:10")) is False          # the other scheduler lost
    assert auto.get(runs, a)["next_run_at"] == "2026-10-30 09:10:00"
    row = auto.get(runs, a)
    assert auto.claim(runs, row, _at("2026-10-30 12:00")) is False          # missed by hours: skipped
    assert auto.get(runs, a)["next_run_at"] == "2026-10-30 13:00:00"


def test_who_may_use_it_and_what_an_automation_may_be(conn, settings, people, directory, automations_conn):
    runs = automations_conn                     # automations.db (Phase 9)
    alice, bob, boss = (users.get_user(conn, n) for n in ("alice", "bob", "boss"))
    assert auto.may_use(conn, settings, alice, directory) and auto.may_use(conn, settings, boss, directory)
    assert not auto.may_use(conn, settings, bob, directory)
    body = {"name": "Brief", "prompt": "Brief me", "schedule": {"every": "day", "at": "07:30"}}
    with pytest.raises(auto.AutomationError, match="routine"):
        auto.create(runs, settings, alice, directory, {**body, "runner": "claude_routine"})
    with pytest.raises(auto.AutomationError, match="routine's address"):
        auto.create(runs, settings, alice, directory, {**body, "runner": "claude_routine",
                                                       "routine_url": "https://evil.example/fire", "routine_token": "sk-ant-x"})
    with pytest.raises(auto.AutomationError, match="somewhere"):
        auto.create(runs, settings, alice, directory, {**body, "deliver": {"inbox": False}})
    made = auto.create(runs, settings, alice, directory, {**body, "runner": "claude_routine", "routine_url": ROUTINE,
                                                          "routine_token": "sk-ant-oat01-secret"})
    assert apis.decrypt_secret(settings, auto.get(runs, made)["routine_token_enc"]) == "sk-ant-oat01-secret"
    assert auto.view(runs, auto.get(runs, made))["routine_token"] == "saved"
    with pytest.raises(auto.AutomationError, match="already"):
        auto.create(runs, settings, alice, directory, {**body, "name": "brief"})
    # Rooms: a Claude runner posts there only when its owner shares their access (Erynne reads what the room may).
    rooms.set_enabled(conn, True)
    room_id = rooms.create_channel(conn, alice, "ops")
    with pytest.raises(auto.AutomationError, match="Share what my access finds"):
        auto.update(runs, settings, alice, directory, made, {"deliver": {"room_id": room_id}})
    auto.set_enabled(conn, False)
    assert not auto.may_use(conn, settings, boss, directory)


# ---------------------------------------------------------------- timeline
def test_run_length_is_from_scheduled_start_to_last_message(conn, settings, people, directory, automations_conn):
    runs = automations_conn
    alice = users.get_user(conn, "alice")
    a = auto.create(runs, settings, alice, directory, {"name": "Digest", "prompt": "Digest",
                                                        "schedule": {"every": "day", "at": "07:30"}})
    row = auto.get(runs, a)
    # Scheduled for 07:30, started 07:30:20 (the scheduler's tick), finished 07:33, last message out 07:34.
    timed = auto.start_run(runs, row, "schedule", scheduled_for="2026-11-24 07:30:00")
    runs.execute("UPDATE automation_runs SET status = 'done', started_at = '2026-11-24 07:30:20', "
                 "finished_at = '2026-11-24 07:33:00', delivered_at = '2026-11-24 07:34:00' WHERE id = ?", (timed,))
    # Run now (no schedule): from its start; quiet (nothing sent): until it finished.
    quiet = auto.start_run(runs, row, "manual")
    runs.execute("UPDATE automation_runs SET status = 'quiet', started_at = '2026-11-24 09:00:00', "
                 "finished_at = '2026-11-24 09:02:00' WHERE id = ?", (quiet,))
    failed = auto.start_run(runs, row, "manual")                        # failures don't count toward the average
    runs.execute("UPDATE automation_runs SET status = 'failed', started_at = '2026-11-24 10:00:00', "
                 "finished_at = '2026-11-24 11:00:00' WHERE id = ?", (failed,))
    assert auto.run_stats(runs, a) == {"avg_seconds": 180.0, "max_seconds": 240.0, "measured": 2}
    going = auto.start_run(runs, row, "manual")                         # still running: no end yet
    runs.execute("UPDATE automation_runs SET started_at = '2026-11-24 12:00:00' WHERE id = ?", (going,))
    day = auto.timeline(runs, people["alice"], _at("2026-11-24 00:00"), _at("2026-11-25 00:00"))
    lane = day["automations"][0]
    assert [(r["run_id"], r["start"], r["end"]) for r in lane["runs"][:2]] == [
        (timed, "2026-11-24T07:30:00Z", "2026-11-24T07:34:00Z"), (quiet, "2026-11-24T09:00:00Z", "2026-11-24T09:02:00Z")]
    assert lane["runs"][-1]["run_id"] == going and lane["runs"][-1]["running"] and lane["runs"][-1]["end"] is None
    assert lane["avg_seconds"] == 180.0 and day["sample"] == auto.TIMELINE_SAMPLE


def test_planned_slots_follow_the_schedule_from_its_next_run(conn, settings, people, directory, automations_conn):
    runs = automations_conn
    alice = users.get_user(conn, "alice")
    a = auto.create(runs, settings, alice, directory, {"name": "Check", "prompt": "Check",
                                                        "schedule": {"every": "hours", "n": 4}})
    runs.execute("UPDATE automations SET next_run_at = '2026-11-24 02:00:00' WHERE id = ?", (a,))
    times = auto.planned(auto.get(runs, a), _at("2026-11-24 00:00"), _at("2026-11-25 00:00"))
    assert [t.strftime("%H:%M") for t in times] == ["02:00", "06:00", "10:00", "14:00", "18:00", "22:00"]
    runs.execute("UPDATE automations SET enabled = 0 WHERE id = ?", (a,))
    assert auto.planned(auto.get(runs, a), _at("2026-11-24 00:00"), _at("2026-11-25 00:00")) == []   # paused: none


def test_the_timeline_tab_replaces_approvals(settings, conn, people, directory):
    with _web(settings, "alice", directory) as c:
        assert c.post("/api/automations", json={"name": "Brief", "prompt": "Brief me",
                                                "schedule": {"every": "day", "at": "07:30"}}).status_code == 201
        page = c.get("/automations").text
        assert 'data-tab="timeline"' in page and 'data-tab="approvals"' not in page and 'href="/proposals"' in page
        day = c.get("/api/automations/timeline", params={"start": "2026-11-24T08:00:00.000Z", "hours": 24}).json()
        assert day["start"] == "2026-11-24T08:00:00Z" and day["end"] == "2026-11-25T08:00:00Z"
        assert day["automations"][0]["name"] == "Brief" and day["automations"][0]["measured"] == 0
        assert c.get("/api/automations/timeline", params={"start": "soon"}).status_code == 422


# ------------------------------------------------------------ prompt files
def test_a_prompt_file_gives_its_first_fenced_block_or_all_of_it():
    explained = "# Morning job\n\nWhat this does.\n\n```text\nBrief me from the file.\n```\n\n```\nnot this one\n```\n"
    assert auto.prompt_from_text(explained) == "Brief me from the file."             # the first block only
    assert auto.prompt_from_text("﻿Just do the thing.\n") == "Just do the thing."  # no block: all of it


def test_an_automation_can_follow_a_prompt_file_read_fresh_each_run(settings, conn, people, directory):
    import io
    from mnemerynne import permissions
    from mnemerynne.ingest import store

    def add(owner, name, text):                       # a file in that person's own File box
        got = store.receive(io.BytesIO(text.encode()), name, settings)
        return store.add_file(conn, settings, got, permissions.private_collection_id(conn, people[owner]),
                              people[owner])[0]

    mine = add("alice", "morning-prompt.md", "Explains the job.\n\n```\nBrief me from the file.\n```\n")
    theirs = add("bob", "bobs-prompt.md", "Bob's own instructions.")
    pdfish = add("alice", "morning-notes.csv", "a,b\n1,2\n")
    llm = AgentLLM()
    llm.turns(ToolTurn("From the file [1]."))
    with _web(settings, "alice", directory, agent_llm=llm) as c:
        # The editor's search: her own text files only (not Bob's, not a .csv).
        found = c.get("/api/automations/prompt-files", params={"q": "prompt"}).json()["files"]
        assert [f["id"] for f in found] == [mine]
        assert c.get("/api/automations/prompt-files", params={"q": "morning"}).json()["files"][0]["id"] == mine
        base = {"name": "From file", "prompt": "", "schedule": {"every": "day", "at": "07:30"}}
        for bad in (theirs, pdfish, 999999):                                # can't read it, not text, gone
            assert c.post("/api/automations", json={**base, "prompt_file_id": bad}).status_code == 422
        assert c.post("/api/automations", json=base).status_code == 422     # neither a prompt nor a file
        made = c.post("/api/automations", json={**base, "prompt_file_id": mine})
        assert made.status_code == 201, made.text
        a = made.json()
        assert a["prompt_file"] == {"id": mine, "name": "morning-prompt.md", "link": f"/files/{mine}/view",
                                    "missing": False}
        run_id = c.post(f"/api/automations/{a['id']}/run").json()["run_id"]
        run = _wait(c, run_id)
        assert run["status"] == "done", run
        prompt = llm.calls[0][0][-1]["content"]
        assert "Brief me from the file." in prompt and "Explains the job" not in prompt   # the block, not the notes
        steps = _trace_done(c, run_id)["trace"]                             # the raw output names file and version
        assert any(str(s.get("text", "")).startswith(f"Prompt: file {mine} “morning-prompt.md”, version ") for s in steps)
        # Claude sees which file it follows.
        tools = Tools(settings, lambda: FakeEmbedder(), lambda: directory)
        reader = api_tokens.verify(conn, api_tokens.create(conn, people["alice"], "r")[1])
        assert tools.list_automations(conn, reader)[0]["automations"][0]["prompt_file"]["id"] == mine
        # The file goes: the next run fails, saying so; the page shows it as gone.
        conn.execute("UPDATE files SET deleted_at = datetime('now') WHERE id = ?", (mine,))
        run_id = c.post(f"/api/automations/{a['id']}/run").json()["run_id"]
        run = _wait(c, run_id)
        assert run["status"] == "failed" and "prompt file" in run["error"]
        assert c.get("/api/automations").json()["automations"][0]["prompt_file"]["missing"] is True
        # Back to typing it: the box can't be empty then.
        assert c.patch(f"/api/automations/{a['id']}", json={"prompt_file_id": 0}).status_code == 422
        back = c.patch(f"/api/automations/{a['id']}", json={"prompt_file_id": 0, "prompt": "Brief me"})
        assert back.status_code == 200 and back.json()["prompt_file"] is None


# -------------------------------------------------------------------- runs
def test_erynne_runs_it_with_the_tools_it_picked_and_the_answer_reaches_the_inbox(settings, conn, people, directory):
    llm = AgentLLM()
    llm.turns(ToolTurn("Three things today [1]."))
    with _web(settings, "alice", directory, agent_llm=llm) as c:
        made = c.post("/api/automations", json={"name": "Morning", "prompt": "Brief me for the day",
                                                "schedule": {"every": "day", "at": "07:30"},
                                                "tools": ["search_documents"]})
        assert made.status_code == 201, made.text
        a = made.json()
        assert a["when"] == "every day at 07:30 (UTC)" and a["next_run_at"]
        assert 'href="/automations"' in c.get("/").text
        # The narrow pages carry the page width picker (_nav.html), saved apart from the file view's; Ask doesn't.
        page = c.get("/automations").text
        assert 'class="doc-width"' in page and 'data-key="erynne.pageWidth"' in page and "doc_width.js" in page
        assert 'class="doc-width"' not in c.get("/").text
        run_id = c.post(f"/api/automations/{a['id']}/run").json()["run_id"]
        run = _wait(c, run_id)
        assert run["status"] == "done" and run["output"] == "Three things today [1]." and "<p>" in run["html"]
        assert run["delivered"] == [{"to": "inbox", "ok": True}]
        note = messages.inbox(conn, people["alice"])[0]
        assert note["text"] == "Morning: Three things today ." and note["link"] == f"/automations#run={run_id}"
        prompt = llm.calls[0][0][-1]["content"]
        assert "scheduled automation, “Morning”" in prompt and "Brief me for the day" in prompt
        assert llm.calls[0][1] == ["search_documents"]                       # only what it picked
        assert c.post(f"/api/automations/{a['id']}/run").status_code == 200   # finished: may run again
        _wait(c, run_id + 1)

        # Nothing to report: nothing delivered.
        llm.turns(ToolTurn("NOTHING_TO_REPORT"))
        quiet = _wait(c, c.post(f"/api/automations/{a['id']}/run").json()["run_id"])
        assert quiet["status"] == "quiet" and quiet["delivered"] == [], quiet["output"]

    with _web(settings, "bob", directory) as c:                                # not in the group
        assert c.get("/automations").status_code == 404 and c.get("/api/automations").status_code == 404
        assert c.get(f"/api/automations/runs/{run_id}").status_code == 404
        assert 'href="/automations"' not in c.get("/").text


def test_the_scheduler_runs_what_is_due_and_pauses_what_its_owner_lost(settings, conn, people, directory, automations_conn):
    runs = automations_conn                     # automations.db (Phase 9)
    llm = AgentLLM()
    with _web(settings, "alice", directory, agent_llm=llm) as c:
        a = c.post("/api/automations", json={"name": "Hourly", "prompt": "Check", "schedule": {"every": "hours", "n": 1}}).json()
        runs.execute("UPDATE automations SET next_run_at = datetime('now', '-1 minute') WHERE id = ?", (a["id"],))
        started = c.portal.call(c.app.state.automator.tick)
        assert len(started) == 1 and _wait(c, started[0])["status"] == "done"
        assert c.portal.call(c.app.state.automator.tick) == []                # moved on an hour
        offered = llm.calls[0][1]                     # every reading tool, the briefing ones too; not the admins'
        assert {"my_briefing", "security_feeds"} <= set(offered) and "m365_security" not in offered
        assert "create_file" not in offered                                   # it may not propose
        # Alice leaves the group: the next due run pauses it and tells her.
        directory.members["alice"] = {"staff"}
        runs.execute("UPDATE automations SET next_run_at = datetime('now', '-1 minute') WHERE id = ?", (a["id"],))
        assert c.portal.call(c.app.state.automator.tick) == []
    row = auto.get(conn, a["id"])
    assert row["enabled"] == 0 and "no longer use Automator" in row["paused_reason"]
    assert "was paused" in messages.inbox(conn, people["alice"])[0]["text"]


def test_a_room_automation_posts_as_erynne_reading_only_what_the_room_may(settings, conn, people, directory):
    rooms.set_enabled(conn, True)
    alice = users.get_user(conn, "alice")
    room_id = rooms.create_channel(conn, alice, "ops")
    llm = AgentLLM()
    llm.turns(ToolTurn("All systems normal."))
    with _web(settings, "alice", directory, agent_llm=llm) as c:
        a = c.post("/api/automations", json={"name": "Ops check", "prompt": "Check the systems",
                                             "schedule": {"every": "hours", "n": 4},
                                             "deliver": {"inbox": False, "room_id": room_id}}).json()
        run = _wait(c, c.post(f"/api/automations/{a['id']}/run").json()["run_id"])
        assert run["status"] == "done" and run["delivered"] == [{"to": "room", "ok": True, "room_id": room_id}]
        with rooms.on(conn) as rooms_conn:                                  # moved to rooms.db at the app's start
            posted = rooms.history(rooms_conn, alice, room_id, directory)[-1]
        assert posted["who"] == "Erynne" and posted["body"].startswith("**Ops check**") and "All systems normal." in posted["body"]
        assert set(llm.calls[0][1]) <= {"search_documents", "read_passage", "list_libraries", "browse_folder",
                                        "find_files", "open_file", "list_versions"}      # the room's reading tools


def test_a_claude_routine_is_fired_and_its_report_is_delivered(settings, conn, people, directory):
    fired, hooks = [], []

    async def handler(request):
        if request.url.host == "api.anthropic.com":
            fired.append(request)
            return httpx.Response(200, json={"type": "routine_fire", "claude_code_session_id": "s1",
                                             "claude_code_session_url": "https://claude.ai/code/s1"})
        hooks.append(request)
        return httpx.Response(200 if len(hooks) > 1 else 503)
    automations_run.TRANSPORT = httpx.MockTransport(handler)

    with _web(settings, "boss", directory) as c:
        c.post("/api/admin/automations/destinations", json={"name": "Teams", "url": "https://hooks.example/t",
                                                            "format": "teams", "secret": "hush"})
        hook_id = c.get("/api/admin/automations").json()["destinations"][0]["id"]
    with _web(settings, "alice", directory) as c:
        a = c.post("/api/automations", json={"name": "Digest", "prompt": "Security digest please",
                                             "schedule": {"every": "day", "at": "08:00"}, "runner": "claude_routine",
                                             "routine_url": ROUTINE, "routine_token": "sk-ant-oat01-secret",
                                             "deliver": {"inbox": True, "destinations": [hook_id]}})
        assert a.status_code == 201, a.text
        run_id = c.post(f"/api/automations/{a.json()['id']}/run").json()["run_id"]
        run = _wait(c, run_id)
        assert run["status"] == "waiting" and run["claude_session_url"] == "https://claude.ai/code/s1"
        sent = fired[0]
        assert sent.headers["authorization"] == "Bearer sk-ant-oat01-secret" and sent.headers["anthropic-version"]
        text = json.loads(sent.content)["text"]
        assert f"run {run_id}" in text and "Security digest please" in text and "report_automation_run" in text

        tools = Tools(settings, lambda: FakeEmbedder(), lambda: directory)
        reader = api_tokens.verify(conn, api_tokens.create(conn, people["alice"], "r")[1])
        editor = api_tokens.verify(conn, api_tokens.create(conn, people["alice"], "e", can_edit=True)[1])
        bob = api_tokens.verify(conn, api_tokens.create(conn, people["bob"], "b", can_edit=True)[1])
        assert tools.list_automation_tasks(conn, editor)[0]["tasks"][0]["run_id"] == run_id
        with pytest.raises(Exception, match="isn't turned on"):
            tools.report_automation_run(conn, bob, run_id, "mine now")
        out = c.portal.call(asyncio.to_thread, tools.report_automation_run, conn, reader, run_id, "Patch the VPN [1].")
        assert out[0]["status"] == "done"
        with pytest.raises(Exception, match="isn't waiting"):
            tools.report_automation_run(conn, editor, run_id, "again")
        run = c.get(f"/api/automations/runs/{run_id}").json()
        assert run["output"] == "Patch the VPN [1]." and run["model"] == "claude routine"
        assert {d["to"]: d["ok"] for d in run["delivered"]} == {"inbox": True, "Teams": True}
        body = hooks[-1].content
        assert len(hooks) == 2                                                 # a 503, then tried again
        expected = hmac.new(b"hush", body, hashlib.sha256).hexdigest()
        assert hooks[-1].headers["x-mnemerynne-signature"] == f"sha256={expected}"
        assert json.loads(body)["attachments"][0]["content"]["body"][0]["text"] == "Digest"


def test_a_routine_that_never_reports_times_out(settings, conn, people, directory, automations_conn):
    runs = automations_conn                     # automations.db (Phase 9)
    alice = users.get_user(conn, "alice")
    a = auto.create(runs, settings, alice, directory, {"name": "Slow", "prompt": "x", "schedule": {"every": "hours", "n": 1}})
    run_id = auto.start_run(runs, auto.get(runs, a), "manual")
    runs.execute("UPDATE automation_runs SET status = 'waiting', started_at = datetime('now', '-3 hours') WHERE id = ?",
                 (run_id,))
    assert auto.expire_waiting(runs, auto.now()) == 1
    assert auto.get_run(runs, run_id)["status"] == "timed_out"


def test_admins_turn_it_on_and_off_and_pause_any_automation(settings, conn, people, directory, automations_conn):
    runs = automations_conn                     # automations.db (Phase 9)
    alice = users.get_user(conn, "alice")
    a = auto.create(runs, settings, alice, directory, {"name": "Brief", "prompt": "x", "schedule": {"every": "hours", "n": 1}})
    if runs.in_transaction:
        runs.commit()
    with _web(settings, "alice", directory) as c:
        assert c.get("/api/admin/automations").status_code == 403
    with _web(settings, "boss", directory) as c:
        status = c.get("/api/admin/automations").json()
        assert status["enabled"] and [x["name"] for x in status["automations"]] == ["Brief"]
        assert c.post("/api/admin/automations/destinations", json={"name": "x", "url": "http://plain", "format": "json"}).status_code == 422
        assert c.post("/api/admin/automations/feeds", json={"name": "KEV", "kind": "json_kev",
                                                            "url": "https://www.cisa.gov/kev.json"}).status_code == 201
        c.post(f"/api/admin/automations/{a}/pause")
        assert c.put("/api/admin/automations", json={"enabled": False}).json()["enabled"] is False
    assert auto.get(conn, a)["enabled"] == 0
    with _web(settings, "alice", directory) as c:
        assert c.get("/api/automations").status_code == 404


# ------------------------------------------------- raw output, live, and auto-approval
def _trace_done(c, run_id):
    """The run, once its trace has its last step (saved a moment after the run's own status)."""
    for _ in range(200):
        run = c.get(f"/api/automations/runs/{run_id}").json()
        if run["trace"] and run["trace"][-1]["kind"] in ("finish", "error"):
            return run
        c.portal.call(asyncio.sleep, 0.05)
    raise AssertionError(f"run {run_id}'s trace never finished: {run['trace']}")


def _sse(text):
    """Server-sent events as (event, data) pairs."""
    out = []
    for block in text.split("\n\n"):
        kind = next((line[7:] for line in block.splitlines() if line.startswith("event: ")), None)
        data = next((line[6:] for line in block.splitlines() if line.startswith("data: ")), None)
        if kind:
            out.append((kind, json.loads(data)))
    return out


def test_a_runs_raw_output_is_kept_streamed_and_its_proposals_approve_themselves(settings, conn, people, directory):
    llm = AgentLLM()
    with _web(settings, "alice", directory, agent_llm=llm) as c:
        base = {"name": "Notes", "prompt": "Keep my notes", "schedule": {"every": "day", "at": "07:30"}}
        refused = c.post("/api/automations", json={**base, "auto_approve": True})           # nothing to approve
        assert refused.status_code == 422 and "May propose" in refused.json()["detail"], refused.text
        a = c.post("/api/automations", json={**base, "may_propose": True, "auto_approve": True}).json()
        assert a["auto_approve"] and a["may_propose"]

        llm.turns(ToolTurn("Writing it down.", [ToolCall("c1", "create_file",
                                                         {"name": "notes.md", "content": "# Notes\nhello\n"})]),
                  ToolTurn("Wrote notes.md."))
        run_id = c.post(f"/api/automations/{a['id']}/run").json()["run_id"]
        run = _trace_done(c, run_id)
        assert run["status"] == "done" and len(run["proposals"]) == 1
        pid = run["proposals"][0]
        applied = conn.execute("SELECT status, decision_note FROM agent_proposals WHERE id = ?", (pid,)).fetchone()
        assert applied["status"] == "applied" and f"Automator (run {run_id})" in applied["decision_note"]
        assert conn.execute("SELECT count(*) FROM files WHERE display_name = 'notes.md'").fetchone()[0] == 1

        # The raw output: every step, numbered, with the tool's arguments and its raw result.
        trace = run["trace"]
        kinds = [s["kind"] for s in trace]
        assert kinds[0] == "start" and kinds[1] == "prompt" and kinds[-1] == "finish", kinds
        assert [s["n"] for s in trace] == list(range(1, len(trace) + 1))
        assert "Keep my notes" in trace[1]["text"]
        assert any(s["kind"] == "note" and s["text"] == "Writing it down." for s in trace)        # the model's own words
        calls = [s for s in trace if s["kind"] == "tool"]
        assert calls[0]["state"] == "running" and calls[0]["arguments"]["name"] == "notes.md"
        assert calls[1]["state"] == "done" and "proposal" in json.dumps(calls[1]["result"])
        assert [s["ok"] for s in trace if s["kind"] == "approval"] == [True]
        assert {"kind": "delivered", "to": "inbox", "ok": True}.items() <= next(
            s for s in trace if s["kind"] == "delivered").items()

        # Live: the stream sends each step, then the whole run; `after` skips what the page already has.
        streamed = _sse(c.get(f"/api/automations/runs/{run_id}/stream").text)
        assert [d["n"] for k, d in streamed if k == "step"] == [s["n"] for s in trace]
        assert streamed[-1][0] == "end" and streamed[-1][1]["output"] == "Wrote notes.md."
        later = _sse(c.get(f"/api/automations/runs/{run_id}/stream?after=3").text)
        assert [d["n"] for k, d in later if k == "step"][0] == 4

        # The Approvals tab: by run, with what was decided.
        shown = c.get("/api/automations/approvals").json()
        assert shown["pending"] == 0 and shown["approvals"][0]["run_id"] == run_id
        assert shown["approvals"][0]["proposal"]["status"] == "applied" and shown["approvals"][0]["auto_approve"]

        # Auto-approval off: the next proposal waits on Approvals, applied by hand from there.
        assert c.patch(f"/api/automations/{a['id']}", json={"auto_approve": False}).status_code == 200
        llm.turns(ToolTurn("", [ToolCall("c2", "create_file", {"name": "more.md", "content": "more"})]),
                  ToolTurn("Drafted more.md."))
        second = _trace_done(c, c.post(f"/api/automations/{a['id']}/run").json()["run_id"])
        assert not any(s["kind"] == "approval" for s in second["trace"])
        shown = c.get("/api/automations/approvals").json()
        assert shown["pending"] == 1 and shown["approvals"][0]["proposal"]["status"] == "pending"
        waiting = shown["approvals"][0]["proposal"]["id"]
        assert c.post(f"/api/agent/proposals/{waiting}/apply").json()["status"] == "applied"

        # Switching proposing off takes auto-approval with it.
        c.patch(f"/api/automations/{a['id']}", json={"auto_approve": True})
        c.patch(f"/api/automations/{a['id']}", json={"may_propose": False})
        now = next(x for x in c.get("/api/automations").json()["automations"] if x["id"] == a["id"])
        assert not now["may_propose"] and not now["auto_approve"]

    with _web(settings, "bob", directory) as c:                                # not theirs
        assert c.get(f"/api/automations/runs/{run_id}/stream").status_code == 404


def test_an_admin_approval_is_never_auto_approved(settings, conn, people, directory):
    """A proposal that waits for a Mnemerynne admin stays pending, and the trace says why."""
    llm = AgentLLM()
    with _web(settings, "alice", directory, agent_llm=llm) as c:
        a = c.post("/api/automations", json={"name": "Notes", "prompt": "Keep my notes", "may_propose": True,
                                             "auto_approve": True, "schedule": {"every": "day", "at": "07:30"}}).json()
        llm.turns(ToolTurn("", [ToolCall("c1", "create_file", {"name": "n.md", "content": "n"})]), ToolTurn("Done."))
        real = automations_run.Scheduler._approve_all

        def as_if_admin(self, run_id):                                     # the proposal turned out to need an admin
            conn.execute("UPDATE agent_proposals SET approver = 'admin'")
            return real(self, run_id)
        automations_run.Scheduler._approve_all = as_if_admin
        try:
            run = _trace_done(c, c.post(f"/api/automations/{a['id']}/run").json()["run_id"])
        finally:
            automations_run.Scheduler._approve_all = real
        step = next(s for s in run["trace"] if s["kind"] == "approval")
        assert step["ok"] is False and "admin" in step["error"]
        assert conn.execute("SELECT status FROM agent_proposals").fetchone()[0] == "pending"


def test_a_claude_routine_posts_in_a_room_only_when_its_owner_shares_their_access(settings, conn, people, directory):
    """Claude works with the owner's access, so a room only gets its report when they tick the share box (Doll's
    choice, 2026-11-18); the post says whose access it used."""
    rooms.set_enabled(conn, True)
    alice = users.get_user(conn, "alice")
    room_id = rooms.create_channel(conn, alice, "ops")

    async def handler(request):
        return httpx.Response(200, json={"claude_code_session_url": "https://claude.ai/code/s2"})
    automations_run.TRANSPORT = httpx.MockTransport(handler)
    with _web(settings, "alice", directory) as c:
        body = {"name": "Audit", "prompt": "Audit Wazuh", "schedule": {"every": "day", "at": "07:30"},
                "runner": "claude_routine", "routine_url": ROUTINE, "routine_token": "sk-ant-oat01-secret",
                "deliver": {"inbox": False, "room_id": room_id}}
        refused = c.post("/api/automations", json=body)
        assert refused.status_code == 422 and "Share what my access finds" in refused.json()["detail"], refused.text
        made = c.post("/api/automations", json={**body, "deliver": {**body["deliver"], "room_share_ack": True}})
        assert made.status_code == 201, made.text
        a = made.json()
        assert a["deliver"] == {"inbox": False, "room_id": room_id, "room_share": True}
        assert c.patch(f"/api/automations/{a['id']}", json={"name": "Daily audit"}).status_code == 200   # kept

        run_id = c.post(f"/api/automations/{a['id']}/run").json()["run_id"]
        assert _wait(c, run_id)["status"] == "waiting"
        tools = Tools(settings, lambda: FakeEmbedder(), lambda: directory)
        reader = api_tokens.verify(conn, api_tokens.create(conn, people["alice"], "r")[1])
        out = c.portal.call(asyncio.to_thread, tools.report_automation_run, conn, reader, run_id, "Two hosts need patches.")
        assert out[0]["status"] == "done" and {"to": "room", "ok": True, "room_id": room_id} in out[0]["delivered"]
        with rooms.on(conn) as rooms_conn:
            posted = rooms.history(rooms_conn, alice, room_id, directory)[-1]
        assert posted["body"].startswith("**Daily audit** · _run by Claude with alice's access, shared by them_")
        assert "Two hosts need patches." in posted["body"]
