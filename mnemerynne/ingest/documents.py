"""Office documents, OpenDocument, RTF, HTML, spreadsheets and source code,
read directly into segments.

Each reader returns Segments whose section_path is what a citation shows:
the heading path (Word, OpenDocument, HTML), "Slide 3: Title" (PowerPoint),
"Rates, rows 12-30" (spreadsheets) or "lines 120-180 (def sync)" (code).
Every segment gets an anchor (s1, s2, ...) so the text view can link to it.

Old binary Word and PowerPoint files (.doc, .ppt) have no reliable pure-Python
reader: when LibreOffice is installed they are converted to .docx/.pptx first,
otherwise they are stored without being indexed.
"""

from __future__ import annotations

import csv
import io
import os
import re
import shutil
import subprocess
import tempfile
import zipfile
from html.parser import HTMLParser
from pathlib import Path
from xml.etree import ElementTree

from mnemerynne.ingest import Segment

ROWS_PER_SEGMENT = 30
MAX_SHEET_ROWS = 20_000            # per sheet; the rest is left out of search
LINES_PER_SEGMENT = 60
MAX_UNZIPPED = 300 * 1024 * 1024   # zip-based formats: refuse anything that unpacks bigger

# extension -> (mime, kind, reader name)
FORMATS: dict[str, tuple[str, str]] = {
    ".docx": ("application/vnd.openxmlformats-officedocument.wordprocessingml.document", "docx"),
    ".docm": ("application/vnd.ms-word.document.macroEnabled.12", "docx"),
    ".pptx": ("application/vnd.openxmlformats-officedocument.presentationml.presentation", "pptx"),
    ".pptm": ("application/vnd.ms-powerpoint.presentation.macroEnabled.12", "pptx"),
    ".xlsx": ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "xlsx"),
    ".xlsm": ("application/vnd.ms-excel.sheet.macroEnabled.12", "xlsx"),
    ".xls": ("application/vnd.ms-excel", "xls"),
    ".csv": ("text/csv", "csv"),
    ".tsv": ("text/tab-separated-values", "csv"),
    ".odt": ("application/vnd.oasis.opendocument.text", "odt"),
    ".odp": ("application/vnd.oasis.opendocument.presentation", "odp"),
    ".ods": ("application/vnd.oasis.opendocument.spreadsheet", "ods"),
    ".rtf": ("application/rtf", "rtf"),
    ".html": ("text/html", "html"),
    ".htm": ("text/html", "html"),
    ".doc": ("application/msword", "doc"),
    ".ppt": ("application/vnd.ms-powerpoint", "ppt"),
}
LEGACY = {"doc", "ppt"}            # need LibreOffice

CODE_SUFFIXES = {
    ".py", ".pyi", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx", ".java", ".kt", ".kts", ".scala", ".groovy",
    ".c", ".h", ".cc", ".cpp", ".cxx", ".hpp", ".hh", ".cs", ".fs", ".vb", ".go", ".rs", ".rb", ".php", ".pl",
    ".pm", ".lua", ".r", ".swift", ".m", ".mm", ".dart", ".ex", ".exs", ".erl", ".clj", ".hs", ".elm", ".jl",
    ".sh", ".bash", ".zsh", ".fish", ".ps1", ".psm1", ".psd1", ".bat", ".cmd", ".sql", ".graphql", ".proto",
    ".json", ".jsonc", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".conf", ".env", ".properties", ".xml",
    ".xaml", ".csproj", ".sln", ".gradle", ".css", ".scss", ".sass", ".less", ".vue", ".svelte", ".tf",
    ".hcl", ".nix", ".cmake", ".mk", ".dockerfile", ".gitignore", ".editorconfig", ".service", ".timer",
}
CODE_NAMES = {"makefile", "dockerfile", "containerfile", "jenkinsfile", "vagrantfile", "gemfile", "rakefile",
              "caddyfile", "procfile"}

_DEF = re.compile(r"^\s*(?:export\s+|public\s+|private\s+|protected\s+|internal\s+|static\s+|async\s+|override\s+)*"
                  r"(def|class|function|func|fn|sub|procedure|interface|struct|enum|impl|module|trait)\s+"
                  r"([A-Za-z_$][\w$]*)")


class DocumentError(ValueError):
    """The file can't be read; the message is safe to show."""


def kind_of(name: str) -> str | None:
    """'doc' or 'code' for a file name this module reads, else None."""
    lower = name.lower()
    suffix = Path(lower).suffix
    if suffix in FORMATS:
        return "doc"
    if suffix in CODE_SUFFIXES or lower in CODE_NAMES or lower.startswith("dockerfile"):
        return "code"
    return None


def mime_of(name: str) -> str | None:
    fmt = FORMATS.get(Path(name.lower()).suffix)
    return fmt[0] if fmt else None


def needs_libreoffice(name: str) -> bool:
    fmt = FORMATS.get(Path(name.lower()).suffix)
    return bool(fmt and fmt[1] in LEGACY)


def libreoffice(command: str) -> str | None:
    return shutil.which(command) if command else None


# ------------------------------------------------------------------ helpers
def _decode(data: bytes) -> str:
    if b"\x00" in data[:8192]:
        raise DocumentError("This doesn't look like a text file (it contains binary data).")
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("latin-1")


def _check_zip(path: Path) -> None:
    try:
        with zipfile.ZipFile(path) as z:
            total = sum(i.file_size for i in z.infolist())
    except zipfile.BadZipFile as exc:
        raise DocumentError("The file is damaged (not a valid Office/OpenDocument package).") from exc
    if total > MAX_UNZIPPED:
        raise DocumentError("The file unpacks to more than 300 MB; it isn't indexed.")


class _Sections:
    """Builds heading-path segments, anchored s1, s2, ..."""

    def __init__(self) -> None:
        self.out: list[Segment] = []
        self.stack: list[tuple[int, str]] = []
        self.lines: list[str] = []

    def flush(self) -> None:
        text = "\n".join(line for line in self.lines if line.strip()).strip()
        if text:
            path = " > ".join(title for _, title in self.stack)
            self.out.append(Segment(text=text, section_path=path, anchor=f"s{len(self.out) + 1}"))
        self.lines = []

    def heading(self, level: int, title: str) -> None:
        title = " ".join(title.split())
        if not title:
            return
        self.flush()
        while self.stack and self.stack[-1][0] >= level:
            self.stack.pop()
        self.stack.append((level, title))

    def text(self, line: str) -> None:
        self.lines.append(line)

    def done(self) -> list[Segment]:
        self.flush()
        return self.out


def _anchored(segments: list[Segment]) -> list[Segment]:
    return [Segment(s.text, s.section_path, s.page_no, f"s{i}", s.ocr) for i, s in enumerate(segments, 1)]


def _row_text(header: list[str] | None, row: list[str]) -> str:
    if header and len(header) >= len([c for c in row if c]):
        cells = [f"{header[i] or f'Column {i + 1}'}: {v}" for i, v in enumerate(row) if v]
    else:
        cells = [v for v in row if v]
    return " | ".join(cells)


def _sheet_segments(sheet: str, rows) -> list[Segment]:
    """Rows (lists of cell strings) in blocks; the first non-empty row names the columns."""
    out: list[Segment] = []
    header: list[str] | None = None
    block: list[str] = []
    first = last = 0
    for n, row in enumerate(rows, start=1):
        if n > MAX_SHEET_ROWS:
            break
        row = [str(v).strip() if v is not None else "" for v in row]
        if not any(row):
            continue
        if header is None:
            header = row
            continue
        if not block:
            first = n
        block.append(_row_text(header, row))
        last = n
        if len(block) >= ROWS_PER_SEGMENT:
            out.append(Segment("\n".join(block), f"{sheet}, rows {first}-{last}"))
            block = []
    if block:
        out.append(Segment("\n".join(block), f"{sheet}, rows {first}-{last}"))
    elif header and not out:
        out.append(Segment(" | ".join(v for v in header if v), f"{sheet}, row 1"))
    return out


# ------------------------------------------------------------------ readers
def _docx(path: Path) -> list[Segment]:
    import docx
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    _check_zip(path)
    try:
        document = docx.Document(str(path))
    except Exception as exc:  # python-docx raises many kinds for bad packages
        raise DocumentError(f"Couldn't read the Word document: {exc}") from exc
    sections = _Sections()
    for child in document.element.body.iterchildren():
        tag = child.tag.rsplit("}", 1)[-1]
        if tag == "p":
            p = Paragraph(child, document)
            style = (p.style.name if p.style is not None else "") or ""
            match = re.match(r"(?:Heading|Überschrift|Titre|Título)\s*(\d)", style)
            if match:
                sections.heading(int(match.group(1)), p.text)
            elif style == "Title":
                sections.heading(0, p.text)
            else:
                sections.text(p.text)
        elif tag == "tbl":
            for row in Table(child, document).rows:
                cells = list(dict.fromkeys(c.text.strip() for c in row.cells))   # merged cells repeat
                sections.text(" | ".join(c for c in cells if c))
    return sections.done()


def _pptx(path: Path) -> list[Segment]:
    from pptx import Presentation

    _check_zip(path)
    try:
        deck = Presentation(str(path))
    except Exception as exc:
        raise DocumentError(f"Couldn't read the presentation: {exc}") from exc

    def texts(shapes):
        for shape in shapes:
            if getattr(shape, "shape_type", None) == 6 and hasattr(shape, "shapes"):   # group
                yield from texts(shape.shapes)
            if getattr(shape, "has_text_frame", False) and shape.text_frame.text.strip():
                yield shape.text_frame.text
            if getattr(shape, "has_table", False):
                for row in shape.table.rows:
                    yield " | ".join(c.text.strip() for c in row.cells if c.text.strip())

    out = []
    for n, slide in enumerate(deck.slides, start=1):
        title = slide.shapes.title.text.strip() if slide.shapes.title is not None and slide.shapes.title.text else ""
        body = [t for t in texts(slide.shapes) if t.strip() and t.strip() != title]
        if slide.has_notes_slide and slide.notes_slide.notes_text_frame is not None:
            notes = slide.notes_slide.notes_text_frame.text.strip()
            if notes:
                body.append(f"Notes: {notes}")
        text = "\n".join([title] + body if title else body).strip()
        if text:
            out.append(Segment(text, f"Slide {n}: {' '.join(title.split())}" if title else f"Slide {n}"))
    return _anchored(out)


def _xlsx(path: Path) -> list[Segment]:
    import openpyxl

    _check_zip(path)
    try:
        book = openpyxl.load_workbook(str(path), read_only=True, data_only=True)
    except Exception as exc:
        raise DocumentError(f"Couldn't read the workbook: {exc}") from exc
    out = []
    try:
        for sheet in book.worksheets:
            out += _sheet_segments(sheet.title, ([_cell(v) for v in row] for row in sheet.iter_rows(values_only=True)))
    finally:
        book.close()
    return _anchored(out)


def _cell(value) -> str:
    if value is None:
        return ""
    if hasattr(value, "isoformat"):
        text = value.isoformat(sep=" ") if hasattr(value, "hour") else value.isoformat()
        return text.removesuffix(" 00:00:00")
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _xls(path: Path) -> list[Segment]:
    import xlrd

    try:
        book = xlrd.open_workbook(str(path), on_demand=True)
    except Exception as exc:
        raise DocumentError(f"Couldn't read the workbook: {exc}") from exc
    out = []
    for sheet in book.sheets():
        def rows(sheet=sheet):
            for r in range(min(sheet.nrows, MAX_SHEET_ROWS)):
                values = []
                for cell in sheet.row(r):
                    if cell.ctype == xlrd.XL_CELL_DATE:
                        try:
                            values.append(_cell(xlrd.xldate_as_datetime(cell.value, book.datemode)))
                            continue
                        except Exception:
                            pass
                    values.append(_cell(cell.value))
                yield values
        out += _sheet_segments(sheet.name, rows())
    return _anchored(out)


def _csv(path: Path) -> list[Segment]:
    text = _decode(path.read_bytes())
    try:
        dialect = csv.Sniffer().sniff(text[:16384], delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel_tab if path.suffix.lower() == ".tsv" else csv.excel
    return _anchored(_sheet_segments(path.stem, csv.reader(io.StringIO(text), dialect)))


_ODF = {
    "text": "urn:oasis:names:tc:opendocument:xmlns:text:1.0",
    "table": "urn:oasis:names:tc:opendocument:xmlns:table:1.0",
    "draw": "urn:oasis:names:tc:opendocument:xmlns:drawing:1.0",
    "office": "urn:oasis:names:tc:opendocument:xmlns:office:1.0",
    "presentation": "urn:oasis:names:tc:opendocument:xmlns:presentation:1.0",
}


def _q(prefix: str, name: str) -> str:
    return f"{{{_ODF[prefix]}}}{name}"


def _odf_text(el) -> str:
    """Text of an ODF element: spaces (text:s), tabs and line breaks included."""
    parts = [el.text or ""]
    for child in el:
        tag = child.tag
        if tag == _q("text", "s"):
            parts.append(" " * int(child.get(_q("text", "c"), "1")))
        elif tag in (_q("text", "tab"), _q("text", "line-break")):
            parts.append("\t" if tag.endswith("tab") else "\n")
        else:
            parts.append(_odf_text(child))
        parts.append(child.tail or "")
    return "".join(parts)


def _odf_root(path: Path):
    _check_zip(path)
    try:
        with zipfile.ZipFile(path) as z:
            return ElementTree.fromstring(z.read("content.xml"))
    except (KeyError, ElementTree.ParseError) as exc:
        raise DocumentError("The OpenDocument file is damaged (no readable content.xml).") from exc


def _odt(path: Path) -> list[Segment]:
    body = _odf_root(path).find(f"{_q('office', 'body')}/{_q('office', 'text')}")
    sections = _Sections()
    for el in body.iter() if body is not None else ():
        if el.tag == _q("text", "h"):
            sections.heading(int(el.get(_q("text", "outline-level"), "1")), _odf_text(el))
        elif el.tag == _q("text", "p"):
            sections.text(_odf_text(el))
    return sections.done()


def _odp(path: Path) -> list[Segment]:
    root = _odf_root(path)
    out = []
    for n, page in enumerate(root.iter(_q("draw", "page")), start=1):
        lines = [_odf_text(p).strip() for p in page.iter(_q("text", "p"))]
        lines = [line for line in lines if line]
        if lines:
            name = page.get(_q("draw", "name"), "")
            title = lines[0] if not name or name.lower().startswith("page") else name
            out.append(Segment("\n".join(lines), f"Slide {n}: {title[:80]}"))
    return _anchored(out)


def _ods(path: Path) -> list[Segment]:
    root = _odf_root(path)
    out = []
    for table in root.iter(_q("table", "table")):
        def rows(table=table):
            for row in table.iter(_q("table", "table-row")):
                repeat_rows = min(int(row.get(_q("table", "number-rows-repeated"), "1")), 1000)
                values = []
                for cell in row:
                    if cell.tag not in (_q("table", "table-cell"), _q("table", "covered-table-cell")):
                        continue
                    text = "\n".join(_odf_text(p) for p in cell.iter(_q("text", "p")))
                    repeat = int(cell.get(_q("table", "number-columns-repeated"), "1"))
                    values += [text] * (min(repeat, 100) if text else min(repeat, 1))
                for _ in range(repeat_rows if any(values) else 1):
                    yield values
        out += _sheet_segments(table.get(_q("table", "name"), "Sheet"), rows())
    return _anchored(out)


def _rtf(path: Path) -> list[Segment]:
    from striprtf.striprtf import rtf_to_text

    raw = path.read_bytes()
    if not raw.lstrip().startswith(b"{\\rtf"):
        raise DocumentError("This isn't an RTF document.")
    text = rtf_to_text(raw.decode("latin-1"), errors="ignore")
    return _anchored([Segment(text.strip())]) if text.strip() else []


class _HTMLText(HTMLParser):
    _SKIP = {"script", "style", "noscript", "template", "svg", "head"}
    _BLOCK = {"p", "div", "li", "tr", "br", "section", "article", "table", "ul", "ol", "pre", "blockquote",
              "dd", "dt", "header", "footer", "main", "nav", "form", "hr", "td", "th"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.sections = _Sections()
        self.skip = 0
        self.heading: int | None = None
        self.buf: list[str] = []

    def _line(self) -> None:
        text = " ".join("".join(self.buf).split())
        self.buf = []
        if text:
            if self.heading is not None:
                self.sections.heading(self.heading, text)
            else:
                self.sections.text(text)

    def handle_starttag(self, tag, attrs):
        if tag in self._SKIP:
            self.skip += 1
        elif re.fullmatch(r"h[1-6]", tag):
            self._line()
            self.heading = int(tag[1])
        elif tag in self._BLOCK:
            self._line()

    def handle_endtag(self, tag):
        if tag in self._SKIP:
            self.skip = max(0, self.skip - 1)
        elif re.fullmatch(r"h[1-6]", tag):
            self._line()
            self.heading = None
        elif tag in self._BLOCK:
            self._line()

    def handle_data(self, data):
        if not self.skip:
            self.buf.append(data)


def _html(path: Path) -> list[Segment]:
    parser = _HTMLText()
    parser.feed(_decode(path.read_bytes()))
    parser.close()
    parser._line()
    return parser.sections.done()


def code_segments(text: str) -> list[Segment]:
    """Blocks of lines, named after the first definition in them (or the
    nearest one above)."""
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    out = []
    above = ""   # the last definition seen in earlier blocks
    for start in range(0, len(lines), LINES_PER_SEGMENT):
        block = lines[start:start + LINES_PER_SEGMENT]
        names = [f"{m.group(1)} {m.group(2)}" for m in map(_DEF.match, block) if m]
        label = names[0] if names else above
        if names:
            above = names[-1]
        if "\n".join(block).strip():
            path = f"lines {start + 1}-{start + len(block)}" + (f" ({label})" if label else "")
            out.append(Segment("\n".join(block), path))
    return _anchored(out)


def _code(path: Path) -> list[Segment]:
    return code_segments(_decode(path.read_bytes()))


def _legacy(path: Path, target: str, command: str | None, timeout: int) -> list[Segment]:
    """.doc/.ppt: LibreOffice converts to .docx/.pptx, which is then read."""
    exe = libreoffice(command or "")
    if exe is None:
        raise DocumentError("Old Word/PowerPoint files need LibreOffice on the server to be searchable.")
    with tempfile.TemporaryDirectory(prefix="mnemerynne-office-") as tmp:
        src = Path(tmp) / f"in{path.suffix.lower()}"
        shutil.copyfile(path, src)
        profile = Path(tmp, "profile").as_uri()
        try:
            subprocess.run([exe, f"-env:UserInstallation={profile}", "--headless", "--norestore",
                            "--convert-to", target, "--outdir", tmp, str(src)],
                           check=True, capture_output=True, timeout=timeout,
                           env={**os.environ, "HOME": tmp})
        except (subprocess.SubprocessError, OSError) as exc:
            raise DocumentError(f"LibreOffice couldn't convert the file: {exc}") from exc
        converted = Path(tmp) / f"in.{target}"
        if not converted.is_file():
            raise DocumentError("LibreOffice couldn't convert the file.")
        return _docx(converted) if target == "docx" else _pptx(converted)


def segments(path: Path, name: str, *, libreoffice_command: str = "soffice", timeout: int = 300) -> list[Segment]:
    """Segments for a 'doc' or 'code' file; `name` (its display name) decides the format."""
    suffix = Path(name.lower()).suffix
    fmt = FORMATS.get(suffix)
    if fmt is None:
        return _code(path)
    reader = fmt[1]
    if reader == "doc":
        return _legacy(path, "docx", libreoffice_command, timeout)
    if reader == "ppt":
        return _legacy(path, "pptx", libreoffice_command, timeout)
    return {"docx": _docx, "pptx": _pptx, "xlsx": _xlsx, "xls": _xls, "csv": _csv, "odt": _odt,
            "odp": _odp, "ods": _ods, "rtf": _rtf, "html": _html}[reader](path)
