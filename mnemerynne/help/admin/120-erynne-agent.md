# The Erynne agent (tools in chat)

People in one group get Erynne with the same tools Claude has: she searches, reads and browses their documents, opens PDFs at a page, asks the internal APIs they may use, reads mail and calendar if they linked Microsoft 365, and drafts changes to `.txt`, `.md`, `.docx` and `.xlsx` files. Everyone else gets her tools too, but only for reading and for proposals inside their own Agents folder.

## Setting it up

1. **Create the group** on the domain controller (for example `Erynne Agents`) and add people. They need their Mnemerynne user linked to their domain account. Admins aren't included automatically.
2. **The model.** The agent uses the same model path as answers: the [balancer nodes](balancer.md), least busy first, then `MNEMERYNNE_LLM_URL` when none answers. Those servers need tool support: llama-server with `--jinja`, or a chat template file. With LFM2.5, the no-think template (`lfm2-no-think.jinja`) is required, or every JSON call fails while the model thinks.
3. **Access → Settings → Erynne agent**: pick the group and save. The status line says whether a model answers and by which route. Below it are the limits: timeout per call, tool steps per question, tokens, and whether the model may think first.

## How she behaves

- **Edits wait for a click.** Each edit or new file is a card under the answer, with the change list and a diff for text files. Nothing happens until the person presses **Apply**, which checks everything again (still in the group, can still edit there, the file hasn't changed since; otherwise the card goes stale). The previous version is kept.
- **Where she may write**: the person's Agents folder and folders of theirs with AI edit on; libraries where *Claude and Erynne can edit files here* is on and the person can write. Outside the agent group, only the Agents folder.
- **What she reads**: everything the person can read (she runs on the box), unlike Claude. APIs follow each API's group.
- **MinErynnes**: when Claude answers in chat with a person's own key, reading-heavy work is handed to up to two local Erynne instances with the same limits.
- **Fallback.** If no model answers (nodes and the local generator are checked every 30 seconds) or it fails before doing anything, the question gets a normal cited answer instead.
- **Tool switches.** People can turn individual tools off for their questions in ⚙ Advanced settings; that never adds access.

## Records

Every tool call is logged in `agent_calls` (kept `MNEMERYNNE_AGENT_CALLS_DAYS`, 90), and answers record the agent model's name. Proposals nobody decides expire after `MNEMERYNNE_PROPOSAL_DAYS` (7); their content sits under `<data_dir>/proposals` until then and isn't backed up.

## Mail and calendar

Only for the agent group, and only through a linked Microsoft account: search and read mail, see the calendar, search SharePoint live, draft emails and replies. **Save to Drafts** puts a draft in Outlook; nothing is ever sent, moved or deleted, and there is no code to do it. Mail text goes to Claude through the connector only for the separate group named under Access → Connectors → Mail and calendar for Claude, and for people who tick *Use External API* in chat, under their own key. See [Microsoft 365](microsoft-365.md#mail-and-calendar).

## Evaluating the agent

`mnemerynne eval-answers GOLD --agent --as USER` measures what chat actually does: each gold question goes through the agent as that person, read-only (no proposals), on the same model path, and the report counts tool calls, model turns, fallbacks and uncited answers. See [Indexing and evaluation](indexing.md#evaluation).
