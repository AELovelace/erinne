# The Files tab

Files is a full file manager for your **File box** and every **library** you can see.

## The layout

- **Left: locations.** 🏠 your File box, 📚 each library, with their folders. Click a folder to open it; the arrow expands it. Each location you can change has a 🗑️ **Recycle bin** at the bottom.
- **Middle: the list.** Name, status, modified date, size and kind; **list** or **grid** view; sort by name, date, size or type. People with Claude connected also see a **Claude** column, and people with AI edits an **AI edit** column (see [Properties](properties.md)).
- **Right: details** (the ⓘ button): a preview, Open, Download, Edit, properties, comments and versions for the selected file.
- **Breadcrumbs** above the list show where you are.

## Selecting

Click to select, Ctrl-click to add, Shift-click for a range, Ctrl-A for all, Escape to clear. Right-click opens a menu with the same actions as the toolbar. Double-click opens a folder or a file.

## Uploading

- **Upload ▾ → Files…** or **Folder…**: a folder upload keeps its subfolders.
- **Drag and drop** files or whole folders from your computer onto the list.
- The **upload queue** at the bottom right shows each file's progress: uploaded, then Queued, Indexing and Ready as its text is read for search. "Already here" means the same file was already in that folder, so nothing was added.

Any kind of file can be stored (up to 500 MB). PDF, Markdown, text, Word, PowerPoint, Excel, CSV, OpenDocument, RTF, HTML, code and config files become searchable; other kinds are stored only. Very large documents are stored but not indexed. See [Opening and previewing files](previews.md#searchable-or-not) for the statuses.

You can upload only where you can change files: your File box, and libraries or folders where you have write access.

## Toolbar actions

| Action | What it does |
|---|---|
| **New folder** | A folder here. |
| **Open** | Preview the file in a new tab, or open the folder. |
| **Edit** | Text and Markdown files you can change: the browser editor ([The organisation wiki](wiki.md#the-editor)). |
| **Download** | The original file(s). |
| **Rename** | Rename a file or folder (a file keeps its extension). Names must suit Windows too: no `\ / : * ? " < > \|`, no trailing dot or space. |
| **Move to…** / **Copy to…** | Pick a destination in any location you can write to. A copy of a file keeps its search index. |
| **Delete** | To the [recycle bin](recycle-bin.md). |
| **Re-embed** | Read the selection (or everything here) again for search. |
| **Properties** | Details, search state and the Claude / AI edit switches ([Properties](properties.md)). |
| **Restore**, **Delete forever**, **Empty bin** | In a recycle bin. |

Actions you can't use here are greyed out.

## Searching file names

The search box on the toolbar finds files and folders by name across everything you can read. Case, spaces, hyphens and underscores don't matter ("it ops" finds "IT-OPS"). This searches names only; to search contents, ask on the Ask tab.

## Your File box and the Agents folder

Your File box is private. It contains an **Agents** folder, made for you: that is where Claude and Erynne put files they create unless you say where, and the one place the AI may edit by default. You can move things out of it, and switch AI edits on for other folders of yours.

## Libraries on the file share

Some libraries live on the organisation's file share (`\\server\share`). For those, the share is the truth: what people add, rename, move or delete in Windows Explorer shows up here within a minute, and what you do here happens on the share at once. Who can see and change what follows the Windows permissions of your linked domain account, folder by folder. Office lock files, `Thumbs.db` and hidden files are ignored.

## Keyboard

Enter opens, Delete sends to the bin, F2 renames, Ctrl-A selects all, Escape clears the selection or closes a menu.
