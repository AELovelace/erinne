"""Excel (.xlsx) reading and editing by patching the workbook's XML in place.

An .xlsx file is a zip of XML parts. Instead of loading the whole workbook
into openpyxl and saving a brand-new file (which loses pictures, charts,
pivots and anything else openpyxl doesn't understand), this module opens only
the parts an operation needs, changes just those elements, and copies every
other part of the zip across untouched. So a cell edit in a workbook with a
logo, a chart and data validation leaves the logo, the chart and the
validation exactly as they were.

What it can do (each one is an `op` of edit_spreadsheet):
  set_cells, set_range, clear_cells, set_by_key, append_rows,
  insert_rows, delete_rows, insert_columns, delete_columns,
  add_sheet, rename_sheet.
Inserting or deleting rows/columns moves everything that points at them:
formulas on every sheet, defined names (print areas), merged cells,
conditional formatting, data validation, filters, hyperlinks, tables,
pictures/chart anchors, chart series and comments. Where a part can't be
moved safely (pivot tables, slicers, threaded comments, a delete that cuts an
array formula) the operation is refused with a reason instead.

Cell values Claude sends:
  12.5 / 4916        a number          true / false   a boolean
  "text"             text              "=SUM(B2:B9)"  a formula
  null               empty the cell (its formatting stays)
  {"text": "0123"}   text even if it looks like a number or formula
  {"date": "2026-11-26"} or {"date": "2026-11-26T08:30"}   a date
  {"formula": "SUM(B2:B9)"}   a formula
"""

from __future__ import annotations

import datetime as dt
import io
import math
import posixpath
import re
import zipfile
from dataclasses import dataclass, field

from lxml import etree

from mnemerynne.editing import EditError

# ------------------------------------------------------------------ namespaces
MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"           # cells, sheets, styles
REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"  # r:id attributes
PKG_REL = "http://schemas.openxmlformats.org/package/2006/relationships"     # .rels files
CT_NS = "http://schemas.openxmlformats.org/package/2006/content-types"       # [Content_Types].xml
XDR = "http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing"  # picture/chart anchors
CHART = "http://schemas.openxmlformats.org/drawingml/2006/chart"             # chart parts
XM = "http://schemas.microsoft.com/office/excel/2006/main"                   # x14 extension refs
VML_X = "urn:schemas-microsoft-com:office:excel"                             # legacy comment boxes
XML_SPACE = "{http://www.w3.org/XML/1998/namespace}space"                    # xml:space="preserve"


def q(tag: str, ns: str = MAIN) -> str:
    """Clark-notation tag name ({namespace}tag) for lxml."""
    return f"{{{ns}}}{tag}"


SHEET_CT = "application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"   # new sheets
SST_CT = "application/vnd.openxmlformats-officedocument.spreadsheetml.sharedStrings+xml"  # new string table
REL_BASE = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/"        # relationship types

MAX_DIFF = 50              # cell changes listed in a result; the rest are counted
MAX_FIND = 50              # matches returned by a find
MAX_STRING = 32767         # Excel's limit for one cell's text
_PARSER = etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=True,
                          remove_blank_text=False)   # safe: no external entities, keeps whitespace as it was
_BAD_XML_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f￾￿]")   # can't appear in XML at all


# ------------------------------------------------------------------ addresses
_CELL = re.compile(r"^\$?([A-Za-z]{1,3})\$?([1-9]\d{0,6})$")   # B4, $B$4


def col_num(letters: str) -> int:
    """Column letters to a number: A -> 1, Z -> 26, AA -> 27."""
    n = 0
    for ch in letters.upper():
        n = n * 26 + (ord(ch) - 64)
    return n


def col_letters(n: int) -> str:
    """Column number to letters: 1 -> A, 27 -> AA."""
    out = ""
    while n > 0:
        n, rem = divmod(n - 1, 26)
        out = chr(65 + rem) + out
    return out


def split_ref(ref: str) -> tuple[int, int]:
    """'B4' -> (row 4, column 2); refuses anything that isn't a single cell."""
    m = _CELL.match(str(ref).strip())
    if not m or col_num(m.group(1)) > 16384 or int(m.group(2)) > 1048576:
        raise EditError(f"{ref!r} isn't a cell name like B4.")
    return int(m.group(2)), col_num(m.group(1))


def ref_of(row: int, col: int) -> str:
    """(4, 2) -> 'B4'."""
    return f"{col_letters(col)}{row}"


def parse_range(text: str) -> tuple[int, int, int, int]:
    """'A1:C9' (or one cell 'B4', or a column span 'J:K') -> (min_row, min_col, max_row, max_col)."""
    text = str(text).split("!")[-1].replace("$", "").strip()
    m = re.fullmatch(r"([A-Za-z]{1,3}):([A-Za-z]{1,3})", text)   # whole columns
    if m:
        a, b = col_num(m.group(1)), col_num(m.group(2))
        return 1, min(a, b), 1048576, max(a, b)
    parts = text.split(":")
    if len(parts) == 1:
        r, c = split_ref(parts[0])
        return r, c, r, c
    if len(parts) != 2:
        raise EditError(f"Name a range like A1:F40, not {text!r}.")
    (r1, c1), (r2, c2) = split_ref(parts[0]), split_ref(parts[1])
    return min(r1, r2), min(c1, c2), max(r1, r2), max(c1, c2)


def range_text(r1: int, c1: int, r2: int, c2: int) -> str:
    """Back to 'A1:C9' (or 'B4' for one cell)."""
    a, b = ref_of(r1, c1), ref_of(r2, c2)
    return a if a == b else f"{a}:{b}"


# ------------------------------------------------------------------ the zip
class Package:
    """The .xlsx zip: parts are parsed only when asked for, and only parts that
    were changed are written back; everything else is copied as it was."""

    def __init__(self, data: bytes):
        if data[:4] == b"\xd0\xcf\x11\xe0":    # an OLE container: Excel's password-protected format
            raise EditError("This workbook is password-protected, so it can't be read or edited here. "
                            "Remove the password in Excel first.")
        try:
            self.zip = zipfile.ZipFile(io.BytesIO(data))
        except zipfile.BadZipFile:
            raise EditError("This doesn't look like a valid .xlsx file.") from None
        self.infos = self.zip.infolist()                   # original order and compression
        self.names = {i.filename for i in self.infos}       # quick membership checks
        self.trees: dict[str, etree._Element] = {}          # parsed parts
        self.dirty: set[str] = set()                        # parts to write back
        self.removed: set[str] = set()                      # parts to leave out
        self.added: dict[str, bytes] = {}                   # brand-new parts (serialized at save)

    def has(self, name: str) -> bool:
        return (name in self.names or name in self.trees) and name not in self.removed

    def xml(self, name: str) -> etree._Element:
        """The parsed root element of a part (cached)."""
        if name not in self.trees:
            try:
                self.trees[name] = etree.fromstring(self.zip.read(name), _PARSER)
            except KeyError:
                raise EditError(f"The workbook is missing its {name} part.") from None
            except etree.XMLSyntaxError as exc:
                raise EditError(f"Couldn't read {name} in this workbook ({exc}).") from None
        return self.trees[name]

    def touch(self, name: str) -> None:
        """Mark a parsed part as changed so save() writes it back."""
        self.dirty.add(name)

    def add(self, name: str, root: etree._Element) -> None:
        """A brand-new part."""
        self.trees[name] = root
        self.dirty.add(name)
        self.added[name] = b""

    def remove(self, name: str) -> None:
        self.removed.add(name)

    # -- relationships
    @staticmethod
    def rels_path(part: str) -> str:
        """xl/worksheets/sheet1.xml -> xl/worksheets/_rels/sheet1.xml.rels"""
        folder, base = posixpath.split(part)
        return posixpath.join(folder, "_rels", base + ".rels")

    def rels(self, part: str) -> list[tuple[str, str, str, etree._Element]]:
        """(id, type suffix, resolved target, element) for a part's internal relationships."""
        path = self.rels_path(part)
        if not self.has(path):
            return []
        out = []
        for rel in self.xml(path):
            if rel.get("TargetMode") == "External":
                continue                                          # links to other files/web: not parts
            target = rel.get("Target") or ""
            resolved = target.lstrip("/") if target.startswith("/") else \
                posixpath.normpath(posixpath.join(posixpath.dirname(part), target))
            out.append((rel.get("Id"), (rel.get("Type") or "").rsplit("/", 1)[-1], resolved, rel))
        return out

    def add_rel(self, part: str, rel_type: str, target: str) -> str:
        """Add a relationship from `part`; returns its new rId."""
        path = self.rels_path(part)
        if not self.has(path):
            self.add(path, etree.Element(q("Relationships", PKG_REL), nsmap={None: PKG_REL}))
        root = self.xml(path)
        used = {r.get("Id") for r in root}
        n = 1
        while f"rId{n}" in used:
            n += 1
        etree.SubElement(root, q("Relationship", PKG_REL), Id=f"rId{n}", Type=REL_BASE + rel_type, Target=target)
        self.touch(path)
        return f"rId{n}"

    def add_content_type(self, part: str, content_type: str) -> None:
        root = self.xml("[Content_Types].xml")
        etree.SubElement(root, q("Override", CT_NS), PartName="/" + part, ContentType=content_type)
        self.touch("[Content_Types].xml")

    def drop_content_type(self, part: str) -> None:
        root = self.xml("[Content_Types].xml")
        for el in root.findall(q("Override", CT_NS)):
            if el.get("PartName") == "/" + part:
                root.remove(el)
                self.touch("[Content_Types].xml")

    def save(self) -> bytes:
        """A new zip: changed parts re-serialized, the rest copied byte for byte, same order."""
        out = io.BytesIO()
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
            for info in self.infos:
                name = info.filename
                if name in self.removed:
                    continue                                        # e.g. calcChain.xml, rebuilt by Excel
                if name in self.dirty:
                    copy = zipfile.ZipInfo(name, date_time=info.date_time)
                    copy.compress_type = info.compress_type
                    copy.external_attr = info.external_attr
                    z.writestr(copy, _serialize(self.trees[name]))
                else:
                    z.writestr(info, self.zip.read(name))           # untouched: same bytes
            for name in self.added:
                if name not in self.removed:
                    z.writestr(name, _serialize(self.trees[name]))
        return out.getvalue()


def _serialize(root: etree._Element) -> bytes:
    return etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)


# ------------------------------------------------------------------ workbook model
@dataclass
class SheetInfo:
    name: str          # tab name
    path: str          # zip part, e.g. xl/worksheets/sheet1.xml
    element: etree._Element   # its <sheet> in workbook.xml
    index: int         # 0-based tab position (localSheetId)


@dataclass
class Change:
    sheet: str
    cell: str
    old: object
    new: object


@dataclass
class Book:
    """The workbook: sheets, shared strings and styles, read lazily from a Package."""
    pkg: Package
    path: str = "xl/workbook.xml"
    sheets: list[SheetInfo] = field(default_factory=list)
    _sst: list[str] | None = None          # shared strings, plain text
    _sst_index: dict[str, int] | None = None   # text -> index, for reuse
    _sst_path: str | None = None
    _date_styles: dict[int, bool] | None = None
    changes: list[Change] = field(default_factory=list)   # cell-level diff
    formulas_changed: bool = False          # calcChain must go, Excel recalculates

    @classmethod
    def open(cls, data: bytes) -> "Book":
        pkg = Package(data)
        path = "xl/workbook.xml"
        for _, kind, target, _ in pkg.rels(""):            # the root .rels names the workbook part
            if kind == "officeDocument":
                path = target
        book = cls(pkg, path)
        rels = {rid: target for rid, _, target, _ in pkg.rels(path)}
        sheets = book.wb.find(q("sheets"))
        for i, el in enumerate(sheets if sheets is not None else []):
            rid = el.get(q("id", REL_NS))
            if rid in rels:
                book.sheets.append(SheetInfo(el.get("name"), rels[rid], el, i))
        if not book.sheets:
            raise EditError("This workbook has no worksheets that can be read here.")
        return book

    @property
    def wb(self) -> etree._Element:
        return self.pkg.xml(self.path)

    def sheet(self, name: str | None) -> SheetInfo:
        """A sheet by tab name (the first sheet when no name is given)."""
        if name in (None, ""):
            return self.sheets[0]
        for s in self.sheets:
            if s.name == name:
                return s
        for s in self.sheets:                                    # forgive case differences
            if s.name.casefold() == str(name).casefold():
                return s
        raise EditError(f"There's no sheet {name!r}; the sheets are {', '.join(s.name for s in self.sheets)}.")

    def sheet_xml(self, info: SheetInfo) -> etree._Element:
        return self.pkg.xml(info.path)

    @property
    def date1904(self) -> bool:
        pr = self.wb.find(q("workbookPr"))
        return pr is not None and pr.get("date1904") in ("1", "true")

    # -- shared strings
    def _load_sst(self) -> None:
        if self._sst is not None:
            return
        self._sst, self._sst_index = [], {}
        for _, kind, target, _ in self.pkg.rels(self.path):
            if kind == "sharedStrings":
                self._sst_path = target
        if self._sst_path and self.pkg.has(self._sst_path):
            for i, si in enumerate(self.pkg.xml(self._sst_path).findall(q("si"))):
                text = _si_text(si)
                self._sst.append(text)
                if len(si) == 1 and si[0].tag == q("t"):              # plain (not rich) strings can be reused
                    self._sst_index.setdefault(text, i)

    def string(self, index: int) -> str:
        self._load_sst()
        return self._sst[index] if 0 <= index < len(self._sst) else ""

    def string_index(self, text: str) -> int:
        """The shared-string index for `text`, adding it when it's new."""
        self._load_sst()
        if text in self._sst_index:
            return self._sst_index[text]
        if not self._sst_path or not self.pkg.has(self._sst_path):   # no table yet: make one
            self._sst_path = "xl/sharedStrings.xml"
            self.pkg.add(self._sst_path, etree.Element(q("sst"), nsmap={None: MAIN}, count="0", uniqueCount="0"))
            self.pkg.add_rel(self.path, "sharedStrings", "sharedStrings.xml")
            self.pkg.add_content_type(self._sst_path, SST_CT)
        root = self.pkg.xml(self._sst_path)
        si = etree.SubElement(root, q("si"))
        t = etree.SubElement(si, q("t"))
        t.text = text
        if text != text.strip() or "\n" in text:
            t.set(XML_SPACE, "preserve")                          # keep leading/trailing spaces
        self._sst.append(text)
        self._sst_index[text] = len(self._sst) - 1
        root.set("uniqueCount", str(len(self._sst)))
        root.set("count", str(int(root.get("count") or 0) + 1))
        self.pkg.touch(self._sst_path)
        return len(self._sst) - 1

    # -- styles
    def _styles_path(self) -> str | None:
        for _, kind, target, _ in self.pkg.rels(self.path):
            if kind == "styles":
                return target
        return None

    def is_date_style(self, s: int) -> bool:
        """Whether cell style `s` shows numbers as dates/times."""
        if self._date_styles is None:
            from openpyxl.styles.numbers import BUILTIN_FORMATS, is_date_format
            self._date_styles = {}
            path = self._styles_path()
            if path and self.pkg.has(path):
                root = self.pkg.xml(path)
                custom = {int(n.get("numFmtId")): n.get("formatCode") or ""
                          for n in root.iterfind(f"{q('numFmts')}/{q('numFmt')}")}
                xfs = root.find(q("cellXfs"))
                for i, xf in enumerate(xfs if xfs is not None else []):
                    fid = int(xf.get("numFmtId") or 0)
                    code = custom.get(fid, BUILTIN_FORMATS.get(fid, "General"))
                    self._date_styles[i] = fid in range(14, 23) or fid in (45, 46, 47) or is_date_format(code)
        return self._date_styles.get(s, False)

    def date_style_like(self, s: int, with_time: bool) -> int:
        """A cell style that looks like style `s` but shows a date (adds one to styles.xml if needed)."""
        if self.is_date_style(s):
            return s
        path = self._styles_path()
        if not path:
            raise EditError("This workbook has no styles part, so a date can't be formatted; send the date as text.")
        root = self.pkg.xml(path)
        xfs = root.find(q("cellXfs"))
        fid = "22" if with_time else "14"                         # built-in m/d/yyyy (h:mm) formats
        base = xfs[s] if 0 <= s < len(xfs) else None
        for i, xf in enumerate(xfs):                              # reuse an identical style when there is one
            if xf.get("numFmtId") == fid and (base is None or all(
                    xf.get(k) == base.get(k) for k in ("fontId", "fillId", "borderId"))):
                return i
        new = etree.SubElement(xfs, q("xf"))
        for k, v in (base.attrib.items() if base is not None else [("fontId", "0"), ("fillId", "0"),
                                                                    ("borderId", "0"), ("xfId", "0")]):
            new.set(k, v)
        for child in (base if base is not None else []):          # keep alignment/protection
            new.append(etree.fromstring(etree.tostring(child)))
        new.set("numFmtId", fid)
        new.set("applyNumberFormat", "1")
        xfs.set("count", str(len(xfs)))
        self.pkg.touch(path)
        self._date_styles[len(xfs) - 1] = True
        return len(xfs) - 1


def _si_text(si: etree._Element) -> str:
    """Text of a shared string, rich-text runs joined (phonetic hints skipped)."""
    parts = []
    for t in si.iter(q("t")):
        parent = t.getparent()
        if parent is not None and parent.tag == q("rPh"):
            continue
        parts.append(t.text or "")
    return "".join(parts)


# ------------------------------------------------------------------ one sheet
class Sheet:
    """One worksheet's XML with quick access to rows and cells."""

    def __init__(self, book: Book, info: SheetInfo, max_cells: int | None = None):
        self.book, self.info = book, info
        self.root = book.sheet_xml(info)
        self.data = self.root.find(q("sheetData"))
        if self.data is None:
            raise EditError(f"Sheet {info.name!r} has no cell data that can be read here.")
        self.rows: dict[int, etree._Element] = {}
        self._normalize(max_cells)
        self._merges: list[tuple[int, int, int, int]] | None = None

    @property
    def name(self) -> str:
        return self.info.name

    def _normalize(self, max_cells: int | None) -> None:
        """Index rows by number and fill in row/cell numbers some writers leave out."""
        last_row, count = 0, 0
        for row in self.data.findall(q("row")):
            r = int(row.get("r") or last_row + 1)
            if row.get("r") is None:
                row.set("r", str(r))                              # rows without numbers follow the last one
            last_row = r
            self.rows[r] = row
            last_col = 0
            for c in row.findall(q("c")):
                if c.get("r") is None:
                    c.set("r", ref_of(r, last_col + 1))
                last_col = split_ref(c.get("r"))[1]
                count += 1
        if max_cells and count > max_cells:
            raise EditError(f"Sheet {self.name!r} is too large to edit here (over {max_cells:,} cells).")

    def touch(self) -> None:
        self.book.pkg.touch(self.info.path)

    @property
    def protected(self) -> bool:
        p = self.root.find(q("sheetProtection"))
        return p is not None and p.get("sheet") in ("1", "true")

    def check_editable(self) -> None:
        if self.protected:
            raise EditError(f"Sheet {self.name!r} is protected (locked in Excel), so Claude won't change it. "
                            f"Ask someone to unprotect it first.")

    # -- cells
    def cell(self, r: int, c: int, create: bool = False) -> etree._Element | None:
        """The <c> element at (r, c); with create, made (in column order) when missing."""
        row = self.rows.get(r)
        if row is None:
            if not create:
                return None
            row = etree.Element(q("row"), r=str(r))
            later = [n for n in self.rows if n > r]
            if later:
                self.rows[min(later)].addprevious(row)            # keep rows in order
            else:
                self.data.append(row)
            self.rows[r] = row
        for el in row.findall(q("c")):
            col = split_ref(el.get("r"))[1]
            if col == c:
                return el
            if col > c:
                if not create:
                    return None
                new = etree.Element(q("c"), r=ref_of(r, c))
                el.addprevious(new)                               # keep cells in column order
                return new
        if not create:
            return None
        return etree.SubElement(row, q("c"), r=ref_of(r, c))

    def iter_cells(self):
        """Every <c> with its (row, col), in sheet order."""
        for r in sorted(self.rows):
            for el in self.rows[r].findall(q("c")):
                yield split_ref(el.get("r")) + (el,)

    def bounds(self) -> tuple[int, int]:
        """(last row, last column) holding any cell, like Excel's used range."""
        max_r = max_c = 0
        for r, c, _ in self.iter_cells():
            max_r, max_c = max(max_r, r), max(max_c, c)
        return max_r, max_c

    def value(self, el: etree._Element | None, raw: bool = False):
        """A cell's value as Claude sees it: number, text, bool, {"date"}, {"error"},
        or {"formula", "value"}. With raw, formulas give just their text."""
        if el is None:
            return None
        f = el.find(q("f"))
        if f is not None:
            text = self.formula_text(el)
            if raw:
                return "=" + text
            return {"formula": "=" + text, "value": self._cached(el)}
        return self._cached(el)

    def _cached(self, el: etree._Element):
        t = el.get("t") or "n"
        v = el.find(q("v"))
        if t == "inlineStr":
            node = el.find(q("is"))
            return _si_text(node) if node is not None else ""
        if v is None or v.text is None:
            return None
        text = v.text
        if t == "s":
            return self.book.string(int(text))
        if t == "b":
            return text.strip() in ("1", "true")
        if t == "e":
            return {"error": text}
        if t in ("str", "d"):
            return text
        try:
            num = float(text)
        except ValueError:
            return text
        if self.book.is_date_style(int(el.get("s") or 0)) and math.isfinite(num):
            return {"date": _date_from_serial(num, self.book.date1904)}
        return int(num) if num.is_integer() and abs(num) < 2 ** 53 and "." not in text and "E" not in text.upper() \
            else num

    def formula_text(self, el: etree._Element) -> str:
        """A cell's formula without '=', working out shared-formula followers from their leader."""
        f = el.find(q("f"))
        if f is None:
            return ""
        if f.text:
            return f.text
        if f.get("t") == "shared":                                # a follower: translate the leader's text
            leader = self._shared_leader(f.get("si"))
            if leader is not None:
                from openpyxl.formula.translate import Translator
                return Translator("=" + leader.find(q("f")).text, origin=leader.get("r")) \
                    .translate_formula(el.get("r"))[1:]
        return ""

    def _shared_leader(self, si: str | None) -> etree._Element | None:
        for _, _, el in self.iter_cells():
            f = el.find(q("f"))
            if f is not None and f.get("t") == "shared" and f.get("si") == si and f.text:
                return el
        return None

    def unshare(self, si: str | None = None) -> None:
        """Turn shared formulas (all, or group `si`) into ordinary ones, so cells can move or change alone."""
        cells = [el for _, _, el in self.iter_cells()
                 if (f := el.find(q("f"))) is not None and f.get("t") == "shared" and (si is None or f.get("si") == si)]
        texts = {id(el): self.formula_text(el) for el in cells}   # work them all out before changing any
        for el in cells:
            f = el.find(q("f"))
            f.text = texts[id(el)]
            for attr in ("t", "si", "ref"):
                f.attrib.pop(attr, None)
        if cells:
            self.touch()

    # -- merged cells
    def merges(self) -> list[tuple[int, int, int, int]]:
        if self._merges is None:
            self._merges = [parse_range(m.get("ref")) for m in self.root.iterfind(f"{q('mergeCells')}/{q('mergeCell')}")]
        return self._merges

    def merge_at(self, r: int, c: int) -> tuple[int, int, int, int] | None:
        for m in self.merges():
            if m[0] <= r <= m[2] and m[1] <= c <= m[3]:
                return m
        return None

    # -- related parts
    def related(self, kind: str) -> list[str]:
        """Parts this sheet links to of one kind (drawing, table, comments, vmlDrawing, ...)."""
        return [target for _, k, target, _ in self.book.pkg.rels(self.info.path) if k == kind]

    def table_headers(self) -> set[tuple[int, int]]:
        """Header cells of this sheet's tables (renaming those would break the table)."""
        out = set()
        for path in self.related("table"):
            t = self.book.pkg.xml(path)
            if t.get("headerRowCount", "1") != "0":
                r1, c1, _, c2 = parse_range(t.get("ref"))
                out |= {(r1, c) for c in range(c1, c2 + 1)}
        return out

    def default_style(self, r: int, c: int) -> str | None:
        """Style a new cell gets: its row's style, else its column's."""
        row = self.rows.get(r)
        if row is not None and row.get("customFormat") in ("1", "true") and row.get("s"):
            return row.get("s")
        for col in self.root.iterfind(f"{q('cols')}/{q('col')}"):
            if int(col.get("min")) <= c <= int(col.get("max")) and col.get("style"):
                return col.get("style")
        return None

    def fix_dimension(self) -> None:
        """Update <dimension ref> to the cells now present."""
        dim = self.root.find(q("dimension"))
        if dim is None:
            return
        coords = [(r, c) for r, c, _ in self.iter_cells()]
        new = range_text(min(r for r, _ in coords), min(c for _, c in coords),
                         max(r for r, _ in coords), max(c for _, c in coords)) if coords else "A1"
        if dim.get("ref") != new:
            dim.set("ref", new)


def _date_from_serial(num: float, date1904: bool) -> str:
    from openpyxl.utils.datetime import CALENDAR_MAC_1904, CALENDAR_WINDOWS_1900, from_excel
    value = from_excel(num, CALENDAR_MAC_1904 if date1904 else CALENDAR_WINDOWS_1900)
    if isinstance(value, dt.datetime) and value.time() == dt.time(0):
        return value.date().isoformat()                          # plain dates show without 00:00
    return value.isoformat()


def _serial_from_date(text: str, date1904: bool) -> tuple[float, bool]:
    """'2026-11-26' or '2026-11-26T08:30' -> (Excel serial number, has a time part)."""
    from openpyxl.utils.datetime import CALENDAR_MAC_1904, CALENDAR_WINDOWS_1900, to_excel
    try:
        value = dt.datetime.fromisoformat(str(text).strip().replace(" ", "T"))
    except ValueError:
        raise EditError(f"{text!r} isn't a date like 2026-11-26 or 2026-11-26T08:30.") from None
    with_time = value.time() != dt.time(0)
    return to_excel(value, CALENDAR_MAC_1904 if date1904 else CALENDAR_WINDOWS_1900), with_time


# ------------------------------------------------------------------ writing a value
def _clean_text(text: str) -> str:
    if len(text) > MAX_STRING:
        raise EditError(f"A cell holds at most {MAX_STRING:,} characters.")
    if _BAD_XML_CHARS.search(text):
        raise EditError("That text has control characters a spreadsheet can't store.")
    return text


def write_cell(sheet: Sheet, r: int, c: int, value, like: str | None = None) -> None:
    """Put `value` (see the module notes for its forms) into cell (r, c), keeping its style."""
    ref = ref_of(r, c)
    merge = sheet.merge_at(r, c)
    if merge and (merge[0], merge[1]) != (r, c):
        raise EditError(f"{sheet.name}!{ref} is inside the merged cells {range_text(*merge)}; "
                        f"write to {ref_of(merge[0], merge[1])} instead.")
    if (r, c) in sheet.table_headers():
        raise EditError(f"{sheet.name}!{ref} is a table's header; renaming table columns has to be done in Excel.")
    el = sheet.cell(r, c)
    if el is not None:
        f = el.find(q("f"))
        if f is not None and f.get("t") == "array":
            raise EditError(f"{sheet.name}!{ref} holds an array formula; change it in Excel.")
        if f is not None and f.get("t") == "shared" and f.get("ref"):
            sheet.unshare(f.get("si"))                            # its followers keep their formulas
    for a_r1, a_c1, a_r2, a_c2, a_ref in _array_ranges(sheet):
        if a_r1 <= r <= a_r2 and a_c1 <= c <= a_c2 and ref != a_ref:
            raise EditError(f"{sheet.name}!{ref} is part of an array formula in {a_ref}; change it in Excel.")
    old = sheet.value(el, raw=True)

    if value is None:                                             # empty it, keep the formatting
        if el is None:
            return
        _strip_value(el)
        if not el.get("s"):
            el.getparent().remove(el)
        sheet.book.changes.append(Change(sheet.name, ref, old, None))
        sheet.touch()
        return

    el = el if el is not None else sheet.cell(r, c, create=True)
    if like:                                                      # copy another cell's look
        lr, lc = split_ref(like)
        src = sheet.cell(lr, lc)
        if src is not None and src.get("s"):
            el.set("s", src.get("s"))
        else:
            el.attrib.pop("s", None)
    elif not el.get("s"):
        style = sheet.default_style(r, c)
        if style:
            el.set("s", style)
    _strip_value(el)

    book = sheet.book
    if isinstance(value, dict):                                   # the explicit forms
        if "text" in value:
            value = {"__text": str(value["text"])}
        elif "formula" in value:
            value = "=" + str(value["formula"]).lstrip("=")
        elif "date" in value:
            serial, with_time = _serial_from_date(value["date"], book.date1904)
            el.set("s", str(book.date_style_like(int(el.get("s") or 0), with_time)))
            etree.SubElement(el, q("v")).text = repr(serial) if not float(serial).is_integer() else str(int(serial))
            book.changes.append(Change(sheet.name, ref, old, {"date": str(value["date"])}))
            sheet.touch()
            return
        else:
            raise EditError(f"{ref}: a value object is {{\"text\": …}}, {{\"date\": …}} or {{\"formula\": …}}.")
    if isinstance(value, bool):
        el.set("t", "b")
        etree.SubElement(el, q("v")).text = "1" if value else "0"
    elif isinstance(value, (int, float)):
        if isinstance(value, float) and not math.isfinite(value):
            raise EditError(f"{ref}: {value} isn't a number a spreadsheet can store.")
        etree.SubElement(el, q("v")).text = repr(value) if isinstance(value, float) else str(value)
    elif isinstance(value, str) and value.startswith("=") and len(value) > 1:
        formula = _clean_text(value[1:])
        _check_formula(formula, ref)
        etree.SubElement(el, q("f")).text = formula               # no cached value: Excel works it out
        book.formulas_changed = True
    elif isinstance(value, (str, dict)):
        text = _clean_text(value["__text"] if isinstance(value, dict) else value)
        el.set("t", "s")
        etree.SubElement(el, q("v")).text = str(book.string_index(text))
    else:
        raise EditError(f"{ref}: send a number, text, true/false, a formula (\"=…\") or null.")
    new = value["__text"] if isinstance(value, dict) else value
    book.changes.append(Change(sheet.name, ref, old, new))
    sheet.touch()


def _strip_value(el: etree._Element) -> None:
    """Remove a cell's value and formula (and metadata pointers), keep its style."""
    for child in list(el):
        el.remove(child)
    for attr in ("t", "cm", "vm"):
        el.attrib.pop(attr, None)                                 # type, cell/value metadata


def _array_ranges(sheet: Sheet):
    """(r1, c1, r2, c2, leader ref) for each array formula on the sheet."""
    for _, _, el in sheet.iter_cells():
        f = el.find(q("f"))
        if f is not None and f.get("t") == "array" and f.get("ref"):
            yield (*parse_range(f.get("ref")), el.get("r"))


def _check_formula(formula: str, ref: str) -> None:
    """Refuse formulas the tokenizer can't read (Excel would say the file is broken)."""
    from openpyxl.formula.tokenizer import Tokenizer
    try:
        Tokenizer("=" + formula)
    except Exception as exc:   # the tokenizer raises plain errors for unbalanced input
        raise EditError(f"{ref}: the formula ={formula} can't be read ({exc}).") from None
    depth, quote = 0, None
    for ch in formula:                                            # brackets must pair up outside quotes
        if quote:
            quote = None if ch == quote else quote
        elif ch in "\"'":
            quote = ch
        elif ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
            if depth < 0:
                break
    if depth != 0 or quote:
        raise EditError(f"{ref}: the formula ={formula} can't be read (its brackets or quotes don't pair up).")


# ------------------------------------------------------------------ formula reference rewriting
_REF_TOKEN = re.compile(
    r"^(?:(?P<sheet>'(?:[^']|'')+'|[^'!:\[\]]+)!)?"                # optional Sheet! or 'My Sheet'!
    r"(?P<a>\$?[A-Za-z]{1,3}\$?\d+|\$?[A-Za-z]{1,3}|\$?\d+)"       # A1, A, or 1
    r"(?::(?P<b>\$?[A-Za-z]{1,3}\$?\d+|\$?[A-Za-z]{1,3}|\$?\d+))?$")
_PART = re.compile(r"^(\$?)([A-Za-z]{1,3})?(\$?)(\d+)?$")


def sheet_label(name: str) -> str:
    """How a formula names a sheet: Rates! or 'My Rates'!"""
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.]*", name) and not _CELL.match(name) and \
            not re.fullmatch(r"[A-Za-z]{1,3}\d+|R\d*C\d*", name, re.I):
        return name
    return "'" + name.replace("'", "''") + "'"


def _unquote(label: str) -> str:
    return label[1:-1].replace("''", "'") if label.startswith("'") else label


@dataclass
class Shift:
    """Rows (axis 'row') or columns (axis 'col') inserted (count > 0) or deleted (count < 0) at `at`."""
    sheet: str
    axis: str
    at: int
    count: int

    def point(self, n: int) -> int | None:
        """Where row/column n goes; None when it was deleted."""
        if self.count > 0:
            return n + self.count if n >= self.at else n
        end = self.at - self.count - 1                            # last deleted
        if n < self.at:
            return n
        if n <= end:
            return None
        return n + self.count

    def span(self, lo: int, hi: int) -> tuple[int, int] | None:
        """Where the span lo..hi goes; None when all of it was deleted."""
        if self.count > 0:
            return self.point(lo), self.point(hi)
        end = self.at - self.count - 1
        new_lo = self.point(lo) if not (self.at <= lo <= end) else self.at
        new_hi = self.point(hi) if not (self.at <= hi <= end) else self.at - 1
        if new_hi < new_lo:
            return None
        return new_lo, new_hi


def _move_ref(text: str, shift: Shift) -> str | None:
    """Move one A1/range reference (no sheet prefix); None = #REF! (it was deleted)."""
    parts = text.split(":")
    parsed = [_PART.match(p) for p in parts]
    if not all(parsed):
        return text
    axis_row = shift.axis == "row"
    nums = [(int(m.group(4)) if m.group(4) else None) if axis_row else
            (col_num(m.group(2)) if m.group(2) else None) for m in parsed]
    if len(parts) == 1 and not (parsed[0].group(2) and parsed[0].group(4)):
        return text                                               # "Tax" or "12" alone is a name/number, not a cell
    if any(n is None for n in nums):                              # whole columns (rows axis) or rows (cols axis)
        return text
    if len(nums) == 1:
        moved = shift.point(nums[0])
        if moved is None:
            return None
        new = [moved]
    else:
        lo, hi = sorted(nums)
        span = shift.span(lo, hi)
        if span is None:
            return None
        new = list(span) if nums[0] <= nums[1] else list(reversed(span))
    out = []
    for m, n in zip(parsed, new):
        d1, letters, d2, digits = m.groups()
        if axis_row:
            out.append(f"{d1}{letters or ''}{d2}{n}")
        else:
            out.append(f"{d1}{col_letters(n)}{d2}{digits or ''}")
    return ":".join(out)


def rewrite_formula(formula: str, context: str | None, shift: Shift | None = None,
                    rename: tuple[str, str] | None = None, where: str = "") -> str:
    """A formula (without '=') with its references to the shifted sheet moved, or a sheet renamed.
    `context` is the sheet the formula lives on (None for workbook-level names and charts)."""
    from openpyxl.formula.tokenizer import Token, Tokenizer
    target = shift.sheet if shift else rename[0]
    if (shift is None or context is None or context.casefold() != target.casefold())             and target.casefold() not in formula.casefold()             and target.replace("'", "''").casefold() not in formula.casefold():
        return formula                                            # quick exit: can't mention the sheet
    try:
        tok = Tokenizer("=" + formula)
    except Exception:   # the tokenizer raises plain errors for things it can't read
        if (shift and context == target) or target.casefold() in formula.casefold():
            raise EditError(f"The formula {where} =\"{formula[:60]}\" couldn't be read to move its references; "
                            f"do this change in Excel.") from None
        return formula
    changed = False
    for item in tok.items:
        if item.type != Token.OPERAND or item.subtype != Token.RANGE:
            continue
        m = _REF_TOKEN.match(item.value)
        if not m:
            continue                                              # names, Table1[Col], 3-D refs: unchanged
        label = m.group("sheet")
        on = _unquote(label) if label else context
        if on is None or on.casefold() != target.casefold():
            continue
        if rename:
            if label:
                item.value = sheet_label(rename[1]) + "!" + item.value[len(label) + 1:]
                changed = True
            continue
        body = item.value[len(label) + 1:] if label else item.value
        moved = _move_ref(body, shift)
        new = (label + "!" if label else "") + (moved if moved is not None else "#REF!")
        if new != item.value:
            item.value = new
            changed = True
    if not changed:
        return formula
    return "".join(i.value for i in tok.items)


def _move_sqref(text: str, shift: Shift) -> str:
    """A space-separated list of ranges (sqref) moved; deleted ones dropped."""
    out = []
    for part in str(text).split():
        moved = _move_ref(part.replace("$", ""), shift)
        if moved is not None:
            out.append(moved)
    return " ".join(out)


# ------------------------------------------------------------------ shifting rows / columns
def _blockers(sheet: Sheet, shift: Shift) -> None:
    """Refuse a row/column change when a part we can't move safely depends on the sheet."""
    pkg = sheet.book.pkg
    for kind in ("threadedComment", "slicer", "timeline", "pivotTable"):
        if sheet.related(kind):
            raise EditError(f"Sheet {sheet.name!r} has {'pivot tables' if kind == 'pivotTable' else kind + 's'}, "
                            f"which can't be moved here; insert or delete in Excel.")
    for name in pkg.names:                                        # pivot caches built from this sheet
        if re.match(r"xl/pivotCache/pivotCacheDefinition\d+\.xml$", name):
            src = pkg.xml(name).find(f"{q('cacheSource')}/{q('worksheetSource')}")
            if src is not None and (src.get("sheet") or "").casefold() == sheet.name.casefold():
                raise EditError(f"A pivot table is built from sheet {sheet.name!r}; insert or delete in Excel.")
    for path in sheet.related("table"):                           # tables can grow/shrink, not gain columns
        t = pkg.xml(path)
        r1, c1, r2, c2 = parse_range(t.get("ref"))
        lo, hi = (r1, r2) if shift.axis == "row" else (c1, c2)
        end = shift.at + abs(shift.count) - 1
        if shift.axis == "col" and lo < shift.at <= hi:
            raise EditError(f"Table {t.get('displayName')} spans those columns; add or remove table columns in Excel.")
        if shift.axis == "col" and shift.count < 0 and shift.at <= lo and end >= lo and end < hi:
            raise EditError(f"Table {t.get('displayName')} spans those columns; add or remove table columns in Excel.")
        if shift.axis == "row" and shift.count < 0:
            name = t.get("displayName")
            header = t.get("headerRowCount", "1") != "0"
            if shift.at <= r1 and end >= r2:
                raise EditError(f"That would delete all of table {name}; delete the table in Excel.")
            if header and shift.at <= r1 <= end:
                raise EditError(f"That would delete the header row of table {name}.")
            first_data = r1 + 1 if header else r1
            gone = max(0, min(r2, end) - max(first_data, shift.at) + 1)   # data rows inside the deletion
            if r2 - first_data + 1 - gone < 1:
                raise EditError(f"That would leave table {name} with no data rows.")
    for r1, c1, r2, c2, ref in _array_ranges(sheet):                # a delete mustn't cut an array formula
        lo, hi = (r1, r2) if shift.axis == "row" else (c1, c2)
        if shift.count < 0:
            end = shift.at - shift.count - 1
            inside = [n for n in range(lo, hi + 1) if shift.at <= n <= end]
            if inside and len(inside) != hi - lo + 1:
                raise EditError(f"That would cut the array formula in {sheet.name}!{ref}; change it in Excel.")
        elif lo < shift.at <= hi:
            raise EditError(f"That would split the array formula in {sheet.name}!{ref}; change it in Excel.")
    for _, _, el in sheet.iter_cells():
        f = el.find(q("f"))
        if f is not None and f.get("t") == "dataTable":
            raise EditError(f"Sheet {sheet.name!r} has a what-if data table; insert or delete in Excel.")
    if shift.axis == "col":
        af = sheet.root.find(q("autoFilter"))
        if af is not None and af.find(q("filterColumn")) is not None:
            c1, c2 = parse_range(af.get("ref"))[1::2]
            if c1 < shift.at <= c2 or shift.count < 0 and shift.at <= c2:
                raise EditError("The sheet's filter has active column filters there; clear them or do this in Excel.")


def shift_sheet(sheet: Sheet, shift: Shift, copy_style: str = "above") -> list[str]:
    """Insert or delete rows/columns on one sheet and move everything pointing at them.
    Returns short notes of what moved, for the result."""
    _blockers(sheet, shift)
    sheet.unshare()                                               # shared formulas are easier moved one by one
    book, pkg, notes = sheet.book, sheet.book.pkg, []
    rows_axis = shift.axis == "row"
    end = shift.at + abs(shift.count) - 1

    # 1. the cells themselves
    if rows_axis:
        for r in sorted(sheet.rows, reverse=shift.count > 0):     # move from the far end so numbers don't clash
            row = sheet.rows[r]
            new_r = shift.point(r)
            if new_r is None:
                for c in row.findall(q("c")):
                    old = sheet.value(c, raw=True)
                    if old is not None:
                        book.changes.append(Change(sheet.name, c.get("r"), old, None))
                sheet.data.remove(row)
                continue
            if new_r != r:
                row.set("r", str(new_r))
                for c in row.findall(q("c")):
                    c.set("r", ref_of(new_r, split_ref(c.get("r"))[1]))
        sheet.rows = {int(row.get("r")): row for row in sheet.data.findall(q("row"))}
        if shift.count > 0 and copy_style in ("above", "below"):  # new rows look like their neighbour
            source = sheet.rows.get(shift.at - 1 if copy_style == "above" else shift.at + shift.count)
            if source is not None:
                for n in range(shift.at, shift.at + shift.count):
                    _styled_row_like(sheet, source, n)
    else:
        for r, row in sheet.rows.items():
            row.attrib.pop("spans", None)                         # a hint Excel recomputes
            for c in row.findall(q("c")):
                col = split_ref(c.get("r"))[1]
                new_c = shift.point(col)
                if new_c is None:
                    old = sheet.value(c, raw=True)
                    if old is not None:
                        book.changes.append(Change(sheet.name, c.get("r"), old, None))
                    row.remove(c)
                elif new_c != col:
                    c.set("r", ref_of(r, new_c))
        _shift_cols(sheet, shift)
    sheet.touch()

    # 2. formulas on every sheet, defined names, charts
    moved = _rewrite_all(book, shift=shift)
    if moved:
        notes.append(f"{moved} formula(s) updated")

    # 3. ranges stored on this sheet
    root = sheet.root
    count = 0
    for tag, attr in (("mergeCell", "ref"), ("hyperlink", "ref"), ("conditionalFormatting", "sqref"),
                      ("dataValidation", "sqref"), ("selection", "sqref"), ("protectedRange", "sqref"),
                      ("ignoredError", "sqref")):
        for el in list(root.iter(q(tag))):
            old = el.get(attr)
            if old is None:
                continue
            new = _move_sqref(old, shift) if attr == "sqref" else (_move_ref(old.replace("$", ""), shift) or "")
            if not new or (tag == "mergeCell" and ":" not in new):
                el.getparent().remove(el)                         # all of it deleted (or a merge of one cell)
                count += 1
            elif new != old:
                el.set(attr, new)
                count += 1
    for el in root.iter(q("sqref", XM)):                          # x14 extension lists (newer validation/formatting)
        if el.text:
            new = _move_sqref(el.text, shift)
            if new != el.text:
                count += 1
                if new:
                    el.text = new
                else:
                    holder = el.getparent()
                    holder.getparent().remove(holder)
    for tag in ("formula", "formula1", "formula2"):               # validation / conditional formatting rules
        for el in root.iter(q(tag)):
            if el.text:
                el.text = rewrite_formula(el.text, sheet.name, shift, where=f"in {sheet.name}'s rules")
    for el in root.iter(q("f", XM)):
        if el.text:
            el.text = rewrite_formula(el.text, sheet.name, shift, where=f"in {sheet.name}'s rules")
    for container, child in (("mergeCells", "mergeCell"), ("dataValidations", "dataValidation"),
                             ("hyperlinks", "hyperlink")):
        holder = root.find(q(container))
        if holder is not None:
            n = len(holder.findall(q(child)))
            if n == 0:
                root.remove(holder)
            elif holder.get("count") is not None:
                holder.set("count", str(n))
    for cf in list(root.findall(q("conditionalFormatting"))):
        if not cf.get("sqref"):
            root.remove(cf)
    af = root.find(q("autoFilter"))
    if af is not None and af.get("ref"):
        new = _move_ref(af.get("ref").replace("$", ""), shift)
        af.set("ref", new) if new else root.remove(af)
    _shift_views(sheet, shift)
    _shift_breaks(sheet, shift)
    if count:
        notes.append(f"{count} merged range(s)/rule(s)/link(s) moved")
    sheet._merges = None
    sheet.fix_dimension()

    # 4. parts hanging off this sheet
    for path in sheet.related("table"):
        t = pkg.xml(path)
        for el in [t, t.find(q("autoFilter"))]:
            if el is not None and el.get("ref"):
                new = _move_ref(el.get("ref").replace("$", ""), shift)
                if new and new != el.get("ref"):
                    el.set("ref", new)
                    pkg.touch(path)
        for el in t.iter(q("calculatedColumnFormula")):
            if el.text:
                new = rewrite_formula(el.text, sheet.name, shift, where="in a table")
                if new != el.text:
                    el.text = new
                    pkg.touch(path)
    for path in sheet.related("drawing"):
        if _shift_anchors(pkg, path, shift):
            notes.append("pictures/charts moved with their cells")
    for path in sheet.related("comments"):
        _shift_comments(sheet, path, shift)
    for path in sheet.related("vmlDrawing"):
        _shift_vml(pkg, path, shift)

    book.formulas_changed = True
    word = "row" if rows_axis else "column"
    what = (f"{abs(shift.count)} {word}(s) " + ("inserted at " if shift.count > 0 else "deleted at ")
            + (str(shift.at) if rows_axis else col_letters(shift.at))
            + ("" if shift.count > 0 or abs(shift.count) == 1 else f"-{end if rows_axis else col_letters(end)}"))
    return [f"{sheet.name}: {what}" + (f" ({'; '.join(notes)})" if notes else "")]


def _styled_row_like(sheet: Sheet, source: etree._Element, r: int) -> None:
    """Make an empty row r with the same height and cell styles as `source`."""
    row = etree.Element(q("row"), r=str(r))
    for attr in ("ht", "customHeight", "s", "customFormat"):
        if source.get(attr) is not None:
            row.set(attr, source.get(attr))
    for c in source.findall(q("c")):
        if c.get("s"):
            etree.SubElement(row, q("c"), r=ref_of(r, split_ref(c.get("r"))[1]), s=c.get("s"))
    if not len(row) and set(row.attrib) == {"r"}:
        return                                                    # nothing to copy
    later = [n for n in sheet.rows if n > r]
    if later:
        sheet.rows[min(later)].addprevious(row)
    else:
        sheet.data.append(row)
    sheet.rows[r] = row


def _shift_cols(sheet: Sheet, shift: Shift) -> None:
    """Column widths/styles (<cols>) follow inserted or deleted columns."""
    cols = sheet.root.find(q("cols"))
    if cols is None:
        return
    for col in list(cols):
        lo, hi = int(col.get("min")), int(col.get("max"))
        if shift.count > 0 and lo < shift.at <= hi:
            col.set("max", str(hi + shift.count))                 # inserted inside: the span grows
            continue
        span = shift.span(lo, hi)
        if span is None:
            cols.remove(col)
        else:
            col.set("min", str(span[0]))
            col.set("max", str(span[1]))
    if not len(cols):
        sheet.root.remove(cols)


def _shift_views(sheet: Sheet, shift: Shift) -> None:
    """Frozen panes and the remembered selection follow the change."""
    for pane in sheet.root.iter(q("pane")):
        split_attr = "ySplit" if shift.axis == "row" else "xSplit"
        if pane.get(split_attr) and pane.get("state") in ("frozen", "frozenSplit"):
            frozen = int(float(pane.get(split_attr)))
            if shift.count > 0 and shift.at <= frozen:
                pane.set(split_attr, str(frozen + shift.count))   # inserting inside the frozen area grows it
            elif shift.count < 0:
                gone = sum(1 for n in range(shift.at, shift.at - shift.count) if n <= frozen)
                if gone:
                    pane.set(split_attr, str(max(0, frozen - gone)))
        if pane.get("topLeftCell"):
            pane.set("topLeftCell", _move_ref(pane.get("topLeftCell").replace("$", ""), shift) or "A1")
    for sel in sheet.root.iter(q("selection")):
        if sel.get("activeCell"):
            sel.set("activeCell", _move_ref(sel.get("activeCell").replace("$", ""), shift) or "A1")
        if not sel.get("sqref"):
            sel.set("sqref", sel.get("activeCell") or "A1")


def _shift_breaks(sheet: Sheet, shift: Shift) -> None:
    """Manual page breaks."""
    holder = sheet.root.find(q("rowBreaks" if shift.axis == "row" else "colBreaks"))
    if holder is None:
        return
    for brk in list(holder):
        new = shift.point(int(brk.get("id")))
        if new is None:
            holder.remove(brk)
        else:
            brk.set("id", str(new))
    holder.set("count", str(len(holder)))
    if holder.get("manualBreakCount") is not None:
        holder.set("manualBreakCount", str(len(holder)))


def _shift_anchors(pkg: Package, path: str, shift: Shift) -> bool:
    """Picture/chart anchors (0-based rows/columns) in a drawing part."""
    root = pkg.xml(path)
    tag = q("row", XDR) if shift.axis == "row" else q("col", XDR)
    changed = False
    for marker in list(root.iter(q("from", XDR))) + list(root.iter(q("to", XDR))):
        el = marker.find(tag)
        if el is None or el.text is None:
            continue
        n = int(el.text) + 1                                      # to 1-based
        new = shift.point(n)
        if new is None:
            new = shift.at                                        # its cell was deleted: snap to the gap
        if new != n:
            el.text = str(new - 1)
            changed = True
    if changed:
        pkg.touch(path)
    return changed


def _shift_comments(sheet: Sheet, path: str, shift: Shift) -> None:
    """Legacy (note-style) comments follow their cells; comments on deleted cells go."""
    pkg = sheet.book.pkg
    root = pkg.xml(path)
    for comment in list(root.iter(q("comment"))):
        new = _move_ref(comment.get("ref").replace("$", ""), shift)
        if new is None:
            comment.getparent().remove(comment)
        elif new != comment.get("ref"):
            comment.set("ref", new)
    pkg.touch(path)


def _shift_vml(pkg: Package, path: str, shift: Shift) -> None:
    """The boxes Excel draws for legacy comments (x:Row / x:Column, 0-based, and x:Anchor)."""
    try:
        root = pkg.xml(path)
    except EditError:
        raise EditError("The sheet's comment boxes couldn't be read, so rows/columns can't be moved here; "
                        "do this change in Excel.") from None
    tag = q("Row", VML_X) if shift.axis == "row" else q("Column", VML_X)
    changed = False
    for data in root.iter(q("ClientData", VML_X)):
        el = data.find(tag)
        if el is not None and el.text and el.text.strip().isdigit():
            new = shift.point(int(el.text) + 1)
            text = str((new if new is not None else shift.at) - 1)
            changed |= text != el.text.strip()
            el.text = text
        anchor = data.find(q("Anchor", VML_X))
        if anchor is not None and anchor.text:
            nums = [int(x) for x in re.findall(r"-?\d+", anchor.text)]
            if len(nums) == 8:                                    # LeftCol, dx, TopRow, dy, RightCol, dx, BottomRow, dy
                old = list(nums)
                for i in ((2, 6) if shift.axis == "row" else (0, 4)):
                    moved = shift.point(nums[i] + 1)
                    nums[i] = (moved if moved is not None else shift.at) - 1
                if nums != old:
                    anchor.text = ", ".join(str(n) for n in nums)
                    changed = True
    if changed:
        pkg.touch(path)                                           # header/footer pictures have no cells: left alone


def _rewrite_all(book: Book, shift: Shift | None = None, rename: tuple[str, str] | None = None) -> int:
    """Rewrite references in every formula of the workbook (cells on all sheets, defined names,
    charts, internal hyperlinks, pivot sources). Only parts that change are marked for saving."""
    pkg, changed = book.pkg, 0
    for info in book.sheets:
        sheet_root = book.sheet_xml(info)
        touched = False
        for f in sheet_root.iter(q("f")):
            if f.text:
                new = rewrite_formula(f.text, info.name, shift, rename,
                                      where=f"in {info.name}!{f.getparent().get('r')}")
                if new != f.text:
                    f.text = new
                    v = f.getparent().find(q("v"))
                    if v is not None:
                        f.getparent().remove(v)                    # cached value is stale now
                    touched, changed = True, changed + 1
            if f.get("t") == "array" and f.get("ref") and shift and info.name == shift.sheet:
                new = _move_ref(f.get("ref").replace("$", ""), shift)
                if new and new != f.get("ref"):
                    f.set("ref", new)
                    touched = True
        if rename:
            for link in sheet_root.iter(q("hyperlink")):           # links to a place in the workbook
                loc = link.get("location")
                if loc:
                    new = rewrite_formula(loc, None, None, rename)
                    if new != loc:
                        link.set("location", new)
                        touched = True
        if touched:
            pkg.touch(info.path)
    names = book.wb.find(q("definedNames"))
    for dn in (names if names is not None else []):
        if dn.text:
            local = dn.get("localSheetId")
            context = book.sheets[int(local)].name if local is not None and int(local) < len(book.sheets) else None
            new = rewrite_formula(dn.text, context, shift, rename, where=f"in the name {dn.get('name')}")
            if new != dn.text:
                dn.text = new
                pkg.touch(book.path)
                changed += 1
    for name in sorted(pkg.names):
        if re.match(r"xl/charts/chart\d+\.xml$", name) or re.match(r"xl/charts/chartEx\d+\.xml$", name):
            root, touched = pkg.xml(name), False
            for f in root.iter(q("f", CHART), "{http://schemas.microsoft.com/office/drawing/2014/chartex}f"):
                if f.text:
                    new = rewrite_formula(f.text, None, shift, rename, where="in a chart")
                    if new != f.text:
                        f.text, touched = new, True
            if touched:
                pkg.touch(name)
        if rename and re.match(r"xl/pivotCache/pivotCacheDefinition\d+\.xml$", name):
            src = pkg.xml(name).find(f"{q('cacheSource')}/{q('worksheetSource')}")
            if src is not None and src.get("sheet") == rename[0]:
                src.set("sheet", rename[1])
                pkg.touch(name)
    return changed


# ------------------------------------------------------------------ reading for open_file
def view(data: bytes, sheet: str | None = None, cells: str | None = None, find=None,
         max_cells: int = 2000) -> dict:
    """What open_file shows of a workbook: the sheets, then either a window of cells or the matches of `find`."""
    book = Book.open(data)
    info = book.sheet(sheet) if (sheet or find is None) else None
    sheets = []
    for s in book.sheets:
        sh = Sheet(book, s)
        rows, cols = sh.bounds()
        entry = {"name": s.name, "rows": rows, "columns": cols}
        if s.element.get("state") in ("hidden", "veryHidden"):
            entry["hidden"] = True
        if sh.protected:
            entry["protected"] = True
        sheets.append(entry)
    out: dict = {"format": "xlsx", "sheets": sheets}
    if find not in (None, ""):
        out.update(_find(book, info, cells, find))
        return out

    sh = Sheet(book, info)
    if cells:
        min_row, min_col, max_row, max_col = parse_range(cells)
        last_r, last_c = sh.bounds()
        max_row, max_col = min(max_row, max(last_r, min_row)), min(max_col, max(last_c, min_col))
    else:
        last_r, last_c = sh.bounds()
        min_row, min_col = 1, 1
        max_col = max(1, min(last_c, 26))
        max_row = max(1, min(last_r, max(1, max_cells // max_col)))
    if (max_col - min_col + 1) * (max_row - min_row + 1) > max_cells:
        raise EditError(f"Ask for at most {max_cells} cells at a time (e.g. A1:J200).")
    rows = []
    for r in range(min_row, max_row + 1):
        row_el = sh.rows.get(r)
        if row_el is None:
            continue
        row = {}
        for el in row_el.findall(q("c")):
            c = split_ref(el.get("r"))[1]
            if min_col <= c <= max_col:
                value = sh.value(el)
                if value is not None:
                    row[el.get("r")] = value
        if row:
            rows.append(row)
    merged = [range_text(*m) for m in sh.merges()
              if not (m[2] < min_row or m[0] > max_row or m[3] < min_col or m[1] > max_col)]
    out.update({"sheet": info.name, "range": range_text(min_row, min_col, max_row, max_col), "cells": rows})
    if merged:
        out["merged"] = merged
    tables = []
    for path in sh.related("table"):
        t = book.pkg.xml(path)
        tables.append({"name": t.get("displayName"), "range": t.get("ref")})
    if tables:
        out["tables"] = tables
    out["editable"] = not sh.protected
    out["note"] = (f"Sheet {info.name!r} is protected, so Claude can't change it." if sh.protected else
                   "Formulas show with their last calculated value; Excel recalculates on open. "
                   "Use `find` to locate a value (e.g. an extension), then edit_spreadsheet.")
    return out


def _matches(value, query) -> bool:
    """Whether a cell value matches what was searched for: equal numbers, or text containing it."""
    if isinstance(value, dict):
        value = value.get("value", value.get("date", value.get("error")))
    if value is None:
        return False
    if isinstance(query, (int, float)) and not isinstance(query, bool):
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return float(value) == float(query)
        return str(value).strip() == str(query)
    q_text = str(query).strip().casefold()
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            return float(q_text) == float(value)
        except ValueError:
            return False
    return q_text in str(value).casefold()


def _find(book: Book, info: SheetInfo | None, cells: str | None, query) -> dict:
    """Cells matching `query` (on one sheet, or all), each with the rest of its row."""
    bounds = parse_range(cells) if cells else None
    found, total = [], 0
    for s in ([info] if info else book.sheets):
        sh = Sheet(book, s)
        for r, c, el in sh.iter_cells():
            if bounds and not (bounds[0] <= r <= bounds[2] and bounds[1] <= c <= bounds[3]):
                continue
            value = sh.value(el)
            if not _matches(value, query):
                continue
            total += 1
            if len(found) >= MAX_FIND:
                continue
            row = {}
            for other in sh.rows[r].findall(q("c")):
                oc = split_ref(other.get("r"))[1]
                if bounds and not bounds[1] <= oc <= bounds[3]:
                    continue
                v = sh.value(other)
                if v is not None:
                    row[other.get("r")] = v
            found.append({"sheet": s.name, "cell": el.get("r"), "value": value, "row": row})
    out = {"find": query, "found": found, "matches": total}
    if total > len(found):
        out["note"] = f"Showing {len(found)} of {total} matches; narrow it with `sheet` or `cells`."
    elif not total:
        out["note"] = "Nothing matched. Numbers match exactly; text matches any cell containing it (any case)."
    return out


# ------------------------------------------------------------------ applying operations
@dataclass
class Applied:
    data: bytes
    changes: list[str]
    diff: list[dict]


def apply(data: bytes, ops: list[dict], max_cells: int = 200_000) -> Applied:
    """Run edit_spreadsheet operations in order on one copy of the workbook; all or nothing."""
    book = Book.open(data)
    sheets: dict[str, Sheet] = {}
    notes: list[str] = []

    def sheet_for(op: dict, n: int) -> Sheet:
        info = book.sheet(op.get("sheet"))
        if info.path not in sheets:
            sheets[info.path] = Sheet(book, info, max_cells)
        sh = sheets[info.path]
        sh.check_editable()
        return sh

    for n, op in enumerate(ops, 1):
        kind = op.get("op")
        try:
            handler = _OPS[kind]
        except KeyError:
            raise EditError(f"Operation {n}: unknown op {kind!r}. Use {', '.join(_OPS)}.") from None
        try:
            notes.extend(handler(book, op, n, sheet_for))
        except EditError as exc:
            msg = str(exc)
            raise EditError(msg if msg.startswith("Operation") else f"Operation {n}: {msg}") from None

    for sh in sheets.values():
        if sh.info.path in book.pkg.dirty:
            sh.fix_dimension()
    if book.formulas_changed:
        _recalc_on_open(book)
    diff = [{"sheet": c.sheet, "cell": c.cell, "old": c.old, "new": c.new}
            for c in book.changes if c.old != c.new]
    if len(diff) > MAX_DIFF:
        notes.append(f"{len(diff)} cells changed in all (the diff lists the first {MAX_DIFF})")
        diff = diff[:MAX_DIFF]
    return Applied(book.pkg.save(), notes, diff)


def _recalc_on_open(book: Book) -> None:
    """Ask Excel to recalculate everything on open, and drop calcChain.xml (Excel rebuilds it)."""
    wb = book.wb
    calc = wb.find(q("calcPr"))
    if calc is None:
        calc = etree.Element(q("calcPr"))
        anchor = None
        for tag in ("oleSize", "customWorkbookViews", "pivotCaches", "smartTagPr", "smartTagTypes",
                    "webPublishing", "fileRecoveryPr", "webPublishObjects", "extLst"):
            anchor = wb.find(q(tag))
            if anchor is not None:
                break
        anchor.addprevious(calc) if anchor is not None else wb.append(calc)
    if calc.get("fullCalcOnLoad") != "1":
        calc.set("fullCalcOnLoad", "1")
        book.pkg.touch(book.path)
    for rid, kind, target, rel in book.pkg.rels(book.path):
        if kind == "calcChain":
            rel.getparent().remove(rel)
            book.pkg.touch(book.pkg.rels_path(book.path))
            book.pkg.remove(target)
            book.pkg.drop_content_type(target)


# -- the operations: each returns note lines for the result
def _op_set_cells(book, op, n, sheet_for):
    values = op.get("cells")
    if not isinstance(values, dict) or not values:
        raise EditError("`cells` maps cell names to values, e.g. {\"B4\": 12.5}.")
    sh = sheet_for(op, n)
    for ref, value in values.items():
        r, c = split_ref(ref)
        write_cell(sh, r, c, value, op.get("like"))
    return [f"{sh.name}: set {', '.join(str(k).upper() for k in list(values)[:8])}{'…' if len(values) > 8 else ''}"]


def _op_set_range(book, op, n, sheet_for):
    start, values = op.get("start"), op.get("values")
    if not start or not isinstance(values, list) or not all(isinstance(r, list) for r in values):
        raise EditError("set_range needs `start` (e.g. \"J30\") and `values`, a list of rows (lists of values).")
    sh = sheet_for(op, n)
    r0, c0 = split_ref(start)
    for i, row in enumerate(values):
        for j, value in enumerate(row):
            write_cell(sh, r0 + i, c0 + j, value, op.get("like"))
    width = max((len(r) for r in values), default=1)
    return [f"{sh.name}: set {range_text(r0, c0, r0 + max(len(values), 1) - 1, c0 + max(width, 1) - 1)}"]


def _op_clear_cells(book, op, n, sheet_for):
    target = op.get("range") or op.get("cells")
    if not target:
        raise EditError("clear_cells needs `range`, e.g. \"E17:G17\".")
    sh = sheet_for(op, n)
    r1, c1, r2, c2 = parse_range(target)
    if (r2 - r1 + 1) * (c2 - c1 + 1) > 100_000:
        raise EditError("Clear at most 100,000 cells at a time.")
    for r, c, el in list(sh.iter_cells()):
        if r1 <= r <= r2 and c1 <= c <= c2 and sh.value(el, raw=True) is not None:
            merge = sh.merge_at(r, c)
            if merge and (merge[0], merge[1]) != (r, c):
                continue                                          # merged-over cells hold nothing to clear
            write_cell(sh, r, c, None)
    return [f"{sh.name}: cleared {range_text(r1, c1, r2, c2)}"]


def _key_equal(value, key) -> bool:
    if isinstance(value, dict):
        value = value.get("value", value.get("date"))
    if value is None:
        return False
    if isinstance(key, (int, float)) and not isinstance(key, bool) or isinstance(value, (int, float)):
        try:
            return float(str(value).strip()) == float(str(key).strip())
        except ValueError:
            return False
    return str(value).strip().casefold() == str(key).strip().casefold()


def _op_set_by_key(book, op, n, sheet_for):
    key_column, key, updates = op.get("key_column"), op.get("key"), op.get("set")
    if not key_column or key in (None, "") or not isinstance(updates, dict) or not updates:
        raise EditError("set_by_key needs `key_column` (e.g. \"C\"), `key` (e.g. 4916) and `set` "
                        "({\"B\": \"new name\"}).")
    sh = sheet_for(op, n)
    kc = col_num(str(key_column).strip().upper()) if re.fullmatch(r"[A-Za-z]{1,3}", str(key_column).strip()) \
        else None
    if kc is None:
        raise EditError(f"`key_column` is a column letter like C, not {key_column!r}.")
    r1, c1, r2, c2 = parse_range(op["within"]) if op.get("within") else (1, 1, 1048576, 16384)
    if not c1 <= kc <= c2:
        raise EditError(f"Column {key_column} is outside `within` {op['within']}.")
    rows = [r for r, c, el in sh.iter_cells() if c == kc and r1 <= r <= r2 and _key_equal(sh.value(el), key)]
    if not rows:
        raise EditError(f"No row has {key!r} in column {str(key_column).upper()} on {sh.name!r}"
                        + (f" within {op['within']}" if op.get("within") else "") + ".")
    if len(rows) > 1:
        raise EditError(f"{key!r} is in column {str(key_column).upper()} on rows {', '.join(map(str, rows))}; "
                        f"add `within` to pick one block, or use set_cells.")
    r = rows[0]
    for col, value in updates.items():
        if not re.fullmatch(r"[A-Za-z]{1,3}", str(col).strip()):
            raise EditError(f"`set` keys are column letters like B, not {col!r}.")
        write_cell(sh, r, col_num(str(col).strip()), value, op.get("like"))
    return [f"{sh.name}: row {r} ({str(key_column).upper()}={key}) set "
            f"{', '.join(f'{str(c).upper()}{r}' for c in updates)}"]


def _op_append_rows(book, op, n, sheet_for):
    rows = op.get("rows")
    if not isinstance(rows, list) or not rows or not all(isinstance(r, list) for r in rows):
        raise EditError("`rows` is a list of rows, each a list of values.")
    sh = sheet_for(op, n)
    c1, c2 = 1, 16384
    if op.get("columns"):
        _, c1, _, c2 = parse_range(op["columns"])
    last = 0
    for r, c, el in sh.iter_cells():
        if c1 <= c <= c2 and sh.value(el, raw=True) is not None:
            last = max(last, r)
    for i, row in enumerate(rows):
        r = last + 1 + i
        for j, value in enumerate(row):
            like = op.get("like") or (ref_of(last, c1 + j) if last and sh.cell(last, c1 + j) is not None else None)
            if value is None and not like:
                continue
            write_cell(sh, r, c1 + j, value, like)
    return [f"{sh.name}: added {len(rows)} row(s) at {last + 1}"]


def _count_arg(op: dict) -> int:
    try:
        count = int(op.get("count") or 1)
    except (TypeError, ValueError):
        raise EditError("`count` is a whole number.") from None
    if not 1 <= count <= 10_000:
        raise EditError("`count` is between 1 and 10,000.")
    return count


def _op_rows(book, op, n, sheet_for, insert: bool):
    sh = sheet_for(op, n)
    try:
        at = int(op.get("row"))
    except (TypeError, ValueError):
        raise EditError("Give `row`, the row number to insert at or delete from.") from None
    if not 1 <= at <= 1048576:
        raise EditError("`row` is between 1 and 1,048,576.")
    count = _count_arg(op)
    if insert and sh.bounds()[0] + count > 1048576:
        raise EditError("The sheet would run past Excel's last row.")
    return shift_sheet(sh, Shift(sh.name, "row", at, count if insert else -count),
                       str(op.get("copy_style") or "above"))


def _op_cols(book, op, n, sheet_for, insert: bool):
    sh = sheet_for(op, n)
    col = str(op.get("column") or "").strip()
    if not re.fullmatch(r"[A-Za-z]{1,3}", col):
        raise EditError("Give `column`, a letter like D.")
    count = _count_arg(op)
    if insert and sh.bounds()[1] + count > 16384:
        raise EditError("The sheet would run past Excel's last column.")
    return shift_sheet(sh, Shift(sh.name, "col", col_num(col), count if insert else -count))


_BAD_NAME = re.compile(r"[\[\]:*?/\\]")


def _check_name(book: Book, name, current: str | None = None) -> str:
    name = str(name or "").strip()
    if not name or len(name) > 31 or _BAD_NAME.search(name) or name.startswith("'") or name.endswith("'") \
            or name.casefold() == "history":
        raise EditError(f"{name!r} can't be a sheet name: 1-31 characters, none of [ ] : * ? / \\, "
                        f"not starting or ending with ', and not History.")
    if any(s.name.casefold() == name.casefold() and s.name != current for s in book.sheets):
        raise EditError(f"There's already a sheet called {name!r}.")
    return name


def _check_structure(book: Book) -> None:
    prot = book.wb.find(q("workbookProtection"))
    if prot is not None and prot.get("lockStructure") in ("1", "true"):
        raise EditError("The workbook's structure is protected, so sheets can't be added or renamed here.")


def _op_add_sheet(book, op, n, sheet_for):
    _check_structure(book)
    name = _check_name(book, op.get("name"))
    pkg = book.pkg
    k = 1
    while pkg.has(f"xl/worksheets/sheet{k}.xml"):
        k += 1
    path = f"xl/worksheets/sheet{k}.xml"
    root = etree.Element(q("worksheet"), nsmap={None: MAIN, "r": REL_NS})
    etree.SubElement(root, q("dimension"), ref="A1")
    etree.SubElement(root, q("sheetData"))
    pkg.add(path, root)
    rel_target = posixpath.relpath(path, posixpath.dirname(book.path))
    rid = pkg.add_rel(book.path, "worksheet", rel_target)
    pkg.add_content_type(path, SHEET_CT)
    sheets_el = book.wb.find(q("sheets"))
    sheet_id = max(int(s.get("sheetId") or 0) for s in sheets_el) + 1
    el = etree.SubElement(sheets_el, q("sheet"), name=name, sheetId=str(sheet_id))
    el.set(q("id", REL_NS), rid)
    pkg.touch(book.path)
    book.sheets.append(SheetInfo(name, path, el, len(book.sheets)))
    return [f"added sheet {name!r}"]


def _op_rename_sheet(book, op, n, sheet_for):
    _check_structure(book)
    info = book.sheet(op.get("sheet"))
    old = info.name
    new = _check_name(book, op.get("name"), current=old)
    if new == old:
        return []
    moved = _rewrite_all(book, rename=(old, new))
    info.element.set("name", new)
    info.name = new
    book.pkg.touch(book.path)
    if book.pkg.has("docProps/app.xml"):                          # the sheet list Windows shows in file properties
        app = book.pkg.xml("docProps/app.xml")
        for el in app.iter("{http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes}lpstr"):
            if el.text == old:
                el.text = new
                book.pkg.touch("docProps/app.xml")
    return [f"renamed sheet {old!r} to {new!r}" + (f" ({moved} formula(s) updated)" if moved else "")]


_OPS = {
    "set_cells": _op_set_cells,
    "set_range": _op_set_range,
    "clear_cells": _op_clear_cells,
    "set_by_key": _op_set_by_key,
    "append_rows": _op_append_rows,
    "insert_rows": lambda b, o, n, s: _op_rows(b, o, n, s, True),
    "delete_rows": lambda b, o, n, s: _op_rows(b, o, n, s, False),
    "insert_columns": lambda b, o, n, s: _op_cols(b, o, n, s, True),
    "delete_columns": lambda b, o, n, s: _op_cols(b, o, n, s, False),
    "add_sheet": _op_add_sheet,
    "rename_sheet": _op_rename_sheet,
}


def diff_text(diff: list[dict], notes: list[str]) -> str:
    """The diff as plain lines, for a proposal card: 'Sheet1!B2: "Vacant" → "Jane"'."""
    def show(v):
        if isinstance(v, dict):
            v = v.get("date", v.get("error", v))
        return "(empty)" if v is None else repr(v) if isinstance(v, str) else str(v)
    lines = [f"{d['sheet']}!{d['cell']}: {show(d['old'])} → {show(d['new'])}" for d in diff]
    return "\n".join(notes + lines)
