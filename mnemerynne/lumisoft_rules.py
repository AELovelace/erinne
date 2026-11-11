"""Lumi-Soft's rules on the server: a line-for-line copy of static/games/lumi-soft/rules.js, so a run the page played can
be replayed here, shot by shot, before it pays (lumisoft.py). The two must agree exactly: only + - * / on numbers and
whole-step timings, the same order of operations, no random, no sin. Change a rule in one and the same rule in the other;
tests/js/lumi_soft.test.mjs and tests/test_lumisoft.py replay the same recorded runs (tests/fixtures/lumi_soft_runs.json)."""

from __future__ import annotations

import math

# The same numbers as rules.js's C.
W, H = 2732, 1536
CLOCK, POP_TIME, BADGE_TIME, SCENE_TIME, FRIEND_TIME, CLOCK_PICKUP, BOSS_TIME = 3600, 120, 180, 900, 300, 300, 120
CONTINUE_CLOCK = 1800
HEARTS, AMMO, PUMP, RELOAD, MOVE = 3, 6, 30, 72, 120
BODY, BADGE, MOVING, QUICK, QUICK_AGE, FRIEND, STAR, SPOT, BOSS = 50, 100, 25, 50, 90, 250, 500, 150, 2000
FAR, COMBO_MAX = 0.75, 20
CARD_W, CARD_H = 100, 131


def plan(data: dict, mode: str, stage_id: str | None = None, day: int = 0) -> list[dict] | None:
    """The run's segments: [{"stage": index, "scene": index or -1 for the boss}], as rules.js plan()."""
    stages, out = data["stages"], []

    def add(i: int, scenes: list[int]) -> None:
        out.extend({"stage": i, "scene": n} for n in scenes)
        if stages[i].get("boss"):
            out.append({"stage": i, "scene": -1})

    find = next((i for i, s in enumerate(stages) if s["id"] == stage_id), -1) if stage_id else -1
    if mode == "training":
        i = next(i for i, s in enumerate(stages) if s["id"] == "training")
        return [{"stage": i, "scene": 0}]
    if mode == "story":
        for i, s in enumerate(stages):
            if not s.get("training"):
                add(i, list(range(len(s["scenes"]))))
        return out
    if mode == "daily":
        story = [i for i, s in enumerate(stages) if not s.get("training")]
        i = story[day % len(story)]
        add(i, [(day // len(story)) % len(stages[i]["scenes"])])
        return out
    if find < 0 or stages[find].get("training"):
        return None
    add(find, list(range(len(stages[find]["scenes"]))))
    return out


def create(data: dict, segments: list[dict]) -> dict:
    s = {"data": data, "segs": segments, "seg": 0, "segT": 0, "wave": 0, "waveT": 0, "pause": 0, "t": 0, "nextId": 1,
         "clock": CLOCK, "hearts": HEARTS, "ammo": AMMO, "lastShot": -9999, "reloadUntil": 0, "reloading": False,
         "score": 0, "streak": 0, "best": 0, "shots": 0, "hits": 0, "pops": 0, "badges": 0, "friends": 0, "lost": 0, "continues": 0,
         "targets": [], "boss": None, "over": False, "done": False, "cleared": [], "stage": None, "events": []}
    _begin(s)
    return s


def _stage_of(s: dict) -> dict:
    return s["data"]["stages"][s["segs"][s["seg"]]["stage"]]


def _training(s: dict) -> bool:
    return bool(_stage_of(s).get("training"))


def _begin(s: dict) -> None:
    seg = s["segs"][s["seg"]]
    stage = s["data"]["stages"][seg["stage"]]
    s.update(segT=0, wave=0, waveT=0, targets=[], boss=None)
    if not s["stage"] or s["stage"]["index"] != seg["stage"]:
        s["stage"] = {"index": seg["stage"], "id": stage["id"], "shots": 0, "hits": 0, "friends": 0, "lost": 0, "score": 0, "bossLost": 0}
    if seg["scene"] < 0:
        b = stage["boss"]
        s["boss"] = {"def": b, "age": 0, "hp": b["hp"], "wind": 0}
        s["stage"]["bossLost"] = 0
    s["events"].append({"kind": "segment", "stage": stage["id"], "scene": seg["scene"]})


def _scene(s: dict) -> dict | None:
    seg = s["segs"][s["seg"]]
    return None if seg["scene"] < 0 else s["data"]["stages"][seg["stage"]]["scenes"][seg["scene"]]


def tri(age, period):
    half = period / 2
    p = age % period
    return p / half if p < half else 2 - p / half


def _along(pts, dist, loop):
    total, lens = 0, []
    for i in range(len(pts) - 1):
        dx, dy = pts[i + 1][0] - pts[i][0], pts[i + 1][1] - pts[i][1]
        length = math.sqrt(dx * dx + dy * dy)
        lens.append(length)
        total += length
    if total <= 0:
        return pts[0][0], pts[0][1], False
    d = dist
    if loop:
        d = math.fmod(d, total)                  # JavaScript's % on numbers is fmod
    elif d >= total:
        return pts[-1][0], pts[-1][1], True
    for i, length in enumerate(lens):
        if d <= length or i == len(lens) - 1:
            f = d / length if length > 0 else 0
            return pts[i][0] + (pts[i + 1][0] - pts[i][0]) * f, pts[i][1] + (pts[i + 1][1] - pts[i][1]) * f, False
        d -= length
    return pts[0][0], pts[0][1], False


def pose(d: dict, age: int) -> dict:
    m = d.get("move") or {"type": "stand"}
    sc = d.get("s") or 1
    x, y, v, ok, gone = d["x"], d["y"], 1, True, False
    w, h = (d.get("w") or CARD_W) * sc, (d.get("h") or CARD_H) * sc
    kind = m["type"]
    if kind in ("pop", "peek"):
        period = m["up"] + m["stay"] + m["up"] + m["gap"]
        p = age % period
        if p < m["up"]:
            v = p / m["up"]
        elif p < m["up"] + m["stay"]:
            v = 1
        elif p < m["up"] + m["stay"] + m["up"]:
            v = 1 - (p - m["up"] - m["stay"]) / m["up"]
        else:
            v = 0
        if kind == "peek":
            x = d["x"] + m["dx"] * v
            ok = v >= 0.5
        else:
            ok = v > 0.25
    elif kind == "path":
        x, y, gone = _along(m["pts"], age * m["speed"], bool(m.get("loop")))
    elif kind == "bob":
        f = tri(age, m["period"]) * 2 - 1
        x, y = d["x"] + (m.get("dx") or 0) * f, d["y"] + (m.get("dy") or 0) * f
    elif kind == "rise":
        y = d["y"] - age * m["speed"]
        gone = y + h < 0
    shown = h * v if kind == "pop" else h
    return {"x": x, "y": y, "w": w, "h": h, "v": v, "ok": ok and not gone, "gone": gone, "left": x - w / 2, "top": y - shown,
            "badgeTo": y - shown + h * 0.25}


def _moving(d: dict) -> bool:
    return (d.get("move") or {}).get("type") in ("path", "bob", "rise")


def _last_at(wave: dict) -> int:
    return max((t["at"] for t in wave["targets"]), default=0)


def step(s: dict) -> dict:
    s["events"] = []
    if s["over"] or s["done"]:
        return s
    s["t"] += 1
    if s["reloading"] and s["t"] >= s["reloadUntil"]:
        s["reloading"], s["ammo"] = False, AMMO
    if s["pause"] > 0:
        s["pause"] -= 1
        if s["pause"] == 0:
            _begin(s)
        return s
    if not _training(s):
        s["clock"] -= 1
        if s["clock"] <= 0:
            s["clock"] = 0
            _game_over(s)
            return s
    s["segT"] += 1
    if s["boss"]:
        return _step_boss(s)
    sc = _scene(s)
    waves = sc["waves"] if sc else []
    wave = waves[s["wave"]] if s["wave"] < len(waves) else None
    if wave:
        for i, d in enumerate(wave["targets"]):
            if d["at"] == s["waveT"]:
                s["targets"].append({"id": s["nextId"], "def": d, "index": i, "age": 0, "wind": 0})
                s["nextId"] += 1
        s["waveT"] += 1
    kept = []
    for tg in s["targets"]:
        tg["age"] += 1
        p = pose(tg["def"], tg["age"])
        if p["gone"] or (tg["def"].get("life") and tg["age"] >= tg["def"]["life"]):
            continue
        if tg["def"].get("wind") and p["ok"] and p["v"] >= 1:
            tg["wind"] += 1
            if tg["wind"] >= tg["def"]["wind"]:
                tg["wind"] = 0
                _splash(s, tg["id"])
                if s["over"]:
                    kept.append(tg)
                    continue
        kept.append(tg)
    s["targets"] = kept
    if s["over"]:
        return s
    if wave and s["waveT"] > _last_at(wave) and not any(tg["def"]["kind"] == "creature" for tg in s["targets"]):
        s["targets"] = []
        s["wave"] += 1
        s["waveT"] = 0
        if s["wave"] >= len(waves):
            _end_segment(s)
    return s


def _step_boss(s: dict) -> dict:
    b = s["boss"]
    b["age"] += 1
    if b["def"].get("wind"):
        b["wind"] += 1
        if b["wind"] >= b["def"]["wind"]:
            b["wind"] = 0
            _splash(s, 0)
    return s


def _splash(s: dict, ident: int) -> None:
    s["hearts"] -= 1
    s["lost"] += 1
    s["stage"]["lost"] += 1
    if s["boss"]:
        s["stage"]["bossLost"] += 1
    s["streak"] = 0
    s["events"].append({"kind": "splash", "id": ident})
    if s["hearts"] <= 0:
        s["hearts"] = 0
        _game_over(s)


def _game_over(s: dict) -> None:
    s["over"] = True
    s["events"].append({"kind": "over"})


def _end_segment(s: dict) -> None:
    seg = s["segs"][s["seg"]]
    stage = s["data"]["stages"][seg["stage"]]
    if seg["scene"] >= 0 and not stage.get("training"):
        s["clock"] += SCENE_TIME
    last_of_stage = s["seg"] + 1 >= len(s["segs"]) or s["segs"][s["seg"] + 1]["stage"] != seg["stage"]
    if last_of_stage:
        _clear_stage(s, stage)
    s["seg"] += 1
    s["targets"], s["boss"] = [], None
    if s["seg"] >= len(s["segs"]):
        s["done"] = True
    else:
        s["pause"] = MOVE


def rank(st: dict) -> str:
    a, n = st["hits"] * 100, max(1, st["shots"])
    if a >= n * 90 and st["lost"] == 0 and st["friends"] == 0:
        return "S"
    return "A" if a >= n * 75 else "B" if a >= n * 60 else "C" if a >= n * 40 else "D"


def _clear_stage(s: dict, stage: dict) -> None:
    st = s["stage"]
    bonus = 0 if stage.get("training") else math.floor(st["hits"] * 2000 / max(1, st["shots"])) + s["hearts"] * 500 + math.floor(s["clock"] / 60) * 50
    s["score"] += bonus
    st["score"] += bonus
    s["cleared"].append({"stage": stage["id"], "score": st["score"], "bonus": bonus, "rank": "" if stage.get("training") else rank(st),
                         "shots": st["shots"], "hits": st["hits"], "friends": st["friends"], "lost": st["lost"],
                         "bossLost": st["bossLost"], "perfect": st["shots"] > 0 and st["hits"] == st["shots"]})


def _award(s: dict, pts: int, combo: bool) -> int:
    got = math.floor(pts * (10 + min(s["streak"], COMBO_MAX)) / 10) if combo else pts
    s["score"] += got
    s["stage"]["score"] += got
    return got


def impact(s: dict, x: float, y: float) -> tuple[float, float]:
    wind = _stage_of(s).get("wind") or 0
    return x + wind * (1 - y / H), y


def can_fire(s: dict) -> bool:
    return not s["over"] and not s["done"] and s["pause"] == 0 and not s["reloading"] and s["ammo"] > 0 and s["t"] - s["lastShot"] >= PUMP


def fire(s: dict, ax: float, ay: float) -> dict | None:
    if not can_fire(s):
        return None
    s["lastShot"] = s["t"]
    s["ammo"] -= 1
    s["shots"] += 1
    s["stage"]["shots"] += 1
    if s["ammo"] == 0:
        s["reloading"], s["reloadUntil"] = True, s["t"] + RELOAD
    x, y = impact(s, ax, ay)
    out = _hit_boss(s, x, y) if s["boss"] else _hit_target(s, x, y)
    out.update(x=x, y=y)
    return out


def _hit_target(s: dict, x: float, y: float) -> dict:
    best, bp = None, None
    for tg in s["targets"]:
        p = pose(tg["def"], tg["age"])
        if not p["ok"] or x < p["left"] or x > p["left"] + p["w"] or y < p["top"] or y > p["y"]:
            continue
        ts, bs = tg["def"].get("s") or 1, (best["def"].get("s") or 1) if best else 0
        if not best or ts > bs or (ts == bs and tg["id"] > best["id"]):
            best, bp = tg, p
    if not best:
        s["streak"] = 0
        return {"what": "miss"}
    s["targets"] = [tg for tg in s["targets"] if tg is not best]
    d = best["def"]
    s["hits"] += 1
    s["stage"]["hits"] += 1
    if d["kind"] == "friend":
        s["friends"] += 1
        s["stage"]["friends"] += 1
        s["streak"] = 0
        lose = min(s["score"], FRIEND)
        s["score"] -= lose
        s["stage"]["score"] -= lose
        s["clock"] = max(1, s["clock"] - FRIEND_TIME)
        return {"what": "friend", "id": best["id"], "points": -lose}
    if d["kind"] == "pickup":
        look = d.get("look")
        if look == "pickup-star":
            _award(s, STAR, False)
        elif look == "pickup-clock":
            s["clock"] += CLOCK_PICKUP
        elif look == "pickup-heart":
            s["hearts"] = min(HEARTS, s["hearts"] + 1)
        elif look == "pickup-crate":
            s["ammo"], s["reloading"] = AMMO, False
        return {"what": "pickup", "id": best["id"], "look": look, "points": STAR if look == "pickup-star" else 0}
    badge = y < bp["badgeTo"]
    pts = BADGE if badge else BODY
    if _moving(d):
        pts += MOVING
    if best["age"] <= QUICK_AGE + ((d.get("move") or {}).get("up") or 0):
        pts += QUICK
    if (d.get("s") or 1) < FAR:
        pts = math.floor(pts * 3 / 2)
    got = _award(s, pts, True)
    s["streak"] += 1
    s["best"] = max(s["best"], s["streak"])
    s["pops"] += 1
    if badge:
        s["badges"] += 1
    if not _training(s):
        s["clock"] += BADGE_TIME if badge else POP_TIME
    return {"what": "badge" if badge else "pop", "id": best["id"], "points": got}


def _hit_boss(s: dict, x: float, y: float) -> dict:
    b = s["boss"]
    d = b["def"]
    p = pose(d, b["age"])
    sc = d.get("s") or 1
    spot = -1
    for i, sp in enumerate(d.get("spots") or []):
        period = sp["on"] + sp["off"]
        if (b["age"] + (sp.get("phase") or 0)) % period >= sp["on"]:
            continue
        dx, dy, r = x - (p["x"] + sp["dx"] * sc), y - (p["y"] + sp["dy"] * sc), sp["r"] * sc
        if dx * dx + dy * dy <= r * r:
            spot = i
            break
    if spot >= 0:
        s["hits"] += 1
        s["stage"]["hits"] += 1
        got = _award(s, SPOT, True)
        s["streak"] += 1
        s["best"] = max(s["best"], s["streak"])
        b["hp"] -= 1
        b["wind"] = 0
        s["clock"] += BOSS_TIME
        if b["hp"] <= 0:
            s["score"] += BOSS
            s["stage"]["score"] += BOSS
            s["pops"] += 1
            _end_segment(s)
            return {"what": "beaten", "spot": spot, "points": got + BOSS}
        return {"what": "spot", "spot": spot, "points": got}
    if p["left"] <= x <= p["left"] + p["w"] and p["top"] <= y <= p["y"]:
        s["hits"] += 1
        s["stage"]["hits"] += 1
        return {"what": "boing"}
    s["streak"] = 0
    return {"what": "miss"}


def reload(s: dict) -> bool:
    if s["over"] or s["done"] or s["reloading"] or s["ammo"] >= AMMO:
        return False
    s["reloading"], s["reloadUntil"] = True, s["t"] + RELOAD
    return True


def carry_on(s: dict) -> bool:
    if not s["over"]:
        return False
    s["over"], s["hearts"], s["clock"] = False, HEARTS, max(s["clock"], CONTINUE_CLOCK)
    s["continues"] += 1
    s["streak"] = 0
    return True


def replay(data: dict, segments: list[dict], log: dict, limit: int | None = None) -> dict:
    """Replay a recorded run ({shots: [[t, x, y]], reloads: [t], continues: n, end: t}) as rules.js replay() does."""
    s = create(data, segments)
    shots, reloads = list(log.get("shots") or []), list(log.get("reloads") or [])
    end = min(int(log.get("end") or 0), limit if limit is not None else math.inf)
    si = ri = refused = 0
    while True:
        while ri < len(reloads) and reloads[ri] <= s["t"]:
            if reloads[ri] == s["t"]:
                reload(s)
            ri += 1
        while si < len(shots) and shots[si][0] <= s["t"]:
            if shots[si][0] != s["t"] or not fire(s, shots[si][1], shots[si][2]):
                refused += 1
            si += 1
        if s["done"] or s["t"] >= end:
            break
        if s["over"]:
            if s["continues"] < (log.get("continues") or 0):
                carry_on(s)
                continue
            break
        step(s)
    s["refused"] = refused + (len(shots) - si)
    return s


def summary(s: dict) -> dict:
    return {key: s[key] for key in ("score", "shots", "hits", "pops", "badges", "friends", "lost", "best", "continues", "done", "over", "t", "cleared")}
