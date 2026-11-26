# Office editor (ONLYOFFICE)

People can open **Word, Excel and PowerPoint** files in the browser, and in the desktop app's tabs, with their real formatting. They edit them there, together if several open one at once, and every save becomes a version in Mnemerynne. The editor is **ONLYOFFICE Docs Community Edition**, run in a podman container on the box. The free edition allows **20 people editing at the same time**; anyone past that gets a read-only view. Code: `mnemerynne/office.py` and `mnemerynne/web/office.py`, with `office.html` and `office.js` for the page. Deploy files: `deploy/linux/onlyoffice/`, `deploy/linux/nginx-onlyoffice.conf` and `deploy/linux/onlyoffice-setup.sh`. People's side: [Previews](../user/previews.md#the-office-editor-word-excel-powerpoint).

It's **off** until both of these are true:
1. the Document Server is set up, which sets `MNEMERYNNE_ONLYOFFICE_URL` and `_JWT_SECRET`;
2. an admin switches it on.

When it's off, Office files show their extracted text as before.

## What it needs

- **Memory:** about 4 GB for the container (capped at 4 GB). The box has 20 GB since its upgrade (2026-11-26), so there's room beside the llama servers. The setup script warns below 12 GB.
- **Disk:** about 3 GB for the image, plus its cache under `/srv/onlyoffice`.
- **Port:** 8090 on loopback only (`127.0.0.1:8090`). It's never opened in the firewall.

## Installing

On the box, from the checkout in `/opt`:

```
sudo deploy/linux/onlyoffice-setup.sh install
```

The script:
1. Installs podman.
2. Makes a random JWT secret (never printed) and writes `/etc/mnemerynne/onlyoffice.env` (0600).
3. Adds `MNEMERYNNE_ONLYOFFICE_URL`, `_JWT_SECRET` and `_INTERNAL_URL` to `/etc/mnemerynne/mnemerynne.env`.
4. Installs the quadlet `mnemerynne-onlyoffice` (image pinned to one version) and starts it, then waits for `http://127.0.0.1:8090/healthcheck` to say `true`. The first start takes a few minutes.
5. Installs `/etc/nginx/mnemerynne-edge.d/onlyoffice.conf`, checks nginx and reloads it.
6. Restarts the web app and the files service. With nginx as the front door, it also rewrites the front door's routes (`front-door.sh routes`), so `/office/` and `/api/office/` reach the files service.

If opening a file in the editor shows `{"detail":"Not Found"}`, those routes are missing: run `sudo bash /opt/mnemerynne/deploy/linux/front-door.sh services` (or `routes` once the box has it).

Then go to **Access → Settings → Files and documents → Office editor**:
1. Press **Test connection**. You should see ✅ and `answered 200: true`.
2. Tick **On** and press **Save**.
3. Optionally, name a group (AD or Entra, `;`-separated) to limit who sees the editor. Empty means everyone, each person within their own rights on each file.

Turning it off: untick **On** to hide it at once, or run `sudo deploy/linux/onlyoffice-setup.sh off` to stop the container and remove the nginx snippet and env lines. `/srv/onlyoffice` is kept.

`mnemerynne-admin update` keeps the nginx side current: it refreshes the edge snippet when the repo's copy changes (`nginx -t` first, the old one back if refused) and rewrites the front door's routes when the service registry changes.

Updating the Document Server: `sudo deploy/linux/onlyoffice-setup.sh update <tag>`. `mnemerynne-admin update` never touches the container; its version only changes when you run this.

## How the pieces talk

```
browser ──https──▶ edge nginx ──/onlyoffice/ (auth_request: signed in?)──▶ 127.0.0.1:8090  Document Server
browser ──https──▶ edge nginx ──/office/{id}, /api/office/{id}/config────▶ Mnemerynne (signed config)
Document Server ──https (from 10.88.0.0/16)──▶ edge ──/api/office/ds/file|callback──▶ Mnemerynne
Mnemerynne ──http──▶ 127.0.0.1:8090/cache/files/…  (fetching the edited file)
```

- **The page** (`/office/{id}`) asks for a config. Mnemerynne decides **edit** or **view** from the person's real rights. Editing needs a .docx/.xlsx/.pptx in a place they may change, or a file shared with them "Can edit". Older formats (.doc, .xls, .ppt, OpenDocument, .rtf, .csv, .xlsm) are view-only, so nothing gets converted behind anyone's back. The config is signed with the JWT secret, so the Document Server only opens what Mnemerynne signed.
- **The document key.** The first person to open a file starts a **session** (`office_sessions`), and everyone who opens it while it lives gets the same key, so they edit together. A new session gets a new key, so a save from an old session can never match the current one.
- **Download and callback** (`/api/office/ds/…`) are for the container only. Each needs all of these:
  - our own token in the URL, bound to that file and session;
  - the Document Server's JWT;
  - a request from `MNEMERYNNE_ONLYOFFICE_ALLOWED_IPS`.

  On top of that, the edge only lets podman's network reach them.
- **Saving.** Ctrl+S (force-save, status 6) or the last person closing (status 2) makes the Document Server call back with a link to the edited file. Mnemerynne then:
  1. fetches it from the Document Server's own address and nowhere else;
  2. checks the editor may still change the file;
  3. saves it with the version check (`editing.replace_from_path`, *via onlyoffice*, as that person).

  The file is then read again for search.
- **The lock.** While the Document Server reports people in a file (status 1, within the last 12 hours), every other writer is refused with *"open in the Office editor by …"*. That covers Claude (`edit_spreadsheet`, `edit_document`, `edit_text`), Erynne's proposals, the desktop drive, and the text editor. A **restore** isn't refused: it ends the session instead.
- **Never overwrite, never lose.** If the session's key is stale, the file changed underneath, or the person lost their rights, the edit is saved as a new file instead: *Name (editor copy, alice 2026-11-26).xlsx*, beside the original or in their File box. They get a message saying where.

## Looking after it

- **Settings → Files and documents → Office editor** lists files open right now and who has them. **Release** drops a stuck session (say, the Document Server was restarted mid-edit). Claude and the drive may then save again, and the editors' next save becomes a copy.
- Logs:
  - `journalctl -u mnemerynne-web | grep onlyoffice` for each callback's outcome;
  - `journalctl -u mnemerynne-onlyoffice` for the container;
  - `podman logs mnemerynne-onlyoffice` for its own logs.
- The access log records `settings.office` and `settings.office_release`.
- Files over `MNEMERYNNE_ONLYOFFICE_MAX_BYTES` (50 MB) don't open in the editor.

## Testing it on the box (walkthrough)

1. `systemctl status mnemerynne-onlyoffice` is active, and `curl -s 127.0.0.1:8090/healthcheck` prints `true`.
2. In **Access → Settings → Files and documents → Office editor**, **Test connection** shows ✅. Tick **On** and save.
3. **Edit and save.** Upload a .docx to a library you can change.
   - Right-click it → **Open in editor**. The formatted document opens under the flower bar.
   - Type something and press **Ctrl+S**.
   - Back in Files, the ⓘ pane → **Versions** shows *"the Office editor for you"*.
4. **Co-editing.** Open the same file as a second user in another browser. Both cursors show. Close both, and about 10 seconds later one more version lands.
5. **Read-only.** A user with only read rights sees **View formatted** and a read-only editor.
6. **The lock.** Open an .xlsx in the editor and keep it open.
   - Ask Claude to change a cell with `edit_spreadsheet`. It's refused with *"open in the Office editor by …"*.
   - Close the editor, ask again, and it works.
7. **Conflict copy.** Open a file in the editor, then **Restore** an older version of it from Versions. Type in the editor and close it. An *"(editor copy, …)"* file appears beside it, with a message in your inbox.
8. **From outside the LAN** (a phone on cellular):
   - `https://<host>/api/office/ds/file/1` → 403;
   - `https://<host>/onlyoffice/healthcheck` → 403;
   - `https://<host>/onlyoffice/web-apps/apps/api/documents/api.js` without signing in → 401.
9. **Desktop app.** Open a file in the editor inside an app tab, edit, and save. Download and print from the editor's File menu should work too.
