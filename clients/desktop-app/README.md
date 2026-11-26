# Mnemerynne Desktop

A native Windows app for Mnemerynne (Erynne):

- **Tabs.** The site in its own window, with browser-style tabs (Ctrl+T, Ctrl+W, Ctrl+Shift+T, Ctrl+Tab, Ctrl+1..9,
  Alt+←/→, F5, middle-click, drag to reorder). Tabs come back after a restart. Links to the server open as app tabs;
  other sites open in the system browser. Tabs **sort themselves** (`TabKinds.cs`): the site's places first, then
  opened things (file views, the editor, wiki pages, Help), in one row. Places are 100 px with the flower; opened
  things are 200 px without it; opened things shrink first (to 90), then places (to 64) (`TabWidths`).
- **Tab groups**, like Firefox (`MainWindow.Groups.cs`, `TabGroup.cs`): named, coloured, foldable; grouped tabs sit
  between the places and the ungrouped opened things (`TabKinds.Ordered`), saved in tabs.json.
- **Live tab dragging** (`MainWindow.Drag.cs`): the tab lifts and follows the mouse, a line shows where it lands (own
  block only), group labels light up as drop targets, Esc cancels. A folded group's showing tab is half width.
  The site's name glitches like the site's brand (cyan and red copies every 7 s; off when Windows' animations are).
- **One top row, like Edge** (WindowChrome): the site's name (the **pages menu**, read live from the site's own hidden
  top bar, with badges), back/forward/reload, the tabs, the drive's status, **⚙** (servers, settings, drive, sign-in,
  updates, quit) and the window's buttons. No address bar. The site hides its own top bar when it sees the app
  (`html[data-desktop-app]`), and tab names drop the site's name.
- **Themed.** The top row and the menus take their colours and font from the site's theme (`ThemeColors.cs` reads
  its CSS variables), with Erynne-styled menus (`App.xaml`).
- **A drive in File Explorer**, like OneDrive or Box. The libraries people tick appear as folders, files download when
  opened, and saves, new files, renames, moves and deletes go back to the server. It uses Windows' **Cloud Files API**
  (`cldapi.dll`), the same one OneDrive uses.
- **Several servers** (work and home). Each one is a profile with its own window, cookies, sign-in, drive, name and icon.
- **Sign-in** with the site's normal login (OAuth + PKCE, scope `desktop`), kept with DPAPI. Tabs are signed in
  again silently when the site's day-long session runs out.
- **Updates ask first, from GitHub releases.** A banner offers a newer release; the app downloads it, checks its
  SHA-256 and installs it.
- **Tray icon**, and it starts with Windows (the drive needs the app running).

People's help page: `mnemerynne/help/user/225-desktop-app.md`. Admins: `mnemerynne/help/admin/285-desktop-app.md`.

It's **hand-installed** (a privileged app, gated by Access → Settings → Desktop app), per user, no admin rights.
There's no Atera script on purpose.

## Why WPF + WebView2

Doll wanted a native app, not Electron. The window, tab strip, tray, dialogs and drive are native .NET (WPF and the
Win32 Cloud Files API). Each tab's page is drawn by **WebView2**, the Microsoft Edge engine that ships with
Windows 11 (an evergreen runtime, kept up to date by Windows), so the site behaves exactly as it does in Edge. The site sends
`X-Frame-Options: SAMEORIGIN`, so every tab is a top-level WebView2, never an iframe. Tauri was ruled out (not
native C#, and WebView2 anyway); a fully native UI would mean rewriting every page.

## Layout

```
Mnemerynne.Desktop.sln
Directory.Build.props            the version (one place): installer name, About, user agent, updates
src/Mnemerynne.Core/             no Windows dependencies, unit tested
  LinkRouter.cs                  which links stay in the tab, open a new tab, or go to the browser
  TabSession.cs                  tabs.json, closed tabs, keyboard tab maths
  TabKinds.cs                    places vs opened things, and the sorted tab order
  Profiles.cs                    profiles.json (servers), server address clean-up
  OAuthPkce.cs                   the sign-in's PKCE and the loopback redirect it catches
  ServerApi.cs                   every call to the server (tokens, refresh rotation, tickets, drive API)
  Updates.cs                     version compare, download hash check
  GitHubReleases.cs              the update feed: the newest checkable desktop-v* release on GitHub
  Drive/DriveNames.cs            identities ("f:123"), Windows-safe names, conflict names, junk files
  Drive/DriveState.cs            drive.db: server id <-> path, versions, listed folders, feed cursors
  Drive/ChangeApplier.cs         applies the server's change feed to the placeholders (never loses local work)
src/Mnemerynne.Drive/            the Cloud Files provider (Windows only)
  CloudFilesInterop.cs           cfapi.h by hand, every struct offset commented
  Placeholders.cs                make / update / adopt / revert placeholders
  SyncRoot.cs                    register with Windows (navigation pane), unregister
  DriveService.cs                Explorer's callbacks, the local watcher, uploads, the change-feed loop
src/Mnemerynne.Desktop/          the WPF app
  App.xaml.cs                    single instance, tray, windows, sign-in, quit, --background, --unregister-drive
  MainWindow.xaml(.cs)           the one top row: pages menu, ⚙ menu, sorted tabs, window buttons; banner, shortcuts
  ThemeColors.cs                 the app's colours from the site's theme (CSS variables)
  BrowserTab.cs                  one WebView2 tab: links, silent re-sign-in, title and icon
  ProfileSession.cs              per-server WebView2 environment, API client, drive
  Dialogs.cs                     sign-in window, Add a server
  SettingsWindow.cs              drive libraries, pause, start with Windows
  Updater.cs                     ask-first updates
  Platform.cs                    DPAPI token vault, autostart (HKCU Run), log
tests/Mnemerynne.Core.Tests/     xUnit
packaging/installer.iss          Inno Setup, per user
ps/build-windows.ps1             tests, publish, installer, SHA-256
ps/publish-release.ps1           GitHub release desktop-v<version> with the installer and SHA256SUMS
python/make_icon.py              the app icon from the site's own icon
```

## Building

Needs the **.NET 10 SDK** and **Inno Setup 6**:

```powershell
winget install Microsoft.DotNet.SDK.10
```

```powershell
winget install JRSoftware.InnoSetup --scope user
```

Then, from `clients\desktop-app`:

```powershell
powershell -ExecutionPolicy Bypass -File ps\build-windows.ps1
```

It runs the unit tests and publishes self-contained for 64-bit Windows into `publish\`, so the installing PC needs no
.NET. It then writes `dist\Mnemerynne-Desktop-Setup-<version>.exe` and prints its SHA-256. To release a new version,
raise `<Version>` in `Directory.Build.props` first.

Unit tests on their own:

```powershell
dotnet test tests\Mnemerynne.Core.Tests
```

The server side is tested in the repo's Python suite: `uv run pytest tests/test_desktop.py`.

**Signing:** nothing is signed yet. Windows SmartScreen warns once per new version. With a code-signing certificate:
`signtool sign /fd SHA256 /a /tr http://timestamp.digicert.com /td SHA256 dist\Mnemerynne-Desktop-Setup-<version>.exe`.

## Trying a build without touching your own setup

Set `MNEMERYNNE_DESKTOP_HOME` to a scratch folder. The app then keeps its profiles, tokens, log and drive folders
there, and never changes "start with Windows". Against a throwaway local server:

```powershell
.venv\Scripts\python.exe python\casino_preview.py 8781 desktop
```

That runs from the repo root, on loopback only. The users are `alice` / `bob`, and the Desktop app group is "Domain Users". Then:

```powershell
$env:MNEMERYNNE_DESKTOP_HOME = "$env:TEMP\mnemerynne-desktop-try"; .\src\Mnemerynne.Desktop\bin\Debug\net10.0-windows10.0.19041.0\Mnemerynne.Desktop.exe
```

Add the server `http://127.0.0.1:8781` (plain http is only allowed for this computer). Setting
`WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS=--remote-debugging-port=9333` as well lets DevTools reach the tabs. When done:

```powershell
.\src\Mnemerynne.Desktop\bin\Debug\net10.0-windows10.0.19041.0\Mnemerynne.Desktop.exe --unregister-drive
```

That takes the test drive out of File Explorer (it reads the same `MNEMERYNNE_DESKTOP_HOME`).

## Installing

Run `Mnemerynne-Desktop-Setup-<version>.exe` (from Preferences → Desktop app, once published). It installs to
`%LOCALAPPDATA%\Programs\Mnemerynne Desktop` with a Start-menu entry and starts with Windows (`--background`, tray
only). The first start asks for the server (the work one is filled in), then opens the sign-in window and Settings.

It needs the **Microsoft Edge WebView2 Runtime**. Windows 11 has it; the installer warns on a Windows 10 PC without it.

Where things live, per Windows user:

| What | Where |
|---|---|
| Servers, settings | `%LOCALAPPDATA%\Mnemerynne\profiles.json` |
| One server's cookies, tabs, token (DPAPI), drive state | `%LOCALAPPDATA%\Mnemerynne\profiles\<id>\` |
| The drive | `%USERPROFILE%\<server's name>` (e.g. `C:\Users\Edith\Mnemerynne`) |
| Log (no tokens, no file contents) | `%LOCALAPPDATA%\Mnemerynne\desktop.log` (about 1 MB, one old copy) |

## Publishing a release (updates come from GitHub)

Installed apps update from this repo's **GitHub releases** (`GitHubReleases.cs`). At start, once a day and from
⚙ → Check for updates, they read `api.github.com/repos/AELovelace/erinne/releases` (public, no token). They offer the
newest published release tagged `desktop-v<version>` whose installer `Mnemerynne-Desktop-Setup-<version>.exe` has a
known SHA-256 (GitHub's own record of the upload, or a `SHA256SUMS` file in the release). Drafts and pre-releases are
ignored, and a release that can't be checked is never offered. A fork can point `"UpdateRepo"` in profiles.json
elsewhere.

To release:

1. Raise `<Version>` in `Directory.Build.props` (a version is released once). From 2026-11-26 the patch number has two
   digits (Doll): `0.2.02`, `0.2.03` … (`0.2.01` would be the same number as the released `0.2.1`, so the series
   starts at `0.2.02`). Versions compare as numbers, and the app shows the text as written.
2. Build: `powershell -ExecutionPolicy Bypass -File ps\build-windows.ps1`
3. Publish: `powershell -ExecutionPolicy Bypass -File ps\publish-release.ps1 -Notes "What changed"` (needs `gh`,
   signed in). It writes `dist\SHA256SUMS` and creates the release `desktop-v<version>` on `main` with both files.
   `-Draft` makes a draft to look over first: apps don't see drafts until it's published on GitHub.

Optional: copy the installer into `<MNEMERYNNE_DATA_DIR>/desktop-app/` on the server too, so Preferences → Desktop
app offers it for download to people who haven't installed the app yet.

## Uninstalling

Windows Settings → Apps → Mnemerynne Desktop. The uninstaller runs `Mnemerynne.Desktop.exe --unregister-drive`
first, which takes every drive out of File Explorer. Files already downloaded stay in the drive folder as ordinary
files; delete the folder by hand if they aren't wanted. `%LOCALAPPDATA%\Mnemerynne` (cookies, tabs, token) is left
too; delete it to forget everything.

## How the drive works (for whoever changes it)

- **Identity:** each placeholder carries its server identity (`c:<library>`, `d:<folder>`, `f:<file>`); `drive.db`
  maps ids to paths so the change feed can find things.
- **Listing:** a folder is listed the first time Explorer opens it (FETCH_PLACEHOLDERS → `/api/drive/list`).
  Its first listing sets the library's feed cursor.
- **Opening:** FETCH_DATA streams `/api/drive/files/<id>/content` (Range) to Windows in 1 MB pieces.
- **Explorer deletes and renames** reach the callbacks *before* they happen. The server goes first, then Windows is
  told yes or no. The app's own changes (applying the feed, unticking a library) are recognised by process id and
  never sent back, so a local clean-up can't bin anything on the server.
- **Local edits and new files:** a FileSystemWatcher marks paths; after 3 quiet seconds they're sent (PUT content with
  `If-Match`, or POST a new file), then marked in sync. A file whose bytes already match the server's version is only
  re-marked.
- **Conflicts:** a 409 on save, or a server change to a file with unsent local edits, renames the local copy
  `Name (<PC> conflict <date>).ext`. It goes up as a new file and the server's version comes down. Nothing is
  overwritten.
- **The feed:** `/api/drive/changes` once a minute per opened library, oldest first, each item's state now
  (idempotent). See `mnemerynne/drive.py`.

## Not done yet

- Folding the Python backup tray app (`clients/desktop-sync`) into this app (MN-19).
- Linux and macOS (WPF and Cloud Files are Windows only).
- Resumable uploads for very large files (the drive sends whole files up to the 500 MB server limit).
- Web push while the app is closed: Lumi Pet reminders arrive only while the app runs.
