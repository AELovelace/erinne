# The Access page

**Access** is the admin tab. It appears only for admins: local admins (role `admin`), bootstrap usernames from `MNEMERYNNE_ADMIN_USERS`, and members of the admin domain group, and only when they are signed in from an admin network if `MNEMERYNNE_ADMIN_NETWORKS` is set. Everything done here is written to **Recent changes**. On a phone the section tabs are a strip you swipe sideways, and the trees (Files & folders, APIs, Remote) fold above the pane.

## Files & folders

A tree of libraries and their folders on the left; pick one to see and change who can reach it on the right. Create a library with the box above the tree. For each library or folder: read, write and Claude grants to people and groups, inheritance, the library's Claude switches, and deletion. See [Libraries, grants and inheritance](libraries-and-grants.md). Libraries on the file share show their Windows-managed state instead of grants ([The SMB share](share.md)).

## Groups

Mnemerynne's own groups: create one, add people, and grant libraries to it. A group can also *include an Entra group*, so a Microsoft 365 group's members get the group's grants. See [Groups](groups.md).

## Accounts

Every account: create one, reset a password, change the role or "Can sign in", set groups, link or unlink the domain account, see Microsoft 365 identities and Claude connections, revoke Claude tokens. See [Accounts and domain accounts](accounts.md).

## APIs

Internal systems Erynne and Claude may query: presets (Wazuh, Atera), uploaded templates, each API's operations, credentials, certificate pinning, AD group, Claude switch, admin-approval actions, tests and recent calls. See [Internal APIs](internal-apis.md).

## Remote

LAN-only web apps served through Mnemerynne at `/remote/<slug>/` for the groups you pick: address, certificate pinning, link rewriting, in the window or a new tab. See [Remote apps](remote-apps.md).

## Approvals

Requests only an admin can decide: letting the AI edit a whole library, and API actions marked as needing admin approval. Approve runs it on the requester's behalf; Reject turns it down with a note. See [Approvals](approvals.md).

## Announcements

Post a notice for everyone or a group, shown as a card on every page until dismissed or acknowledged; see who saw and acknowledged each. See [Announcements and messages](announcements-and-messages.md).

## Signage

What the screens paired under Media show while nobody uses them: the content browser (the Signage folder and the Media library), playlists, the schedule per screen or group, each screen's state and the CRT effects. See [Signage](signage.md).

## Who can see what

One row per person and library: whether they read, edit and can search it from Claude, and through which grant, group or share permission. The place to answer "why can Rob see the Brand library?"

## Claude activity

Every search Claude made through the connector or a token, who it was for, what it asked, and which files' text went back. Kept for `MNEMERYNNE_MCP_CALLS_DAYS` (90 days). See [Claude](claude.md).

## Recent changes

The access log: every change made on this page, password changes, Claude switch changes, proposals approved or rejected, settings changed, with who did it and when.

## Settings

Two columns.

**This box**

- **Administrators**: the domain group whose members are admins (default `Domain Admins`); one or more groups separated by `;`, AD or Entra.
- **Erynne agent**: the group that gets Erynne's tools, the model status, and the limits (timeout, tool steps, tokens, thinking). See [The Erynne agent](erynne-agent.md).
- **Balancer nodes**: addresses and key of the boxes that run the answer model, with live status. See [Balancer nodes](balancer.md).
- **Organisation wiki**: which library the Wiki tab shows. See [Setting up the wiki](wiki-setup.md).
- **Updates**: whether a newer version is on GitHub, what it changes (database update, service files), and how the last update went. The page can't start an update; see [Updating](updating.md).

**Connectors**

- **Microsoft 365**: tenants, consent, SharePoint libraries, shared mailboxes. See [Microsoft 365](microsoft-365.md).
- **Claude in chat**: the group that may use its own Anthropic API key in chat. See [Claude in chat](claude-in-chat.md).
- **Mail and calendar for Claude**: the group whose members' Claude gets mail and calendar tools through the connector (empty = off). See [Microsoft 365](microsoft-365.md#mail-and-calendar).
- **Links Claude can open**: whether Claude gets short-lived text links and for how long. See [Claude](claude.md#links-claude-can-open).

**Themes** (full width, between the columns and Experimental)

- The looks the box can wear: Sakura, Neon, Corporate, Sad Girls Club and your own, with the default, who gets what, theme packs and the organisation's branding. See [Themes](themes.md).

**Experimental** (full width, at the bottom)

- **Experimental features**: the group that gets features still being tried out.
- **Development diagnostics**: the group whose members' Claude may pull raw diagnostics with the `diagnose` tool; empty = off ([Troubleshooting](troubleshooting.md#development-diagnostics)).

The address bar keeps the tab (`#tab=settings`), so links to a tab can be shared with other admins.
