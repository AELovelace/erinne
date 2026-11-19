# The organisation wiki

The **Wiki** tab shows your organisation's own wiki: a library of Markdown pages that anyone with access can read, and people with write access can edit in the browser. Procedures, contacts, "how we do things here", anything worth writing down. Because the wiki is a library, Erynne searches and cites its pages like any other document, so what you write there becomes part of her answers.

## Reading

Pages and folders are listed on the left; click a page to read it. Links between pages open in place, links to other files in the library open them, and outside links open in a new tab. The address bar follows the page, so you can bookmark or share a page's address with colleagues who have access. **Open in Files** shows the page's file in the Files tab (versions, comments, properties).

If the tab says the wiki isn't set up yet, either your organisation hasn't created it or you haven't been given access to it; ask an admin.

## Writing

- **Edit** (above the page) opens the page in the editor, in a new tab.
- **New page** (above the tree) starts a page in the selected folder. Give it a name; `.md` is added for you.
- Folders are made in Files: open the wiki library there and press **New folder**. Moving, renaming and deleting pages also happen in Files.

You see Edit and New page only where you can change files. On a wiki stored on the file share, that is decided by the Windows permissions of your domain account.

## The editor

The editor shows the Markdown source on the left and a live preview on the right, rendered exactly as the page will look.

- **Save** or **Ctrl+S** saves. The status at the top says *Saved* or *Unsaved changes*; closing the tab with unsaved changes asks first.
- **View** opens the saved page as Files shows it.
- **Close** returns to where you came from.
- Every save keeps the previous version ([Versions](versions.md)), so nothing is lost by saving.
- After a save the page is read again for search; answers follow it once the file's status is back to Ready (usually within a minute).

The same editor opens from **Edit** in Files for any text or Markdown file you can change, not only wiki pages.

## Task lists

A list item that starts with `[ ]` is an open task. One that starts with `[x]` or ✅ is done. In a page view
(Files → Open, or the wiki) and in the editor's preview, they show as checkboxes:

```
- [ ] **T-06** Apply the noise rules
- ✅ **T-05** Teramind on the PCI server
```

- **Where you can change the file**, click a box to tick it. That saves straight away: the `[ ]` becomes ✅
  (or `[x]`, if the file already ticks with `[x]`), and *(done 2026-11-19, yourname)* is added to the end of the
  task's first paragraph. Clicking it again reopens it: back to `[ ]`, and that stamp is removed. Every tick
  keeps the previous version under [Versions](versions.md), like any save.
- **Where you can only read**, the boxes show the state but can't be clicked.
- **In the editor's preview**, a click changes the text on the left. It is saved with **Save**, like your typing.
- If someone changed the file after you opened the page, your click is refused, the box goes back, and the page
  reloads so you can tick it on the file as it is now.

Only list items count: `[ ]` in the middle of a sentence stays text. Rooms, notices and messages show the
markers as plain text.

## When two people edit the same page

If someone else saved the page while you were editing, your save is refused and a bar offers two ways out:

- **Load their version**: your text is copied to the clipboard, their version is loaded, and you can paste yours back where it belongs.
- **Save mine over it**: your text is saved; their version stays under Versions, so nothing is lost either way.

A page open in Word or Excel on the file share (an Office lock file next to it) is also refused until it is closed.

## Markdown in a minute

| You write | You get |
|---|---|
| `# Title`, `## Section`, `### Sub-section` | headings (citations and links can point at them) |
| `**bold**`, `*italic*`, `` `code` `` | **bold**, *italic*, `code` |
| `- item` or `1. item` | bulleted or numbered lists |
| `- [ ] task`, `- [x] task` or `- ✅ task` | a task with a checkbox you can click (see [Task lists](#task-lists)) |
| `[text](Guides/other-page.md)` | a link to another wiki page (relative to this page's folder; `../` goes up) |
| `[text](other-page.md#section)` | a link to a heading in another page |
| `[text](https://example.com)` | an outside link |
| `![alt](diagram.png)` | an image stored in the same folder of the library |
| `> quote` | a quotation |
| `\| a \| b \|` with a `\|---\|---\|` line under the header | a table |
| three backticks on their own lines around text | a code block |

Raw HTML is shown as text, not rendered. A page's title in the tree is its file name without `.md`, so name files the way you want them listed.
