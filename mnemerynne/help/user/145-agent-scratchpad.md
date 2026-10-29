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

## Claims lapse

A claim is a promise, not a lock. If a session stops without resolving its note, the claim lapses after a while (two hours unless your admin changed it) and the next session can take the task. Resolving a note always clears its claim.

## On Preferences

[Preferences](preferences.md#your-agents-scratchpad) shows how many notes are open on which boards, and who holds a claim right now. **Clear scratchpad** deletes every note at once; there is no undo, because notes are the agents' own working memory, not files, and keep no versions.

## Keeping

Notes nobody has touched for 90 days (unless your admin set otherwise) are deleted, open or not. `my_briefing` tells Claude how many notes are open, so a fresh session finds unfinished work without being asked.

## What it isn't

- Not a message to a colleague: that is `send_message` ([Messages and announcements](messages-and-announcements.md)).
- Not team chat: that is [Rooms](rooms.md).
- Not a document: anything worth keeping belongs in a file in your Agents folder, with the note pointing at it.
