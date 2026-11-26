# Desktop app

The **desktop app** is a Windows program that shows this site in its own window with **tabs**, the way a browser does, so you can keep Ask, Files, a wiki page, Rooms and Media open side by side and switch between them. It also puts the libraries you pick into **File Explorer** as a drive, like OneDrive or Box: files download when you open them, and what you save goes back to the site.

It's for people in the group an admin picked. If you're in it, **Preferences → Desktop app** shows the download.

## Setting it up

1. On **Preferences → Desktop app**, download the Windows installer and run it. It doesn't need admin rights.
2. The first time it opens, it asks which server to use. The work one is filled in; press **Add**.
3. A sign-in window opens. Sign in as usual and choose **Allow**. The page says what the app may do.
4. **Settings** opens: tick the libraries to show in File Explorer, then **Save**.

The app starts when you sign in to Windows, and keeps running from the icon next to the clock. Closing the window keeps it running there; right-click the icon and choose **Quit** to stop it.

## Tabs

Like Edge, everything is on one row at the top: the site's name, back / forward / reload, the tabs, the drive's status, ⚙ and the window's own buttons. There's no address bar, and the site's own top bar is hidden in the app (its menu moved into this row):

- **The name** (🌸 and the site's name, with ▾) opens the **pages**: Ask, Files, Wiki, Rooms, Automator, Proposals, Preferences and the rest you have, plus Messages and Help. The page you're on is marked. A number on the name counts unread messages and proposals waiting. **Ctrl+click** a page to open it in a new tab.
- **⚙** (right of the drive's status) has the app's own things: which servers you have, **Add a server…**, **Settings…**, opening the drive, **Sign in / Sign out**, **Check for updates**, **About** and **Quit**.
- The menus and the top row take their colours from the site's theme, so they change with it.

**Tabs sort themselves**: the site's places (Ask, Files, Automator, Wiki…) come first, and opened things (a Markdown file, a spreadsheet, a PDF, the editor, a wiki page, Help) come after them, in the same row. A tab moves on its own when you open a file in it, or go back to a place. Place tabs are compact and wear the 🌸 (they're where you work); opened things get a normal-width tab with no flower. As you open more, the opened things narrow first, then the places, as in a browser. Like the site's own, the name at the top left glitches now and then (not when Windows' animations are turned off). Tab names leave off the site's name ("Files", not "Files · Mnemerynne").

- **New tab**: **＋** or **Ctrl+T**. **Close**: **✕**, a middle-click on the tab, or **Ctrl+W**. **Reopen the last closed tab**: **Ctrl+Shift+T**.
- **Switch**: click a tab, **Ctrl+Tab** / **Ctrl+Shift+Tab**, or **Ctrl+1** … **Ctrl+8** (**Ctrl+9** is the last tab).
- **Back / forward / reload**: the arrows, **Alt+←** / **Alt+→**, **F5**.
- **Reorder**: drag a tab along the strip (places among places, opened things among opened things).

### Tab groups

Like Firefox, tabs can be gathered into named, coloured groups, for example everything for one budget or report:

- **Start one**: right-click a tab → **New group with this tab**, and give it a name. Each new group takes the next colour.
- **Add or take out a tab**: right-click it → **Add to “Budget”** or **Remove from “Budget”**, or drag it onto a group's label.
- **Fold a group away**: click its label. Its tabs hide (they keep their place and stay open) and the label shows how many there are, e.g. **Budget · 3**. Click again to bring them back. The tab you're on stays visible even in a folded group.
- **Right-click a label** for **Rename…**, **Colour**, **Ungroup** (the tabs stay open) and **Close group** (closes its tabs; Ctrl+Shift+T brings them back one at a time).
- A tab a grouped page opens (a file from Files, say) joins the same group.
- Groups sit together between the places and the ungrouped opened things, and come back with your tabs when the app restarts.
- Links to this site that would open a new browser tab (Help, **Open** in Files, a wiki page's **Edit**, links in chat) open as a new app tab. **Ctrl+click** or middle-click a link to open it in a new tab yourself.
- Links to **other sites** open in your normal browser.
- Your tabs come back when you start the app again.

The app keeps you signed in: when the site's own sign-in runs out (after a day), the app signs the tab in again by itself.

**Word, Excel and PowerPoint** files open in the Office editor inside an app tab (when your admin has it on), exactly as in the browser; see [Previews](previews.md#the-office-editor-word-excel-powerpoint). Editing a file from the **drive** in your own Word or Excel instead is fine too, except while someone has it open in the Office editor: then the drive's save waits until they close it.

Downloads go to your Downloads folder, with the usual download list. Uploading works as in a browser, by dragging in or with the buttons. Notifications from Rooms show as Windows notifications while the app runs.

## The drive in File Explorer

The libraries you ticked show under the site's name in File Explorer's left-hand list, in a folder such as `C:\Users\<you>\Mnemerynne`, one folder per library.

- **Files download when you open them.** Until then they take almost no space and show a cloud icon. Once opened they show a green tick.
- **Keep a copy on this computer**: right-click → **Always keep on this device**. **Free up space** goes back to cloud-only.
- **Saving** a file (in Word, Excel, Notepad…) sends it back. The old content is kept as an earlier [version](versions.md).
- **New files and folders** you make or copy in are added to the site. If the name is taken, the site adds a number, like `plan (1).txt`.
- **Renaming and moving** inside the drive does the same on the site, including moving between two of your libraries.
- **Deleting** sends it to the site's [recycle bin](recycle-bin.md), so a mistake can be undone there.
- **Changes made on the site** (by you in a browser, by colleagues, or by Claude) reach your computer within about a minute.
- You can only do what you could do in [Files](files.md). A read-only library stays read-only, and the drive never shows you more than Files does.

**If a file changes in both places**, nothing is lost. Your copy is kept beside it as `Name (YOUR-PC conflict 2026-11-25).docx` and added to the site as a separate file. Compare the two and keep what you want.

Some things the drive doesn't do:

- Moving a file **out** of the drive (to your Desktop, say) is refused. Copy it out instead.
- A library's own top folder can't be renamed or deleted from Explorer. Untick the library in **Settings** to take it off this computer; that doesn't touch the site.
- SharePoint libraries aren't on the drive. They open in SharePoint, as in Files.
- Lock and temporary files (`~$Budget.xlsx`, `.tmp`, `desktop.ini`) are never sent.

**Pause sync** (tray icon or Settings) stops everything going up or coming down until you resume. While it's paused, deleting and renaming on the drive is refused, so nothing gets out of step.

## More than one server

The app can open several servers, for example work and home. Use **⚙ → Add a server…**. Each one gets its own window, tabs, sign-in and drive, with its own name and icon.

## Updates

When a newer version is published, a bar at the top of the window says so (the app checks when it starts and once a day). **Update now** downloads it, checks it, installs it and opens the app again. **Later** skips that version. You can also check from **⚙ → Check for updates**.

## Signing out

**⚙ → Sign out**. The drive leaves File Explorer; files you already downloaded stay in the folder as ordinary files. To sign a computer out from somewhere else (a lost laptop, say), use **Preferences → Desktop app**, which lists every computer signed in, with **Sign out**.

## Troubleshooting

- **A page won't open** ("WebView2 Runtime"): install the Microsoft Edge WebView2 Runtime from Microsoft's site. Windows 11 already has it.
- **The drive says "Signed out"**: **⚙ → Sign in…**.
- **The drive says "Offline: retrying"**: the site can't be reached. It catches up by itself when it's back.
- **Something else**: **⚙ → About** shows where the app's log is. Send that file to an admin; it holds no passwords and no file contents.
