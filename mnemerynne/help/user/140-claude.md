# Using Erynne from Claude

Erynne is also an **MCP server**: Claude (Anthropic's assistant) can search your documents, read passages and pages, browse your files, and, if you allow it, create and change files, all as you, and only within what has been shared with Claude. Everything here is on the right half of [Preferences](preferences.md).

> What Claude reads is sent to Anthropic. That is why Claude only reaches the libraries and files an admin and you have shared with it, never your mail, calendar or memories.

## Three ways to connect

### Claude on the web, phone and Desktop (the connector)

No token to paste. Preferences shows the connector address (`https://…/mcp`):

- **Claude Team or Enterprise**: an organisation owner adds it once as a custom connector for everyone; each person then presses **Connect** in their own connector settings.
- **A personal Claude plan**: add the custom connector yourself with that address, then Connect.

Claude sends you to Erynne to sign in and approve the connection, where you choose whether it may edit files. After that it works in claude.ai, the phone apps and Desktop alike; turn it on in a chat from the tools menu. Your connections are listed under **Connected this way**, where you can disconnect one.

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

With a token or connection that may edit, Claude can create and change **text, Markdown, Word and Excel** files in the places listed under **Where Claude can change files**: your Agents folder, folders of yours where you switched AI edit on, and libraries where an admin allowed it. Every change keeps the previous version ([Versions](versions.md)). Claude can't delete, move things to the bin, or get more access by itself: for those it hands you an **approve link** to a card on *Changes waiting for you*, and nothing happens until you press the button there ([Tools and proposals](tools-and-proposals.md#changes-waiting-for-you)).

Claude can also comment on files and read comments ([Comments](comments.md)), and reads a short-lived copy of a file's text through a private link when it needs more than a passage.

## Claude in chat (your own API key)

If an admin has put you in the Claude group, **Claude in chat** lets you paste your own Anthropic API key and pick a model. Then the **🌐 Use External API** switch on the Ask tab answers with Claude, billed to you, with Erynne's tools but Claude's reach. The key is checked with Anthropic when you save it and is never shown again; **Remove** forgets it.

## Activity

Admins can see every search Claude made and which files' text went back to it (Access → Claude activity), and can cut a connection off at once.
