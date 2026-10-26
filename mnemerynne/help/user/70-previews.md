# Opening and previewing files

Double-click a file in [Files](files.md), press **Open**, or open the details pane (ⓘ) to see a preview next to the list. What you get depends on the kind of file:

| Kind | Preview and Open |
|---|---|
| PDF | The PDF itself in the browser's viewer. Scanned PDFs that were OCR'd show the searchable copy, so the text can be selected. Citations open it at the right page. |
| Markdown (.md) | The page rendered: headings, lists, tables, links. Citations jump to the heading. |
| Text, code and config files | The text, with a section per unit and line numbers for code. |
| Word, PowerPoint, Excel, CSV, OpenDocument, RTF, HTML | The text Erynne read from the document, one section per heading, slide or sheet, so a citation lands on the right part. The original is available with Download. |
| Images (PNG, JPEG, GIF, WebP) | The image. |
| Anything else | No preview; **Download** gets the file. |

**Download** always gives you the original file. **Open** on a file without a preview downloads it.

## Why a preview looks different from the original

Office documents are shown as the text Erynne extracted, not as a rendering of the document. That is what she searches and cites, so it is useful for checking a citation, but for the formatted document use Download.

## Edit

Text and Markdown files in a folder you can change also offer **Edit**, which opens the browser editor in a new tab. See [The organisation wiki](wiki.md#the-editor).

## Searchable or not

The **Status** column tells you whether a file's text has been read for search:

- **Queued** and **Indexing**: being read; answers can't use it yet.
- **Ready**: searchable and citable.
- **Stored**: kept, but this kind of file can't be searched (a ZIP, an image, an old .doc without the converter installed).
- **Failed**: reading it went wrong; the error is in the details pane and in **Properties**, and **Retry indexing** tries again.
