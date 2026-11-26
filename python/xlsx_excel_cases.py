"""Write one edited copy of the test workbook per spreadsheet operation, for
ps/Test-XlsxInExcel.ps1 to open in real Excel (does Excel open it without a
"We found a problem with some content" repair?).

    .venv\\Scripts\\python.exe python\\xlsx_excel_cases.py <out folder>

Each case is <name>.xlsx plus <name>.expect.json: {"Sheet!A1": value, ...}
that the PowerShell checker compares with what Excel reads."""

from __future__ import annotations

import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "tests")]                  # mnemerynne + the test fixture builder

import xlsx_fixture  # noqa: E402
from mnemerynne import xlsx_patch  # noqa: E402

CASES = {   # name: (operations, cells Excel should show afterwards)
    "00-untouched": ([], {"Directory!G3": 4916}),
    "01-set-cells": ([{"op": "set_cells", "cells": {"F3": "Taylor New", "C4": 4901, "B8": {"date": "2026-11-26"}}}],
                     {"Directory!F3": "Taylor New", "Directory!C4": 4901}),
    "02-set-by-key": ([{"op": "set_by_key", "key_column": "G", "key": 4916, "set": {"F": "Casey Mock"}}],
                      {"Directory!F3": "Casey Mock"}),
    "03-formula": ([{"op": "set_cells", "cells": {"C11": "=SUM(C3:C6)"}}], {"Directory!C11": 19649}),
    "04-insert-rows": ([{"op": "insert_rows", "row": 4, "count": 2}],
                       {"Directory!C11": 4, "Directory!A6": "Operations", "Rates!D3": 9808}),
    "05-delete-rows": ([{"op": "delete_rows", "row": 4}], {"Directory!A4": "Office Manager", "Directory!C8": 3}),
    "06-delete-table-row": ([{"op": "delete_rows", "sheet": "Rates", "row": 3}], {"Rates!D2": 378}),
    "07-insert-columns": ([{"op": "insert_columns", "column": "D"}], {"Directory!H3": 4916, "Directory!H9": 14754}),
    "08-delete-columns": ([{"op": "delete_columns", "column": "D"}], {"Directory!F3": 4916}),
    "09-rename-sheet": ([{"op": "rename_sheet", "sheet": "Directory", "name": "Phone List"}],
                        {"Rates!D3": 9808, "Phone List!G3": 4916}),
    "10-add-sheet": ([{"op": "add_sheet", "name": "Notes"},
                      {"op": "set_cells", "sheet": "Notes", "cells": {"A1": "Checked", "B1": "=Directory!G3"}}],
                     {"Notes!B1": 4916}),
    "11-append-and-clear": ([{"op": "append_rows", "columns": "E:F", "rows": [["Benefits", "Casey Mock"]]},
                             {"op": "clear_cells", "range": "A6:C6"}], {"Directory!F6": "Casey Mock"}),
}


def main(out: pathlib.Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    book = xlsx_fixture.build()
    for name, (ops, expect) in CASES.items():
        data = xlsx_patch.apply(book, ops).data if ops else book   # each case starts from the fixture
        (out / f"{name}.xlsx").write_bytes(data)
        (out / f"{name}.expect.json").write_text(json.dumps(expect), encoding="utf-8")
        print(f"wrote {name}.xlsx")


if __name__ == "__main__":
    main(pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "xlsx-excel-cases"))
