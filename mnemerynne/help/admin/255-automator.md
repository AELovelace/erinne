# Automator (alpha)

Automator runs jobs on a schedule for the people allowed to use it: a morning briefing, a security digest, a check on the internal systems. Each one is a prompt, a schedule and a runner, and the answer goes to the person's Inbox, a room, a draft in their Outlook, or a webhook. People make theirs under **Automator**; this page is the admin side.

## Settings → Automator

- **Automator is on for this organisation:** off until an admin turns it on. While it's off, the tab and its API aren't there, nothing runs, and Claude's Automator tools refuse. Automations are kept for when it's back.
- **Who may use it:** a group (several separated by `;`); empty means admins only. Admins always may. Someone who leaves the group, or whose account is turned off, has their automations paused at the next run, with a note saying why.
- **Automations:** everyone's, with **Pause**. The owner is told and can turn it back on. While one of them has a run going, **Cancel run** stops that run (see [Cancelling a run](#cancelling-a-run)); the owner is told, and it's logged as `automations.cancel`.
- **Webhooks:** where answers may go besides Mnemerynne. Only admins add addresses (https only); people pick from this list, optionally limited to a group.
  - **JSON:** posts `{automation, run_id, status, output, link}`.
  - **Teams:** an Adaptive Card for an incoming webhook or a Workflows "post to channel" trigger.
  - **Slack:** an incoming-webhook message.
  - **Signing:** with a secret, each post carries `X-Mnemerynne-Signature: sha256=<HMAC of the body>`.
  - **Retries:** failures are tried three times.
- **Security feeds:** the vulnerability feeds `security_feeds` reads. CISA KEV, NVD, Microsoft, Mozilla, GitHub's Git advisories, nginx.org and Oracle ship set up; see [Security feeds](security-feeds.md).

Every change is logged in Recent changes (`settings.automations*`, `automations.*`).

## Runners

- **Erynne:** the agent's usual model (balancer nodes, then `MNEMERYNNE_LLM_URL`) with the owner's access and the tools they picked. If they picked none, every tool that only reads is used. Tools that change something (files, mail drafts, API actions) are offered only when the automation **may propose changes**, and even then they only make proposals. The owner applies them from the **Proposals** page (/proposals), unless they turned on **Approve its proposals automatically** for that automation. Then the run applies its own proposals as the owner when it ends, before delivering, with the same checks as Apply (agent group or Agents folder; the API operation still allowed to them). Proposals with an admin approver are never auto-approved; they wait in Access → Approvals as usual. Each auto-approval is in the access log as `proposal.apply` ("auto-approved by Automator, run N"), and the proposal's decision note says so.
- **Claude (your key):** the same, driven by Claude under the owner's saved key (*Claude in chat*). Only for people in the Claude group.
- **A Claude routine:** for work done on claude.ai.
  1. The owner makes a routine at claude.ai/code/routines with the Mnemerynne connector and an **API trigger**, and pastes the trigger's address and token into the automation. The token is stored encrypted with `MNEMERYNNE_SECRET_KEY`.
  2. At run time Mnemerynne starts the routine with the task and the run's id.
  3. Claude works through the connector and calls `report_automation_run`, which delivers the answer.
  4. A routine that doesn't report within 2 hours is marked timed out. Anthropic allows 30 starts per routine per hour.

**Rooms:** an Erynne automation that posts in a room runs as Erynne in that room, reading only what *everyone in that room* may read, exactly as when she's @mentioned there. Its answer is posted for all of them, so it can't use the owner's private files or mail. A Claude automation (the owner's key or a routine) may post in a room too, but it works with the owner's own access. Saving one with a room is refused unless the owner ticks **Share what my access finds with everyone in this room** (stored as `deliver.room_share`). Its post is headed "run by Claude with *owner*'s access, shared by them". Rooms where Erynne is turned off aren't offered, for either runner.

**Nothing to report:** an answer of exactly `NOTHING_TO_REPORT` (the templates ask for it, e.g. a health check with nothing wrong) is recorded as *nothing to report* and not delivered.

## Run states

A run (`automation_runs.status`) is in one of these:

| State | Meaning | Still going? |
|---|---|---|
| `queued` | started in a process without the scheduler (the mcp service, the web app while the automations service runs); the scheduler claims it within seconds | yes |
| `running` | Erynne or Claude is working on it, or a routine is being started | yes |
| `waiting` | a Claude routine was started on claude.ai; waiting for its `report_automation_run` | yes |
| `reported` | the routine's report was saved by another process; the scheduler delivers it | yes |
| `done` | answered and delivered | no |
| `quiet` | answered `NOTHING_TO_REPORT`; nothing delivered | no |
| `failed` | something went wrong; the owner's Inbox says what | no |
| `timed_out` | a routine didn't report within 2 hours | no |
| `cancelled` | stopped by its owner, Claude on their behalf, or an admin; nothing delivered | no |

## Cancelling a run

A run that's still going (queued, running, waiting or reported) can be cancelled:
- by its **owner**, with **Cancel** beside the run on the Automator page, in the run's live window, or on the automation's card (`POST /api/automations/runs/{id}/cancel`);
- by **Claude**, for the owner, with `cancel_automation_run` (a connection that may edit, as for `run_automation`);
- by an **admin**, with **Cancel run** under Settings → Automator (`POST /api/admin/automations/{automation_id}/cancel`).

A cancelled run ends as `cancelled`, never `failed`: its error says who cancelled it, its raw output ends with a *Cancelled* step, nothing is delivered and nobody gets a "failed" note. What happens to the work:
- **queued** or **reported:** the scheduler only picks up runs still in those states, so it never starts or delivers it.
- **running** (Erynne, Claude with a key): the scheduler cancels the run's task. A cancel made in the scheduler's own process stops it at once; one made in another process stops it when the scheduler is woken (`automations:wake` in Valkey), or within 30 seconds without Valkey. A tool already working in a thread finishes, but its result is thrown away.
- **waiting** (a Claude routine): nothing of Mnemerynne's is running, and Mnemerynne can't stop a session on claude.ai. The run is marked cancelled, it drops off `list_automation_tasks`, and a report that arrives later is refused: `report_automation_run` tells Claude the run was cancelled and to stop. Open the run's *Claude's session* link to stop the session itself on claude.ai if it's still working.

A run that has already ended can't be cancelled (the API answers 409). Nothing that arrives after a cancel (the model's answer, a routine's report, a failure) changes the run: it stays `cancelled`. Cancelling doesn't pause the automation; its next scheduled run goes ahead.

## Timeline and run timing

Each run records `scheduled_for` (the time the scheduler claimed it for; empty for Run now and `run_automation`) and `delivered_at` (when its last delivery finished; empty when nothing was sent). The **Timeline** tab and `GET /api/automations/timeline?start=<ISO>&hours=24` (1–72 hours) measure a run from `scheduled_for`, else `started_at`, until `delivered_at`, else `finished_at`. So a run that starts late because the scheduler's 30-second tick or a full queue (two runs at once) held it shows that wait too. "Usually takes" averages the last 20 runs that ended `done` or `quiet`. Runs from before automations.db migration 0006 have neither field and are measured from `started_at` to `finished_at`.

## Limits

- 20 automations a person;
- at most every 15 minutes;
- two runs at a time across the server;
- 5 minutes per Erynne or Claude-key run.

Each run keeps its **raw output** (`automation_runs.trace`): every step, with each tool call's arguments and its result cut to 4,000 characters, at most 400 steps. It is saved every half-second while the run works, and `GET /api/automations/runs/{id}/stream` sends it to the owner's page as server-sent events. Each save also wakes the page through the `automations:run:{id}` channel, falling back to a 2-second poll without Valkey. Only the run's owner can read it. The raw output holds what the tools returned, so it is as private as the run's answer.

Runs are kept `MNEMERYNNE_AUTOMATION_RUN_DAYS` (90 by default). A new automation's time zone defaults to `MNEMERYNNE_TIMEZONE` (UTC). The scheduler runs in the web service; a run missed by less than an hour while it was down runs once when it's back, and older ones wait for their next time.

## Briefing and security tools

These are tools for Erynne and Claude, in chat and in automations:

- **my_briefing:** what's waiting for the person in Mnemerynne (unread notes, announcements, room mentions and DMs, proposals to apply).
- **mnemerynne_updates** (admins): updates waiting upstream and what they touch, as on Settings → Updates. It also lists installed packages with known vulnerabilities, checked at most every 6 hours by `pip-audit`, which `uv sync` installs with the app.
- **m365_security** (admins): per Microsoft 365 tenant, with the app's own token. Grant the Entra app these **application** permissions with admin consent, or that kind answers with the permission it's missing:

  | Kind | Permission |
  |---|---|
  | open Defender alerts | `SecurityAlert.Read.All` |
  | risky users | `IdentityRiskyUser.Read.All` |
  | Secure Score | `SecurityEvents.Read.All` |
  | risky sign-ins | `AuditLog.Read.All` (Entra ID P1/P2) |
- **security_feeds** `(since_days, product, min_cvss, kev_only, affects_us_only)`: new CVEs in what we run, one item per CVE across the feeds, with `affects_us` (which Wazuh agents run a version below the fix) and `feed_status` (each feed's health, so a broken feed is never mistaken for a quiet one). See [Security feeds](security-feeds.md).

## Claude's tools

These are for Claude, through the connector:
- `list_automations`;
- `run_automation` (needs a connection that may edit);
- `get_automation_run`;
- `cancel_automation_run` (needs a connection that may edit), to stop a run that's still going;
- `list_automation_tasks` and `report_automation_run`, for routines. A report for a cancelled run is refused.

Erynne doesn't get them: an automation never starts another.
