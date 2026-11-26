"""Demo (MN-21): edit a local copy of the phone sheet cell by cell with the same
code edit_spreadsheet uses, and prove the rest of the workbook is untouched.

    .venv\\Scripts\\python.exe python\\xlsx_phone_sheet_demo.py <phone.xlsx> <out folder>

Nothing here talks to the server: it reads the copy, writes edited copies to
<out folder> (for ps/Test-XlsxInExcel.ps1 to open in real Excel), and prints
what changed."""

from __future__ import annotations

import io
import json
import pathlib
import sys
import zipfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mnemerynne import editing_formats  # noqa: E402


def parts(data: bytes) -> dict[str, bytes]:
    """Every zip entry's uncompressed bytes."""
    z = zipfile.ZipFile(io.BytesIO(data))
    return {n: z.read(n) for n in z.namelist()}


def show(title: str, value) -> None:
    print(f"\n== {title}")
    print(json.dumps(value, indent=1, default=str, ensure_ascii=False)[:1500])


def main(src: pathlib.Path, out: pathlib.Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    original = src.read_bytes()

    view = editing_formats.xlsx_view(original, cells="E1:G3")     # what open_file shows now
    show("open_file: editable now?", {"editable": view["editable"], "sheets": view["sheets"],
                                      "merged": view.get("merged"), "cells": view["cells"]})
    show("open_file find=4916", editing_formats.xlsx_view(original, find=4916)["found"])

    steps = [   # (name, operations, what Excel should show afterwards)
        ("1-set-by-key", [{"op": "set_by_key", "key_column": "G", "key": 4916, "set": {"F": "Vacant"}}],
         {"Sheet1!F2": "Vacant"}),
        ("2-fix-typo", [{"op": "set_cells", "cells": {"E25": "Group Rooms Sales Manager"}}],
         {"Sheet1!E25": "Group Rooms Sales Manager"}),
        ("3-revised-date", [{"op": "set_cells", "cells": {"N43": "Revised on 11/26/2026"}}],
         {"Sheet1!N43": "Revised on 11/26/2026"}),
        ("4-insert-row", [{"op": "insert_rows", "row": 12}], {"Sheet1!A13": "Regional Director of Sales"}),
        ("5-delete-row", [{"op": "delete_rows", "row": 12}],              # undoes step 4
         {"Sheet1!A12": "Regional Director of Sales", "Sheet1!A13": "Regional Hotel Operations"}),
    ]
    current = original
    snapshots = {}                                               # every cell after each step
    for name, ops, expect in steps:
        result = editing_formats.xlsx_apply(current, ops)
        before, after = parts(current), parts(result.data)
        changed = sorted(n for n in before.keys() | after.keys() if before.get(n) != after.get(n))
        same = sorted(n for n in before if before.get(n) == after.get(n))
        show(f"{name}: changes / diff", {"changes": result.changes, "diff": result.diff})
        print(f"   parts rewritten: {changed}")
        print(f"   parts byte-identical: {len(same)} of {len(before)} "
              f"(logo image1.png identical: {'xl/media/image1.png' in same})")
        current = result.data
        snapshots[name] = editing_formats.xlsx_view(current, cells="A1:T43")["cells"]
        (out / f"{name}.xlsx").write_bytes(current)            # each step builds on the last
        (out / f"{name}.expect.json").write_text(json.dumps(expect), encoding="utf-8")
    print(f"\nInsert row 12 then delete it gives back the same cells: "
          f"{snapshots['5-delete-row'] == snapshots['3-revised-date']}")
    final = parts(current)
    untouched = [n for n, b in parts(original).items() if final.get(n) == b]
    print(f"\nAfter all steps, {len(untouched)} of {len(final)} parts are byte-identical to the original, "
          f"including the logo: {'xl/media/image1.png' in untouched}")


if __name__ == "__main__":
    main(pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2]))
