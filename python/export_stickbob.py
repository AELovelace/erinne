"""Turn Doll's GameMaker project (StickBob) into the files the web game reads (static/games/stickbob-runner/assets/).

Run it again whenever the GameMaker art or runner chunks change:

    .venv/Scripts/python.exe python/export_stickbob.py [path to the GameMaker project]

It only reads the GameMaker project. It writes:
    atlas.png    every sprite the runner draws, packed into one image
    bg-*.png     the four scrolling background layers from rm_Runner
    sfx-*        the jump, slide, running and gun sounds
    world.json   sprite sizes and hitboxes, background layout, and all the level chunks with their difficulty
"""

from __future__ import annotations

import json
import re
import shutil
import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]                                        # the erinne repo
OUT = ROOT / "mnemerynne" / "web" / "static" / "games" / "stickbob-runner" / "assets"   # where the web game looks
DEFAULT_PROJECT = Path(r"C:\Users\langley\GameMakerProjects\betterAI")            # Doll's GameMaker project

ROOM_HEIGHT = 768                                                                 # rm_Runner's height; art below it is never seen
FLOOR, TILE = 352, 16                                                             # the two constants scr_runner_slices.gml uses

# Every sprite the runner draws that isn't reached through a level object.
CHARACTER_SPRITES = ("sprPlayerIdle", "sprPlayerRun", "sprPlayerRunLeft", "sprPlayerSkid", "sprPlayerSkidLeft", "sprPlayerSlide",
                     "sprPlayerSlideLeft", "sprPlayerCrawl", "sprPlayerCrawlLeft", "sprPlayerFalling", "sprPlayerFallingLeft",
                     "sprPlayerDie", "sprPlayerKick", "sprPlayerKnife", "sprPlayerGun", "sprPlayerNoArm", "sprPlayerhit",
                     "sprPlayerPuddle", "sprMG", "sprBullet", "spr_Wall_1")

BOTS = {"objSlopBot": "patrol", "objSlopBotSlacker": "slacker", "objSlopBotGunner": "gunner"}   # enemy object -> behaviour in rules.js
BOUNCERS = {"objBouncyBottom": "up", "objBouncyTop": "down", "objBouncyLeft": "right", "objBouncyRight": "left"}   # pad -> push direction
SUPPORTS_EXCLUDED = {"obj_Wall"}                                                  # solid, but obj_RunnerDirector doesn't count walls as floor
ENEMY_WEIGHT_CAP = 3                                                              # same limits as _calculate_slice_difficulty
PARALLAX = {"bgDistantGround": 0.2, "bgClouds": 0.3, "bgNearGround": 0.1, "bgGroundPath": 0}   # from parallaxDefinition() in PlayerCharacter.gml
SOUNDS = {"jump": "jump/jump.mp3", "slide": "slide/slide.mp3", "running": "running/running.mp3", "gun": "wob_wob_2/wob_wob_2.wav"}   # web name -> file


# The runner's flow fixes (Doll, 2026-11-10: "a smoother running experience") are made in the GameMaker project itself:
# scr_runner_slices.gml for the hand-made slices and datafiles/infiniteRunner/world1_chunks.json for the cut-out rooms.
# This only reads them. tests/js/stickbob_runner.test.mjs checks every chunk can still be run through at full speed.


def read_yy(path: Path) -> dict:
    """A GameMaker .yy file: JSON, except that it allows trailing commas."""
    return json.loads(re.sub(r",(\s*[}\]])", r"\1", path.read_text(encoding="utf-8")))


def load_sprite(project: Path, name: str) -> dict:
    """One sprite's frames (in animation order) and the numbers the game needs from its .yy file."""
    folder = project / "sprites" / name
    info = read_yy(folder / f"{name}.yy")
    frames = [Image.open(folder / f"{frame['name']}.png").convert("RGBA") for frame in info["frames"]]     # each frame is a flattened PNG
    sequence = info["sequence"]
    ox, oy = sequence["xorigin"], sequence["yorigin"]                             # the point GameMaker places at x, y
    sprite = {"frames": frames, "w": info["width"], "h": info["height"], "ox": ox, "oy": oy, "fps": sequence["playbackSpeed"],
              "box": [info["bbox_left"] - ox, info["bbox_top"] - oy, info["bbox_right"] - ox + 1, info["bbox_bottom"] - oy + 1]}   # hitbox around the origin; right and bottom are exclusive
    if info["collisionKind"] == 0:                                                # a precise mask: a slope, so record each column's surface
        alpha = frames[0].getchannel("A")
        sprite["tops"] = [next((y for y in range(sprite["h"]) if alpha.getpixel((x, y)) > 0), sprite["h"]) for x in range(sprite["w"])]
    return sprite


def object_info(project: Path, name: str) -> dict:
    """Which sprite a GameMaker object draws, and whether it inherits from objSolid."""
    info = read_yy(project / "objects" / name / f"{name}.yy")
    parent = (info.get("parentObjectId") or {}).get("name")                       # objSolid's children are all solid ground
    return {"sprite": info["spriteId"]["name"], "solid": name == "objSolid" or parent == "objSolid"}


def authored_slices(project: Path) -> list[dict]:
    """The sixteen hand-made slices in scr_runner_slices.gml, read by turning that one array into JSON."""
    text = (project / "scripts" / "scr_runner_slices" / "scr_runner_slices.gml").read_text(encoding="utf-8")
    body = text[text.index("var _slices = [") + len("var _slices = "):text.index("];", text.index("var _slices = [")) + 1]   # just the array
    body = re.sub(r"//[^\n]*", "", body)                                          # comments out
    body = body.replace("FLOOR", str(FLOOR)).replace("TILE", str(TILE))           # constants in
    body = re.sub(r"runner_prefab\(\s*(\w+)\s*,\s*([^,]+),\s*([^)]+)\)",          # prefab calls become plain element objects
                  r'{object_name:"\1", dx:\2, dy:\3, scale_x:1, scale_y:1, rotation:0}', body)
    while re.search(r"\d+\s*-\s*\d+", body):                                      # work out sums like 352 - 96 - 16, left to right
        body = re.sub(r"(\d+)\s*-\s*(\d+)", lambda m: str(int(m[1]) - int(m[2])), body, count=1)
    body = re.sub(r"(\b[a-z_]+)\s*:", r'"\1":', body)                             # quote the keys
    return json.loads(re.sub(r",(\s*[}\]])", r"\1", body))                        # and drop trailing commas


def edge_y(slice_: dict, right_side: bool, objects: dict) -> int:
    """_estimate_edge_y: the lowest walkable surface near one end of a slice, or -1 if there is none."""
    width = slice_["width"]
    start, end = (width - 32, width) if right_side else (0, 32)                   # EDGE_WINDOW is 32
    surfaces = [p["dy"] for p in slice_.get("platforms", []) if p["dx"] + p["w"] > start and p["dx"] < end]
    for e in slice_.get("elements", []):
        name = e["object_name"]
        supports = name == "objBouncyBottom" or (objects[name]["solid"] and name not in SUPPORTS_EXCLUDED)
        if supports and e["dx"] + max(16, 16 * abs(e["scale_x"])) > start and e["dx"] < end:
            surfaces.append(e["dy"])
    return max(surfaces) if surfaces else -1


def column_top(piece: dict, col: int, sprites: dict) -> float | None:
    """Where a solid's surface is in one column (a slope's comes from its mask), or None if it doesn't cover that column."""
    if not piece["x"] <= col < piece["x"] + piece["w"]:
        return None
    tops = sprites[piece["sprite"]].get("tops")
    if tops is None:
        return piece["y"]                                                         # a plain block: flat on top
    top = tops[int((col - piece["x"]) // (piece["w"] / len(tops)))]
    height = sprites[piece["sprite"]]["h"]
    return None if top >= height else piece["y"] + top * piece["h"] / height       # an empty mask column has no surface


def standable_edge_y(out: dict, right_side: bool, sprites: dict) -> int:
    """Where a runner actually lands near one end of a converted slice: the lowest surface there with nothing solid in the
    29 px above it, which is how tall StickBob is. GameMaker's _estimate_edge_y took the lowest surface of any kind, so a
    floor buried under a block of terrain counted, and the chunk picker thought a wall was a flat join. -1: no floor there."""
    def blocked(col: int, y: float) -> bool:                                      # is this point inside any solid?
        for p in out["solids"]:
            top = column_top(p, col, sprites)
            if top is not None and top <= y < p["y"] + p["h"]:
                return True
        return False
    found = []
    for col in range(out["w"] - 32, out["w"], 4) if right_side else range(0, 32, 4):   # the same 32 px window GameMaker used
        for p in out["solids"] + [b for b in out["bouncers"] if b["dir"] == "up"]:
            if p.get("wall"):
                continue                                                          # walls aren't floor, as in GameMaker
            top = column_top(p, col, sprites)
            if top is not None and not any(blocked(col, top - lift) for lift in (1, 10, 20, 28)):
                found.append(top)
    return int(max(found)) if found else -1


def profile(slice_: dict, index: int, objects: dict, out: dict | None = None, sprites: dict | None = None) -> dict:
    """_build_slice_profile: where a slice starts and ends, how hard it is, and how often it should come up."""
    if out is None:
        entry, exit_ = edge_y(slice_, False, objects), edge_y(slice_, True, objects)
    else:                                                                         # the web game's join check: see standable_edge_y
        entry, exit_ = standable_edge_y(out, False, sprites), standable_edge_y(out, True, sprites)
    elements = [e["object_name"] for e in slice_.get("elements", [])]
    slopes = sum(1 for name in elements if name.startswith("objSloped"))          # every slope object is named objSloped...
    difficulty = 1 + min(2, len(slice_.get("hazards", []))) + min(ENEMY_WEIGHT_CAP, sum(1 for name in elements if name in BOTS))
    difficulty += 1 if slopes >= 4 else 0
    if entry >= 0 and exit_ >= 0:                                                 # big height changes make a slice harder
        difficulty += (abs(exit_ - entry) > 64) + (abs(exit_ - entry) > 128)
    difficulty = max(1, min(8, difficulty))
    room = slice_.get("source_room")
    if room:
        base = {"rm_GameRoom": 8, "SP1": 7, "SP2": 5, "SP3": 3.5, "SP4": 2.5}.get(room, 5)   # later levels come up less often
    else:
        base = 10 if index <= 3 else 8 if index <= 9 else 6
    return {"entry": entry, "exit": exit_, "difficulty": difficulty, "weight": max(0.25, base - max(0, difficulty - 3) * 0.8)}


def convert_slice(slice_: dict, index: int, objects: dict, sprites: dict) -> dict:
    """One slice in the shape rules.js reads, with every object already sorted into what it does."""
    source = f"{slice_['source_room']}@{slice_.get('source_start', 0):g}" if slice_.get("source_room") else f"authored #{index}"
    out = {"w": slice_["width"], "source": source, "solids": [], "hazards": [[h["dx"], h["dy"]] for h in slice_.get("hazards", [])],
           "bots": [], "bouncers": []}
    for p in slice_.get("platforms", []):                                         # a platform is one stretched objSolid
        out["solids"].append({"x": p["dx"], "y": p["dy"], "w": p["w"], "h": TILE, "sprite": objects["objSolid"]["sprite"]})
    for e in slice_.get("elements", []):
        name, x, y = e["object_name"], e["dx"], e["dy"]
        if name in BOTS:
            out["bots"].append([BOTS[name], x, y])
        else:
            sprite = sprites[objects[name]["sprite"]]                             # its size comes from the sprite, stretched by its scale
            w, h = sprite["w"] * e["scale_x"], sprite["h"] * e["scale_y"]
            if name in BOUNCERS:
                out["bouncers"].append({"dir": BOUNCERS[name], "x": x, "y": y, "w": w, "h": h, "sprite": objects[name]["sprite"]})
            elif objects[name]["solid"]:
                solid = {"x": x, "y": y, "w": w, "h": h, "sprite": objects[name]["sprite"]}
                if name == "obj_Wall":
                    solid["wall"] = True                                          # only these can be wall-jumped off
                if name == "objSolidInvisible":
                    solid["hidden"] = True                                        # collides, but the room never shows it
                out["solids"].append(solid)
    out["profile"] = profile(slice_, index, objects, out, sprites)                # needs the real shapes, so it comes last
    return out


def pack(sprites: dict) -> tuple[Image.Image, dict]:
    """Pack every frame into one image, in rows, and note where each frame landed."""
    width, x, y, row = 1024, 0, 0, 0
    places: dict[str, list] = {}
    for name in sorted(sprites, key=lambda n: -sprites[n]["h"]):                  # tall sprites first keeps the rows tidy
        s = sprites[name]
        places[name] = []
        for _ in s["frames"]:
            if x + s["w"] > width:                                                # row full: start the next one
                x, y, row = 0, y + row + 1, 0
            places[name].append([x, y])
            x += s["w"] + 1                                                       # a pixel of air so frames can't bleed together
            row = max(row, s["h"])
    atlas = Image.new("RGBA", (width, y + row + 1), (0, 0, 0, 0))
    for name, spots in places.items():
        for frame, spot in zip(sprites[name]["frames"], spots):
            atlas.paste(frame, tuple(spot))
    return atlas, places


def export(project: Path) -> None:
    """Read the project, then write everything into OUT."""
    OUT.mkdir(parents=True, exist_ok=True)
    chunk_file = project / "datafiles" / "infiniteRunner" / "world1_chunks.json"
    slices = authored_slices(project) + json.loads(chunk_file.read_text(encoding="utf-8-sig"))["chunks"]   # same order as runner_get_slices()

    names = {e["object_name"] for s in slices for e in s.get("elements", [])} | {"objSolid"}     # every object the levels place
    objects = {name: object_info(project, name) for name in names}
    sprites = {name: load_sprite(project, name) for name in {o["sprite"] for n, o in objects.items() if n not in BOTS} | set(CHARACTER_SPRITES)}

    atlas, places = pack(sprites)
    atlas.save(OUT / "atlas.png", optimize=True)

    room = read_yy(project / "rooms" / "rm_Runner" / "rm_Runner.yy")
    layers = []
    for layer in sorted((l for l in room["layers"] if l["name"] in PARALLAX), key=lambda l: -l["depth"]):   # farthest first
        art = load_sprite(project, layer["spriteId"]["name"])["frames"][0]
        art = art.crop((0, 0, art.width, min(art.height, ROOM_HEIGHT)))           # nothing below the room is ever on screen
        file = f"bg-{layer['name'][2:].lower()}.png"                              # bgClouds -> bg-clouds.png
        art.save(OUT / file, optimize=True)                                       # lossless first: fewer colours bands the soft edges
        if (OUT / file).stat().st_size > 1_000_000:                               # only the noisy near-ground layer is this big, and banding can't show in noise
            art.quantize(256, method=Image.Quantize.FASTOCTREE).save(OUT / file, optimize=True)
        layers.append({"file": file, "w": art.width, "h": art.height, "y": layer["y"], "parallax": PARALLAX[layer["name"]]})

    for web_name, source in SOUNDS.items():
        shutil.copyfile(project / "sounds" / source, OUT / f"sfx-{web_name}{Path(source).suffix}")

    sprite_table = {}
    for name, s in sprites.items():
        sprite_table[name] = {k: s[k] for k in ("w", "h", "ox", "oy", "fps", "box")} | {"frames": places[name]}
        if "tops" in s:
            sprite_table[name]["tops"] = s["tops"]
    converted = [convert_slice(s, i, objects, sprites) for i, s in enumerate(slices)]
    # A chunk whose floor is under the runner's death line (room height + 48) can only kill you, so it isn't shipped.
    playable = [s for s in converted if max(s["profile"]["entry"], s["profile"]["exit"]) < ROOM_HEIGHT]
    print(f"left out {len(converted) - len(playable)} chunks with floors below the room")
    world = {"sprites": sprite_table, "backgrounds": layers, "sounds": {n: f"sfx-{n}{Path(p).suffix}" for n, p in SOUNDS.items()},
             "slices": playable}
    (OUT / "world.json").write_text(json.dumps(world, separators=(",", ":")), encoding="utf-8")   # compact: it's only read by rules.js
    print(f"{len(world['slices'])} slices, {len(sprite_table)} sprites, atlas {atlas.size}, written to {OUT}")


if __name__ == "__main__":
    export(Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_PROJECT)
