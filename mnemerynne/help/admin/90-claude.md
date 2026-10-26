# Claude: the connector, tokens and switches

Mnemerynne is an MCP server (`/mcp`): Claude on the web, phone, Desktop and Claude Code can search people's documents and, if allowed, edit files, acting as the person and within what is shared with Claude. What Claude reads goes to Anthropic, so every piece of this is opt-in.

## Prerequisites

`MNEMERYNNE_PUBLIC_URL` set to the address people use, HTTPS on the proxy, and for the claude.ai connector the box reachable from the internet at `/mcp`, `/.well-known/oauth-*` and `/oauth/*` ([Security and exposure](security.md)).

## Three ways in

1. **The connector (OAuth)**: Preferences shows the address. A Claude Team or Enterprise owner adds it once as a custom connector for everyone; on personal plans people add it themselves. Claude sends the person here to sign in and approve, where they choose whether it may edit. Only apps whose return address is claude.ai, claude.com or this computer (Claude Code) may register. Access tokens last an hour; refresh tokens 90 days and are replaced on each use (a refresh token used twice ends the connection as copied).
2. **Claude Code**: `claude mcp add --transport http mnemerynne https://HOST/mcp` then `/mcp` to sign in, or with a bearer token.
3. **The Desktop extension**: a small relay packed from `clients/claude-desktop` with the public address and, for an internal CA, Caddy's root certificate as `ca.pem`. Copy `mnemerynne.mcpb` to `<data_dir>/mnemerynne.mcpb` (owner mnemerynne) and Preferences offers it for download. Staff create a token and paste it when the extension asks.

## Tokens and connections

People make tokens on Preferences (30, 90 or 365 days, optionally allowed to edit). Each account's dialog on Access → Accounts lists their tokens and OAuth connections; **Revoke Claude tokens** (or `mnemerynne-admin revoke-tokens NAME`) ends all of them at once, and deactivating the account does too. Connections unused for `MNEMERYNNE_OAUTH_CLIENT_DAYS` (30) are removed by the worker.

## What Claude can reach

Nothing until an admin shares it:

- **Per library**: *Share with Claude* on Access → Files & folders, plus the Claude flag on each grant. Off by default.
- **Per File box**: on the account's dialog, whether Claude may search it; the owner can also turn it off.
- **Per file or folder**: people with write access can untick *Claude can see* (the Claude column in Files); a folder covers everything below it. They can only narrow what you shared, never widen it.
- **Internal APIs**: each has its own *Claude can query this* switch, off by default.
- **SharePoint libraries**: their Claude switch, off by default, with the per-file check against SharePoint.

Erynne is unaffected by any of this: she runs on the box and sees what the person sees. Memories, mail and calendar never go to Claude.

## What Claude can change

With a token or connection that may edit, Claude can create and change `.txt`, `.md`, `.docx` and `.xlsx` files (and folders, renames, moves and comments) where **all** of these hold: the person can edit there themselves; the place is shared with Claude; and AI edits are allowed, which means the person's Agents folder or a folder they switched on in their File box, or a library where *Claude and Erynne can edit files here* is on, minus anything marked read-only for the AI. Every change keeps the previous version. Claude never deletes: `delete_item` only proposes a move to the recycle bin for the person to apply, and `request_edit_access` asks the person (or, for a whole library, an admin) rather than taking.

## Links Claude can open

Tool results carry links for the person, which need a sign-in. When *Links Claude can open* (Access → Settings) is on, results that point at a file also carry a short-lived private address Claude's own fetch tool can open to read that file's **extracted text** (never the original), from the cited page on, a part at a time. Each link is per person, connection and file, expires after the set minutes (60 by default), allows at most 100 fetches, is checked on every fetch (person active, connection valid, file still shared) and is logged with the other calls. The trade-off: anyone holding such an address can read that text until it expires, for example from a shared transcript, which is why they are short-lived. Turn the feature off here to stop issuing them.

## Watching it

**Access → Claude activity** lists every tool call made for Claude: when, for whom, the tool, what was asked, and which files' text went back. It is kept for `MNEMERYNNE_MCP_CALLS_DAYS` (90). Calls are also rate-limited per token (`MNEMERYNNE_MCP_CALLS_PER_MINUTE`, 60).

## The tools, for reference

Read-only: `search_documents`, `read_passage`, `view_page`, `list_libraries`, `browse_folder`, `find_files`, `open_file`, `list_changes`, `list_comments`, `list_versions`, `list_apis`, `query_api`, `list_proposals`, `get_proposal`. Changing files: `create_file`, `edit_text`, `edit_document`, `edit_spreadsheet`, `create_folder`, `rename_item`, `move_item`, `add_comment`. Proposing only: `propose_api_action`, `delete_item`, `request_edit_access`. The server's instructions tell Claude to cite every claim, never fetch links meant for people, and never say a proposed change is done.
