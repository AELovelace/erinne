"""A realistic workbook for the spreadsheet-editing tests, shaped like the hotel's
phone list: side-by-side blocks with merged section titles, styles, widths,
frozen panes, a filter, data validation, conditional formatting, a comment,
a logo picture, a hyperlink, a table, a chart, cross-sheet formulas, shared
formulas, defined names and a calcChain. Every name in it is made up.

openpyxl builds most of it; then the XML is adjusted to look like a file
Excel saved: strings moved into sharedStrings.xml, a shared formula group,
and a calcChain part (none of which openpyxl writes)."""

from __future__ import annotations

import io
import re
import zipfile

import openpyxl
from lxml import etree
from openpyxl.chart import BarChart, Reference
from openpyxl.comments import Comment
from openpyxl.drawing.image import Image
from openpyxl.formatting.rule import CellIsRule
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.worksheet.datavalidation import DataValidation
from openpyxl.worksheet.table import Table

MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"


def _logo_png() -> bytes:
    from PIL import Image as PILImage
    out = io.BytesIO()
    PILImage.new("RGB", (40, 20), (90, 30, 120)).save(out, format="PNG")   # a purple "logo"
    return out.getvalue()


def build() -> bytes:
    """The fixture workbook's bytes."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Directory"
    title = Font(bold=True, color="FFFFFF")
    band = PatternFill("solid", fgColor="4B2C6F")
    # block 1: A-C (role, name, ext); block 2: E-G
    ws["A1"], ws["E1"] = "CORPORATE", "HUMAN RESOURCES"
    ws.merge_cells("A1:C1")
    ws.merge_cells("E1:G1")
    for ref in ("A1", "E1"):
        ws[ref].font, ws[ref].fill = title, band
        ws[ref].alignment = Alignment(horizontal="center")
    ws.append([])  # row 2 left empty on purpose
    rows = [("Corporate Office", "Pat Example", 4904, None, "HR Manager", "Vacant", 4916),
            ("Operations", "Sam Sample", 4900, None, "HR Admin", "Robin Test", 4908),
            ("Office Manager", "Alex Demo", 4922, None, "Payroll", "Jo Placeholder", 4930),
            ("Accountant", "Lee Fixture", 4923, None, None, None, None)]
    for i, row in enumerate(rows, start=3):
        for j, value in enumerate(row, start=1):
            if value is not None:
                ws.cell(i, j, value)
        ws.cell(i, 3).number_format = "0"              # extension column style (custom-looking)
        ws.cell(i, 3).font = Font(color="1F4E79")
    ws["A8"] = "Revised on"
    ws["B8"] = openpyxl.utils.datetime.from_excel(46325)   # a date cell (2026-10-29)
    ws["B8"].number_format = "mm/dd/yyyy"
    ws["A9"] = "Extensions"
    ws["C9"] = "=COUNT(C3:C6)"
    ws["G9"] = "=SUM(G3:G5)"
    ws["A10"] = "Rate check"
    ws["C10"] = "=Rates!B2+C3"
    ws.column_dimensions["A"].width = 28
    ws.column_dimensions["B"].width = 22
    ws.column_dimensions["E"].width = 24
    ws.freeze_panes = "A3"
    ws.auto_filter.ref = "A2:C6"
    dv = DataValidation(type="whole", operator="between", formula1="4000", formula2="4999")
    dv.add("C3:C6")
    ws.add_data_validation(dv)
    ws.conditional_formatting.add("G3:G6", CellIsRule(operator="greaterThan", formula=["4910"],
                                                       fill=PatternFill("solid", fgColor="FFC7CE")))
    ws["B3"].comment = Comment("Covers the front office too", "Lumi")
    ws["B4"].hyperlink = "https://example.com/directory"
    logo = Image(io.BytesIO(_logo_png()))
    ws.add_image(logo, "I2")
    ws.print_area = "A1:G10"

    rates = wb.create_sheet("Rates")
    rates.append(["Room", "Rate"])
    rates.append(["King", 129])
    rates.append(["Queen", 119])
    rates.append(["Suite", 249])
    table = Table(displayName="RateTable", ref="A1:B4")
    rates.add_table(table)
    rates["D1"] = "Total"
    rates["D2"] = "=SUM(B2:B4)"
    rates["D3"] = "=Directory!C3*2"
    rates["E2"], rates["E3"], rates["E4"] = "=B2*2", "=B3*2", "=B4*2"   # turned into a shared group below
    chart = BarChart()
    chart.add_data(Reference(rates, min_col=2, min_row=1, max_row=4), titles_from_data=True)
    chart.set_categories(Reference(rates, min_col=1, min_row=2, max_row=4))
    rates.add_chart(chart, "G2")

    from openpyxl.workbook.defined_name import DefinedName
    wb.defined_names["Exts"] = DefinedName("Exts", attr_text="Directory!$C$3:$C$6")

    out = io.BytesIO()
    wb.save(out)
    return _excelify(out.getvalue())


def _excelify(data: bytes) -> bytes:
    """Make openpyxl's output look like Excel's: shared strings, shared formulas, calcChain."""
    src = zipfile.ZipFile(io.BytesIO(data))
    parts = {n: src.read(n) for n in src.namelist()}
    strings: list[str] = []
    sheet_names = sorted(n for n in parts if re.match(r"xl/worksheets/sheet\d+\.xml$", n))
    for name in sheet_names:
        root = etree.fromstring(parts[name])
        for c in root.iter(f"{{{MAIN}}}c"):
            if c.get("t") == "inlineStr":
                text = "".join(c.itertext())
                for child in list(c):
                    c.remove(child)
                if text not in strings:
                    strings.append(text)
                c.set("t", "s")
                etree.SubElement(c, f"{{{MAIN}}}v").text = str(strings.index(text))
            for v in c.findall(f"{{{MAIN}}}v"):
                if not v.text and c.find(f"{{{MAIN}}}f") is not None:
                    c.remove(v)                                      # Excel leaves no empty cached value
        parts[name] = etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)
    sst = etree.Element(f"{{{MAIN}}}sst", nsmap={None: MAIN}, count=str(len(strings)),
                        uniqueCount=str(len(strings)))
    for s in strings:
        etree.SubElement(etree.SubElement(sst, f"{{{MAIN}}}si"), f"{{{MAIN}}}t").text = s
    parts["xl/sharedStrings.xml"] = etree.tostring(sst, xml_declaration=True, encoding="UTF-8", standalone=True)

    rates = parts["xl/worksheets/sheet2.xml"].decode()          # E2:E4 as one shared formula group
    rates = rates.replace('<c r="E2"><f>B2*2</f></c>', '<c r="E2"><f t="shared" ref="E2:E4" si="0">B2*2</f><v>258</v></c>')
    rates = rates.replace('<c r="E3"><f>B3*2</f></c>', '<c r="E3"><f t="shared" si="0"/><v>238</v></c>')
    rates = rates.replace('<c r="E4"><f>B4*2</f></c>', '<c r="E4"><f t="shared" si="0"/><v>498</v></c>')
    assert 'si="0"/>' in rates, "the fixture's shared formulas weren't placed"
    parts["xl/worksheets/sheet2.xml"] = rates.encode()
    parts["xl/calcChain.xml"] = (b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                                 b'<calcChain xmlns="' + MAIN.encode() + b'"><c r="C9" i="1"/><c r="D2" i="2"/></calcChain>')
    rels = parts["xl/_rels/workbook.xml.rels"].decode()
    rels = rels.replace("</Relationships>",
                        '<Relationship Id="rId90" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
                        'relationships/sharedStrings" Target="sharedStrings.xml"/>'
                        '<Relationship Id="rId91" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
                        'relationships/calcChain" Target="calcChain.xml"/></Relationships>')
    parts["xl/_rels/workbook.xml.rels"] = rels.encode()
    ct = parts["[Content_Types].xml"].decode()
    ct = ct.replace("</Types>",
                    '<Override PartName="/xl/sharedStrings.xml" ContentType="application/vnd.openxmlformats-'
                    'officedocument.spreadsheetml.sharedStrings+xml"/>'
                    '<Override PartName="/xl/calcChain.xml" ContentType="application/vnd.openxmlformats-'
                    'officedocument.spreadsheetml.calcChain+xml"/></Types>')
    parts["[Content_Types].xml"] = ct.encode()
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for name, body in parts.items():
            z.writestr(name, body)
    return out.getvalue()


def parts(data: bytes) -> dict[str, bytes]:
    """Every zip entry's uncompressed bytes."""
    z = zipfile.ZipFile(io.BytesIO(data))
    return {n: z.read(n) for n in z.namelist()}
