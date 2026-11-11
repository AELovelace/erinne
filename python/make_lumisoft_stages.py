"""Make Lumi-Soft's stages: mnemerynne/lumisoft_data/stages.json, the one file both the page (rules.js) and the server
(lumisoft_rules.py) play from. Run it after changing a layout here:

    .venv/Scripts/python.exe python/make_lumisoft_stages.py

The JSON is the source of truth once written (it's committed); this script just saves laying out 300 targets by hand.
A fixed seed per scene keeps the output the same every run, so re-running without changes changes nothing.

A scene is 2732 x 1536 units. y is the ground a target stands on (its bottom edge): small y is far away (small, worth
×1.5), big y is near. Props are cover drawn in front of the targets (art.js): a pop-up rises from behind the prop at
its y; a peeker slides out from behind a wall. Creatures' looks are Lumi Dungeons' creatures, as cardboard standees."""

from __future__ import annotations

import json
import random
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "mnemerynne" / "lumisoft_data" / "stages.json"
W, H = 2732, 1536


def depth(y: float) -> float:
    """How big a standee is at this ground line: far ones (small y) are small."""
    return round(max(0.45, min(1.15, 0.5 + 0.6 * (y - 600) / 800)), 2)


class Scene:
    """One scene: its cover props and its waves. `level` (0-4) makes it harder: shorter stays, quicker balloons."""

    def __init__(self, ident: str, seed: int, level: int, looks: list[str], friends: list[str]) -> None:
        self.id, self.rng, self.level, self.looks, self.friend_looks = ident, random.Random(seed), level, looks, friends
        self.props: list[dict] = []
        self.waves: list[dict] = []
        self.wave: list[dict] = []

    # ---- props
    def prop(self, look: str, x: float, y: float, w: float, h: float = 0) -> None:
        self.props.append({"look": look, "x": round(x), "y": round(y), "w": round(w), "h": round(h or w * 0.35)})

    # ---- targets (added to the wave being written; `at` is steps after the wave starts)
    def _card(self, kind: str, look: str, x: float, y: float, at: int, move: dict, wind: int = 0, life: int = 0, s: float | None = None) -> None:
        t = {"kind": kind, "look": look, "x": round(x), "y": round(y), "s": s if s is not None else depth(y), "at": at, "move": move}
        if wind:
            t["wind"] = wind
        if life:
            t["life"] = life
        self.wave.append(t)

    def look(self) -> str:
        return self.rng.choice(self.looks)

    def stay(self, base: int) -> int:
        return max(40, base - self.level * 12)

    def windup(self) -> int:
        return max(110, 200 - self.level * 18)

    def pop(self, x: float, y: float, at: int, *, wind: bool = False, stay: int = 110, gap: int = 70, life: int = 0, look: str | None = None) -> None:
        self._card("creature", look or self.look(), x, y, at, {"type": "pop", "up": 12, "stay": self.stay(stay), "gap": gap},
                   self.windup() if wind else 0, life)

    def peek(self, x: float, y: float, dx: float, at: int, *, wind: bool = False, stay: int = 80, gap: int = 90, look: str | None = None) -> None:
        self._card("creature", look or self.look(), x, y, at, {"type": "peek", "dx": round(dx), "up": 10, "stay": self.stay(stay), "gap": gap},
                   self.windup() if wind else 0)

    def stand(self, x: float, y: float, at: int, *, wind: bool = False, life: int = 0, look: str | None = None) -> None:
        self._card("creature", look or self.look(), x, y, at, {"type": "stand"}, self.windup() if wind else 0, life)

    def walk(self, pts: list[tuple[float, float]], at: int, speed: float, *, loop: bool = False, wind: bool = False, look: str | None = None, s: float | None = None) -> None:
        x, y = pts[0]
        self._card("creature", look or self.look(), x, y, at, {"type": "path", "pts": [[round(a), round(b)] for a, b in pts],
                   "speed": speed, "loop": loop}, self.windup() if wind else 0, 0, s if s is not None else depth(y))

    def bob(self, x: float, y: float, at: int, dx: float, dy: float, period: int, *, wind: bool = False, look: str | None = None) -> None:
        self._card("creature", look or self.look(), x, y, at, {"type": "bob", "dx": round(dx), "dy": round(dy), "period": period},
                   self.windup() if wind else 0)

    def balloon(self, x: float, y: float, at: int, speed: float = 1.5) -> None:
        self._card("creature", "balloon", x, y, at, {"type": "rise", "speed": speed}, s=depth(y) * 0.8)

    def friend(self, x: float, y: float, at: int, *, move: dict | None = None, life: int = 0) -> None:
        self._card("friend", self.rng.choice(self.friend_looks), x, y, at, move or {"type": "stand"}, 0, life)

    def pickup(self, look: str, x: float, y: float, at: int, life: int = 300) -> None:
        self._card("pickup", "pickup-" + look, x, y, at, {"type": "bob", "dx": 0, "dy": -16, "period": 90}, 0, life, s=depth(y) * 0.7)

    def end_wave(self) -> None:
        self.waves.append({"targets": sorted(self.wave, key=lambda t: t["at"])})
        self.wave = []

    def data(self) -> dict:
        assert not self.wave, "a wave was left open"
        return {"id": self.id, "props": self.props, "waves": self.waves}


def rows(scene: Scene, rows_: list[tuple[float, str, float]]) -> None:
    """Cover along ground lines: (y, look, prop width), repeated across the scene with gaps."""
    for y, look, w in rows_:
        x = w / 2 + scene.rng.randint(20, 120)
        while x < W - w / 2:
            scene.prop(look, x, y, w)
            x += w + scene.rng.randint(180, 420)


def pop_row(scene: Scene, y: float, count: int, at: int, spread: int, *, wind_every: int = 0, gap: int = 70) -> None:
    """Pop-ups from behind this row's props."""
    cover = [p for p in scene.props if p["y"] == round(y)] or [{"x": W / 2, "w": 400}]
    for i in range(count):
        c = scene.rng.choice(cover)
        x = c["x"] + scene.rng.uniform(-c["w"] / 2 + 60, c["w"] / 2 - 60)
        scene.pop(x, y, at + i * spread, wind=bool(wind_every) and i % wind_every == wind_every - 1, gap=gap)


# ------------------------------------------------------------------ the stages
def training() -> dict:
    sc = Scene("training-1", 11, 0, ["can"], ["friend-lumi"])
    sc.prop("bench", 700, 1250, 520, 120)
    sc.prop("bench", 2000, 1250, 520, 120)
    sc.prop("hay", 1366, 900, 600, 140)
    for i, x in enumerate((560, 700, 840)):                  # three cans on a bench
        sc.stand(x, 1250, i * 10, look="can")
    sc.end_wave()
    for i, x in enumerate((1860, 2000, 2140, 1366)):         # farther, then a far one
        sc.stand(x, 1250 if i < 3 else 760, i * 10, look="can")
    sc.end_wave()
    for i in range(5):                                       # balloons drifting up
        sc.balloon(500 + i * 420, 1450, i * 40, 1.6)
    sc.end_wave()
    sc.pop(1366, 900, 0, look="dust-bunny", stay=140)        # the first pop-up behind the hay
    sc.pop(1200, 900, 60, look="dust-bunny", stay=140)
    sc.friend(1560, 900, 0)                                  # a friend with a ♡ sign: leave her be
    sc.end_wave()
    sc.walk([(300, 1100), (2400, 1100)], 0, 2.5, look="puddle-slime")
    sc.pickup("star", 1366, 700, 40, 600)
    sc.end_wave()
    return {"id": "training", "name": "Training Range", "training": True, "sky": "range", "scenes": [sc.data()], "boss": None}


def garden() -> dict:
    looks, friends, out = ["dust-bunny", "puddle-slime", "thornling", "static-sprite"], ["friend-lumi", "friend-bunny"], []
    for n in range(3):
        sc = Scene(f"garden-{n + 1}", 100 + n, 0 + n // 2, looks, friends)
        rows(sc, [(760, "hedge", 360), (1020, "flowerbed", 420), (1320, "fence", 480)])
        pop_row(sc, 1020, 4, 0, 50)
        sc.friend(sc.props[0]["x"], 760, 20, life=400)
        sc.end_wave()
        pop_row(sc, 760, 3, 0, 40, wind_every=3)
        pop_row(sc, 1320, 2, 60, 50)
        sc.end_wave()
        sc.walk([(-80, 1180), (W + 80, 1180)], 0, 3.0 + n * 0.5)                    # the watering-can cart rolls by
        sc.walk([(W + 80, 1180), (-80, 1180)], 120, 3.0 + n * 0.5)
        pop_row(sc, 1020, 3, 30, 60, wind_every=2)
        sc.pickup(["clock", "star", "heart"][n], 1366, 640, 60)
        sc.end_wave()
        for i in range(4):
            sc.balloon(400 + i * 640 + sc.rng.randint(-80, 80), 1500, i * 30, 1.8)
        pop_row(sc, 760, 3, 20, 45, wind_every=2)
        sc.friend(1366, 1320, 10, life=360)
        sc.end_wave()
        out.append(sc.data())
    boss = {"look": "moss-golem", "x": 1366, "y": 1300, "w": 460, "h": 560, "s": 1, "hp": 8, "wind": 300,
            "move": {"type": "bob", "dx": 520, "dy": 0, "period": 900},
            "spots": [{"dx": -130, "dy": -400, "r": 46, "on": 150, "off": 150, "phase": 0},
                      {"dx": 130, "dy": -400, "r": 46, "on": 150, "off": 150, "phase": 150},
                      {"dx": 0, "dy": -500, "r": 40, "on": 100, "off": 200, "phase": 60}]}
    return {"id": "garden", "name": "Stardust Garden", "sky": "morning", "zoom": 2.5, "wind": 0, "scenes": out, "boss": boss}


def tower() -> dict:
    looks, friends, out = ["gloom-bat", "frost-moth", "static-sprite", "cinder-mite"], ["friend-lumi", "friend-bunny"], []
    for n in range(3):
        sc = Scene(f"tower-{n + 1}", 200 + n, 1 + n // 2, looks, friends)
        rows(sc, [(640, "balcony", 420), (900, "balcony", 460), (1200, "rail", 520)])
        pop_row(sc, 640, 3, 0, 45, wind_every=3)
        pop_row(sc, 900, 3, 30, 45)
        sc.end_wave()
        for i in range(3):                                   # spinning teacups bob along the roof
            sc.bob(500 + i * 860, 1350, i * 20, 140, 0, 240 + i * 40, wind=i == 1)
        sc.friend(900, 1200, 0, life=420)
        sc.end_wave()
        sc.walk([(-80, 900), (W + 80, 900)], 0, 4.0, wind=True)
        pop_row(sc, 1200, 3, 40, 50, wind_every=2)
        sc.pickup(["star", "crate", "clock"][n], 2200, 560, 50)
        sc.end_wave()
        tall = [p for p in sc.props if p["look"] == "balcony"]                    # peekers hide behind the tall balcony walls
        for i in range(3):
            c = tall[sc.rng.randrange(len(tall))]
            sc.peek(c["x"] - c["w"] / 2 + 40, c["y"], -150, i * 50, wind=i == 2)
        pop_row(sc, 640, 3, 60, 40)
        sc.end_wave()
        out.append(sc.data())
    figure8 = [(500, 520), (1366, 760), (2232, 520), (2232, 900), (1366, 760), (500, 900), (500, 520)]
    boss = {"look": "storm-kite", "x": 500, "y": 520, "w": 380, "h": 330, "s": 1, "hp": 9, "wind": 270,
            "move": {"type": "path", "pts": [list(p) for p in figure8], "speed": 4, "loop": True},
            "spots": [{"dx": -120, "dy": 40, "r": 34, "on": 140, "off": 100, "phase": 0},
                      {"dx": 0, "dy": 90, "r": 34, "on": 140, "off": 100, "phase": 80},
                      {"dx": 120, "dy": 40, "r": 34, "on": 140, "off": 100, "phase": 160}]}
    return {"id": "tower", "name": "Teacup Tower", "sky": "sunset", "zoom": 2.5, "wind": 46, "scenes": out, "boss": boss}


def lounge() -> dict:
    looks, friends, out = ["shade-cat", "lantern-imp", "tin-sentinel", "static-sprite"], ["friend-lumi", "friend-patron"], []
    for n in range(3):
        sc = Scene(f"lounge-{n + 1}", 300 + n, 2 + n // 2, looks, friends)
        rows(sc, [(700, "slots", 300), (980, "table", 440), (1300, "bar", 620)])
        pop_row(sc, 980, 4, 0, 40, wind_every=2)
        sc.friend(sc.props[1]["x"], 980, 30, life=360)
        sc.friend(sc.props[-1]["x"] + 120, 1300, 60, life=360)
        sc.end_wave()
        for i in range(3):
            c = [p for p in sc.props if p["look"] == "slots"][i % 3]
            sc.peek(c["x"] + c["w"] / 2 - 40, 700, 140, i * 45, wind=i != 1)
        sc.walk([(W + 80, 1150), (-80, 1150)], 30, 4.5)
        sc.end_wave()
        pop_row(sc, 1300, 3, 0, 35, wind_every=2)
        pop_row(sc, 700, 3, 20, 35)
        sc.friend(1366, 700, 0, move={"type": "bob", "dx": 200, "dy": 0, "period": 300}, life=500)
        sc.pickup(["heart", "star", "crate"][n], 600, 600, 30)
        sc.end_wave()
        pop_row(sc, 980, 5, 0, 30, wind_every=2)
        sc.end_wave()
        out.append(sc.data())
    hops = [(500, 560), (1366, 480), (2232, 560), (1366, 480)]
    boss = {"look": "bell-phantom", "x": 500, "y": 560, "w": 340, "h": 420, "s": 0.95, "hp": 10, "wind": 240,
            "move": {"type": "path", "pts": [list(p) for p in hops] + [list(hops[0])], "speed": 7, "loop": True},
            "spots": [{"dx": 0, "dy": -330, "r": 36, "on": 120, "off": 120, "phase": 0},
                      {"dx": -90, "dy": -150, "r": 30, "on": 100, "off": 140, "phase": 100},
                      {"dx": 90, "dy": -150, "r": 30, "on": 100, "off": 140, "phase": 30}]}
    return {"id": "lounge", "name": "Lumi’s Lounge", "sky": "neon", "zoom": 2.5, "wind": 0, "scenes": out, "boss": boss}


def dungeon() -> dict:
    looks, friends, out = ["gloom-bat", "lantern-imp", "cinder-mite", "frost-moth", "shade-cat"], ["friend-lumi", "friend-bunny"], []
    for n in range(3):
        sc = Scene(f"dungeon-{n + 1}", 400 + n, 3 + n // 2, looks, friends)
        rows(sc, [(680, "pillar", 200), (960, "crate", 300), (1280, "barrel", 260)])
        for i in range(3):
            c = [p for p in sc.props if p["look"] == "pillar"][i]
            sc.peek(c["x"] - c["w"] / 2 + 30, 680, -130, i * 40, wind=True)
        pop_row(sc, 960, 3, 30, 40)
        sc.end_wave()
        for i in range(4):                                   # bats swoop on bobbing paths
            sc.bob(400 + i * 640, 560 + (i % 2) * 120, i * 25, 180, 60, 200 + i * 30, look="gloom-bat", wind=i % 2 == 1)
        sc.friend(1366, 1280, 0, life=420)
        sc.end_wave()
        pop_row(sc, 1280, 4, 0, 35, wind_every=2)
        sc.walk([(-80, 1120), (W + 80, 1120)], 40, 5.0)
        sc.pickup(["clock", "heart", "star"][n], 2300, 900, 40)
        sc.end_wave()
        pop_row(sc, 960, 4, 0, 30, wind_every=2)
        pop_row(sc, 680, 2, 40, 40)
        sc.end_wave()
        out.append(sc.data())
    boss = {"look": "clockwork-owl", "x": 1366, "y": 1150, "w": 480, "h": 560, "s": 1, "hp": 10, "wind": 230,
            "move": {"type": "bob", "dx": 0, "dy": 24, "period": 240},
            "spots": [{"dx": -110, "dy": -360, "r": 40, "on": 60, "off": 180, "phase": 0},
                      {"dx": 110, "dy": -360, "r": 40, "on": 60, "off": 180, "phase": 0},
                      {"dx": 0, "dy": -170, "r": 46, "on": 90, "off": 150, "phase": 120}]}
    return {"id": "dungeon", "name": "The Deep Dungeon", "sky": "dark", "dark": True, "zoom": 3, "wind": 0, "scenes": out, "boss": boss}


def fair() -> dict:
    looks = ["dust-bunny", "gloom-bat", "lantern-imp", "tin-sentinel", "shade-cat", "static-sprite", "puddle-slime"]
    friends, out = ["friend-lumi", "friend-bunny", "friend-patron"], []
    wheel = [(1900 + 320 * dx, 700 + 320 * dy) for dx, dy in ((0, -1), (0.7, -0.7), (1, 0), (0.7, 0.7), (0, 1), (-0.7, 0.7), (-1, 0), (-0.7, -0.7), (0, -1))]
    for n in range(3):
        sc = Scene(f"fair-{n + 1}", 500 + n, 4, looks, friends)
        rows(sc, [(760, "stall", 380), (1060, "stall", 420), (1340, "rail", 560)])
        for i in range(4):                                   # riders on the Ferris wheel
            sc.walk(wheel, i * 50, 3.0, loop=True, wind=i == 2, s=0.6)
        sc.friend(wheel[0][0], wheel[0][1], 100, move={"type": "path", "pts": [list(p) for p in wheel], "speed": 3.0, "loop": True}, life=600)
        sc.end_wave()
        carousel = [(300, 1200), (1100, 1240), (300, 1280), (300, 1200)]
        for i in range(3):
            sc.walk(carousel, i * 60, 4.0, loop=True, wind=i == 1)
        pop_row(sc, 760, 3, 40, 35, wind_every=2)
        sc.end_wave()
        sc.walk([(-100, 1000), (W + 100, 1000)], 0, 5.5, wind=True)                # a parade float
        sc.walk([(W + 100, 1000), (-100, 1000)], 90, 5.5)
        pop_row(sc, 1060, 4, 20, 30, wind_every=2)
        sc.friend(1366, 1340, 30, life=360)
        sc.pickup(["star", "heart", "clock"][n], 1366, 560, 50)
        sc.end_wave()
        for i in range(5):
            sc.balloon(300 + i * 540, 1500, i * 20, 2.4)
        pop_row(sc, 1340, 3, 0, 30, wind_every=2)
        sc.end_wave()
        out.append(sc.data())
    parade = [(300, 1200), (2432, 1200), (2432, 1100), (300, 1100), (300, 1200)]
    boss = {"look": "comet-drake", "x": 300, "y": 1200, "w": 600, "h": 520, "s": 1, "hp": 12, "wind": 210,
            "move": {"type": "path", "pts": [list(p) for p in parade], "speed": 4.5, "loop": True},
            "spots": [{"dx": 190, "dy": -400, "r": 40, "on": 120, "off": 120, "phase": 0},
                      {"dx": -40, "dy": -300, "r": 36, "on": 90, "off": 150, "phase": 60},
                      {"dx": -230, "dy": -200, "r": 34, "on": 120, "off": 120, "phase": 120},
                      {"dx": 60, "dy": -120, "r": 30, "on": 60, "off": 180, "phase": 180}]}
    return {"id": "fair", "name": "Night Fair", "sky": "fireworks", "zoom": 3, "wind": 28, "scenes": out, "boss": boss}


def main() -> None:
    data = {"version": 1, "stages": [training(), garden(), tower(), lounge(), dungeon(), fair()]}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    count = sum(len(w["targets"]) for st in data["stages"] for sc in st["scenes"] for w in sc["waves"])
    print(f"Wrote {OUT} ({count} targets in {sum(len(st['scenes']) for st in data['stages'])} scenes).")


if __name__ == "__main__":
    main()
