"""Markdown extraction and safe rendering (markdown-it-py, MIT).

Extraction and rendering share one heading/anchor pass, so a citation's
`#anchor` always matches an `id` in the rendered view.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Callable

from markdown_it import MarkdownIt
from markdown_it.token import Token

from mnemerynne.ingest import Segment

_FRONT_MATTER = re.compile(r"\A(?:---|\+\+\+)[ \t]*\n.*?\n(?:---|\.\.\.|\+\+\+)[ \t]*(?:\n|\Z)", re.DOTALL)
_ALLOWED_LINK = re.compile(r"^(?:https?:|mailto:|#|/|\./|\.\./|[^:]*$)", re.IGNORECASE)


class MarkdownError(ValueError):
    pass


def decode(data: bytes) -> str:
    """UTF-8 (BOM allowed) text, or MarkdownError for binary/other encodings."""
    if b"\x00" in data:
        raise MarkdownError("This doesn't look like a text file (it contains binary data).")
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise MarkdownError("Markdown files must be UTF-8 encoded.") from exc


def strip_front_matter(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return _FRONT_MATTER.sub("", text, count=1)


def _parser() -> MarkdownIt:
    md = MarkdownIt("commonmark", {"html": False, "linkify": False}).enable("table")
    md.validateLink = lambda url: bool(_ALLOWED_LINK.match(url.strip()))
    return md


def slugify(title: str) -> str:
    """GitHub-style heading slug: lowercase, punctuation dropped, spaces -> '-'."""
    text = unicodedata.normalize("NFKC", title).strip().lower()
    text = re.sub(r"[^\w\- ]", "", text)
    return text.replace(" ", "-") or "section"


class _Slugger:
    def __init__(self) -> None:
        self.seen: dict[str, int] = {}

    def __call__(self, title: str) -> str:
        base = slugify(title)
        n = self.seen.get(base, 0)
        self.seen[base] = n + 1
        return base if n == 0 else f"{base}-{n}"


@dataclass
class _Heading:
    level: int
    title: str
    anchor: str
    line_start: int  # first line of the heading
    line_end: int    # first line after the heading


def _headings(tokens) -> list[_Heading]:
    slug = _Slugger()
    found = []
    for i, tok in enumerate(tokens):
        if tok.type == "heading_open" and tok.map:
            inline = tokens[i + 1]
            title = "".join(
                child.content for child in (inline.children or []) if child.type in ("text", "code_inline")
            ).strip()
            anchor = slug(title)
            tok.attrSet("id", anchor)
            found.append(_Heading(int(tok.tag[1]), title, anchor, tok.map[0], tok.map[1]))
    return found


def segments(text: str) -> list[Segment]:
    """One segment per heading section (plus any text before the first heading)."""
    body = strip_front_matter(text)
    lines = body.split("\n")
    headings = _headings(_parser().parse(body))
    out: list[Segment] = []

    first = headings[0].line_start if headings else len(lines)
    preamble = "\n".join(lines[:first]).strip()
    if preamble:
        out.append(Segment(text=preamble))

    stack: list[tuple[int, str]] = []
    for i, h in enumerate(headings):
        while stack and stack[-1][0] >= h.level:
            stack.pop()
        stack.append((h.level, h.title))
        end = headings[i + 1].line_start if i + 1 < len(headings) else len(lines)
        section = "\n".join(lines[h.line_end:end]).strip()
        if section:
            path = " > ".join(title for _, title in stack)
            out.append(Segment(text=section, section_path=path, anchor=h.anchor))
    return out


def _rewrite_links(tokens, rewrite: Callable[[str], str]) -> None:
    """Pass every link's href and image's src through `rewrite` (wiki pages linking each other)."""
    for tok in tokens:
        for child in tok.children or []:
            if child.type == "link_open" and child.attrGet("href") is not None:
                child.attrSet("href", rewrite(child.attrGet("href")))
            elif child.type == "image" and child.attrGet("src") is not None:
                child.attrSet("src", rewrite(child.attrGet("src")))


# ------------------------------------------------------------------- task lists
# A list item whose text starts with "[ ]" (open), "[x]"/"[X]" or "✅" (done) is a task. With tasks=True,
# render_html draws it as a checkbox carrying the item's source line (data-line, 0-based in the whole
# file, front matter included). The box is disabled: tasks.js / mdeditor.js enable it where a click
# can save. A click comes back as toggle_task(), which re-parses the text, so a stale or forged line
# number is refused instead of changing the wrong line.
_TASK_MARK = re.compile(r"^(\[ \]|\[[xX]\]|✅️?)[ \t]+")    # on the item's inline text
_TASK_LINE = re.compile(r"^(?P<lead>[ \t]*(?:>[ \t]?)*[ \t]*(?:[-*+]|\d{1,9}[.)])[ \t]+)"
                        r"(?P<mark>\[ \]|\[[xX]\]|✅️?)(?=[ \t])")   # on the raw source line
_DONE_STAMP = re.compile(r"[ \t]*\*\(done \d{4}-\d{2}-\d{2}, [^()*\n]*\)\*[ \t]*$")   # what ticking appends
_DONE_MARKS = ("[x]", "[X]", "✅", "✅️")


class TaskError(ValueError):
    """The line isn't a task (any more), or it is already in the asked state."""


def _body_and_offset(text: str) -> tuple[str, int]:
    """The text the parser sees, and how many lines the front matter took off the top."""
    normal = text.replace("\r\n", "\n").replace("\r", "\n")
    body = strip_front_matter(normal)
    return body, normal[:len(normal) - len(body)].count("\n")


@dataclass
class _Task:
    line: int      # the item's first line (in the whole file)
    last: int      # the last line of the item's first paragraph (where a done stamp goes)
    done: bool
    inline: object  # the paragraph's inline token (render_html strips the marker off it)
    mark_len: int   # characters of marker + spacing to strip


def _tasks(tokens, offset: int) -> list[_Task]:
    """Every task item: a list item whose first block is a paragraph that starts with a marker."""
    found = []
    for i, tok in enumerate(tokens[:-2]):
        para, inline = tokens[i + 1], tokens[i + 2]
        if tok.type != "list_item_open" or not tok.map or para.type != "paragraph_open" \
                or inline.type != "inline" or not para.map or para.map[0] != tok.map[0]:
            continue                                   # not an item that opens with a paragraph
        m = _TASK_MARK.match(inline.content)
        if not m:
            continue                                   # an ordinary list item
        found.append(_Task(tok.map[0] + offset, para.map[1] - 1 + offset, m.group(1) in _DONE_MARKS,
                           inline, len(m.group(0))))
        tok.attrJoin("class", "task-item")             # the <li> gets class="task-item" (no bullet: erynne.css)
    return found


def _strip_mark(inline, n: int) -> None:
    """Take the first n characters (the marker) off the item's rendered text."""
    for child in inline.children or []:
        if n <= 0 or child.type != "text":
            break
        cut = min(n, len(child.content))
        child.content = child.content[cut:]
        n -= cut


def _checkbox(task: _Task) -> Token:
    """The <input> drawn in front of a task's text (built here, never from the source: safe as HTML)."""
    box = Token("html_inline", "", 0)
    box.content = (f'<input type="checkbox" class="md-task" data-line="{task.line}"'
                   f'{" checked" if task.done else ""} disabled aria-label="Done"> ')
    return box


def render_html(text: str, link_rewriter: Callable[[str], str] | None = None, *, tasks: bool = False) -> str:
    """HTML for the file view. Raw HTML in the source is escaped, not rendered.
    `link_rewriter` maps each link target (after the allowlist) to the one to show.
    `tasks` draws task-list items ("- [ ] ...", "- ✅ ...") as checkboxes (file view, wiki, editor)."""
    md = _parser()
    body, offset = _body_and_offset(text)
    tokens = md.parse(body)
    _headings(tokens)  # sets id attributes, identical to the anchors in segments()
    if link_rewriter is not None:
        _rewrite_links(tokens, link_rewriter)
    if tasks:
        for task in _tasks(tokens, offset):
            _strip_mark(task.inline, task.mark_len)         # "[ ] " leaves the text...
            task.inline.children.insert(0, _checkbox(task))  # ...and a checkbox takes its place
    return md.renderer.render(tokens, md.options, {})


def toggle_task(text: str, line: int, done: bool, *, who: str, today: str) -> dict[int, str]:
    """The lines to change to tick (done=True) or untick the task starting on `line`, as
    {line number: new line} (line endings not included; the line count never changes).

    Ticking writes the document's own done marker (✅ unless the file already ticks with [x]) and
    appends "*(done YYYY-MM-DD, who)*" to the end of the item's first paragraph. Unticking writes
    "[ ]" and removes that stamp. TaskError when the line isn't a task or is already that way."""
    if "\r" in text.replace("\r\n", ""):               # old-Mac line endings: the parser's lines aren't ours
        raise TaskError("This file's line endings can't be ticked here; edit it instead.")
    body, offset = _body_and_offset(text)
    tasks = {t.line: t for t in _tasks(_parser().parse(body), offset)}
    task = tasks.get(line)
    if task is None:
        raise TaskError("That line isn't a task any more. Reload the page to see the file as it is now.")
    if task.done == done:
        raise TaskError("That task is already " + ("ticked." if done else "open."))
    lines = [s.rstrip("\r") for s in text.replace("\r\n", "\n").split("\n")]
    m = _TASK_LINE.match(lines[line])
    if m is None:                                      # parsed as a task, but not in a shape we rewrite
        raise TaskError("This task's line is in a shape that can't be ticked here; edit it instead.")
    if done:
        ticks = [lines[t.line] for t in tasks.values() if t.done]
        mark = "[x]" if ticks and all("✅" not in s for s in ticks) else "✅"   # follow the file's habit
    else:
        mark = "[ ]"
    edits = {line: m.group("lead") + mark + lines[line][m.end():]}
    last = edits.get(task.last, lines[task.last])
    if done:
        edits[task.last] = last.rstrip() + f" *(done {today}, {who})*"   # what the hardening lists ask for
    else:
        edits[task.last] = _DONE_STAMP.sub("", last)                      # only our own stamp, at the end
    return {n: s for n, s in edits.items() if s != lines[n]}


def apply_lines(text: str, edits: dict[int, str]) -> str:
    """`text` with whole lines replaced, keeping its line endings (\\n or \\r\\n) as they were."""
    parts = text.split("\n")
    for n, new in edits.items():
        parts[n] = new + ("\r" if parts[n].endswith("\r") else "")
    return "\n".join(parts)
