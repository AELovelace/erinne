# Mnemerynne Desktop

A native Windows app for Mnemerynne (Erynne):

- **Tabs.** The site in its own window, with browser-style tabs (Ctrl+T, Ctrl+W, Ctrl+Shift+T, Ctrl+Tab, Ctrl+1..9,
  Alt+←/→, F5, middle-click, drag to reorder). Tabs come back after a restart. Links to the server open as app tabs;
  other sites open in the system browser. Like Edge, the tabs *are* the title bar: one row with the server menu,
  back/forward/reload, the tabs, the drive's status and the window's own buttons (WindowChrome), and no address bar
  (Doll's call: the site's 🌸 menu is the way around).
- **A drive in File Explorer**, like OneDrive or Box. The libraries people tick appear as folders, files download when
  opened, and saves, new files, renames, moves and deletes go back to the server. It uses Windows' **Cloud Files API**
  (`cldapi.dll`), the same one OneDrive uses.
- **Several servers** (work and home). Each one is a profile with its own window, cookies, sign-in, drive, name and icon.
- **Sign-in** with the site's normal login (OAuth + PKCE, scope `desktop`), kept with DPAPI. Tabs are signed in
  again silently when the site's day-long session runs out.
- **Updates ask first.** A banner offers a newer build. The app downloads it, checks its SHA-256 and installs it.
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
  Profiles.cs                    profiles.json (servers), server address clean-up
  OAuthPkce.cs                   the sign-in's PKCE and the loopback redirect it catches
  ServerApi.cs                   every call to the server (tokens, refresh rotation, tickets, drive API)
  Updates.cs                     version compare, download hash check
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
  MainWindow.xaml(.cs)           the one top row (tabs + window buttons), banner, shortcuts, drag to reorder
  BrowserTab.cs                  one WebView2 tab: links, silent re-sign-in, title and icon
  ProfileSession.cs              per-server WebView2 environment, API client, drive
  Dialogs.cs                     sign-in window, Add a server
  SettingsWindow.cs              drive libraries, pause, start with Windows
  Updater.cs                     ask-first updates
  Platform.cs                    DPAPI token vault, autostart (HKCU Run), log
tests/Mnemerynne.Core.Tests/     xUnit
packaging/installer.iss          Inno Setup, per user
ps/build-windows.ps1             tests, publish, installer, SHA-256
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

## Publishing a build

Copy the installer into `<MNEMERYNNE_DATA_DIR>/desktop-app/` on the server. `GET /api/desktop/info` then announces
the newest one (by the version in its name) with its SHA-256. Preferences → Desktop app offers it, and running apps
offer it as an update within a day.

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
