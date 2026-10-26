# Versions and restoring

Every change made to a file **through Erynne or Claude**, or in the **browser editor**, keeps the previous content as a version. Uploading a new copy of a file over SMB or replacing it by upload does not create a version; use the recycle bin and re-upload for that.

## Where to find them

In [Files](files.md), select the file and open the **details pane** (the ⓘ button). Under **Versions** you see each earlier version, newest first:

- when it was replaced;
- by whom: "Claude for alice" (Claude working for Alice), "Erynne for alice" (a proposal Alice applied), just a username (someone saving in the browser editor), or "restore by alice";
- what changed, in the words of whoever changed it;
- **Download**, to look at that version without touching the file;
- **Restore**, if you can change files in that folder.

## Restoring

**Restore** puts that version back as the current content. What was there a moment ago becomes a version itself, so a restore can be undone the same way. The file is read again for search afterwards, so answers follow the restored text once its status is back to Ready.

A file in the recycle bin must be restored from the bin first.

## How long versions are kept

Versions are kept for **30 days** and at most **20 per file** (admins can change both). After that they are deleted; the current file is of course unaffected.

## Conflicts

If you save a file in the editor that someone else saved in the meantime, the save is refused and you are offered a choice: load their version (your text is copied to the clipboard) or save yours over it. Either way the other person's text stays under Versions. See [The organisation wiki](wiki.md#when-two-people-edit-the-same-page).
