# Setting up the organisation wiki

The **Wiki** tab shows one library of Markdown pages as a page tree with each page rendered, with Edit and New page for people who can write there. Because it is an ordinary library, its pages are indexed and cited like every other document, so what people write there ends up in Erynne's answers.

## Which library

By default, **the library called `Wiki`**. An admin can pick any other library on **Access → Settings → This box → Organisation wiki**; choose "The library called Wiki" to go back to the name rule. Until a library exists, the tab tells users it isn't set up yet, and tells admins how to make one.

## On the file share (recommended)

Create a folder named **`Wiki`** at the root of the share. The next scan (within a minute) turns it into a library. Set its Security in Windows:

- everyone who should read the wiki: Read;
- the people who maintain it: Modify;
- keep *Unix User\mnemerynne* with Modify, as on every folder.

Sub-folders of the wiki can carry their own permissions, so a department can keep a section only it can edit. People can also drop `.md` files into the folder from Windows; they appear in the Wiki tab on the next scan.

## Without a share

Create a library called `Wiki` on Access → Files & folders and grant it: Read to everyone who should see it (a group is simplest), Write to the maintainers.

## What the tab shows

- Folders and `.md` / `.markdown` files, nested as in the library, with each file's name (without the extension) as its title. Other files in the library (images, PDFs) aren't listed but can be linked from pages: a relative link to an image shows it inline, to another file offers the download.
- The first page opened is `index.md`, `home.md`, `readme.md` or `start.md` at the root if one exists, else the first page.
- Links between pages are written as relative Markdown links (`Guides/intro.md`, `../index.md#section`) and resolved to the right page when shown. Links to pages that don't exist are left as written.

## Editing

Edit and New page open the browser editor ([the user help describes it](../user/wiki.md)): Markdown source left, a live preview rendered by the server right, Save or Ctrl+S. Saves go through the same path as Claude's edits, so:

- every save keeps the previous version (Files → Versions; `MNEMERYNNE_VERSION_DAYS` and `VERSION_KEEP` apply);
- a page someone else saved meanwhile is refused with both ways out offered;
- a page open in Word on the share (a `~$` lock file) is refused;
- on the share the new content lands in one rename;
- the page is read again for search after each save (status Queued for a moment).

Versions list a browser edit by the person's username alone; Claude's and Erynne's edits say "Claude for name" / "Erynne for name". The same editor is offered in Files for any text or Markdown file a person can change.

The preview endpoint renders Markdown on the server with the same renderer as the file view (raw HTML escaped, links checked) and is rate-limited per person, so it can't be used to hammer the server.

## Folders, renames, deletions

Those happen in Files (open the wiki library there), or in Windows on the share. Moving a page doesn't break relative links from other pages only if they are updated too; the Wiki tab shows unresolved links as written, so a quick read of linked pages after a reorganisation is worth it.

## Claude and the wiki

Share the wiki library with Claude like any other library if you want Claude to read it, and allow AI edits on it if Claude or Erynne should be able to propose or make changes to pages. Both switches are off by default.
