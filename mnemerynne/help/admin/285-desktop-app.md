# Desktop app

People in one group can install the **desktop app** (Windows). It shows the site in tabs and puts the libraries they pick in File Explorer as a drive with files on demand, using Windows' Cloud Files API like OneDrive. It's hand-installed and privileged, so there's no Atera deployment. Code: `mnemerynne/desktop.py`, `mnemerynne/drive.py`, `mnemerynne/web/desktop.py`, and the app in `clients/desktop-app`. People's side: [Desktop app](../user/desktop-app.md).

## Turning it on

1. **Access → Settings → Desktop app.** Name the group (AD or Entra, separated by `;`) whose members may download the app and sign it in. It's checked on every request, so someone taken out of the group stops at once. Leave it empty to switch the app off for everyone; signed-in apps stop working immediately.
2. **The installer.** Build it (`clients/desktop-app/README.md`) and copy `Mnemerynne-Desktop-Setup-<version>.exe` into `<data dir>/desktop-app/`. **Preferences → Desktop app** offers the newest one to the group, and running apps offer it as an update (they ask first) within a day. The version comes from the file name. The server announces the file's SHA-256, and the app checks its download against it before running it.

Nothing else is needed on the server. The drive uses the main address, so its uploads are held to the front door's limit (`client_max_body_size 510m` on the edge) and to `MNEMERYNNE_MAX_FILE_BYTES` (500 MB).

## How it behaves

- **Sign-in:** OAuth with PKCE, scope `desktop`, in a window inside the app. Only an app on the person's own computer (a loopback redirect) and only people in the group can approve it. Desktop tokens only work on `/api/desktop/*` and `/api/drive/*`, and Claude's tokens don't work there.
- **Tabs stay signed in:** the site's session cookie lasts a day (`MNEMERYNNE_SESSION_MAX_AGE`). When a tab lands on `/login`, the app swaps its token for a ticket (single use, 60 seconds, only its hash is stored in `desktop_tickets`) and opens `/desktop/signin`, which sets the ordinary session.
- **The page knows:** the app adds `MnemerynneDesktop/<version>` to its user agent, and the top bar then drops its **Install** buttons.
- **What the drive may do:** exactly what the person may do in Files (every call goes through `fs.Access`): list, download (Range), save, add, rename, move, and delete to the recycle bin. Saves send the version the app last had (`If-Match`). If the file changed on the server since, the save is refused (409) and the app keeps both copies. Saved-over content is kept as a version, *via drive*. SharePoint libraries are left out.
- **Changes reach the app** through `GET /api/drive/changes?collection_id&cursor`. Every add, rename, move, new content, bin or restore of a file or folder takes the next number from `drive_seq` (migration 0078, kept by SQLite triggers, so any code path counts: web, share watcher, Claude, backups). Apps ask once a minute per library they've opened.
- **Logging:** the admin setting goes in the log (`settings.desktop_app`). Drive edits show in Files as edits by the person (`edited_via drive:<name>`).

## Signing computers out

People see their computers under **Preferences → Desktop app**, each with **Sign out**. That revokes the connection, so its tokens and tickets stop at once. Taking someone out of the group stops all their computers.

## Known limits

- A file moved from a drive library into another library the person hasn't put on the drive just disappears from the first one (the drive only follows libraries it shows).
- Moving a file out of the drive in Explorer is refused; people copy instead.
- Windows only (WPF and Cloud Files). Linux and macOS would need a different app.
