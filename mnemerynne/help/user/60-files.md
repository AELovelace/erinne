# The Files tab

Files is a full file manager for your **File box** and every **library** you can see.

## The layout

- **Left: locations.** 🏠 your File box, 📚 each library, with their folders. Click a folder to open it; the arrow expands it. Each location you can change has a 🗑️ **Recycle bin** at the bottom.
- **Middle: the list.** Name, status, modified date, size and kind; **list** or **grid** view; sort by name, date, size or type. People with Claude connected also see a **Claude** column, and people with AI edits an **AI edit** column (see [Properties](properties.md)).
- **Right: details** (the ⓘ button): a preview, Open, Download, Edit, properties, comments and versions for the selected file.
- **Breadcrumbs** above the list show where you are.

## Selecting

Click to select, Ctrl-click to add, Shift-click for a range, Ctrl-A for all, Escape to clear. Right-click opens a menu with the same actions as the toolbar. Double-click opens a folder or a file.

**Select all** on the toolbar ticks everything listed here, on a phone too; once everything is ticked it reads **Select none**. Right-clicking an empty part of the list offers it as well.

## Uploading

- **Upload ▾ → Files…** or **Folder…**: a folder upload keeps its subfolders.
- **Drag and drop** files or whole folders from your computer onto the list.
- The **upload queue** at the bottom right shows each file's progress: uploaded, then Queued, Indexing and Ready as its text is read for search. "Already here" means the same file was already in that folder, so nothing was added.
- **Big files (over 32 MB) go up in pieces**, so a dropped connection doesn't lose the upload:
  - If your Wi-Fi drops, your laptop sleeps or the server restarts for an update, the queue says **Connection lost · trying again** and carries on by itself where it stopped.
  - If you closed the tab or reloaded, choose the same file for the same folder again: it says **Carrying on** and sends only what's missing. Unfinished uploads are kept for a day.
  - **Putting it together** at the end checks the whole file; a very big one takes a minute or two.
  - While files go up, the page keeps your screen awake so the computer doesn't sleep. Keep the tab open until the queue says done.

Any kind of file can be stored, up to the server's limit (500 MB unless your admin raised it). PDF, Markdown, text, Word, PowerPoint, Excel, CSV, OpenDocument, RTF, HTML, code and config files become searchable; other kinds are stored only. Very large documents are stored but not indexed. See [Opening and previewing files](previews.md#searchable-or-not) for the statuses.

You can upload only where you can change files: your File box, and libraries or folders where you have write access.

## Toolbar actions

| Action | What it does |
|---|---|
| **New folder** | A folder here. |
| **Open** | Preview the file in a new tab, or open the folder. |
| **Edit** | Text and Markdown files you can change: the browser editor ([The organisation wiki](wiki.md#the-editor)). The page view (Open) has an **Edit** button too, left of Download, for the same files. |
| **Download** | One file downloads as it is. Several files, or a folder, come down as **one zip** with the folders inside kept as they are. See [Downloading folders and many files](#downloading-folders-and-many-files). |
| **Rename** | Rename a file or folder (a file keeps its extension). Names must suit Windows too: no `\ / : * ? " < > \|`, no trailing dot or space. |
| **Move to…** / **Copy to…** | Pick a destination in any location you can write to. A copy of a file keeps its search index. |
| **Delete** | To the [recycle bin](recycle-bin.md). |
| **Re-embed** | Read the selection (or everything here) again for search. |
| **Properties** | Details, search state and the Claude / AI edit switches ([Properties](properties.md)). |
| **Share** | Share one file or folder with a person, a group, everyone, or anyone with a link. See [Sharing](#sharing). |
| **Restore**, **Delete forever**, **Empty bin** | In a recycle bin. |

Actions you can't use here are greyed out.

## Downloading folders and many files

Select a folder, several files, or everything (**Select all**), then **Download** (or right-click → **Download as zip**). A folder's details (ⓘ) have **Download as zip** too, and right-clicking an empty part of the list offers **Download everything here as zip**.

- The zip is named after the folder, or the folder the items are in. A message says how many files it holds and how big it is.
- It starts at once: the server makes the zip while it sends it, so a big folder doesn't make you wait first. The browser shows how much has arrived, but not the total, because that is only known at the end.
- Only what you may open goes in: folders you can't see, files in folders you may only pass through, and anything in the recycle bin are left out.
- Pictures, films, PDFs and Office files go in as they are, since they don't shrink. Text files are compressed.
- If a file can't be read while the zip is made (say it was moved on the share that moment), the zip has a **Not included.txt** naming it.
- Up to 20,000 files at a time. For more, download a few folders at a time.
- The download link works for an hour, for you only. If a download stops halfway, download it again from Files.

## Sharing

Select one file or folder and choose **Share** (or right-click → **Share…**). The dialog lists who has it through shares, and lets you add:

| Share with | Who gets it |
|---|---|
| **A person** | One account. |
| **A domain group** | Everyone in one of the groups on the NAS (the domain's groups), as its members change. |
| **A Mnemerynne group** | Everyone in a group an admin made under Access. |
| **Everyone with an account** | Every active Mnemerynne account. |
| **Anyone with the link** | Whoever has the link, without signing in. |

- **They can:** **View** (open and download) or **Edit** (also save changes in the browser editor, and in a shared folder add and change files). You can only give **Edit** for something you can change yourself.
- **For:** until removed, or 1, 7 or 30 days. A link lasts **7 days** unless you choose otherwise.
- Sharing a **folder** shares everything in it, including files added later. People see it as the top: the folders above it stay hidden from them.
- Shares work **in Mnemerynne only**. The permissions on the share itself (what Explorer shows) don't change, so a share never opens anything over `\\server\share`.
- A share lasts only while **you** could still open the item yourself. If you lose access, or your account is switched off, your shares stop working until you get it back.
- Things shared with you can't be shared on. SharePoint files can't be shared here; share them in SharePoint.
- **Remove** (or **Turn off** for a link) takes a share back at once. You can remove the shares you made, and people who can change the item can remove any of its shares.

### Shared with me

**🤝 Shared with me** in the tree lists what people shared with you. Files open, download and (with **Edit**) change as anywhere else. A shared folder opens with everything in it, and the path above it starts at "Shared with me".

### Anyone with the link

- The link is random and names nothing: no file name, number or folder, so links can't be guessed or counted through. It looks like `https://…/s/kQ9x…` (43 characters after `/s/`).
- Links are always **view only**. Opening one shows the file's name and a **Download** button, or a shared folder's list, where files download when clicked. Nothing opens as a page in the browser.
- Anyone can pass a link on, so only use one for what you'd be happy to see forwarded.
- A link that was turned off, has expired, or never existed shows the same plain "Not found".
- **Copy link** in the dialog copies it again whenever you need it.

## Searching file names

The search box on the toolbar finds files and folders by name across everything you can read. Case, spaces, hyphens and underscores don't matter ("it ops" finds "IT-OPS"). This searches names only; to search contents, ask on the Ask tab.

## Your File box and the Agents folder

Your File box is private. It contains an **Agents** folder, made for you: that is where Claude and Erynne put files they create unless you say where, and the one place the AI may edit by default. You can move things out of it, and switch AI edits on for other folders of yours.

## Libraries on the file share

Some libraries live on the organisation's file share (`\\server\share`). For those, the share is the truth: what people add, rename, move or delete in Windows Explorer shows up here within a minute, and what you do here happens on the share at once. Who can see and change what follows the Windows permissions of your linked domain account, folder by folder. Office lock files, `Thumbs.db` and hidden files are ignored.

## Keyboard

Enter opens, Delete sends to the bin, F2 renames, Ctrl-A selects all, Escape clears the selection or closes a menu.
