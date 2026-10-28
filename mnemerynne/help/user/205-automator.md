# Automator (alpha)

**Automator** runs jobs for you on a schedule: a morning briefing on weekdays, a security digest every morning, a check on the systems you can query every few hours. Erynne (or Claude) does the job with your access and sends you the answer. It's there if an admin turned it on for you.

## Making one

**New automation** opens an empty one; **Start from a template** fills it in with a Morning briefing, a Security digest or an API health check, for you to adjust.

- **What to do:** write it as a request, as you'd ask in Ask. If there may be nothing worth saying (a health check), ask for exactly `NOTHING_TO_REPORT` then, and nothing is sent.
- **When:** at a time on chosen days, or every so often (at most every 15 minutes), in your time zone.
- **Who runs it:**
  - **Erynne**, with the tools you tick. Leave them all unticked for every tool that only reads.
  - **Claude (your key)**, if you use Claude in chat, under your saved key.
  - **A Claude routine** you made on claude.ai. Give it the Mnemerynne connector and an API trigger, then paste the trigger's address and token here. Claude does the job there and reports back.
- **May propose changes:** off by default. When on, it may draft file edits, emails or system changes, but only as proposals you apply yourself. Nothing is ever changed for you.
- **Send the answer to:**
  - **your Inbox:** a note linking to the full answer;
  - **a room:** posted by Erynne. Everyone there reads it, so she uses only what they all may read;
  - **a draft in your Outlook:** it isn't sent;
  - **a webhook** your admins set up, such as a Teams channel.

## Runs

Each automation lists its runs; open one to read the answer with its sources. **Run now** runs it straight away. If a run fails, your Inbox says why. Your automation pauses (and your Inbox tells you) if you lose access to Automator; turn it back **On** once you have it again.

## Through Claude

With Claude connected:
- `list_automations` shows yours;
- `run_automation` starts one;
- `get_automation_run` reads an answer.
