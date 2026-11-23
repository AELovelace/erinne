"""Cancelling an automation run: queued, running, waiting for a Claude routine, already over, and a report that
arrives after the cancel. A cancelled run ends as 'cancelled' (never 'failed'), nothing is delivered, and nothing
that comes later turns it back into done."""

import asyncio

import httpx
import pytest

from fakes import FakeEmbedder
from mnemerynne import api_tokens, automations as auto, automations_run, db, messages, users
from mnemerynne.clients.llm import ToolTurn
from mnemerynne.mcp_server import Tools
from test_agent import AgentLLM
from test_automations import ROUTINE, _hooks, _wait, _web, directory, people  # noqa: F401


class SlowLLM(AgentLLM):
    """The agent model, taking its time: every call waits `delay` seconds first (so a run is still going when
    the test cancels it). `started` counts the calls that began."""

    def __init__(self, delay: float = 30.0):
        super().__init__()
        self.delay, self.started = delay, 0

    async def chat_tools(self, messages, tools, **kwargs):
        self.started += 1                                  # the run reached the model: it's really running
        await asyncio.sleep(self.delay)                    # a cancel lands here
        return await super().chat_tools(messages, tools, **kwargs)


def _until(c, check, what):
    """Wait (on the app's loop) until check() is true."""
    for _ in range(200):
        if check():
            return
        c.portal.call(asyncio.sleep, 0.05)
    raise AssertionError(f"never: {what}")


def _token(conn, user_id, name, can_edit=True):
    return api_tokens.verify(conn, api_tokens.create(conn, user_id, name, can_edit=can_edit)[1])


def test_a_queued_run_is_cancelled_and_never_started(settings, conn, people, directory, automations_conn):
    llm = AgentLLM()
    with _web(settings, "alice", directory, agent_llm=llm) as c:
        made = c.post("/api/automations", json={"name": "Audit", "prompt": "Check the night audit",
                                                "schedule": {"every": "day", "at": "07:30"}}).json()
        scheduler, automations_run.ACTIVE = automations_run.ACTIVE, None   # another process: it's queued
        with db.transaction(automations_conn):
            run_id = automations_run.start(automations_conn, auto.get(automations_conn, made["id"]), "manual",
                                           settings, directory)
        assert auto.get_run(automations_conn, run_id)["status"] == "queued"
        listed = c.get("/api/automations").json()["automations"][0]
        assert listed["running"] is True                                # the page offers Cancel

        got = c.post(f"/api/automations/runs/{run_id}/cancel")
        assert got.status_code == 200, got.text
        run = got.json()
        assert run["status"] == "cancelled" and run["finished_at"] and run["error"] == "Cancelled by alice."
        assert run["trace"][-1]["kind"] == "cancelled"

        automations_run.ACTIVE = scheduler
        assert c.portal.call(scheduler.tick) == []                      # the scheduler never claims it
        assert auto.get_run(automations_conn, run_id)["status"] == "cancelled"
        assert llm.calls == []                                          # the model was never asked
        assert c.get("/api/automations").json()["automations"][0]["running"] is False
        again = c.post(f"/api/automations/{made['id']}/run")             # cancelled isn't "running already"
        assert again.status_code == 200, again.text
    assert messages.inbox(conn, people["alice"]) == []                  # not delivered, not "failed"


def test_a_running_run_is_stopped_and_ends_cancelled_not_failed(settings, conn, people, directory):
    llm = SlowLLM()
    with _web(settings, "alice", directory, agent_llm=llm) as c:
        scheduler = c.app.state.automator
        made = c.post("/api/automations", json={"name": "Digest", "prompt": "Security digest",
                                                "schedule": {"every": "day", "at": "07:30"}}).json()
        run_id = c.post(f"/api/automations/{made['id']}/run").json()["run_id"]
        _until(c, lambda: llm.started == 1, "the run reached the model")
        assert run_id in scheduler.tasks                                # its task is going

        with _web(settings, "bob", directory) as other:                 # not theirs: not even there
            assert other.post(f"/api/automations/runs/{run_id}/cancel").status_code == 404
        automations_run.ACTIVE = scheduler                              # bob's app took (and dropped) the one scheduler
        got = c.post(f"/api/automations/runs/{run_id}/cancel")
        assert got.status_code == 200 and got.json()["status"] == "cancelled", got.text
        _until(c, lambda: run_id not in scheduler.tasks, "the run's task stopped")   # really stopped, not left

        run = _wait(c, run_id, done=("cancelled",))
        assert run["status"] == "cancelled" and run["output"] == "" and run["delivered"] == []
        assert run["trace"][-1]["kind"] == "cancelled"                  # the raw output ends on the stop
        assert c.post(f"/api/automations/runs/{run_id}/cancel").status_code == 409   # once
    assert messages.inbox(conn, people["alice"]) == []                  # no "failed" note, nothing delivered


def test_a_cancel_made_in_another_process_stops_the_run_at_the_next_tick(settings, conn, people, directory,
                                                                         automations_conn):
    llm = SlowLLM()
    with _web(settings, "alice", directory, agent_llm=llm) as c:
        scheduler = c.app.state.automator
        made = c.post("/api/automations", json={"name": "Digest", "prompt": "x",
                                                "schedule": {"every": "day", "at": "07:30"}}).json()
        run_id = c.post(f"/api/automations/{made['id']}/run").json()["run_id"]
        _until(c, lambda: llm.started == 1, "the run reached the model")
        automations_run.ACTIVE = None                                   # the mcp service: only the row changes
        with db.transaction(automations_conn):
            assert automations_run.cancel(automations_conn, run_id, "alice")
        automations_run.ACTIVE = scheduler
        assert run_id in scheduler.tasks                                # still going here...
        c.portal.call(scheduler.tick)                                   # ...until the scheduler looks (or is woken)
        _until(c, lambda: run_id not in scheduler.tasks, "the run's task stopped")
        assert auto.get_run(automations_conn, run_id)["status"] == "cancelled"


def test_a_finished_run_cant_be_cancelled(settings, conn, people, directory, automations_conn):
    llm = AgentLLM()
    llm.turns(ToolTurn("All quiet."))
    with _web(settings, "alice", directory, agent_llm=llm) as c:
        made = c.post("/api/automations", json={"name": "Brief", "prompt": "Brief me",
                                                "schedule": {"every": "day", "at": "07:30"}}).json()
        run_id = c.post(f"/api/automations/{made['id']}/run").json()["run_id"]
        assert _wait(c, run_id)["status"] == "done"
        refused = c.post(f"/api/automations/runs/{run_id}/cancel")
        assert refused.status_code == 409 and "already ended (done)" in refused.json()["detail"]
        tools = Tools(settings, lambda: FakeEmbedder(), lambda: directory)
        with pytest.raises(Exception, match="already ended"):
            tools.cancel_automation_run(conn, _token(conn, people["alice"], "e"), run_id)
        run = c.get(f"/api/automations/runs/{run_id}").json()
        assert run["status"] == "done" and run["output"] == "All quiet."   # untouched
    with db.transaction(automations_conn):
        assert automations_run.cancel(automations_conn, run_id, "alice") is False


def test_a_routines_late_report_after_a_cancel_is_refused_and_the_run_stays_cancelled(settings, conn, people,
                                                                                        directory, automations_conn):
    async def handler(request):
        return httpx.Response(200, json={"claude_code_session_url": "https://claude.ai/code/s9"})
    automations_run.TRANSPORT = httpx.MockTransport(handler)
    with _web(settings, "alice", directory) as c:
        made = c.post("/api/automations", json={"name": "Morning Security Audit", "prompt": "Audit",
                                                "schedule": {"every": "day", "at": "07:30"},
                                                "runner": "claude_routine", "routine_url": ROUTINE,
                                                "routine_token": "sk-ant-oat01-secret"}).json()
        run_id = c.post(f"/api/automations/{made['id']}/run").json()["run_id"]
        assert _wait(c, run_id)["status"] == "waiting"
        tools = Tools(settings, lambda: FakeEmbedder(), lambda: directory)
        editor, reader = _token(conn, people["alice"], "e"), _token(conn, people["alice"], "r", can_edit=False)
        bob = _token(conn, people["bob"], "b")
        with pytest.raises(Exception, match="can only search"):            # as starting one: needs edit
            tools.cancel_automation_run(conn, reader, run_id)
        with pytest.raises(Exception, match="isn't turned on"):            # bob may not use Automator
            tools.cancel_automation_run(conn, bob, run_id)

        out, _ = tools.cancel_automation_run(conn, editor, run_id)          # Claude cancels it for alice
        assert out["status"] == "cancelled"
        assert tools.list_automation_tasks(conn, editor)[0]["tasks"] == []  # no longer waiting for Claude
        with pytest.raises(Exception, match="was cancelled"):               # the routine reports late: refused
            c.portal.call(asyncio.to_thread, tools.report_automation_run, conn, reader, run_id, "Patch the VPN.")
        with pytest.raises(Exception, match="was cancelled"):
            tools.report_automation_run(conn, reader, run_id, "It broke.", "failed")
        run = tools.get_automation_run(conn, reader, run_id)[0]
        assert run["status"] == "cancelled" and run["output"] == "" and "alice (through Claude)" in run["error"]

        # A report that slipped in just before the cancel (claimed, then cancelled while delivering) stays dropped.
        assert auto.finish_run(automations_conn, run_id, "done", output="late") is False
        out = c.portal.call(c.app.state.automator.complete, run_id, "Late answer.", [], "claude routine")
        assert out == {"status": "cancelled", "delivered": []}
        assert auto.get_run(automations_conn, run_id)["status"] == "cancelled"
    assert messages.inbox(conn, people["alice"]) == []                   # nothing delivered


def test_an_admin_cancels_anyones_running_run_and_the_owner_is_told(settings, conn, people, directory,
                                                                    automations_conn):
    llm = SlowLLM()
    with _web(settings, "alice", directory, agent_llm=llm) as c:
        scheduler = c.app.state.automator
        made = c.post("/api/automations", json={"name": "Digest", "prompt": "x",
                                                "schedule": {"every": "day", "at": "07:30"}}).json()
        run_id = c.post(f"/api/automations/{made['id']}/run").json()["run_id"]
        _until(c, lambda: llm.started == 1, "the run reached the model")
        assert c.post(f"/api/admin/automations/{made['id']}/cancel").status_code == 403   # alice isn't an admin
        boss = users.get_user(conn, "boss")
        c.cookies.clear()
        c.post("/login", data={"username": boss["username"], "password": "password123"})
        listed = c.get("/api/admin/automations").json()["automations"]
        assert listed[0]["running_run_id"] == run_id                    # the admin page offers Cancel run
        got = c.post(f"/api/admin/automations/{made['id']}/cancel")
        assert got.status_code == 200, got.text
        assert got.json()["automations"][0]["running_run_id"] is None
        _until(c, lambda: run_id not in scheduler.tasks, "the run's task stopped")
        assert c.post(f"/api/admin/automations/{made['id']}/cancel").status_code == 409   # nothing going now
    run = auto.get_run(automations_conn, run_id)
    assert run["status"] == "cancelled" and run["error"] == "Cancelled by an admin (boss)."
    assert "An admin (boss) cancelled" in messages.inbox(conn, people["alice"])[0]["text"]
