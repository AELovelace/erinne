# Automator (alpha)

**Automator** runs jobs for you on a schedule: a morning briefing on weekdays, a security digest every morning, a check on the systems you can query every few hours. Erynne (or Claude) does the job with your access and sends you the answer. It's there if an admin turned it on for you.

On a big screen, the **Width** buttons on the right of the top bar make the page wider (50%, 75%, 100% of the window or Custom). See [Page width](previews.md#page-width).

## Making one

**New automation** opens an empty one; **Start from a template** fills it in with a Morning briefing, a Security digest or an API health check, for you to adjust.

- **Instructions:** either **Write it here** or **Use a prompt file**.
  - **Write it here:** type into **What to do** as a request, as you'd ask in Ask. If there may be nothing worth saying (a health check), ask for exactly `NOTHING_TO_REPORT` then, and nothing is sent.
  - **Use a prompt file:** search for a `.md` or `.txt` file you can read and pick it. Every run reads the file fresh, so editing the file changes what the automation does from its next run, and **Versions** on the file is its history (restore an older one to undo). If the file has a fenced code block (a block between two ```` ``` ```` lines), the first block is the instructions and the rest of the file can explain them to people. Otherwise the whole file is the instructions. The file can hold up to 60,000 characters of instructions.
  - Anyone who can edit the file can change what the automation does, so keep prompt files in a folder only the right people can edit.
  - If the file is deleted, or you lose access to it, the run fails and your Inbox says why. The automation's card shows the file as gone; pick another, or switch back to **Write it here**. What you typed in the box is kept when you switch, so switching back loses nothing.
- **When:** at a time on chosen days, or every so often (at most every 15 minutes), in your time zone.
- **Who runs it:**
  - **Erynne**, with the tools you tick. Leave them all unticked for every tool that only reads.
  - **Claude (your key)**, if you use Claude in chat, under your saved key.
  - **A Claude routine** you made on claude.ai. Give it the Mnemerynne connector and an API trigger, then paste the trigger's address and token here. Claude does the job there and reports back.
- **May propose changes:** off by default. When on, it may draft file edits, emails or system changes as proposals. They wait on the **Approvals** tab for you to apply.
- **Approve its proposals automatically:** off by default, and only there while *May propose changes* is on. When on, what the automation proposed is applied as you at the end of each run, with the same checks as pressing Apply yourself, so turn it on only for jobs you trust. Proposals that need a Mnemerynne admin (a whole library's AI edit, API actions marked for admin approval) still wait for one. The run's raw output says what was applied and what wasn't.
- **Send the answer to:**
  - **your Inbox:** a note linking to the full answer;
  - **a room:** posted by Erynne. When Erynne runs it, she uses only what everyone there may read. When Claude runs it (your key or a routine), Claude works with *your* access, so the report can hold what only you may see. To post it anyway, tick **Share what my access finds with everyone in this room**. The post then says it ran with your access;
  - **a draft in your Outlook:** it isn't sent;
  - **a webhook** your admins set up, such as a Teams channel.

## Runs

Each automation lists its runs; open one to read the answer with its sources. **Run now** runs it straight away and opens a window that shows the run as it happens. If a run fails, your Inbox says why.

**Raw output** (beside each run, and in the window Run now opens) shows every step the run took, numbered and timed:
- when it started and exactly what the model was asked (with a prompt file, also which file and version it followed);
- what the model said between steps;
- each tool it called, with the arguments it sent and, under **raw**, the result that came back (long ones are cut short);
- what it proposed, what was auto-approved, where the answer went, and any error.

While a run is going, the steps appear live; closing the window doesn't stop the run.

**Cancel** (beside a run that's still going, on the automation's card, and in the live window) stops it. The run ends as *cancelled*: nothing is sent anywhere and you don't get a "failed" note. Cancelling one run doesn't pause the automation; it still runs at its next time. For a Claude routine, Mnemerynne stops waiting and turns away its report if it comes later, but the session on claude.ai may keep going until Claude notices: open *Claude's session* to stop it there. A run that has already finished can't be cancelled. **Copy raw JSON** copies every step, which helps when you ask someone why a run went wrong. A run made through a room doesn't show its tool steps, and a Claude routine shows what it was sent and when it reported back, not its own steps.

## Approvals

The **Approvals** tab lists what your automations proposed, grouped by run: the ones waiting for you first, then those decided in the last two weeks, including any approved automatically. Apply or dismiss them there, just as under a chat answer. The number on the tab is how many are waiting. Your automation pauses (and your Inbox tells you) if you lose access to Automator; turn it back **On** once you have it again.

## Through Claude

With Claude connected:
- `list_automations` shows yours, and the prompt file each one follows;
- `run_automation` starts one;
- `get_automation_run` reads an answer;
- `cancel_automation_run` stops a run that's still going.
