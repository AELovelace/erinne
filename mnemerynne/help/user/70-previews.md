# Opening and previewing files

Double-click a file in [Files](files.md), press **Open**, or open the details pane (ⓘ) to see a preview next to the list. What you get depends on the kind of file:

| Kind | Preview and Open |
|---|---|
| PDF | The PDF itself in the browser's viewer. Scanned PDFs that were OCR'd show the searchable copy, so the text can be selected. Citations open it at the right page. |
| Markdown (.md) | The page rendered: headings, lists, tables, links. Citations jump to the heading. |
| Text, code and config files | The text, with a section per unit and line numbers for code. |
| Word, PowerPoint, Excel, CSV, OpenDocument, RTF, HTML | The text Erynne read from the document, one section per heading, slide or sheet, so a citation lands on the right part. When the Office editor is on, Word, Excel and PowerPoint files also offer **Open in editor** or **View formatted** (see below). The original is available with Download. |
| Images (PNG, JPEG, GIF, WebP) | The image. |
| Anything else | No preview; **Download** gets the file. |

**Download** always gives you the original file. **Open** on a file without a preview downloads it.

## Why a preview looks different from the original

Office documents are shown as the text Erynne extracted, not as a rendering of the document. That is what she searches and cites, so it is useful for checking a citation. For the formatted document, use **Open in editor** / **View formatted** when your admin has turned the Office editor on, or Download.

## The Office editor (Word, Excel, PowerPoint)

When an admin has turned it on, Word, Excel and PowerPoint files have an extra button: in the right-click menu, in the details pane (ⓘ), and on the file's text view.

- **Open in editor**: shown on .docx, .xlsx and .pptx files you may change. The file opens in a new tab with its real formatting (ONLYOFFICE), and you can edit it there like in Word or Excel. If someone else opens it at the same time, you edit together and see each other's cursors.
- **View formatted**: shown when you may only read the file, or for older formats (.doc, .xls, .ppt, OpenDocument, .rtf, .csv, .xlsm). It's read-only; old formats are never converted behind your back.

**Saving:** press **Ctrl+S** to save a version right away. Changes are also saved when the last person closes the editor (give it about ten seconds). Every save is a new version under [Versions](versions.md), with your name, marked *the Office editor*, and the file is read again for search.

**While a file is open in the editor**, nobody else can change it in another way: Claude, Erynne, the desktop drive and the text editor are told it's open in the Office editor by you and asked to try later. Once everyone has closed it, they can carry on.

**If the file changed anyway** (someone restored an old version, or saved it in Word on the file share), your edit isn't saved over it and isn't lost either. It's saved as a new file beside it, called *Name (editor copy, yourname date)*, or in your File box if you can no longer change that folder, and a message tells you where.

Files bigger than 50 MB (your admin can change this) don't open in the editor; use Download.

## Page width

On a big screen, the **Width** buttons in the middle of the top bar of an opened Markdown, text or document page set how wide the page is: **50%**, **75%** or **100%** of the window, or **Custom** for any width from 20% to 100% (type it, or use the arrows; the page resizes as you go). Your pick is remembered in this browser for every file you open. Until you pick one, pages use the usual reading width. The buttons are hidden on phones, where the page already fills the screen.

The same **Width** buttons sit on the right of the top bar on the pages that use a narrow centre column: **Automator**, **Proposals**, **Messages** and the list tabs under **Access** (Groups, Accounts, Approvals and so on). One pick covers all of those pages, and it is kept apart from the one for opened files, so a wide dashboard doesn't make your documents wide too.

## Edit

Text and Markdown files in a folder you can change also offer **Edit**, which opens the browser editor in a new tab. See [The organisation wiki](wiki.md#the-editor). Word, Excel and PowerPoint files use [the Office editor](#the-office-editor-word-excel-powerpoint) instead.

## Searchable or not

The **Status** column tells you whether a file's text has been read for search:

- **Queued** and **Indexing**: being read; answers can't use it yet.
- **Ready**: searchable and citable.
- **Stored**: kept, but this kind of file can't be searched (a ZIP, an image, an old .doc without the converter installed).
- **Failed**: reading it went wrong; the error is in the details pane and in **Properties**, and **Retry indexing** tries again.
