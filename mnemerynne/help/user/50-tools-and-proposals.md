# Tools and proposals

Erynne can do more than answer from documents. With **tools** she searches, reads and browses your files, opens PDFs at a page, asks internal systems, and drafts changes. Which tools you get depends on your groups; the ⚙ Advanced settings box on the Ask tab lists yours.

## Who gets what

| You are… | Erynne can… |
|---|---|
| Anyone | search, read and browse your documents; propose new files and edits **inside your Agents folder** only |
| In the **agent group** (an admin picks it) | also change files anywhere you may edit and where AI edits are allowed; read your mail and calendar and draft replies, if you linked Microsoft 365 |
| In an **internal system's group** | ask that system live (tickets, alerts, stats) |

If no model is available for the tools at the moment, your question gets a normal answer instead.

## Proposals: nothing happens until you say so

Erynne never changes anything herself. A change she drafts shows as a **card** under her answer:

- **An edit or a new file**: the file, where it goes, a list of what changes and, for text files, the exact difference.
- **An API action** (close a ticket, resolve an alert): the operation and its values.
- **A mail draft**: the recipient, subject and text.

Each card has **Apply** (or **Run it**, **Save to Drafts**, **Move to recycle bin**, **Allow**, depending on the kind) and **Dismiss**. Applying checks everything again at that moment: that you may still edit there, still use that system, and that the file hasn't changed since the draft. If the file did change, the card turns **stale**; ask her to redo it on the current version. Applied file changes keep the previous version ([Versions](versions.md)).

Each card says who asked ("Claude for you", or Erynne), carries the same one-sentence summary you were given in the chat, and shows when it expires: proposals nobody decides on expire after 7 days (admins can change this). Expired or dismissed proposals change nothing. When you dismiss one you can add a short reason, which Claude or Erynne can see afterwards instead of asking you.

**Several changes, one approval.** Claude can propose a batch, for example ten ticket status changes, as one card with one link. Each change has a tick box: untick the ones you don't want and press **Run the ticked ones**. The card then shows what ran, what you declined and what the system refused.

## Changes waiting for you

Claude (through the connector) proposes the same way, but its cards aren't in a chat here. It hands you a link to **Changes waiting for you** (`/proposals`), where every pending card from Claude and Erynne is listed. Open it from the link Claude gives you; nothing is applied until you press the button there.

## Some proposals wait for an admin

Two kinds can't be applied by you: letting the AI edit a whole library, and API actions an admin has marked as needing approval. These sit in the admins' queue (Access → Approvals). You can **Withdraw** one; an admin approves it (it then runs on your behalf) or turns it down with a note, which you and your Claude can see.

## Mail and calendar

People in the agent group with a linked Microsoft account can ask about their mail and calendar ("what did the Portland GM send me about the audit?", "what's on my calendar Thursday?") and have replies drafted. **Save to Drafts** puts the draft in Outlook's Drafts folder. Erynne never sends, moves or deletes mail, and your mail never goes to Claude through the connector (it does when you tick *Use External API*, under your own key).

## Internal systems

Questions that match a system's purpose go there automatically ("any critical alerts right now?", "which tickets changed today?"). The answer cites the call, and the citation opens the exact response it came from. Actions (closing a ticket, resolving an alert) are always proposals.

## Switching tools off

In Advanced settings, untick a tool to stop Erynne using it for your questions (for example mail, when you want answers from documents only). **Switch them all back on** restores the default. A *Use tools* switch, where shown, turns them all off for a question.
