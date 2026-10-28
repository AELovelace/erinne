# Automator (alpha)

Automator runs jobs on a schedule for the people allowed to use it: a morning briefing, a security digest, a check on the internal systems. Each one is a prompt, a schedule and a runner, and the answer goes to the person's Inbox, a room, a draft in their Outlook, or a webhook. People make theirs under **Automator**; this page is the admin side.

## Settings → Automator

- **Automator is on for this organisation:** off until an admin turns it on. While it's off, the tab and its API aren't there, nothing runs, and Claude's Automator tools refuse. Automations are kept for when it's back.
- **Who may use it:** a group (several separated by `;`); empty means admins only. Admins always may. Someone who leaves the group, or whose account is turned off, has their automations paused at the next run, with a note saying why.
- **Automations:** everyone's, with **Pause**. The owner is told and can turn it back on.
- **Webhooks:** where answers may go besides Mnemerynne. Only admins add addresses (https only); people pick from this list, optionally limited to a group.
  - **JSON:** posts `{automation, run_id, status, output, link}`.
  - **Teams:** an Adaptive Card for an incoming webhook or a Workflows "post to channel" trigger.
  - **Slack:** an incoming-webhook message.
  - **Signing:** with a secret, each post carries `X-Mnemerynne-Signature: sha256=<HMAC of the body>`.
  - **Retries:** failures are tried three times.
- **Security feeds:** the outside sources `security_feeds` reads (see below).

Every change is logged in Recent changes (`settings.automations*`, `automations.*`).

## Runners

- **Erynne:** the agent's usual model (balancer nodes, then `MNEMERYNNE_LLM_URL`) with the owner's access and the tools they picked. If they picked none, every tool that only reads is used. Tools that change something (files, mail drafts, API actions) are offered only when the automation **may propose changes**, and even then they only make proposals the owner applies.
- **Claude (your key):** the same, driven by Claude under the owner's saved key (*Claude in chat*). Only for people in the Claude group.
- **A Claude routine:** for work done on claude.ai.
  1. The owner makes a routine at claude.ai/code/routines with the Mnemerynne connector and an **API trigger**, and pastes the trigger's address and token into the automation. The token is stored encrypted with `MNEMERYNNE_SECRET_KEY`.
  2. At run time Mnemerynne starts the routine with the task and the run's id.
  3. Claude works through the connector and calls `report_automation_run`, which delivers the answer.
  4. A routine that doesn't report within 2 hours is marked timed out. Anthropic allows 30 starts per routine per hour.

**Rooms:** an automation that posts in a room always runs as Erynne, reading only what *everyone in that room* may read, exactly as when she's @mentioned there. Its answer is posted for all of them, so it can't use the owner's private files or mail.

**Nothing to report:** an answer of exactly `NOTHING_TO_REPORT` (the templates ask for it, e.g. a health check with nothing wrong) is recorded as *nothing to report* and not delivered.

## Limits

- 20 automations a person;
- at most every 15 minutes;
- two runs at a time across the server;
- 5 minutes per Erynne or Claude-key run.

Runs are kept `MNEMERYNNE_AUTOMATION_RUN_DAYS` (90 by default). A new automation's time zone defaults to `MNEMERYNNE_TIMEZONE` (UTC). The scheduler runs in the web service; a run missed by less than an hour while it was down runs once when it's back, and older ones wait for their next time.

## Briefing and security tools

These are tools for Erynne and Claude, in chat and in automations:

- **my_briefing:** what's waiting for the person in Mnemerynne (unread notes, announcements, room mentions and DMs, proposals to apply).
- **mnemerynne_updates** (admins): updates waiting upstream and what they touch, as on Settings → Updates. With `pip-audit` installed on the server (`uv tool install pip-audit`), it also lists installed packages with known vulnerabilities, checked at most every 6 hours.
- **m365_security** (admins): per Microsoft 365 tenant, with the app's own token. Grant the Entra app these **application** permissions with admin consent, or that kind answers with the permission it's missing:

  | Kind | Permission |
  |---|---|
  | open Defender alerts | `SecurityAlert.Read.All` |
  | risky users | `IdentityRiskyUser.Read.All` |
  | Secure Score | `SecurityEvents.Read.All` |
  | risky sign-ins | `AuditLog.Read.All` (Entra ID P1/P2) |
- **security_feeds:** the feeds above, fetched at most hourly and kept to each feed's keywords. The kinds:
  - **RSS/Atom:** vendor advisories.
  - **CISA KEV:** `https://www.cisa.gov/sites/default/files/feeds/known_exploited_vulnerabilities.json`.
  - **NVD:** `https://services.nvd.nist.gov/rest/json/cves/2.0`, with one keyword per feed.

## Claude's tools

These are for Claude, through the connector:
- `list_automations`;
- `run_automation` (needs a connection that may edit);
- `get_automation_run`;
- `list_automation_tasks` and `report_automation_run`, for routines.

Erynne doesn't get them: an automation never starts another.
