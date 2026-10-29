# Troubleshooting

## Signing in

**"Too many failed attempts."** Five wrong passwords for your username, or many across usernames from your computer, lock sign-in for 5 minutes. Wait, then try again. Forgotten it entirely? An admin resets it on Access → Accounts.

**I was signed out for no reason.** You signed out in another browser (signing out ends every session of your account), an admin reset your password, you pressed *Sign out other browsers* elsewhere, or 24 hours passed since you signed in. Sign in again.

## Asking

**"I couldn't find that in your documents."** The answer isn't in anything you can see, the file isn't Ready yet, or the document words it differently. Check the file's status in Files, widen the scope to All my documents, or ask with the system's own term. If the document really is missing, tell whoever maintains the library.

**The answer is wrong.** Click the citation. If the document says something else, press 👎 and say so; if the document itself is out of date, that's where the fix belongs. Erynne only repeats what the documents say.

**A library I should see is missing.** Your Preferences page lists your groups. If the group that grants the library isn't there, an admin needs to add you (in the domain or on Access → Groups), or link your domain account if the library is on the file share.

**Advanced settings / tools are missing.** You aren't in the agent group; an admin can add you. Internal systems also have their own groups.

## Files

**My file says Queued or Indexing for a long time.** Large PDFs and scanned documents (OCR) take a while; a 3,000-page manual can take hours. Answers can't use it until it is Ready.

**Failed.** Open Properties for the error. Common causes: a corrupt PDF, a file that isn't what its extension says, text that isn't UTF-8. Fix the file and upload it again, or press *Retry indexing*.

**Stored.** This kind of file can't be searched (images, archives, old .doc without the converter). It is kept and can be downloaded.

**"You can't change files here."** You have read access only. Ask the library's owner or an admin for write access; on the file share that is a Windows permission.

**A name is refused.** Names must work on Windows: no `\ / : * ? " < > |`, no trailing dot or space, and not names like `CON` or `PRN`.

**"The share isn't available right now."** The file share is unmounted or unreachable from the server. Files already indexed still answer questions; changes wait. Tell an admin.

## Editing

**"…changed since it was opened."** Someone else saved the file meanwhile. See [the wiki page](wiki.md#when-two-people-edit-the-same-page) for both ways out.

**"…is open in Word or Excel by someone right now."** An Office lock file sits next to the file on the share. Ask them to close it, then save again.

**Edit is greyed out.** Only text and Markdown files you can change can be edited in the browser; Word and Excel are changed through Claude or Erynne, or on your PC.

## Claude

**Claude says a file "isn't shared with Claude".** The library isn't shared with Claude by an admin, or someone unticked *Claude can see* on it. Erynne herself still sees it.

**Claude can't edit.** The token or connection was made without *Let Claude edit files*, the place doesn't allow AI edits (see [Properties](properties.md#ai-can-edit)), or the request is waiting on *Changes waiting for you* or with an admin.

**The connector asks me to sign in again.** Connections expire after 90 days without use, and an admin can revoke them. Connect again from Claude's settings.

## Who to ask

Your Mnemerynne admin: the Access page is theirs, and Preferences shows whether your own account counts as admin. For the documents themselves, the people who maintain the library, or the [wiki](wiki.md).
