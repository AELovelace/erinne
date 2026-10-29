# Desktop backup

**Mnemerynne Backup** is an app for your Windows or Linux computer that copies folders you choose (your Desktop, Documents, a photo folder…) to **Backups** in your [File box](files.md), and keeps them up to date. It works at the office and from anywhere else. It's for people in the group an admin picked; if you're in it, **Preferences → Backups** shows the download.

## Setting it up

1. On **Preferences → Backups**, download the Windows installer or the Linux AppImage and run it. Nothing needs admin rights.
2. In the app's window, choose **Sign in…**, type this site's address, and allow it in the browser page that opens. The page says what the app may do: only add, change, move and delete files in your Backups folder.
3. Choose **Add folder…** for each folder to back up, and give each a name. On the NAS it becomes `Backups\<your computer>\<that name>`.

The app then runs from an icon next to the clock. It starts when you sign in to your computer (you can turn that off in its Settings).

## What it does

- **New and changed files** are sent soon after you save them. When a file changes, its old copy is kept as an earlier [version](versions.md).
- **Deleted files** go to the [recycle bin](recycle-bin.md) on the NAS, so a mistake can be undone there.
- **Renamed and moved** files and folders are moved on the NAS too, not sent again.
- It's **one way**: your computer is the original. If someone changes a backed-up file on the NAS, your computer's copy is put back on the next check.
- **Big files** go up in pieces. If the connection drops, it carries on where it stopped.

Some files are left out, and the app lists them under **Skipped files…**:

- files another program has open and locked (Outlook's `.pst` while Outlook runs, a running database);
- files still being written (they go on the next pass);
- online-only cloud files (OneDrive's cloud icon): make them available offline to back them up;
- names Windows can't hold, like `a:b.txt`, or two names that differ only in capital letters (mostly on Linux);
- hidden files and Office's `~$` lock files.

Use **Leave out…** to skip more, like `node_modules` or `*.iso`.

## Finding your backups

They're in [Files](files.md) under **Backups**. Backups aren't searchable by default: Erynne and Claude don't read them, so they don't crowd your answers. To let Erynne search one, tick **Searchable** for it on **Preferences → Backups** (or in the app). Its documents are then read in the background. Claude also needs the folder shown to Claude in Files.

## Getting files back

- One file or a few: open them in Files and download them, or restore an earlier version from the file's versions.
- A whole folder, or most of it: in the app, **Restore…**, pick the backup and where to put it. Files already there are left as they are, never overwritten.
- From the command line: `mnemerynne-backup restore Documents --to D:\Restored`.

## Signing a computer out

**Preferences → Backups** lists every computer signed in, with **Sign out**. Do that for a computer you no longer use. Its backups stay in Files until you delete them.
