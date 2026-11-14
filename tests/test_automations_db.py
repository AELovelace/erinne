"""automations.db and the automations service (docs/plans/services-rewrite.md, Phase 9): Automator's tables live in a
file of their own, and its scheduler runs in exactly one process. A run started in another process (the mcp service,
the web app while the automations service runs) is queued for it, and a Claude routine's report saved there is
delivered by it."""

import sqlite3

import pytest
from fastapi.testclient import TestClient

from fakes import FakeEmbedder
from mnemerynne import api_tokens, automations as auto, automations_run, db, messages, users
from mnemerynne.clients.llm import ToolTurn
from mnemerynne.mcp_server import Tools
from mnemerynne.services import nginx, registry
from mnemerynne.web.app import Services, create_role_app
from test_agent import AgentLLM
from test_automations import _hooks, _wait, _web, directory, people  # noqa: F401


def test_automators_tables_move_out_of_core_and_go_with_their_owner(settings):
    core = db.connect(settings.db_path, features=False)                 # a box from before Phase 9
    db.migrate(core, settings.embed_dim)
    alice = users.create_user(core, "alice", "password123")
    core.execute("INSERT INTO automations(owner_id, name, prompt, schedule) VALUES (?, 'Brief', 'x', '{}')", (alice,))
    core.execute("INSERT INTO automation_runs(automation_id, trigger, status) VALUES (1, 'manual', 'done')")
    core.execute("INSERT INTO automation_feeds(name, url, kind) VALUES ('KEV', 'https://k', 'json_kev')")
    core.execute("INSERT INTO automation_destinations(name, url) VALUES ('Teams', 'https://t')")
    core.close()

    db.migrate_all(settings.db_path, settings.embed_dim)
    conn = db.connect(settings.db_path)
    tables = {r[0] for r in conn.execute("SELECT name FROM main.sqlite_master WHERE type = 'table'")}
    for table in ("automations", "automation_runs", "automation_feeds", "automation_destinations"):
        assert table not in tables and conn.execute(f"SELECT count(*) FROM automations.{table}").fetchone()[0] == 1
    with pytest.raises(sqlite3.OperationalError, match="readonly"):
        conn.execute("DELETE FROM automations")                         # writes go through automations.db's own
    assert users.delete_user(conn, "alice")
    assert conn.execute("SELECT count(*) FROM automations").fetchone()[0] == 0        # with their owner
    assert conn.execute("SELECT count(*) FROM automation_runs").fetchone()[0] == 0    # and its runs
    assert conn.execute("SELECT count(*) FROM automation_feeds").fetchone()[0] == 1   # the admins' choices stay
    conn.close()


def test_a_run_started_where_there_is_no_scheduler_is_queued_and_the_scheduler_runs_it(settings, conn, people,
                                                                                       directory, automations_conn):
    llm = AgentLLM()
    llm.turns(ToolTurn("All quiet on the night audit [1]."))
    with _web(settings, "alice", directory, agent_llm=llm) as c:
        made = c.post("/api/automations", json={"name": "Audit", "prompt": "Check the night audit",
                                                "schedule": {"every": "day", "at": "07:30"}}).json()
        scheduler, automations_run.ACTIVE = automations_run.ACTIVE, None   # another process: no scheduler here
        with db.transaction(automations_conn):
            run_id = automations_run.start(automations_conn, auto.get(automations_conn, made["id"]), "mcp",
                                           settings, directory)
        assert auto.get_run(automations_conn, run_id)["status"] == "queued"
        with pytest.raises(auto.AutomationError, match="running already"):   # queued counts as running
            automations_run.start(automations_conn, auto.get(automations_conn, made["id"]), "mcp", settings, directory)
        automations_run.ACTIVE = scheduler
        assert c.portal.call(scheduler.tick) == [run_id]                # its process claims it
        run = _wait(c, run_id)
        assert run["status"] == "done" and run["output"] == "All quiet on the night audit [1]."
        assert c.portal.call(scheduler.tick) == []                      # once
    assert messages.inbox(conn, people["alice"])[0]["text"].startswith("Audit: All quiet")


def test_a_routines_report_from_claude_in_another_process_is_delivered_by_the_scheduler(settings, conn, people,
                                                                                        directory, automations_conn):
    alice = users.get_user(conn, "alice")
    made = auto.create(automations_conn, settings, alice, directory, {"name": "Digest", "prompt": "x",
                                                                      "schedule": {"every": "day", "at": "07:30"}})
    run_id = auto.start_run(automations_conn, auto.get(automations_conn, made), "manual")
    automations_conn.execute("UPDATE automation_runs SET status = 'waiting' WHERE id = ?", (run_id,))
    caller = api_tokens.verify(conn, api_tokens.create(conn, people["alice"], "routine", can_edit=True)[1])
    tools = Tools(settings, lambda: FakeEmbedder(), lambda: directory)
    with _web(settings, "alice", directory) as c:
        scheduler, automations_run.ACTIVE = automations_run.ACTIVE, None   # Claude reached the mcp service
        out, _ = tools.report_automation_run(conn, caller, run_id, "Two new advisories.")
        assert out["status"] == "reported"
        assert auto.get_run(automations_conn, run_id)["status"] == "reported"
        with pytest.raises(Exception, match="isn.t waiting for a report"):   # a second report can't deliver twice
            tools.report_automation_run(conn, caller, run_id, "Again.")
        automations_run.ACTIVE = scheduler
        c.portal.call(scheduler.tick)                                   # the scheduler delivers it
        run = _wait(c, run_id, done=("done", "quiet", "failed"))
        assert run["status"] == "done" and run["delivered"] == [{"to": "inbox", "ok": True}]
    assert messages.inbox(conn, people["alice"])[0]["text"] == "Digest: Two new advisories."


def test_with_the_automations_service_running_the_web_app_starts_no_scheduler(settings, conn, people, directory,
                                                                              automations_conn):
    elsewhere = settings.model_copy(update={"services_on": "automations"})
    with _web(elsewhere, "alice", directory) as c:
        assert c.app.state.automator is None and automations_run.ACTIVE is None
        made = c.post("/api/automations", json={"name": "Brief", "prompt": "x",
                                                "schedule": {"every": "day", "at": "07:30"}}).json()
        run_id = c.post(f"/api/automations/{made['id']}/run").json()["run_id"]   # the page's "Run now"
        assert auto.get_run(automations_conn, run_id)["status"] == "queued"      # for the service to claim


def test_the_automations_process_holds_the_one_scheduler_and_serves_automators_api(settings, conn, people, directory):
    db.migrate_all(settings.db_path, settings.embed_dim)
    with _web(settings, "alice", directory) as web:                     # signed in on the web app...
        cookies = dict(web.cookies)
    service = create_role_app("automations", settings, Services(llm=None, embedder=FakeEmbedder(),
                                                                directory=directory, agent_llm=AgentLLM()))
    with TestClient(service) as c:
        assert c.get("/healthz").json() == {"status": "ok", "role": "automations"}
        assert automations_run.ACTIVE is c.app.state.automator is not None
        c.cookies.update(cookies)                                       # ...and here
        assert c.post("/api/automations", json={"name": "Brief", "prompt": "x",
                                                "schedule": {"every": "day", "at": "07:30"}}).status_code == 201
        assert c.get("/api/files").status_code == 404 and c.get("/api/ask").status_code in (404, 405)
    assert automations_run.ACTIVE is None                               # stopped: nothing reaches it


def test_automations_is_one_process_and_nginx_sends_its_api_to_it():
    service = registry.SERVICES["automations"]
    assert service.single and service.processes("ryzen") == 1
    assert service.port not in {s.port for s in registry.SERVICES.values() if s.name != "automations"}
    conf = nginx.render("192.168.1.5:38200", running=["automations"])
    for location in ("/api/automations", "/api/admin/automations"):
        part = conf[conf.index(f"location {location} {{"):]
        assert part[part.index("proxy_pass http://") + 18:part.index(";")] == "mnemerynne_automations", location
    assert "location /automations {" not in conf                        # the page stays with the web app
