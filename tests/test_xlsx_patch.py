"""Cell-level .xlsx editing (mnemerynne/xlsx_patch.py) on a realistic workbook:
every operation, untouched parts staying byte-for-byte, references moving with
inserted/deleted rows and columns, refusals, and the MCP tool path (version
conflicts, proposals, permissions)."""

import io
import re
import zipfile

import openpyxl
import pytest
from lxml import etree

import xlsx_fixture
from mnemerynne import editing, xlsx_patch
from mnemerynne.editing import EditError
from test_editing import _add, _bytes, _caller, tools, world  # noqa: F401  (fixtures used by name)

MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
DIRECTORY, RATES = "xl/worksheets/sheet1.xml", "xl/worksheets/sheet2.xml"


@pytest.fixture(scope="module")
def book() -> bytes:
    return xlsx_fixture.build()


def run(data: bytes, *ops) -> xlsx_patch.Applied:
    out = xlsx_patch.apply(data, list(ops))
    openpyxl.load_workbook(io.BytesIO(out.data))                 # still a workbook openpyxl (and Excel) can read
    return out


def cells(data: bytes, sheet: str = "Directory", rng: str | None = None) -> dict:
    """All cells of a sheet as {ref: value} (formulas as their '=' text)."""
    view = xlsx_patch.view(data, sheet, rng)
    out = {}
    for row in view["cells"]:
        for ref, value in row.items():
            out[ref] = value["formula"] if isinstance(value, dict) and "formula" in value else value
    return out


def xml(data: bytes, part: str) -> etree._Element:
    return etree.fromstring(zipfile.ZipFile(io.BytesIO(data)).read(part))


def style_of(data: bytes, part: str, ref: str) -> str | None:
    el = xml(data, part).find(f".//{{{MAIN}}}c[@r='{ref}']")
    return None if el is None else el.get("s")


def changed_parts(before: bytes, after: bytes) -> set[str]:
    a, b = xlsx_fixture.parts(before), xlsx_fixture.parts(after)
    return {n for n in a.keys() | b.keys() if a.get(n) != b.get(n)}


# ------------------------------------------------------------------ reading
def test_view_shows_values_formulas_dates_merges_and_tables(book):
    view = xlsx_patch.view(book)
    assert [s["name"] for s in view["sheets"]] == ["Directory", "Rates"]
    assert view["merged"] == ["A1:C1", "E1:G1"] and view["editable"]
    got = cells(book)
    assert got["G3"] == 4916 and got["B3"] == "Pat Example" and got["C9"] == "=COUNT(C3:C6)"
    assert got["B8"] == {"date": "2026-10-30"}
    rates = xlsx_patch.view(book, "Rates")
    assert rates["tables"] == [{"name": "RateTable", "range": "A1:B4"}]
    e3 = rates["cells"][2]["E3"]                                   # a shared-formula follower, worked out
    assert e3 == {"formula": "=B3*2", "value": 238}


def test_find_locates_numbers_and_text_with_their_rows(book):
    hit = xlsx_patch.view(book, find=4916)
    assert hit["matches"] == 1 and hit["found"][0]["cell"] == "G3"
    assert hit["found"][0]["row"]["F3"] == "Vacant"
    assert xlsx_patch.view(book, find="4916")["found"][0]["cell"] == "G3"   # text query, number cell
    vacant = xlsx_patch.view(book, find="vacant")
    assert [f["cell"] for f in vacant["found"]] == ["F3"]
    everywhere = xlsx_patch.view(book, find="king")                # no sheet: all sheets
    assert everywhere["found"][0]["sheet"] == "Rates"
    boxed = xlsx_patch.view(book, sheet="Directory", cells="A1:C10", find="Payroll")
    assert boxed["matches"] == 0 and "Nothing matched" in boxed["note"]


# ------------------------------------------------------------------ cell edits
def test_set_cells_touches_only_the_sheet_and_strings(book):
    out = run(book, {"op": "set_cells", "cells": {"F3": "Taylor New", "C4": 4901}})
    assert changed_parts(book, out.data) == {DIRECTORY, "xl/sharedStrings.xml"}   # logo, chart, styles: same bytes
    got = cells(out.data)
    assert got["F3"] == "Taylor New" and got["C4"] == 4901 and got["G3"] == 4916
    assert style_of(out.data, DIRECTORY, "C4") == style_of(book, DIRECTORY, "C4")   # the cell kept its look
    assert out.diff == [{"sheet": "Directory", "cell": "F3", "old": "Vacant", "new": "Taylor New"},
                        {"sheet": "Directory", "cell": "C4", "old": 4900, "new": 4901}]


def test_existing_strings_are_reused(book):
    out = run(book, {"op": "set_cells", "cells": {"F6": "Vacant"}})
    sst = xml(out.data, "xl/sharedStrings.xml")
    assert [si[0].text for si in sst].count("Vacant") == 1


def test_value_forms(book):
    out = run(book, {"op": "set_cells", "cells": {
        "B6": {"text": "0123"}, "B8": {"date": "2026-11-26"}, "A6": None, "D3": True, "D4": 1.5,
        "D5": {"formula": "C5+1"}, "E6": {"date": "2026-11-26T08:30"}}})
    got = cells(out.data)
    assert got["B6"] == "0123" and got["B8"] == {"date": "2026-11-26"} and "A6" not in got
    assert got["D3"] is True and got["D4"] == 1.5 and got["D5"] == "=C5+1"
    assert got["E6"] == {"date": "2026-11-26T08:30:00"}           # a fresh cell got a date style


def test_formulas_drop_calc_chain_and_recalculate_on_open(book):
    out = run(book, {"op": "set_cells", "cells": {"C11": "=SUM(C3:C6)"}})
    names = zipfile.ZipFile(io.BytesIO(out.data)).namelist()
    assert "xl/calcChain.xml" not in names
    assert b"calcChain" not in xlsx_fixture.parts(out.data)["[Content_Types].xml"]
    assert b"calcChain" not in xlsx_fixture.parts(out.data)["xl/_rels/workbook.xml.rels"]
    assert openpyxl.load_workbook(io.BytesIO(out.data)).calculation.fullCalcOnLoad


def test_like_copies_a_style_onto_new_cells(book):
    out = run(book, {"op": "set_cells", "cells": {"C7": 4955}, "like": "C6"})
    assert style_of(out.data, DIRECTORY, "C7") == style_of(book, DIRECTORY, "C6") is not None


def test_merged_and_table_header_cells_are_refused(book):
    with pytest.raises(EditError, match="inside the merged cells A1:C1; write to A1"):
        xlsx_patch.apply(book, [{"op": "set_cells", "cells": {"B1": "x"}}])
    assert cells(run(book, {"op": "set_cells", "cells": {"A1": "CORPORATE OFFICE"}}).data)["A1"] == "CORPORATE OFFICE"
    with pytest.raises(EditError, match="table's header"):
        xlsx_patch.apply(book, [{"op": "set_cells", "sheet": "Rates", "cells": {"A1": "Type"}}])


def test_writing_a_shared_formula_leader_keeps_its_followers(book):
    out = run(book, {"op": "set_cells", "sheet": "Rates", "cells": {"E2": 0}})
    got = cells(out.data, "Rates")
    assert got["E2"] == 0 and got["E3"] == "=B3*2" and got["E4"] == "=B4*2"


def test_set_range_and_clear_cells(book):
    out = run(book, {"op": "set_range", "start": "E5", "values": [["Payroll Lead", "Jo P", 4931], ["Benefits", None, 4932]]},
              {"op": "clear_cells", "range": "A6:C6"})
    got = cells(out.data)
    assert (got["E5"], got["F5"], got["G5"], got["E6"], got["G6"]) == ("Payroll Lead", "Jo P", 4931, "Benefits", 4932)
    assert not {"A6", "B6", "C6"} & got.keys()
    assert style_of(out.data, DIRECTORY, "C6") == style_of(book, DIRECTORY, "C6")   # formatting stays


def test_set_by_key_finds_exactly_one_row(book):
    out = run(book, {"op": "set_by_key", "key_column": "G", "key": 4916, "set": {"F": "Taylor New"}})
    assert cells(out.data)["F3"] == "Taylor New"
    assert out.changes == ["Directory: row 3 (G=4916) set F3"]
    with pytest.raises(EditError, match="No row has 4999"):
        xlsx_patch.apply(book, [{"op": "set_by_key", "key_column": "G", "key": 4999, "set": {"F": "x"}}])
    dup = [{"op": "set_cells", "cells": {"C5": 4904}},
           {"op": "set_by_key", "key_column": "C", "key": 4904, "set": {"B": "Kim"}}]
    with pytest.raises(EditError, match="rows 3, 5; add `within`"):
        xlsx_patch.apply(book, dup)
    dup[1]["within"] = "A3:C4"
    assert cells(run(book, *dup).data)["B3"] == "Kim"


def test_append_rows_under_a_block(book):
    out = run(book, {"op": "append_rows", "columns": "E:F", "rows": [["Benefits", "Casey Mock"]]})
    got = cells(out.data)
    assert (got["E6"], got["F6"]) == ("Benefits", "Casey Mock") and got["G9"] == "=SUM(G3:G5)"
    out = run(book, {"op": "append_rows", "columns": "E:G", "rows": [["Benefits", "Casey Mock", 4935]]})
    assert cells(out.data)["G10"] == 4935                       # under G9's total, the block's last used row
    assert style_of(out.data, DIRECTORY, "G10") == style_of(book, DIRECTORY, "G9")


def test_operations_are_all_or_nothing(book):
    with pytest.raises(EditError, match="Operation 2"):
        xlsx_patch.apply(book, [{"op": "set_cells", "cells": {"F3": "x"}}, {"op": "explode"}])


# ------------------------------------------------------------------ rows and columns
def _names(data):
    wb = xml(data, "xl/workbook.xml")
    return {dn.get("name"): dn.text for dn in wb.iter(f"{{{MAIN}}}definedName")}


def test_insert_rows_moves_everything_below(book):
    out = run(book, {"op": "insert_rows", "row": 4, "count": 1})
    got = cells(out.data)
    assert got["C10"] == "=COUNT(C3:C7)" and got["G10"] == "=SUM(G3:G6)" and got["C11"] == "=Rates!B2+C3"
    assert got["B9"] == {"date": "2026-10-30"} and got["A5"] == "Operations" and "A4" not in got
    assert cells(out.data, "Rates")["D3"] == "=Directory!C3*2"   # above the insert: unchanged
    names = _names(out.data)
    assert names["Exts"] == "Directory!$C$3:$C$7" and names["_xlnm.Print_Area"].endswith("$A$1:$G$11")
    sheet = xml(out.data, DIRECTORY)
    q = lambda t: f"{{{MAIN}}}{t}"                               # noqa: E731
    assert sheet.find(f".//{q('dataValidation')}").get("sqref") == "C3:C7"
    assert sheet.find(f".//{q('conditionalFormatting')}").get("sqref") == "G3:G7"
    assert sheet.find(q("autoFilter")).get("ref") == "A2:C7"
    assert sheet.find(f".//{q('hyperlink')}").get("ref") == "B5"
    assert [m.get("ref") for m in sheet.iter(q("mergeCell"))] == ["A1:C1", "E1:G1"]
    assert style_of(out.data, DIRECTORY, "C4") == style_of(book, DIRECTORY, "C3")   # new row styled like row above
    assert "xl/media/image1.png" not in changed_parts(book, out.data)


def test_insert_rows_inside_the_frozen_area_and_above_the_logo(book):
    out = run(book, {"op": "insert_rows", "row": 2})
    pane = xml(out.data, DIRECTORY).find(f".//{{{MAIN}}}pane")
    assert pane.get("ySplit") == "3" and pane.get("topLeftCell") == "A4"
    xdr = "http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing"
    anchor_row = xml(out.data, "xl/drawings/drawing1.xml").find(f".//{{{xdr}}}from/{{{xdr}}}row")
    assert anchor_row.text == "2"                                  # the logo at I2 is now at I3
    comment = xml(out.data, "xl/comments/comment1.xml").find(f".//{{{MAIN}}}comment")
    assert comment.get("ref") == "B4"


def test_delete_rows_shrinks_ranges_and_breaks_refs_to_deleted_cells(book):
    out = run(book, {"op": "delete_rows", "row": 3})
    got = cells(out.data)
    assert got["C8"] == "=COUNT(C3:C5)" and got["A3"] == "Operations"
    assert cells(out.data, "Rates")["D3"] == "=Directory!#REF!*2"
    assert _names(out.data)["Exts"] == "Directory!$C$3:$C$5"
    assert xml(out.data, "xl/comments/comment1.xml").find(f".//{{{MAIN}}}comment") is None   # its cell went
    gone = {(d["cell"], d["old"]) for d in out.diff}
    assert ("G3", 4916) in gone and ("B3", "Pat Example") in gone


def test_delete_rows_in_a_table_and_its_chart(book):
    out = run(book, {"op": "delete_rows", "sheet": "Rates", "row": 3})
    got = cells(out.data, "Rates")
    assert got["D2"] == "=SUM(B2:B3)" and got["E3"] == "=B3*2" and got["A3"] == "Suite"
    assert xml(out.data, "xl/tables/table1.xml").get("ref") == "A1:B3"
    chart = xlsx_fixture.parts(out.data)["xl/charts/chart1.xml"].decode()
    assert "'Rates'!$B$2:$B$3" in chart and "'Rates'!$A$2:$A$3" in chart and "'Rates'!B1" in chart
    with pytest.raises(EditError, match="header row of table RateTable"):
        xlsx_patch.apply(book, [{"op": "delete_rows", "sheet": "Rates", "row": 1}])
    with pytest.raises(EditError, match="all of table RateTable"):
        xlsx_patch.apply(book, [{"op": "delete_rows", "sheet": "Rates", "row": 1, "count": 4}])


def test_insert_and_delete_columns(book):
    out = run(book, {"op": "insert_columns", "column": "D"})
    got = cells(out.data)
    assert got["H9"] == "=SUM(H3:H5)" and got["C10"] == "=Rates!B2+C3" and got["H3"] == 4916
    sheet = xml(out.data, DIRECTORY)
    assert [m.get("ref") for m in sheet.iter(f"{{{MAIN}}}mergeCell")] == ["A1:C1", "F1:H1"]
    widths = {c.get("min"): c.get("width") for c in sheet.iter(f"{{{MAIN}}}col")}
    assert widths.get("6") is not None                             # column E's width moved to F
    back = run(out.data, {"op": "delete_columns", "column": "D"})
    assert cells(back.data) == cells(book)
    with pytest.raises(EditError, match="Table RateTable spans those columns"):
        xlsx_patch.apply(book, [{"op": "insert_columns", "sheet": "Rates", "column": "B"}])


# ------------------------------------------------------------------ sheets
def test_rename_sheet_updates_formulas_and_names(book):
    out = run(book, {"op": "rename_sheet", "sheet": "Directory", "name": "Phone List"})
    assert cells(out.data, "Rates")["D3"] == "='Phone List'!C3*2"
    assert _names(out.data)["Exts"] == "'Phone List'!$C$3:$C$6"
    assert openpyxl.load_workbook(io.BytesIO(out.data)).sheetnames == ["Phone List", "Rates"]
    with pytest.raises(EditError, match="already a sheet"):
        xlsx_patch.apply(book, [{"op": "rename_sheet", "sheet": "Directory", "name": "rates"}])
    with pytest.raises(EditError, match="can't be a sheet name"):
        xlsx_patch.apply(book, [{"op": "add_sheet", "name": "a/b"}])


def test_add_sheet_then_fill_it(book):
    out = run(book, {"op": "add_sheet", "name": "Notes"},
              {"op": "set_cells", "sheet": "Notes", "cells": {"A1": "Checked", "B1": 4916}})
    wb = openpyxl.load_workbook(io.BytesIO(out.data))
    assert wb.sheetnames == ["Directory", "Rates", "Notes"] and wb["Notes"]["B1"].value == 4916


# ------------------------------------------------------------------ refusals
def _rezip(data: bytes, part: str, change) -> bytes:
    src = zipfile.ZipFile(io.BytesIO(data))
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for name in src.namelist():
            body = src.read(name)
            z.writestr(name, change(body) if name == part else body)
    return out.getvalue()


def test_protected_sheets_and_workbooks_are_refused(book):
    locked = _rezip(book, DIRECTORY, lambda b: b.replace(b"<pageMargins", b'<sheetProtection sheet="1"/><pageMargins', 1))
    assert not xlsx_patch.view(locked)["editable"]
    with pytest.raises(EditError, match="protected"):
        xlsx_patch.apply(locked, [{"op": "set_cells", "cells": {"F3": "x"}}])
    run(locked, {"op": "set_cells", "sheet": "Rates", "cells": {"B2": 130}})   # the other sheet is fine
    structure = _rezip(book, "xl/workbook.xml",
                       lambda b: re.sub(rb"(<workbook[^>]*>)", rb'\1<workbookProtection lockStructure="1"/>', b, count=1))
    with pytest.raises(EditError, match="structure is protected"):
        xlsx_patch.apply(structure, [{"op": "add_sheet", "name": "New"}])


def test_password_protected_and_broken_files_are_refused():
    with pytest.raises(EditError, match="password-protected"):
        xlsx_patch.view(b"\xd0\xcf\x11\xe0" + b"\0" * 100)
    with pytest.raises(EditError, match="valid .xlsx"):
        xlsx_patch.apply(b"not a zip", [{"op": "set_cells", "cells": {"A1": 1}}])


def test_bad_input_is_explained(book):
    for ops, message in (([{"op": "set_cells", "cells": {"B0": 1}}], "isn't a cell name"),
                         ([{"op": "set_cells", "cells": {"A1": float("nan")}}], "isn't a number"),
                         ([{"op": "set_cells", "cells": {"D3": "=SUM(("}}], "can't be read"),
                         ([{"op": "set_cells", "cells": {"D3": {"colour": "red"}}}], "value object"),
                         ([{"op": "set_cells", "sheet": "Nope", "cells": {"A1": 1}}], "no sheet 'Nope'"),
                         ([{"op": "insert_rows"}], "Give `row`"),
                         ([{"op": "delete_columns", "column": "4"}], "letter like D")):
        with pytest.raises(EditError, match=message):
            xlsx_patch.apply(book, ops)


# ------------------------------------------------------------------ through the MCP tools
def test_tool_path_diff_version_conflict_and_permissions(conn, settings, world, tools):  # noqa: F811
    alice = _caller(conn, world["alice"])
    agents = editing.agents_folder(conn, world["alice"])
    book = xlsx_fixture.build()
    file_id = _add(conn, settings, world["inbox"], "Phone list.xlsx", book, folder=agents)
    opened = tools.open_file(conn, alice, file_id, find=4916)[0]
    assert opened["can_edit"] and opened["found"][0]["cell"] == "G3"
    done = tools.edit_spreadsheet(conn, alice, file_id, opened["version"], [
        {"op": "set_by_key", "key_column": "G", "key": 4916, "set": {"F": "Taylor New"}}])[0]
    assert done["diff"] == [{"sheet": "Directory", "cell": "F3", "old": "Vacant", "new": "Taylor New"}]
    saved = _bytes(conn, settings, file_id)
    assert changed_parts(book, saved) == {DIRECTORY, "xl/sharedStrings.xml"}
    with pytest.raises(editing.EditConflict):                       # the old version is stale now
        tools.edit_spreadsheet(conn, alice, file_id, opened["version"], [{"op": "set_cells", "cells": {"F4": "x"}}])
    with pytest.raises(EditError):                                  # read-only connection
        tools.edit_spreadsheet(conn, _caller(conn, world["alice"], can_edit=False), file_id, done["version"],
                               [{"op": "set_cells", "cells": {"F4": "x"}}])
    assert _bytes(conn, settings, file_id) == saved


def test_erynne_proposes_with_a_cell_diff(conn, settings, world):  # noqa: F811
    from fakes import FakeEmbedder
    from mnemerynne import proposals, users
    from mnemerynne.mcp_server import Tools
    erynne = Tools(settings, lambda: FakeEmbedder(), mode="erynne")
    alice = _caller(conn, world["alice"])
    agents = editing.agents_folder(conn, world["alice"])
    book = xlsx_fixture.build()
    file_id = _add(conn, settings, world["inbox"], "Phone list.xlsx", book, folder=agents)
    version = erynne.open_file(conn, alice, file_id)[0]["version"]
    proposed = erynne.edit_spreadsheet(conn, alice, file_id, version,
                                       [{"op": "set_cells", "cells": {"F3": "Taylor New"}}])[0]
    assert proposed["status"] == "proposed" and _bytes(conn, settings, file_id) == book
    row = conn.execute("SELECT diff FROM agent_proposals WHERE id = ?", (proposed["proposal"],)).fetchone()
    assert "Directory!F3: 'Vacant' → 'Taylor New'" in row["diff"]
    applied = proposals.apply(conn, settings, users.get_user_by_id(conn, world["alice"]), proposed["proposal"])
    assert cells(_bytes(conn, settings, applied["file_id"]))["F3"] == "Taylor New"
