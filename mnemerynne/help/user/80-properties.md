# Properties, "Claude can see" and "AI can edit"

Right-click a file or folder and choose **Properties…** (or press the toolbar button). The dialog has three parts.

## Details

Name, kind, size, where it lives, who uploaded it and when, and for folders how many items are inside.

## Search state

For a file: its status (see [Opening and previewing files](previews.md#searchable-or-not)), how many passages were read from it and how many of those are embedded (ready for meaning-based search), and the last error if reading failed. For a folder: the same, added up. **Re-embed** reads the selection again in the background; it is useful after a file was changed outside Mnemerynne or when an admin says the search model changed.

## Switches

Two switches, each applying to the file, or to a folder and everything inside it. Only people who can change that folder can flip them, and they apply to everyone, not just to you.

### Claude can see this

Whether Claude (through the connector or the Desktop extension) may search, browse, read and open this file. Ticked by default wherever an admin has shared the library with Claude at all. Untick it to keep a file out of Claude's reach while people here still use it. Where the admin has not shared the library with Claude, the switch is greyed out: you cannot share more than the admin did.

Erynne is not affected by this switch; she runs on your server and sees whatever you can read.

### AI can edit

Whether Claude, and Erynne's proposals, may **change** this file or the files in this folder.

- In your **own File box** you can switch it on for any folder or file. Your **Agents** folder has it on from the start: that is where Claude and Erynne put new files unless you say otherwise.
- In a **library**, only an admin can allow AI edits for the library as a whole; there the switch can only make a file or folder read-only for the AI again.
- A folder's setting covers everything below it.

Nothing the AI changes is lost: every edit keeps the previous version ([Versions](versions.md)), and Erynne's edits are only proposals until you apply them ([Tools and proposals](tools-and-proposals.md)).
