"""The Lumi Pet catalog editor (python/lumi_pet_editor.py): the parts behind its window. They list every entry,
check an edit with the server's own loaders before writing, and keep a backup. The window itself needs a person."""

import json
import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "python"))
import lumi_pet_editor as editor  # noqa: E402


@pytest.mark.parametrize("name, count", [("Snacks, treats and toys", 24), ("House", 446), ("Closet and Star Capsule", 129), ("Battles: enemies, skills, mods", 87), ("Dungeon environments", 12)])
def test_every_entry_of_every_catalog_is_listed_and_reachable(name, count):
    path, loader = editor.CATALOGS[name]
    data = json.loads(path.read_text(encoding="utf-8"))
    found = editor.entries(data)
    assert len(found) == count and len({tuple(p) for _, p in found}) == count
    assert all(isinstance(editor.get(data, p), dict) for _, p in found)
    assert editor.check(loader, data) is None


def test_a_bad_edit_is_explained_and_nothing_is_written(tmp_path):
    source, loader = editor.CATALOGS["House"]
    copy = tmp_path / "house.json"
    shutil.copyfile(source, copy)
    data = json.loads(copy.read_text(encoding="utf-8"))
    path = next(p for label, p in editor.entries(data) if label.endswith("Moon rug"))
    editor.put(data, path, {**editor.get(data, path), "price": -5})
    assert editor.save(copy, loader, data) == "The house's rug-moon needs a price."
    assert copy.read_bytes() == source.read_bytes() and not copy.with_suffix(".json.bak").exists()


def test_a_good_edit_is_saved_with_a_backup_and_still_loads(tmp_path):
    source, loader = editor.CATALOGS["Snacks, treats and toys"]
    copy = tmp_path / "items.json"
    shutil.copyfile(source, copy)
    data = json.loads(copy.read_text(encoding="utf-8"))
    path = next(p for label, p in editor.entries(data) if label.endswith("Cookie"))
    editor.put(data, path, {**editor.get(data, path), "price": 9})
    assert editor.save(copy, loader, data) is None
    assert copy.with_suffix(".json.bak").read_bytes() == source.read_bytes()
    assert next(s for s in loader(copy)["snacks"] if s["id"] == "cookie")["price"] == 9
