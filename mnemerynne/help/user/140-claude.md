# Using Erynne from Claude

Erynne is also an **MCP server**: Claude (Anthropic's assistant) can search your documents, read passages and pages, browse your files, and, if you allow it, create and change files, all as you, and only within what has been shared with Claude. Everything here is on the right half of [Preferences](preferences.md).

> What Claude reads is sent to Anthropic. That is why Claude through the connector only reaches the libraries and files an admin and you have shared with it, never your memories, and your mail and calendar only if an admin put you in the group for it (below). The exception is **Use External API** in chat (below), where you deliberately send Erynne's full reach under your own key.

## Three ways to connect

### Claude on the web, phone and Desktop (the connector)

No token to paste. Preferences shows the connector address (`https://…/mcp`):

- **Claude Team or Enterprise**: an organisation owner adds it once as a custom connector for everyone; each person then presses **Connect** in their own connector settings.
- **A personal Claude plan**: add the custom connector yourself with that address, then Connect.

Claude sends you to Erynne to sign in and approve the connection, where you choose whether it may edit files. After that it works in claude.ai, the phone apps and Desktop alike; turn it on in a chat from the tools menu. Your connections are listed under **Connected this way**, where you can disconnect one.

**More than one Mnemerynne** (work and home, say): give each connector its own name when you add it. Claude shows that name on every tool, and each box also introduces itself by the product name its admins set under Branding, so Claude can say which one an answer came from; ask it "which Mnemerynne is this?" and it answers with the `about` tool.

### Claude Code

```
claude mcp add --transport http mnemerynne https://YOUR-ADDRESS/mcp
```

then `/mcp` in Claude Code to sign in. Or use a token: add `--header "Authorization: Bearer YOUR_TOKEN"` to the command shown on Preferences.

### Claude Desktop with the extension

1. **Create token** on Preferences and copy it (shown once).
2. **Download the Mnemerynne extension** and double-click it; Claude Desktop opens an install dialog.
3. Paste the token when asked. The address is already filled in.

If the extension isn't offered, an admin hasn't published it yet; use the connector instead.

## Tokens

A token is a password for Claude. Each has a name, an expiry (30, 90 or 365 days), and a **Let Claude edit files** switch; without it, that connection can only search. Tokens are shown once; the list shows when each was created, expires and was last used. **Revoke** ends one at once. Changing your password keeps tokens working; an admin can revoke all of them.

## What Claude can search for you

The list under **What Claude can search for you** is exactly what Claude reaches: your File box (if you shared it) and the libraries an admin shared with Claude, minus anything someone unticked with the **Claude can see** switch ([Properties](properties.md)). Claude cannot see more than this, whatever it is asked.

## Where Claude can change files

With a token or connection that may edit, Claude can create and change **text, Markdown, Word and Excel** files, and save **any other kind of file it made itself** (an image, a PDF, a slide deck, a zip; stored as is, up to 20 MB), in the places listed under **Where Claude can change files**: your Agents folder, folders of yours where you switched AI edit on, and libraries where an admin allowed it. Every change keeps the previous version ([Versions](versions.md)). Claude can't delete, move things to the bin, or get more access by itself: for those it hands you an **approve link** to a card on *Changes waiting for you*, and nothing happens until you press the button there ([Tools and proposals](tools-and-proposals.md#changes-waiting-for-you)).

Claude can also comment on files and read comments ([Comments](comments.md)), and reads a short-lived copy of a file's text through a private link when it needs more than a passage.

**Following a proposal.** Claude can check what became of a proposal (`get_proposal`), list its pending and recently decided ones, and withdraw one it no longer needs, so it can propose something partway through a task, carry on, and report the outcome without asking you whether you approved. It can never approve, apply or reject anything itself: that stays with you on *Changes waiting for you*.

**Messages.** With a connection that may edit, Claude can leave a colleague a note inside Erynne (`send_message`, shown as "Claude for you") and read your unread notes and announcements (`list_messages`). See [Messages and announcements](messages-and-announcements.md).

**The scratchpad.** Your own Claude sessions can leave each other hand-over notes about work that spans sessions, on boards only your connections see (`read_scratchpad`, `write_scratchpad`, `claim_note`, `resolve_note`). See [The agents' scratchpad](agent-scratchpad.md).

## Mail and calendar

If an admin has put you in the mail group for Claude and you have linked your Microsoft 365 account on Preferences, Claude can search and read your mail (and the shared mailboxes you may use), see your calendar, and draft replies and new emails. A draft is a proposal: Claude hands you an approve link, you review the text and **Save to Drafts** puts it in Outlook's Drafts folder. Nothing is ever sent, moved or deleted. Everything Claude reads this way goes to Anthropic, which is why it takes the group; without it these tools simply refuse. See [Microsoft 365](microsoft-365.md).

## Claude in chat (your own API key)

If an admin has put you in the Claude group, **Claude in chat** lets you paste your own Anthropic API key and pick a model (Opus, Sonnet or Haiku). Then the **🌐 Use External API** switch on the Ask tab answers with Claude, billed to you, with all of Erynne's tools and reach: every document you can read, your internal systems, your mail and calendar if you are in the agent group, your memories and the conversation. All of that is sent to Anthropic under your key; the group exists so that only people who accept that get the switch. The key is checked with Anthropic when you save it and is never shown again; **Remove** forgets it.

## Activity

Admins can see every search Claude made and which files' text went back to it (Access → Claude activity), and can cut a connection off at once.
