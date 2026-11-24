"""Automator's runner: the scheduler, the three runners, and delivery.

The Scheduler lives in one process: the automations service when it runs (docs/plans/services-rewrite.md,
Phase 9), else the web app, beside the balancer poller, with Erynne's agent and its model path. Every TICK
(or at once on "automations:wake") it claims due automations (automations.claim: a row moves to its next
time in one UPDATE, so two schedulers never run one twice) and runs them, at most MAX_AT_ONCE at a time.

Run now (the page, MCP) goes through start(): in the scheduler's own process straight to it; from another
process (the mcp service, the web app while the automations service runs) the run is saved as 'queued' and
the scheduler claims it. A Claude routine's report is the same: saved as 'reported' where it arrived, and
delivered by the scheduler. Automator's tables live in automations.db; the Inbox notes go to core after
the change they're about commits (automations.tell).

- erynne / claude_key: Agent.run with the automation's prompt as the question, the owner's access, and
  every tool the automation didn't pick switched off (write tools too unless it may propose). With a room
  to post in, Erynne instead answers as in that room (RoomErynne.report): reading only what everyone in
  it may read.
- claude_routine: the routine's API trigger is fired with the prompt and the run's id; the run waits
  until Claude calls report_automation_run (or ROUTINE_WAIT passes).
- The prompt is the typed one, or a prompt file read fresh at the start of each run as the owner
  (automations.prompt_for_run); its raw output says which file and version it followed.

Delivery: the owner's Inbox (a note linking to the run), a room, a draft in their Outlook, webhooks.
An answer of just automations.QUIET is not delivered (a health check with nothing wrong).

Cancel (cancel(), from any process): the row becomes 'cancelled' in one UPDATE; the scheduler then cancels the
run's asyncio task (Scheduler.stop, or Scheduler._reap at its next tick or wake for a cancel made elsewhere).
A cancelled row is never overwritten: automations.finish_run skips it, so a late answer or a routine's late
report can't turn it back into done or failed.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import re
import threading
import time
from typing import Any, Callable

import httpx

from mnemerynne import apis, automations as auto, db, events, messages
from mnemerynne.config import Settings

log = logging.getLogger("mnemerynne.automations")

TICK = 30.0
MAX_AT_ONCE = 2
RUN_TIMEOUT = 300.0
WEBHOOK_TRIES = 3
WEBHOOK_TIMEOUT = 10.0
ROUTINE_VERSION = "2026-10-21"
TASK_PREAMBLE = ("This is a scheduled automation, “{name}”, run for {user}. Nobody is waiting in a chat: do the "
                 "task with your tools, then answer with the result itself, as a short report in Markdown with "
                 "[n] citations. If there is nothing worth reporting, answer with exactly " + auto.QUIET + ".\n\n"
                 "The task:\n")
ROUTINE_TEXT = ("Mnemerynne automation “{name}” for {user}, run {run_id}.\n\n{prompt}\n\nUse the Mnemerynne "
                "connector's tools. When you're done, call report_automation_run with run_id {run_id}, "
                "status \"done\" (or \"failed\") and your report as Markdown; if there is nothing worth "
                "reporting, report exactly " + auto.QUIET + ".")

# Tests: an httpx transport for webhooks and routine fires.
TRANSPORT: httpx.AsyncBaseTransport | None = None
# This process's scheduler (the automations service's, or the web app's), for start() and the MCP tools; None in a
# process without one: those hand runs and reports on through automations.db.
ACTIVE: "Scheduler | None" = None
WAKE = "automations:wake"       # tells the scheduler's process there's a queued run or a report to deliver


def connect(settings: Settings) -> "db.sqlite3.Connection":
    """A connection to Automator's tables: automations.db once they've moved there, else core."""
    return db.connect_for(settings.db_path, "automations", "automations")


def unfit(conn, settings: Settings, row, directory) -> str:
    """Why this automation can't run now ("" if it can)."""
    user = auto.owner(conn, row)
    if user is None or not user["active"]:
        return "its owner's account is off."
    if not auto.may_use(conn, settings, user, directory):
        return "its owner can no longer use Automator."
    return ""


TRACE_FLUSH = 0.5               # a run's new steps are saved (and a watching page woken) at most this often
TRACE_MAX = 400                 # steps kept per run; the last one then says the rest was cut
TRACE_TEXT = 4000               # characters kept of any one field (a tool's result, the prompt, the answer)


def trace_channel(run_id: int) -> str:
    """The event channel a run's watchers listen on: "look again, there are new steps"."""
    return f"automations:run:{run_id}"


def _trim(value: Any) -> Any:
    """A step's field, kept small: long text cut to TRACE_TEXT, a big structure turned into cut JSON text."""
    if isinstance(value, str):
        return value if len(value) <= TRACE_TEXT else value[:TRACE_TEXT] + f"… ({len(value) - TRACE_TEXT} more characters)"
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    text = json.dumps(value, ensure_ascii=False, default=str)   # tool arguments and results, sources
    return value if len(text) <= TRACE_TEXT else _trim(text)


def add_steps(conn, run_id: int, steps: list[dict]) -> int:
    """Append steps to a run's trace, inside the caller's transaction on Automator's tables; the step count after.
    Numbers them on from the saved ones. Past TRACE_MAX they're dropped, with one step saying so. Nothing (0) where
    the runs table has no trace column (tests that keep Automator's tables in core)."""
    try:
        row = conn.execute("SELECT trace FROM automation_runs WHERE id = ?", (run_id,)).fetchone()
    except Exception:                                      # sqlite3.OperationalError: no such column
        return 0
    if row is None:
        return 0
    have = json.loads(row["trace"] or "[]")
    for step in steps:
        if len(have) >= TRACE_MAX:                         # full: the last slot already says so
            break
        if len(have) == TRACE_MAX - 1:                     # the last slot: say the rest is cut, keep nothing more
            step = {"at": step["at"], "kind": "cut", "text": f"More steps happened; only the first {TRACE_MAX - 1} are kept."}
        have.append({"n": len(have) + 1, **step})
    conn.execute("UPDATE automation_runs SET trace = ? WHERE id = ?", (json.dumps(have, ensure_ascii=False), run_id))
    return len(have)


def note(settings: Settings, run_id: int, kind: str, **fields) -> None:
    """One step, saved at once, from any process (a routine's report arriving, a failure recorded elsewhere)."""
    conn = connect(settings)
    try:
        with db.transaction(conn):
            add_steps(conn, run_id, [_step(kind, fields)])
    finally:
        conn.close()
    try:
        events.publish_sync(trace_channel(run_id), {})     # a watching page fetches it
    except Exception:
        pass                                               # Valkey gone: the page polls anyway


def _step(kind: str, fields: dict) -> dict:
    return {"at": round(time.time(), 2), "kind": kind, **{k: _trim(v) for k, v in fields.items()}}


class Trace:
    """A run's raw output while it runs: steps are buffered and saved together every TRACE_FLUSH seconds (and when
    it closes), each save waking the run's watchers (trace_channel). add() is safe from any thread; it is also the
    writer Agent.run is handed, with `raw` set so the agent includes each tool's arguments and result."""

    raw = True                                             # Agent._loop: put the arguments and results in the events

    def __init__(self, settings: Settings, run_id: int):
        self.settings, self.run_id = settings, run_id
        self.loop = asyncio.get_running_loop()
        self.pending: list[dict] = []
        self.lock = threading.Lock()
        self.flusher: asyncio.Task | None = None

    def __call__(self, event: dict) -> None:
        """Agent.run's writer: every event it sends becomes a step of the same kind."""
        event = dict(event)
        kind = str(event.pop("type", "event"))
        for clash in ("kind", "n", "at"):                  # a proposal's own "kind" (edit, api...) keeps its value
            if clash in event:
                event[f"{kind}_{clash}"] = event.pop(clash)
        self.add(kind, **event)

    def add(self, kind: str, /, **fields) -> None:
        with self.lock:
            self.pending.append(_step(kind, fields))
        self.loop.call_soon_threadsafe(self._soon)         # the flush is scheduled on the run's own loop

    def _soon(self) -> None:
        if self.flusher is None or self.flusher.done():
            self.flusher = self.loop.create_task(self._flush_later())

    async def _flush_later(self) -> None:
        await asyncio.sleep(TRACE_FLUSH)                   # gather what comes in the meantime into one save
        while self.pending:
            await asyncio.to_thread(self._save)

    def _save(self) -> None:
        with self.lock:
            steps, self.pending = self.pending, []         # taken under the lock: add() never loses one
        if not steps:
            return
        conn = connect(self.settings)
        try:
            with db.transaction(conn):
                add_steps(conn, self.run_id, steps)
        except Exception:
            log.exception("Couldn't save run %s's trace", self.run_id)
        finally:
            conn.close()
        try:
            events.publish_sync(trace_channel(self.run_id), {})
        except Exception:
            pass                                           # Valkey gone: watching pages poll anyway

    async def close(self) -> None:
        """Save whatever is still waiting."""
        if self.flusher is not None:
            await asyncio.gather(self.flusher, return_exceptions=True)
        while self.pending:
            await asyncio.to_thread(self._save)


def _wake() -> None:
    try:
        events.publish_sync(WAKE, {})
    except Exception:                      # Valkey gone: the scheduler finds it at its next tick
        log.warning("Couldn't wake the scheduler; it picks the run up within %d s.", int(TICK))


def start(conn, row, trigger: str, settings: Settings, directory) -> int:
    """Run an automation now, from any process, inside the caller's transaction on Automator's tables (`conn`);
    its run id. With the scheduler here, it runs at once; else it's queued for the scheduler's process."""
    if ACTIVE is not None:
        return ACTIVE.run_now(conn, row, trigger)
    reason = unfit(conn, settings, row, directory)
    if reason:
        raise auto.AutomationError(reason[0].upper() + reason[1:])
    if conn.execute("SELECT 1 FROM automation_runs WHERE automation_id = ? AND status IN "
                    "('queued', 'running', 'waiting', 'reported')", (row["id"],)).fetchone():
        raise auto.AutomationError("It's running already.")
    run_id = auto.start_run(conn, row, trigger, status="queued")
    add_steps(conn, run_id, [_step("info", {"text": "Queued; the scheduler picks it up in a moment."})])
    db.after_commit(conn, _wake)           # once it's there to be claimed
    return run_id


def save_report(conn, run_id: int, text: str) -> bool:
    """A Claude routine's report, in a process without the scheduler: saved as 'reported' for the scheduler to
    deliver (False: the run wasn't waiting for one any more). `conn` holds Automator's tables."""
    saved = conn.execute("UPDATE automation_runs SET status = 'reported', output = ?, model = 'claude routine' "
                         "WHERE id = ? AND status = 'waiting'", (text, run_id)).rowcount == 1
    if saved:
        add_steps(conn, run_id, [_step("info", {"text": "Claude's routine reported back; delivering it."})])
        db.after_commit(conn, _wake)
    return saved


def cancel(conn, run_id: int, who: str) -> bool:
    """Cancel a run, from any process, inside the caller's transaction on Automator's tables (`conn`): it ends as
    'cancelled' and whatever runs it stops. False when it had already ended (then nothing changes).
    - queued / reported: the scheduler only claims rows still in those states, so it never starts or delivers it.
    - running here (Erynne, Claude with a key): the scheduler cancels the run's asyncio task (Scheduler.stop), at
      once in its own process, else at its next tick or wake (Scheduler._reap).
    - waiting (a Claude routine on claude.ai): nothing of ours is running; Mnemerynne can't stop the session on
      claude.ai, but a report that comes later is refused (report_automation_run says it was cancelled)."""
    if not auto.cancel_run(conn, run_id, who):
        return False
    add_steps(conn, run_id, [_step("cancelled", {"text": f"Cancelled by {who}."})])   # the raw output ends here

    def after() -> None:                   # once the row says cancelled, so the runner sees it when it looks
        if ACTIVE is not None:
            ACTIVE.loop.call_soon_threadsafe(ACTIVE.stop, run_id)   # the scheduler is in this process
        else:
            _wake()                        # the scheduler's process: it reaps cancelled runs on every wake
        try:
            events.publish_sync(trace_channel(run_id), {})          # a watching page sees it end
        except Exception:
            pass
    db.after_commit(conn, after)
    return True


def fail(settings: Settings, run_id: int, text: str, error: str) -> None:
    """Record a failed run and tell its owner (any process). Nothing, if it was cancelled first."""
    conn = connect(settings)
    try:
        with db.transaction(conn):
            if not auto.finish_run(conn, run_id, "failed", output=text, sources=[], error=error, model="claude routine"):
                return                     # cancelled: it stays so, and nobody is told it failed
            add_steps(conn, run_id, [_step("error", {"text": error, "report": text})])   # the raw output says why
            run = auto.get_run(conn, run_id)
            auto.tell(conn, run["owner_id"], f"Your automation “{run['name']}” failed: {error}",
                      f"/automations#run={run_id}")
    finally:
        conn.close()
    try:
        events.publish_sync(trace_channel(run_id), {})     # a watching page sees it failed
    except Exception:
        pass


class Scheduler:
    def __init__(self, settings: Settings, agent, rooms_erynne, bus, get_directory: Callable[[], Any],
                 background: set):
        self.settings, self.agent, self.rooms_erynne, self.bus = settings, agent, rooms_erynne, bus
        self.get_directory, self.background = get_directory, background
        self.slots = asyncio.Semaphore(MAX_AT_ONCE)
        self.loop = asyncio.get_running_loop()
        self.tasks: dict[int, asyncio.Task] = {}       # run id -> the task running or delivering it here (stop())
        self.cancelled: set[int] = set()              # runs stopped on purpose: their CancelledError is swallowed

    # -------------------------------------------------------------- timing
    async def run_forever(self) -> None:
        woken = asyncio.Event()

        async def listen() -> None:        # another process queued a run or saved a report
            while True:
                try:
                    async for _ in events.subscribe(WAKE):
                        woken.set()
                except asyncio.CancelledError:
                    raise
                except Exception:
                    await asyncio.sleep(5)

        listener = asyncio.create_task(listen())
        feeds_polling: asyncio.Task | None = None
        try:
            while True:
                try:
                    await self.tick()
                except Exception:
                    log.exception("Automator's scheduler tick failed")
                if feeds_polling is None or feeds_polling.done():     # T-21: the security feeds that are due, in a
                    feeds_polling = asyncio.create_task(self._poll_feeds())   # thread, never blocking the runs
                try:
                    await asyncio.wait_for(woken.wait(), TICK)
                except asyncio.TimeoutError:
                    pass
                woken.clear()
        finally:
            listener.cancel()
            await asyncio.gather(listener, return_exceptions=True)

    async def _poll_feeds(self) -> None:
        """Poll the security feeds that are due (each has its own interval; security_feeds.poll_due), whether or
        not Automator is on: the feeds serve the security_feeds tool too."""
        from mnemerynne import security_feeds

        def work() -> None:
            conn = connect(self.settings)
            try:
                security_feeds.poll_due(conn, self.settings)
            finally:
                conn.close()
        try:
            await asyncio.to_thread(work)
        except Exception:
            log.exception("Polling the security feeds failed")

    async def tick(self) -> list[int]:
        """Claim what's due (and what other processes queued) and start it, and deliver the reports they saved;
        the run ids started."""
        claimed, reported = await asyncio.to_thread(self._claim_due)
        await self._reap()                                 # runs another process cancelled: stop them here
        for run_id in claimed:
            self._spawn(run_id)
        for run_id in reported:
            self._spawn(run_id, self._deliver_report(run_id))
        return claimed

    def _claim_handed_on(self, conn) -> tuple[list[int], list[int]]:
        """Runs queued and reports saved by processes without the scheduler: claimed here, one UPDATE each, so
        nothing runs or is delivered twice."""
        started, reported = [], []
        for status, into, out in (("queued", "running", started), ("reported", "running", reported)):
            for (run_id,) in conn.execute("SELECT id FROM automation_runs WHERE status = ? ORDER BY id",
                                          (status,)).fetchall():
                with db.transaction(conn):
                    if conn.execute("UPDATE automation_runs SET status = ? WHERE id = ? AND status = ?",
                                    (into, run_id, status)).rowcount:
                        out.append(run_id)
        return started, reported

    def _claim_due(self) -> tuple[list[int], list[int]]:
        conn = connect(self.settings)
        try:
            at = auto.now()
            with db.transaction(conn):
                auto.expire_waiting(conn, at)
            started, reported = self._claim_handed_on(conn)
            if not auto.enabled(conn):
                return started, reported
            for row in auto.due(conn, at):
                with db.transaction(conn):
                    if not auto.claim(conn, row, at):
                        continue
                    reason = self._unfit(conn, row)
                    if reason:
                        auto.pause(conn, row["id"], reason)
                        continue
                    started.append(auto.start_run(conn, row, "schedule",
                                                  scheduled_for=row["next_run_at"]))   # when it was due (Timeline)
            return started, reported
        finally:
            conn.close()

    def _unfit(self, conn, row) -> str:
        return unfit(conn, self.settings, row, self.get_directory())

    def run_now(self, conn, row, trigger: str) -> int:
        """Start a run now (from any thread, inside the caller's transaction); its id."""
        reason = self._unfit(conn, row)
        if reason:
            raise auto.AutomationError(reason[0].upper() + reason[1:])
        if conn.execute("SELECT 1 FROM automation_runs WHERE automation_id = ? AND status IN ('running', 'waiting')",
                        (row["id"],)).fetchone():
            raise auto.AutomationError("It's running already.")
        run_id = auto.start_run(conn, row, trigger)
        self.loop.call_soon_threadsafe(self._spawn, run_id)
        return run_id

    def _spawn(self, run_id: int, work=None) -> None:
        task = self.loop.create_task(work if work is not None else self.execute(run_id))
        self.background.add(task)
        task.add_done_callback(self.background.discard)
        self.tasks[run_id] = task                      # so a cancel can find it

        def forget(done: asyncio.Task) -> None:
            if self.tasks.get(run_id) is done:
                del self.tasks[run_id]
            self.cancelled.discard(run_id)
        task.add_done_callback(forget)

    # ------------------------------------------------------------- cancel
    def stop(self, run_id: int) -> bool:
        """Stop a cancelled run's task here (on the scheduler's loop); False when nothing runs it here (queued,
        waiting for a routine, or already over). The row must already say 'cancelled' (cancel())."""
        task = self.tasks.get(run_id)
        if task is None or task.done():
            return False
        self.cancelled.add(run_id)                     # execute() swallows this CancelledError, not a shutdown's
        task.cancel()
        return True

    async def _reap(self) -> list[int]:
        """Stop the tasks here whose runs were cancelled elsewhere (the mcp service, the web app while the
        automations service runs: they only change the row, then wake us). The run ids stopped."""
        mine = [run_id for run_id, task in self.tasks.items() if not task.done() and run_id not in self.cancelled]
        if not mine:
            return []

        def read() -> list[int]:
            conn = connect(self.settings)
            try:
                marks = ", ".join("?" * len(mine))
                return [r[0] for r in conn.execute(f"SELECT id FROM automation_runs WHERE status = 'cancelled' "
                                                   f"AND id IN ({marks})", mine).fetchall()]
            finally:
                conn.close()
        stopped = [run_id for run_id in await asyncio.to_thread(read) if self.stop(run_id)]
        return stopped

    # ------------------------------------------------------------- running
    async def _deliver_report(self, run_id: int) -> None:
        """A routine's report another process saved: recorded as done (or quiet) and delivered from here."""
        conn = connect(self.settings)
        try:
            run = auto.get_run(conn, run_id)
        finally:
            conn.close()
        if run is not None:
            try:
                await self.complete(run_id, run["output"], [], run["model"] or "claude routine")
            except Exception:
                log.exception("Delivering run %s's report failed", run_id)
                await asyncio.to_thread(self._finish, run_id, "failed", run["output"], [], "claude routine",
                                        "Delivering it failed.")

    async def execute(self, run_id: int) -> None:
        """Run it (or, for a routine, fire it). Cancelled on purpose (stop()), it ends quietly: the row already says
        'cancelled', its trace gets its last steps saved, and nothing is delivered."""
        try:
            await self._execute(run_id)
        except asyncio.CancelledError:
            if run_id not in self.cancelled:
                raise                                      # the scheduler shutting down: not ours to swallow
            log.info("Automation run %s was cancelled", run_id)

    async def _execute(self, run_id: int) -> None:
        async with self.slots:                             # a cancel while waiting for a slot ends it here
            conn = connect(self.settings)
            try:
                run = auto.get_run(conn, run_id)
                row = auto.get(conn, run["automation_id"]) if run else None
                user = auto.owner(conn, row) if row else None
            finally:
                conn.close()
            if row is None or user is None or run["status"] == "cancelled":
                return                                     # gone, or cancelled before it began
            trace = Trace(self.settings, run_id)          # the raw output, saved as it happens
            trace.add("start", trigger=run["trigger"], runner=row["runner"], automation=row["name"],
                      owner=user["username"], tools=json.loads(row["tools"]) or "every reading tool",
                      may_propose=bool(row["may_propose"]))
            error, stopped = "", False
            try:
                if row["runner"] == "claude_routine":
                    await self._fire_routine(run_id, row, user, trace)
                    return
                text, sources, model = await asyncio.wait_for(self._answer(row, user, trace), RUN_TIMEOUT)
            except asyncio.CancelledError:
                stopped = run_id in self.cancelled         # cancel() already wrote the row and its "cancelled" step
                raise
            except asyncio.TimeoutError:
                error = f"It took longer than {int(RUN_TIMEOUT // 60)} minutes and was stopped."
            except RunFailed as exc:
                error = str(exc)
            except Exception as exc:
                log.exception("Automation %s run %s failed", row["id"], run_id)
                error = f"Something went wrong: {type(exc).__name__}: {str(exc)[:300]}"   # the trace keeps the detail
            finally:
                if error:
                    trace.add("error", text=error)
                    await asyncio.to_thread(self._finish, run_id, "failed", "", [], "", error)
                if stopped:                                # the trace's last step: the page's stream ends on it
                    trace.add("cancelled", text="Stopped: nothing more runs, and nothing is delivered.")
                if error or stopped or row["runner"] == "claude_routine":
                    await trace.close()                    # everything saved before the page hears it ended
            if error:
                return
            await self.complete(run_id, text, sources, model, trace)

    async def _answer(self, row, user, trace: Trace) -> tuple[str, list[dict], str]:
        deliver = json.loads(row["deliver"])
        task = await self._instructions(row, trace)        # the typed prompt, or the prompt file read now
        prompt = TASK_PREAMBLE.format(name=row["name"], user=user["username"]) + task
        trace.add("prompt", text=prompt)                   # exactly what the model is asked
        if deliver.get("room_id") and row["runner"] == "erynne":
            if self.rooms_erynne is None:
                raise RunFailed("Rooms isn't running.")
            trace.add("info", text=f"Answering as Erynne in room {deliver['room_id']}, with what everyone there may "
                                   "read. A room run doesn't show its tool steps here.")
            text, sources = await self.rooms_erynne.report(user["id"], deliver["room_id"], prompt)
            return text, sources, ""
        claude = row["runner"] == "claude_key"
        ready = await self.agent.ready(user["id"], claude=claude)
        if ready == "nokey":
            raise RunFailed("Save a Claude key in Preferences first: this automation runs Claude with it.")
        if ready != "ok":
            raise RunFailed("Claude isn't available to you." if claude else "Erynne's model isn't answering.")
        off = await self._switched_off(row, user["id"])
        if off:
            trace.add("info", text="Tools switched off for this run", tools=sorted(off))
        events: dict = {}

        def writer(event: dict) -> None:
            if event.get("type") == "sources":
                events["sources"] = event["sources"]
            trace(event)                                   # every event, into the raw output

        writer.raw = True                                  # Agent._loop: include each tool's arguments and result
        out = await self.agent.run({"user_id": user["id"], "question": prompt, "disabled_tools": sorted(off),
                                    "use_claude": claude, "automation": True}, writer, None)
        if out is None:
            raise RunFailed("The model couldn't be reached.")
        if out.get("model"):
            trace.add("info", text="Answered by " + out["model"])
        events["proposals"] = list(out.get("proposals") or [])   # what it proposed: kept with the run
        if events["proposals"]:
            await asyncio.to_thread(self._keep_proposals, trace.run_id, events["proposals"])
        return out["answer"]["text"], events.get("sources", []), out.get("model") or ""

    async def _instructions(self, row, trace: Trace) -> str:
        """What this run is asked to do: the typed prompt, or its prompt file read fresh as the owner (so an edit
        to the file counts from the next run). A file that can't be read fails the run, saying why."""
        try:
            text, source = await asyncio.to_thread(auto.prompt_for_run, self.settings, row)   # file reads can block
        except auto.AutomationError as exc:
            raise RunFailed(str(exc)) from None
        if source:
            trace.add("info", text=source)                 # which file and version this run followed
        return text

    def _keep_proposals(self, run_id: int, ids: list[int]) -> None:
        """The proposals a run made, on its row (the run's links to them, auto-approval, /api/automations/approvals). Skipped where the runs table
        is still core's (no such column)."""
        conn = connect(self.settings)
        try:
            with db.transaction(conn):
                conn.execute("UPDATE automation_runs SET proposals = ? WHERE id = ?", (json.dumps(ids), run_id))
        except Exception:
            log.warning("Couldn't keep run %s's proposals", run_id, exc_info=True)
        finally:
            conn.close()

    def _approve_all(self, run_id: int) -> list[dict]:
        """Auto-approval: apply each proposal this run made, as its owner, with the same checks as applying by hand
        (proposals.check_may_apply, then proposals.apply). Only when the automation may propose and auto-approves;
        never one that waits for a Mnemerynne admin. One result per proposal; [] when there's nothing to do."""
        from mnemerynne import proposals
        from mnemerynne.web.admin import _log

        conn = connect(self.settings)
        try:
            run = auto.get_run(conn, run_id)
            row = auto.get(conn, run["automation_id"]) if run else None
            user = auto.owner(conn, row) if row else None
            ids = json.loads((run["proposals"] if run is not None and "proposals" in run.keys() else "") or "[]")
            wanted = row is not None and "auto_approve" in row.keys() and row["auto_approve"] and row["may_propose"]
        finally:
            conn.close()
        if not ids or not wanted or user is None:
            return []
        directory = self.get_directory()
        core = db.connect(self.settings.db_path)
        done: list[dict] = []
        try:
            admin = auto.is_admin(core, self.settings, user, directory)
            for pid in ids:
                try:
                    prow = proposals.get(core, pid, user["id"])
                    if prow["status"] != "pending":
                        done.append({"proposal": pid, "ok": False, "error": f"It was already {prow['status']}."})
                        continue
                    if prow["approver"] == "admin":            # those stay with an admin, always
                        done.append({"proposal": pid, "ok": False, "summary": prow["summary"],
                                     "error": "It waits for a Mnemerynne admin (Access → Approvals); "
                                              "auto-approval never decides those."})
                        continue
                    proposals.check_may_apply(core, user, prow, directory)
                    out = proposals.apply(core, self.settings, user, pid, directory,
                                          admin=prow["kind"] == "access" and admin)
                    with db.transaction(core):                 # say who decided it, for the person and their Claude
                        core.execute("UPDATE agent_proposals SET decision_note = ? WHERE id = ? AND decision_note IS NULL",
                                     (f"Approved automatically by Automator (run {run_id}).", pid))
                    _log(core, user, "proposal.apply", str(out.get("summary", ""))[:200],
                         f"#{pid} (auto-approved by Automator, run {run_id})")
                    done.append({"proposal": pid, "ok": True, "summary": out.get("summary", ""),
                                 "status": out.get("status", "applied")})
                except Exception as exc:                        # ProposalError, EditError, the API's own refusal
                    done.append({"proposal": pid, "ok": False, "error": str(exc)[:300] or type(exc).__name__})
        finally:
            core.close()
        return done

    async def _switched_off(self, row, user_id: int) -> set[str]:
        listed = (await self.agent.tool_list(user_id, automation=True))["tools"]
        picked = set(json.loads(row["tools"]))
        off = {t["name"] for t in listed if picked and t["name"] not in picked}
        if not row["may_propose"]:
            off |= {t["name"] for t in listed if t.get("write")}
        return off

    async def complete(self, run_id: int, text: str, sources: list[dict], model: str = "",
                       trace: Trace | None = None) -> dict:
        """Record the answer and deliver it (also for a routine's report). The trace is the run's, or a new one
        that carries on the saved steps (a routine's report, arriving later)."""
        if trace is None:                                  # a routine's report: its answer wasn't streamed
            trace = Trace(self.settings, run_id)
            trace.add("answer", text=text or "")
        text = (text or "").strip()
        quiet = text.strip(" \t\r\n.*_`\"'") == auto.QUIET
        status = "quiet" if quiet else "done"
        try:
            if not await asyncio.to_thread(self._finish, run_id, status, text, sources, model, ""):
                trace.add("cancelled", text="It was cancelled before its answer was recorded; nothing is delivered.")
                return {"status": "cancelled", "delivered": []}   # a late answer never un-cancels a run
            try:
                approved = await asyncio.to_thread(self._approve_all, run_id)   # auto-approval, before it's sent on
            except Exception as exc:                       # never let it stop the delivery
                log.exception("Auto-approving run %s's proposals failed", run_id)
                approved = [{"ok": False, "error": f"Auto-approval failed: {type(exc).__name__}; "
                                                   "the proposals wait on the Proposals page."}]
            for a in approved:
                trace.add("approval", **a)
            if quiet:
                trace.add("finish", status=status, text="Nothing to report, so nothing is delivered.")
                return {"status": status, "delivered": []}
            delivered: list[dict] = []
            try:
                delivered = await self.deliver(run_id)
            finally:
                for d in delivered:
                    trace.add("delivered", **d)            # where it went, and what failed
                trace.add("finish", status=status)         # the page's stream ends on this step
            return {"status": status, "delivered": delivered}
        finally:
            await trace.close()                            # all of it saved before the page hears it ended

    def _finish(self, run_id, status, text, sources, model, error) -> bool:
        """Record how it ended (and tell the owner if it failed); False when it was cancelled first."""
        conn = connect(self.settings)
        try:
            with db.transaction(conn):
                if not auto.finish_run(conn, run_id, status, output=text, sources=sources, error=error, model=model):
                    return False                           # cancelled: stays cancelled, nobody told it failed
                if status == "failed":
                    run = auto.get_run(conn, run_id)
                    auto.tell(conn, run["owner_id"], f"Your automation “{run['name']}” failed: {error}",
                              f"/automations#run={run_id}")
            return True
        finally:
            conn.close()

    # --------------------------------------------------------- the routine
    async def _fire_routine(self, run_id: int, row, user, trace: Trace) -> None:
        token = apis.decrypt_secret(self.settings, row["routine_token_enc"])
        if not token:
            raise RunFailed("The routine's token can't be read; save it again.")
        task = await self._instructions(row, trace)        # the typed prompt, or the prompt file read now
        text = ROUTINE_TEXT.format(name=row["name"], user=user["username"], run_id=run_id, prompt=task)
        trace.add("prompt", text=text)                     # exactly what the routine is sent (never its token)
        headers = {"authorization": f"Bearer {token}", "anthropic-version": ROUTINE_VERSION,
                   "content-type": "application/json"}
        async with httpx.AsyncClient(timeout=30.0, transport=TRANSPORT, follow_redirects=False) as client:
            got = None
            for attempt in range(2):
                try:
                    got = await client.post(row["routine_url"], json={"text": text[:65_000]}, headers=headers)
                except httpx.HTTPError as exc:
                    # No idempotency key: a request that may have arrived is never sent again.
                    raise RunFailed(f"Claude's routine couldn't be reached: {type(exc).__name__}.") from None
                if got.status_code == 429 and attempt == 0:
                    await asyncio.sleep(min(float(got.headers.get("retry-after", "5") or 5), 60.0))
                    continue
                break
        if got.status_code >= 400:
            trace.add("routine", status_code=got.status_code, response=got.text)   # Anthropic's own words, raw
            raise RunFailed({401: "The routine's token was refused; make a new one on claude.ai.",
                             403: "The routine's token isn't allowed to start it.",
                             404: "No such routine (check its address).",
                             429: "Claude's routine limit was reached (30 an hour); try later."}.get(
                got.status_code, f"Claude's routine answered {got.status_code}."))
        try:
            session = str(got.json().get("claude_code_session_url") or "")
        except ValueError:
            session = ""
        trace.add("routine", status_code=got.status_code, session=session, response=got.text,
                  text="Claude's routine started; waiting for it to report back (up to "
                       f"{int(auto.ROUTINE_WAIT.total_seconds() // 3600)} hours).")
        conn = connect(self.settings)
        try:
            with db.transaction(conn):
                conn.execute("UPDATE automation_runs SET status = CASE status WHEN 'running' THEN 'waiting' "
                             "ELSE status END, claude_session_url = ? WHERE id = ?",   # cancelled meanwhile: stays so
                             (session[:500], run_id))
        finally:
            conn.close()

    # ------------------------------------------------------------- delivery
    async def deliver(self, run_id: int) -> list[dict]:
        conn = connect(self.settings)
        try:
            run = auto.get_run(conn, run_id)
            row = auto.get(conn, run["automation_id"])
            user = auto.owner(conn, row)
            deliver = json.loads(row["deliver"])
            hooks = [conn.execute("SELECT * FROM automation_destinations WHERE id = ?", (d,)).fetchone()
                     for d in deliver.get("destinations", [])]
        finally:
            conn.close()
        body = run["output"] + footnotes(run["output"], json.loads(run["sources"]), self.settings.public_url)
        link = f"/automations#run={run_id}"
        done: list[dict] = []
        if deliver.get("inbox"):
            done.append(await asyncio.to_thread(self._to_inbox, user["id"], row["name"], run["output"], link))
        if deliver.get("room_id"):
            shared = row["runner"] != "erynne"             # Claude, with the owner's access (automations._deliver)
            done.append(await asyncio.to_thread(self._to_room, deliver["room_id"], row["name"], body,
                                                user["username"] if shared else ""))
        if deliver.get("email_draft"):
            done.append(await asyncio.to_thread(self._to_draft, user["id"], row["name"], body))
        for hook in hooks:
            if hook is not None:
                done.append(await self._to_webhook(hook, row, run, body, link))
        conn = connect(self.settings)
        try:
            with db.transaction(conn):
                conn.execute("UPDATE automation_runs SET delivered = ? WHERE id = ?", (json.dumps(done), run_id))
                if done and auto.has_column(conn, "automation_runs", "delivered_at"):   # the last message is out
                    conn.execute("UPDATE automation_runs SET delivered_at = datetime('now') WHERE id = ?", (run_id,))
                failed = [d for d in done if not d["ok"]]
                if failed and not deliver.get("inbox"):
                    auto.tell(conn, user["id"], f"Your automation “{row['name']}” ran, but couldn't be "
                              f"delivered to {', '.join(d['to'] for d in failed)}.", link)
        finally:
            conn.close()
        return done

    def _to_inbox(self, user_id: int, name: str, text: str, link: str) -> dict:
        conn = db.connect(self.settings.db_path)
        try:
            with db.transaction(conn):
                summary = re.sub(r"\s+", " ", re.sub(r"\[(\d{1,3})\]", "", text)).strip()
                note = messages.system(conn, user_id, f"{name}: {summary[:messages.MAX_CHARS - len(name) - 10]}", link)
            return {"to": "inbox", "ok": note is not None}
        finally:
            conn.close()

    def _to_room(self, room_id: int, name: str, body: str, shared_by: str = "") -> dict:
        """Post the answer in a room as Erynne. shared_by: Claude ran it with that person's access, which the post
        says, so the room knows it may hold what only they can see."""
        from mnemerynne import rooms

        head = f"**{name}**" + (f" · _run by Claude with {shared_by}'s access, shared by them_" if shared_by else "")

        conn = db.connect_for(self.settings.db_path, "rooms", "chat_rooms")   # rooms.db once moved there
        try:
            with db.transaction(conn):
                if not rooms.enabled(conn):
                    return {"to": "room", "ok": False, "error": "Rooms is off."}
                rooms.post(conn, None, room_id, f"{head}\n\n{body}"[:rooms.MAX_CHARS], self.get_directory(),
                           via="erynne")
            if self.bus is not None:
                self.bus.wake()
            return {"to": "room", "ok": True, "room_id": room_id}
        except rooms.RoomError as exc:
            return {"to": "room", "ok": False, "error": str(exc)}
        finally:
            conn.close()

    def _to_draft(self, user_id: int, name: str, body: str) -> dict:
        from mnemerynne import m365_mail

        conn = db.connect(self.settings.db_path)
        try:
            identity = conn.execute("SELECT i.id, i.upn FROM m365_identities i JOIN m365_tenants t ON t.id = i.tenant_row "
                                    "WHERE i.user_id = ? AND i.status = 'ok' AND t.enabled = 1 ORDER BY i.id LIMIT 1",
                                    (user_id,)).fetchone()
            if identity is None:
                return {"to": "email draft", "ok": False, "error": "No Microsoft 365 account is linked."}
            link = m365_mail.save_draft(conn, self.settings, user_id, {
                "identity_id": identity["id"], "to": [identity["upn"]], "subject": name, "body": body})
            return {"to": "email draft", "ok": True, "link": link}
        except Exception as exc:   # MailError, GraphError, the network: say so, deliver the rest
            return {"to": "email draft", "ok": False, "error": str(exc)[:300] or type(exc).__name__}
        finally:
            conn.close()

    async def _to_webhook(self, hook, row, run, body: str, link: str) -> dict:
        absolute = self.settings.public_url.rstrip("/") + link
        payload = webhook_payload(hook["format"], row["name"], run["id"], body, absolute)
        data = json.dumps(payload, ensure_ascii=False).encode()
        headers = {"content-type": "application/json", "user-agent": "Mnemerynne-Automator"}
        secret = apis.decrypt_secret(self.settings, hook["secret_enc"]) if hook["secret_enc"] else None
        if secret:
            headers["x-mnemerynne-signature"] = "sha256=" + hmac.new(secret.encode(), data, hashlib.sha256).hexdigest()
        error = ""
        async with httpx.AsyncClient(timeout=WEBHOOK_TIMEOUT, transport=TRANSPORT, follow_redirects=False) as client:
            for attempt in range(WEBHOOK_TRIES):
                try:
                    got = await client.post(hook["url"], content=data, headers=headers)
                    if got.status_code < 300:
                        return {"to": hook["name"], "ok": True}
                    error = f"it answered {got.status_code}"
                    if got.status_code < 500 and got.status_code != 429:
                        break
                except httpx.HTTPError as exc:
                    error = type(exc).__name__
                if attempt + 1 < WEBHOOK_TRIES:
                    await asyncio.sleep(2 ** attempt)
        return {"to": hook["name"], "ok": False, "error": error}


class RunFailed(Exception):
    """A run that can't go on; the message is for the owner."""


def footnotes(text: str, sources: list[dict], base: str) -> str:
    """The sources [n] markers point at, as Markdown links under the report."""
    cited = {int(n) for n in re.findall(r"\[(\d{1,3})\]", text or "")}
    lines = []
    for s in sources:
        if s.get("n") in cited and s.get("url"):
            url = s["url"] if not s["url"].startswith("/") else base.rstrip("/") + s["url"]
            lines.append(f"[{s['n']}] [{s.get('file') or 'source'}{' · ' + s['location'] if s.get('location') else ''}]({url})")
    return ("\n\n" + "  \n".join(lines)) if lines else ""


def webhook_payload(fmt: str, name: str, run_id: int, body: str, link: str) -> dict:
    if fmt == "slack":
        return {"text": f"*{name}*\n{body}\n<{link}|Open in Mnemerynne>"}
    if fmt == "teams":
        return {"type": "message", "attachments": [{
            "contentType": "application/vnd.microsoft.card.adaptive",
            "content": {"$schema": "http://adaptivecards.io/schemas/adaptive-card.json", "type": "AdaptiveCard",
                        "version": "1.4", "body": [
                            {"type": "TextBlock", "text": name, "weight": "Bolder", "size": "Medium", "wrap": True},
                            {"type": "TextBlock", "text": body[:20_000], "wrap": True}],
                        "actions": [{"type": "Action.OpenUrl", "title": "Open in Mnemerynne", "url": link}]}}]}
    return {"automation": name, "run_id": run_id, "status": "done", "output": body, "link": link}
