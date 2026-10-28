"""Automator's runner: the scheduler in the web process, the three runners, and delivery.

The Scheduler lives in the web app's lifespan, beside the balancer poller, because Erynne's agent and its
model path exist only there. Every TICK it claims due automations (automations.claim: a row moves to its
next time in one UPDATE, so two web workers never run one twice) and runs them, at most MAX_AT_ONCE at
a time. Run now (the page, MCP) goes through the same path from any thread.

- erynne / claude_key: Agent.run with the automation's prompt as the question, the owner's access, and
  every tool the automation didn't pick switched off (write tools too unless it may propose). With a room
  to post in, Erynne instead answers as in that room (RoomErynne.report): reading only what everyone in
  it may read.
- claude_routine: the routine's API trigger is fired with the prompt and the run's id; the run waits
  until Claude calls report_automation_run (or ROUTINE_WAIT passes).

Delivery: the owner's Inbox (a note linking to the run), a room, a draft in their Outlook, webhooks.
An answer of just automations.QUIET is not delivered (a health check with nothing wrong).
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import re
from typing import Any, Callable

import httpx

from mnemerynne import apis, automations as auto, db, messages
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
# The web app's scheduler, for the MCP tools (run now, a routine's report); None outside the web app.
ACTIVE: "Scheduler | None" = None


class Scheduler:
    def __init__(self, settings: Settings, agent, rooms_erynne, bus, get_directory: Callable[[], Any],
                 background: set):
        self.settings, self.agent, self.rooms_erynne, self.bus = settings, agent, rooms_erynne, bus
        self.get_directory, self.background = get_directory, background
        self.slots = asyncio.Semaphore(MAX_AT_ONCE)
        self.loop = asyncio.get_running_loop()

    # -------------------------------------------------------------- timing
    async def run_forever(self) -> None:
        while True:
            try:
                await self.tick()
            except Exception:
                log.exception("Automator's scheduler tick failed")
            await asyncio.sleep(TICK)

    async def tick(self) -> list[int]:
        """Claim what's due and start it; the run ids started."""
        claimed = await asyncio.to_thread(self._claim_due)
        for run_id in claimed:
            self._spawn(run_id)
        return claimed

    def _claim_due(self) -> list[int]:
        conn = db.connect(self.settings.db_path)
        try:
            at = auto.now()
            with db.transaction(conn):
                auto.expire_waiting(conn, at)
            if not auto.enabled(conn):
                return []
            started = []
            for row in auto.due(conn, at):
                with db.transaction(conn):
                    if not auto.claim(conn, row, at):
                        continue
                    reason = self._unfit(conn, row)
                    if reason:
                        auto.pause(conn, row["id"], reason)
                        continue
                    started.append(auto.start_run(conn, row, "schedule"))
            return started
        finally:
            conn.close()

    def _unfit(self, conn, row) -> str:
        user = auto.owner(conn, row)
        if user is None or not user["active"]:
            return "its owner's account is off."
        if not auto.may_use(conn, self.settings, user, self.get_directory()):
            return "its owner can no longer use Automator."
        return ""

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

    def _spawn(self, run_id: int) -> None:
        task = self.loop.create_task(self.execute(run_id))
        self.background.add(task)
        task.add_done_callback(self.background.discard)

    # ------------------------------------------------------------- running
    async def execute(self, run_id: int) -> None:
        async with self.slots:
            conn = db.connect(self.settings.db_path)
            try:
                run = auto.get_run(conn, run_id)
                row = auto.get(conn, run["automation_id"]) if run else None
                user = auto.owner(conn, row) if row else None
            finally:
                conn.close()
            if row is None or user is None:
                return
            try:
                if row["runner"] == "claude_routine":
                    await self._fire_routine(run_id, row, user)
                    return
                text, sources, model = await asyncio.wait_for(self._answer(row, user), RUN_TIMEOUT)
            except asyncio.TimeoutError:
                await asyncio.to_thread(self._finish, run_id, "failed", "", [], "", "It took longer than "
                                        f"{int(RUN_TIMEOUT // 60)} minutes and was stopped.")
                return
            except RunFailed as exc:
                await asyncio.to_thread(self._finish, run_id, "failed", "", [], "", str(exc))
                return
            except Exception as exc:
                log.exception("Automation %s run %s failed", row["id"], run_id)
                await asyncio.to_thread(self._finish, run_id, "failed", "", [], "",
                                        f"Something went wrong: {type(exc).__name__}.")
                return
            await self.complete(run_id, text, sources, model)

    async def _answer(self, row, user) -> tuple[str, list[dict], str]:
        deliver = json.loads(row["deliver"])
        prompt = TASK_PREAMBLE.format(name=row["name"], user=user["username"]) + row["prompt"]
        if deliver.get("room_id") and row["runner"] == "erynne":
            if self.rooms_erynne is None:
                raise RunFailed("Rooms isn't running.")
            text, sources = await self.rooms_erynne.report(user["id"], deliver["room_id"], prompt)
            return text, sources, ""
        claude = row["runner"] == "claude_key"
        ready = await self.agent.ready(user["id"], claude=claude)
        if ready == "nokey":
            raise RunFailed("Save a Claude key in Preferences first: this automation runs Claude with it.")
        if ready != "ok":
            raise RunFailed("Claude isn't available to you." if claude else "Erynne's model isn't answering.")
        off = await self._switched_off(row, user["id"])
        events: dict = {}

        def writer(event: dict) -> None:
            if event.get("type") == "sources":
                events["sources"] = event["sources"]

        out = await self.agent.run({"user_id": user["id"], "question": prompt, "disabled_tools": sorted(off),
                                    "use_claude": claude, "automation": True}, writer, None)
        if out is None:
            raise RunFailed("The model couldn't be reached.")
        return out["answer"]["text"], events.get("sources", []), out.get("model") or ""

    async def _switched_off(self, row, user_id: int) -> set[str]:
        listed = (await self.agent.tool_list(user_id, automation=True))["tools"]
        picked = set(json.loads(row["tools"]))
        off = {t["name"] for t in listed if picked and t["name"] not in picked}
        if not row["may_propose"]:
            off |= {t["name"] for t in listed if t.get("write")}
        return off

    async def complete(self, run_id: int, text: str, sources: list[dict], model: str = "") -> dict:
        """Record the answer and deliver it (also for a routine's report)."""
        text = (text or "").strip()
        quiet = text.strip(" \t\r\n.*_`\"'") == auto.QUIET
        status = "quiet" if quiet else "done"
        await asyncio.to_thread(self._finish, run_id, status, text, sources, model, "")
        if quiet:
            return {"status": status, "delivered": []}
        delivered = await self.deliver(run_id)
        return {"status": status, "delivered": delivered}

    def _finish(self, run_id, status, text, sources, model, error) -> None:
        conn = db.connect(self.settings.db_path)
        try:
            with db.transaction(conn):
                auto.finish_run(conn, run_id, status, output=text, sources=sources, error=error, model=model)
                if status == "failed":
                    run = auto.get_run(conn, run_id)
                    messages.system(conn, run["owner_id"], f"Your automation “{run['name']}” failed: {error}",
                                    link=f"/automations#run={run_id}")
        finally:
            conn.close()

    # --------------------------------------------------------- the routine
    async def _fire_routine(self, run_id: int, row, user) -> None:
        token = apis.decrypt_secret(self.settings, row["routine_token_enc"])
        if not token:
            raise RunFailed("The routine's token can't be read; save it again.")
        text = ROUTINE_TEXT.format(name=row["name"], user=user["username"], run_id=run_id, prompt=row["prompt"])
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
            raise RunFailed({401: "The routine's token was refused; make a new one on claude.ai.",
                             403: "The routine's token isn't allowed to start it.",
                             404: "No such routine (check its address).",
                             429: "Claude's routine limit was reached (30 an hour); try later."}.get(
                got.status_code, f"Claude's routine answered {got.status_code}."))
        try:
            session = str(got.json().get("claude_code_session_url") or "")
        except ValueError:
            session = ""
        conn = db.connect(self.settings.db_path)
        try:
            with db.transaction(conn):
                conn.execute("UPDATE automation_runs SET status = 'waiting', claude_session_url = ? WHERE id = ?",
                             (session[:500], run_id))
        finally:
            conn.close()

    # ------------------------------------------------------------- delivery
    async def deliver(self, run_id: int) -> list[dict]:
        conn = db.connect(self.settings.db_path)
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
            done.append(await asyncio.to_thread(self._to_room, deliver["room_id"], row["name"], body))
        if deliver.get("email_draft"):
            done.append(await asyncio.to_thread(self._to_draft, user["id"], row["name"], body))
        for hook in hooks:
            if hook is not None:
                done.append(await self._to_webhook(hook, row, run, body, link))
        conn = db.connect(self.settings.db_path)
        try:
            with db.transaction(conn):
                conn.execute("UPDATE automation_runs SET delivered = ? WHERE id = ?", (json.dumps(done), run_id))
                failed = [d for d in done if not d["ok"]]
                if failed and not deliver.get("inbox"):
                    messages.system(conn, user["id"], f"Your automation “{row['name']}” ran, but couldn't be "
                                    f"delivered to {', '.join(d['to'] for d in failed)}.", link=link)
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

    def _to_room(self, room_id: int, name: str, body: str) -> dict:
        from mnemerynne import rooms

        conn = db.connect(self.settings.db_path)
        try:
            with db.transaction(conn):
                if not rooms.enabled(conn):
                    return {"to": "room", "ok": False, "error": "Rooms is off."}
                rooms.post(conn, None, room_id, f"**{name}**\n\n{body}"[:rooms.MAX_CHARS], self.get_directory(),
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
