# Libraries, grants and inheritance

## Collections

Every file lives in a **collection**: a person's private **File box**, or a shared **library**. Libraries are made on Access → Files & folders (or `mnemerynne-admin create-library NAME`), and, with a share configured, by creating a top-level folder on the share ([The SMB share](share.md)). SharePoint libraries are read-only copies managed by the worker ([Microsoft 365](microsoft-365.md)).

## Grants

For a library that lives inside Mnemerynne (not on the share), access is given by **grants**, to a person or a group, on the library or on a folder in it:

| Grant | Lets them |
|---|---|
| **Read** | see, search, open and be cited from everything the grant covers |
| **Write** | also upload, make folders, rename, move, copy in, delete to the bin, edit in the browser, restore versions, comment, flip the Claude and AI-edit switches |
| **Claude** | search it from Claude as well (only meaningful once the library itself is shared with Claude) |

Folder grants narrow a library: grant the library to nobody and a folder to a group, and that group sees only that folder. A folder **inherits** its parent's grants by default; turn inheritance off on a folder to start from a clean slate there (optionally copying the inherited grants first), for a restricted sub-folder.

`mnemerynne-admin grant LIBRARY USER [--write]` grants from the CLI.

Everyone's File box is theirs alone: no grants apply, and admins can't open it (they can revoke its Claude access and wipe memories, not read files).

## Claude switches on a library

Two switches per library, on Access → Files & folders:

- **Share with Claude**: whether Claude may reach the library at all (then each grant's Claude flag and each file's *Claude can see* switch decide the rest). Off by default for new libraries.
- **Claude and Erynne can edit files here**: whether AI edits are allowed in the library. Off by default. People with write access can still make individual files or folders read-only for the AI with the *AI can edit* switch; they can't allow more than this switch does.

The same two exist per File box on each account's dialog (Claude may search it; Claude may edit in it), with the owner's own switches on top.

## Erynne's reach versus Claude's

Erynne runs on your server and reads everything the person can read. Claude reads a subset: libraries shared with Claude, through grants whose Claude flag is on, minus files and folders hidden with *Claude can see*, and the File box only if its switch is on. **Who can see what** shows both columns.

## Deleting a library

Deleting on Access → Files & folders moves a share library's folder to the trash directory, and removes a Mnemerynne-hosted library with its files (they go through the recycle bin like any deletion). Grants go with it.

## Folder roles

Every File box has an **Agents** folder (role `agents`), made at start-up or on first use: the default place for files Claude and Erynne create, and the one place in a File box where AI edits are on from the start. If someone deletes it, a new one is made next time it is needed.
