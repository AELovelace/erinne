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
from mnemerynne.clients.llm import ToolTurn
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
    for _ in range(200):
        run = c.get(f"/api/automations/runs/{run_id}").json()
        if run["status"] in done:
            return run
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


def test_a_due_automation_is_claimed_once_and_long_missed_runs_are_skipped(conn, settings, people, directory):
    alice = users.get_user(conn, "alice")
    a = auto.create(conn, settings, alice, directory, {"name": "Brief", "prompt": "Brief me",
                                                       "schedule": {"every": "hours", "n": 1}})
    conn.execute("UPDATE automations SET next_run_at = '2026-10-30 08:00:00' WHERE id = ?", (a,))
    row = auto.get(conn, a)
    assert [r["id"] for r in auto.due(conn, _at("2026-10-30 08:10"))] == [a]
    assert auto.claim(conn, row, _at("2026-10-30 08:10")) is True
    assert auto.claim(conn, row, _at("2026-10-30 08:10")) is False          # the other scheduler lost
    assert auto.get(conn, a)["next_run_at"] == "2026-10-30 09:10:00"
    row = auto.get(conn, a)
    assert auto.claim(conn, row, _at("2026-10-30 12:00")) is False          # missed by hours: skipped
    assert auto.get(conn, a)["next_run_at"] == "2026-10-30 13:00:00"


def test_who_may_use_it_and_what_an_automation_may_be(conn, settings, people, directory):
    alice, bob, boss = (users.get_user(conn, n) for n in ("alice", "bob", "boss"))
    assert auto.may_use(conn, settings, alice, directory) and auto.may_use(conn, settings, boss, directory)
    assert not auto.may_use(conn, settings, bob, directory)
    body = {"name": "Brief", "prompt": "Brief me", "schedule": {"every": "day", "at": "07:30"}}
    with pytest.raises(auto.AutomationError, match="routine"):
        auto.create(conn, settings, alice, directory, {**body, "runner": "claude_routine"})
    with pytest.raises(auto.AutomationError, match="routine's address"):
        auto.create(conn, settings, alice, directory, {**body, "runner": "claude_routine",
                                                       "routine_url": "https://evil.example/fire", "routine_token": "sk-ant-x"})
    with pytest.raises(auto.AutomationError, match="somewhere"):
        auto.create(conn, settings, alice, directory, {**body, "deliver": {"inbox": False}})
    made = auto.create(conn, settings, alice, directory, {**body, "runner": "claude_routine", "routine_url": ROUTINE,
                                                          "routine_token": "sk-ant-oat01-secret"})
    assert apis.decrypt_secret(settings, auto.get(conn, made)["routine_token_enc"]) == "sk-ant-oat01-secret"
    assert auto.view(conn, auto.get(conn, made))["routine_token"] == "saved"
    with pytest.raises(auto.AutomationError, match="already"):
        auto.create(conn, settings, alice, directory, {**body, "name": "brief"})
    # Rooms: only Erynne posts there.
    rooms.set_enabled(conn, True)
    room_id = rooms.create_channel(conn, alice, "ops")
    with pytest.raises(auto.AutomationError, match="Only Erynne"):
        auto.update(conn, settings, alice, directory, made, {"deliver": {"room_id": room_id}})
    auto.set_enabled(conn, False)
    assert not auto.may_use(conn, settings, boss, directory)


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


def test_the_scheduler_runs_what_is_due_and_pauses_what_its_owner_lost(settings, conn, people, directory):
    llm = AgentLLM()
    with _web(settings, "alice", directory, agent_llm=llm) as c:
        a = c.post("/api/automations", json={"name": "Hourly", "prompt": "Check", "schedule": {"every": "hours", "n": 1}}).json()
        conn.execute("UPDATE automations SET next_run_at = datetime('now', '-1 minute') WHERE id = ?", (a["id"],))
        started = c.portal.call(c.app.state.automator.tick)
        assert len(started) == 1 and _wait(c, started[0])["status"] == "done"
        assert c.portal.call(c.app.state.automator.tick) == []                # moved on an hour
        offered = llm.calls[0][1]                     # every reading tool, the briefing ones too; not the admins'
        assert {"my_briefing", "security_feeds"} <= set(offered) and "m365_security" not in offered
        assert "create_file" not in offered                                   # it may not propose
        # Alice leaves the group: the next due run pauses it and tells her.
        directory.members["alice"] = {"staff"}
        conn.execute("UPDATE automations SET next_run_at = datetime('now', '-1 minute') WHERE id = ?", (a["id"],))
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
        posted = rooms.history(conn, alice, room_id, directory)[-1]
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


def test_a_routine_that_never_reports_times_out(settings, conn, people, directory):
    alice = users.get_user(conn, "alice")
    a = auto.create(conn, settings, alice, directory, {"name": "Slow", "prompt": "x", "schedule": {"every": "hours", "n": 1}})
    run_id = auto.start_run(conn, auto.get(conn, a), "manual")
    conn.execute("UPDATE automation_runs SET status = 'waiting', started_at = datetime('now', '-3 hours') WHERE id = ?",
                 (run_id,))
    assert auto.expire_waiting(conn, auto.now()) == 1
    assert auto.get_run(conn, run_id)["status"] == "timed_out"


def test_admins_turn_it_on_and_off_and_pause_any_automation(settings, conn, people, directory):
    alice = users.get_user(conn, "alice")
    a = auto.create(conn, settings, alice, directory, {"name": "Brief", "prompt": "x", "schedule": {"every": "hours", "n": 1}})
    if conn.in_transaction:
        conn.commit()
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
