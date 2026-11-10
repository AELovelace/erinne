"""A small editor for Lumi Pet's catalogs: snacks and toys, the house, the closet, battles (enemies, skills, mods)
and the dungeon's environments. (The dungeon's dialogue is plain files of lines in lumipet_data/tales/: edit those in
any text editor. Its Sokoban levels are made by python/make_lumi_sokoban.py.)

Run it from the repository:   .venv/Scripts/python.exe python/lumi_pet_editor.py

Pick a catalog on the left, pick an entry, change its values in the box on the right, and press Save. Before anything
is written, the whole catalog is checked by the same code the server runs at start (mnemerynne/lumipet.py and
lumibattle.py), so a mistake is explained here instead of stopping the server later. The previous file is kept
beside it as <name>.json.bak. Restart Mnemerynne's web service and worker to pick up a saved change.

Adapted from the shape of MommyBot's python/game_editor_gui.py (a list, a form, validate, save). New art is not made
here: an object, accessory or enemy body also needs a drawing in web/static/games/lumi-pet/ (house.js, closet.js,
enemies.js), and the tests in tests/js/ say so if one is missing.
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mnemerynne import lumibattle, lumidungeon, lumipet  # noqa: E402  (needs the path above)

DATA = ROOT / "mnemerynne" / "lumipet_data"
# catalog name -> (its file, the server's own loader that checks it)
CATALOGS = {
    "Snacks, treats and toys": (DATA / "items.json", lumipet.load_catalog),
    "House": (DATA / "house.json", lumipet.load_house),
    "Closet and Star Capsule": (DATA / "closet.json", lumipet.load_closet),
    "Battles: enemies, skills, mods": (DATA / "battle.json", lumibattle.load_catalog),
    "Dungeon environments": (DATA / "dungeon.json", lumidungeon.load_catalog),
}


def entries(data: dict) -> list[tuple[str, list]]:
    """Every editable entry in a catalog as (label, path), where path is the keys and indexes that reach it.
    Lists of things with ids become one entry each; lists inside them (a spot's items, a tree's skills) too."""
    found: list[tuple[str, list]] = []

    def walk(value, path: list, label: str) -> None:
        if isinstance(value, dict) and "id" in value:
            found.append((f"{label} › {value.get('name', value['id'])}", path))
            for key, inner in value.items():
                if isinstance(inner, list) and inner and isinstance(inner[0], dict) and "id" in inner[0]:
                    for index, child in enumerate(inner):
                        walk(child, path + [key, index], f"{label} › {value.get('name', value['id'])}")
        elif isinstance(value, dict):
            if value and all(not isinstance(inner, (dict, list)) or isinstance(inner, dict) and "id" not in inner and not any(isinstance(v, (dict, list)) for v in inner.values())
                             for inner in value.values()) and path:
                found.append((label, path))                # a plain settings block, like the capsule's price and odds
                return
            for key, inner in value.items():
                walk(inner, path + [key], key.capitalize())
        elif isinstance(value, list):
            for index, child in enumerate(value):
                walk(child, path + [index], label)

    walk(data, [], "")
    return found


def get(data, path: list):
    for step in path:
        data = data[step]
    return data


def put(data, path: list, value) -> None:
    """Replace the entry at `path` (never the whole catalog)."""
    get(data, path[:-1])[path[-1]] = value


def check(loader, data: dict) -> str | None:
    """Run the server's own check on a catalog that hasn't been saved yet. None: it's fine. Otherwise, what's wrong."""
    with tempfile.TemporaryDirectory() as folder:
        trial = Path(folder) / "trial.json"
        trial.write_text(json.dumps(data), encoding="utf-8")
        try:
            loader(trial)
        except (ValueError, KeyError, TypeError) as problem:   # the loaders explain themselves; a missing key is named
            return str(problem) or type(problem).__name__
    return None


def save(path: Path, loader, data: dict) -> str | None:
    """Check, keep a backup, then write. Returns what's wrong, or None when saved."""
    problem = check(loader, data)
    if problem:
        return problem
    shutil.copyfile(path, path.with_suffix(".json.bak"))
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return None


def main() -> None:
    """The window: catalogs and entries on the left, the chosen entry's values on the right."""
    import tkinter as tk
    from tkinter import messagebox, ttk

    window = tk.Tk()
    window.title("Lumi Pet catalog editor")
    window.geometry("980x640")
    state: dict = {"name": None, "data": None, "list": [], "path": None}

    left = ttk.Frame(window, padding=8)
    left.pack(side="left", fill="y")
    chooser = ttk.Combobox(left, values=list(CATALOGS), state="readonly", width=34)
    chooser.pack(fill="x")
    search = tk.StringVar()
    ttk.Entry(left, textvariable=search).pack(fill="x", pady=6)     # type to filter the entries
    listing = tk.Listbox(left, width=44, exportselection=False)
    listing.pack(fill="both", expand=True)

    right = ttk.Frame(window, padding=8)
    right.pack(side="left", fill="both", expand=True)
    title = ttk.Label(right, text="Choose a catalog, then an entry.", font=("", 11, "bold"))
    title.pack(anchor="w")
    box = tk.Text(right, wrap="none", font=("Consolas", 11), undo=True)
    box.pack(fill="both", expand=True, pady=6)
    status = ttk.Label(right, text="", foreground="#a33", wraplength=560, justify="left")
    status.pack(anchor="w")

    def fill(*_ignored) -> None:
        """Show the entries that match the search box."""
        wanted = search.get().lower()
        state["list"] = [entry for entry in entries(state["data"]) if wanted in entry[0].lower()] if state["data"] else []
        listing.delete(0, "end")
        for label, _path in state["list"]:
            listing.insert("end", label.lstrip(" ›"))

    def open_catalog(_event=None) -> None:
        state.update(name=chooser.get(), path=None)
        state["data"] = json.loads(CATALOGS[state["name"]][0].read_text(encoding="utf-8"))
        box.delete("1.0", "end")
        title.config(text="Choose an entry.")
        status.config(text="")
        fill()

    def open_entry(_event=None) -> None:
        if not listing.curselection():
            return
        label, path = state["list"][listing.curselection()[0]]
        state["path"] = path
        value = get(state["data"], path)
        if isinstance(value, dict):                       # nested lists are their own entries: edit them there
            value = {k: v for k, v in value.items() if not (isinstance(v, list) and v and isinstance(v[0], dict) and "id" in v[0])}
        title.config(text=label.lstrip(" ›"))
        box.delete("1.0", "end")
        box.insert("1.0", json.dumps(value, indent=2, ensure_ascii=False))
        status.config(text="")

    def save_entry() -> None:
        if state["path"] is None:
            return
        try:
            edited = json.loads(box.get("1.0", "end"))
        except json.JSONDecodeError as problem:
            status.config(text=f"That isn't valid JSON yet: {problem}")
            return
        old = get(state["data"], state["path"])
        if isinstance(old, dict) and isinstance(edited, dict):      # keep the nested lists that weren't shown
            edited = {**{k: v for k, v in old.items() if k not in edited and isinstance(v, list)}, **edited}
        trial = json.loads(json.dumps(state["data"]))
        put(trial, state["path"], edited)
        path, loader = CATALOGS[state["name"]]
        problem = save(path, loader, trial)
        if problem:
            status.config(text=f"Not saved: {problem}")
            return
        state["data"] = trial
        status.config(text="")
        fill()
        messagebox.showinfo("Saved", f"Saved {path.name}. Restart the web service and the worker to use it.\nThe previous version is {path.with_suffix('.json.bak').name}.")

    chooser.bind("<<ComboboxSelected>>", open_catalog)
    listing.bind("<<ListboxSelect>>", open_entry)
    search.trace_add("write", fill)
    ttk.Button(right, text="Save this entry", command=save_entry).pack(anchor="e")
    window.mainloop()


if __name__ == "__main__":
    main()
