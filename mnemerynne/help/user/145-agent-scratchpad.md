# The agents' scratchpad

When you run more than one Claude against Mnemerynne (a couple of Claude Code sessions, Claude Desktop, claude.ai), they can't see each other's conversations. The scratchpad is a small notebook they share, so one session can leave the next a hand-over: "steps 1 to 3 done, step 4 next, leave the backup job alone."

It is yours alone. Every connection you hold (each token and each connector sign-in) reads and writes the same boards, and nobody else's Claude, Erynne or an admin can read them. It needs a connection that may edit, like any other writing ([Claude](claude.md#tokens)); a search-only connection can read the boards but not write on them.

## Boards and notes

A **board** is named after the work, for example `nas-migration`, and appears the first time something is written on it. A **note** is a short piece of Markdown on a board, signed with the agent that wrote it. Claude picks its own label (the machine or task it is on); when it doesn't, the note is signed with the connection's name, such as *laptop #3* or *Claude Code #7*, so two unlabelled sessions still look different.

What Claude can do through the connector:

| Tool | What it does |
|---|---|
| `read_scratchpad` | Lists your boards, or one board's open notes, oldest first. `since` shows only what changed after a time; `include_done` adds resolved notes. |
| `write_scratchpad` | Jots a note on a board. `replaces` retires an older note of its own, so the board shows one current state instead of a trail of corrections. |
| `claim_note` | Takes a note's task for that agent. If another agent already holds it, Claude is told who and when, and picks something else. `release` hands it back. |
| `resolve_note` | Marks a note done (or open again). |

The connector's instructions tell Claude to read the board before multi-session work, claim a task before starting it, resolve it when done, and jot a short hand-over before it stops. You can also just ask: "check the scratchpad for nas-migration" or "leave a note for the next session".

## The Scratchpad page: reading and directing

**Scratchpad** in the top bar shows every board: each note with who wrote it, who holds a claim, and replies under
the note they answer. **Sessions** (on the right; a button on a phone) lists the agents that worked on the board in
the last week: active (a call in the last 10 minutes), idle, or gone, with the last tool each used and the notes it
holds. The page refreshes by itself every few seconds.

You can write on a board too. Your note is a **direction**:

- **For:** everyone on the board (every session that has worked there), or one session by its label.
- **Urgent** puts it first and asks the session to deal with it before what it was doing.
- **Stop and hand over**, **Push what you have** and **Status?** fill in the common ones.
- **Reply** answers a note; **Edit** sends a new direction in place of yours; **Resolve**, **Reopen**,
  **Release** (someone's claim) and **Delete** work on any note.

A direction reaches each session it's for **once, with that session's next call to the connector**, whatever tool
it calls. It shows *seen by …* when it has. The session should claim it, answer on the board and resolve it. A
session that is waiting for you, or that never calls the connector, won't see it: the
[Claude Code hook](#the-claude-code-hook) reaches those.

Agents can't write directions: a note only counts as yours when you write it on this page.

## The Claude Code hook

`clients/claude-code-hooks` in the Mnemerynne repository is a small script for Claude Code. It asks for your
directions when a session starts, whenever you send a prompt, and every so often while Claude works. It also keeps
a session from stopping while a direction for it is still open. Installing it is in its README; it uses a Claude
token from [Claude](claude.md#tokens) and a label per session, which it tells Claude to sign its notes with.

## Claims lapse

A claim is a promise, not a lock. If a session stops without resolving its note, the claim lapses after a while (two hours unless your admin changed it) and the next session can take the task. Resolving a note always clears its claim.

## On Preferences

[Preferences](preferences.md#your-agents-scratchpad) shows how many notes are open on which boards, and who holds a
claim right now, with a link to the Scratchpad page. **Clear scratchpad** takes every note off at once (they move to
the archive).

## Keeping and the archive

Notes nobody has touched for 90 days (unless your admin set otherwise) come off the scratchpad, open or not. So do
notes you delete and boards you clear. None of them is thrown away: they go to the **Archive** (on the Scratchpad
page), where you can search them by text, agent or board. `my_briefing` tells Claude how many notes are open, so a
fresh session finds unfinished work without being asked.

## What it isn't

- Not a message to a colleague: that is `send_message` ([Messages and announcements](messages-and-announcements.md)).
- Not team chat: that is [Rooms](rooms.md).
- Not a document: anything worth keeping belongs in a file in your Agents folder, with the note pointing at it.
