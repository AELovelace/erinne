"""Lumi Launch's rules on the server: a LINE-FOR-LINE copy of static/games/lumi-launch/rules.js, so a go the page played
can be replayed here, press by press, before it pays (lumilaunch.py). The two must agree exactly: only + - * / and
floor on numbers, the same order of operations, whole-frame timings, no sin and no random. The angles are a table of
literal numbers that python/make_lumi_launch_tables.py writes into both files. Change a rule in one and the same rule
in the other; tests/js/lumi_launch.test.mjs and tests/test_lumilaunch.py replay the same recorded goes
(tests/fixtures/lumi_launch_runs.json)."""

from __future__ import annotations

import math

M = 2147483647                                         # the arcade's seed modulus (arcade.MODULUS)

# The same numbers as rules.js's C.
UNIT = 20                                              # course units in one metre
SHOTS = 3                                              # launches in a go
AIM_LOW, AIM_STEPS, AIM_EVERY = 15, 60, 2              # the needle: 15°..75°, one degree every 2 frames
AIM_MAX, POWER_MAX = 1800, 1800                        # 30 s to choose, then the cannon chooses for you
POWER_STEP = 1                                         # the power bar: 0..100, one a frame
SPEED_MIN, SPEED_MAX = 10, 30                          # the launch speed at power 0 and 100 (units a frame)
MUZZLE_X, MUZZLE_Y = 60, 60                            # where she leaves the cannon
GRAVITY, AIR, VMAX = 0.25, 0.999, 42                   # a frame's pull, drag, and the fastest she may go sideways
BOUNCE, SKID, BOUNCE_MIN = 0.5, 0.82, 2                # a ground bounce keeps half her fall; a soft landing rolls
ROLL, STOP = 0.97, 0.15                                # rolling slows her; this slow, she has stopped
FLY_MAX = 3600                                         # a minute in the air and on the ground, at most
PUFFS, PUFF_UP, PUFF_FWD = 3, 6, 2                     # sparkle puffs: a hop up and forward each
START, SLOT, GAP = 1600, 600, 200                      # the course: one slot every 30 m from 80 m out
EMPTY, HAZARD_BASE, HAZARD_STEP, HAZARD_MAX = 120, 40, 8, 320   # per mille: empty slots, hazards (more further out)
PILLOW_KICK, PILLOW_MIN, PILLOW_FWD = 1.05, 9, 1       # Cloud Pillow: a big bounce
CUP_SPEED, CUP_COS, CUP_SIN, CUP_UP = 16, 0.573576, 0.819152, 3   # Teacup Flinger: out at 55°
YARN_FWD, YARN_UP = 6, 3                               # Yarn Roll: a burst along the ground
WIND, WIND_H = 0.55, 420                               # Wish Wind: an updraft column
COMET_FWD, COMET_UP = 14, 7                            # Comet Ride: a rocket burst

# Each kind's size: w across; h tall (ground pieces) or the box's height (floating pieces).
SIZE = {"pillow": (120, 40), "teacup": (80, 60), "yarn": (100, 30), "wind": (160, 420), "stardust": (60, 60),
        "comet": (70, 70), "honey": (140, 8), "nap": (110, 12)}
HAZARDS = ("honey", "nap")

# ANGLES BEGIN (python/make_lumi_launch_tables.py writes this block: (cos, sin) for 15°..75°)
ANGLES = (
    (0.965926, 0.258819), (0.961262, 0.275637), (0.956305, 0.292372), (0.951057, 0.309017), (0.945519, 0.325568), (0.939693, 0.342020),
    (0.933580, 0.358368), (0.927184, 0.374607), (0.920505, 0.390731), (0.913545, 0.406737), (0.906308, 0.422618), (0.898794, 0.438371),
    (0.891007, 0.453990), (0.882948, 0.469472), (0.874620, 0.484810), (0.866025, 0.500000), (0.857167, 0.515038), (0.848048, 0.529919),
    (0.838671, 0.544639), (0.829038, 0.559193), (0.819152, 0.573576), (0.809017, 0.587785), (0.798636, 0.601815), (0.788011, 0.615661),
    (0.777146, 0.629320), (0.766044, 0.642788), (0.754710, 0.656059), (0.743145, 0.669131), (0.731354, 0.681998), (0.719340, 0.694658),
    (0.707107, 0.707107), (0.694658, 0.719340), (0.681998, 0.731354), (0.669131, 0.743145), (0.656059, 0.754710), (0.642788, 0.766044),
    (0.629320, 0.777146), (0.615661, 0.788011), (0.601815, 0.798636), (0.587785, 0.809017), (0.573576, 0.819152), (0.559193, 0.829038),
    (0.544639, 0.838671), (0.529919, 0.848048), (0.515038, 0.857167), (0.500000, 0.866025), (0.484810, 0.874620), (0.469472, 0.882948),
    (0.453990, 0.891007), (0.438371, 0.898794), (0.422618, 0.906308), (0.406737, 0.913545), (0.390731, 0.920505), (0.374607, 0.927184),
    (0.358368, 0.933580), (0.342020, 0.939693), (0.325568, 0.945519), (0.309017, 0.951057), (0.292372, 0.956305), (0.275637, 0.961262),
    (0.258819, 0.965926),
)
# ANGLES END


def mix(seed: int, index: int, salt: int) -> int:
    """arcade.mix (snakeSeedValue from the GameMaker game)."""
    v = (max(1, abs(int(seed))) * 1664525 + (max(0, int(index)) + 1) * 1013904223 + max(1, abs(int(salt))) * 69069) % M
    return v if v > 0 else v + M - 1


def scramble(value: int) -> int:
    """arcade.scramble: xorshift32, so neighbours roll apart."""
    u = value & 0xFFFFFFFF
    u = (u ^ (u << 13)) & 0xFFFFFFFF
    u = (u ^ (u >> 17)) & 0xFFFFFFFF
    return (u ^ (u << 5)) & 0xFFFFFFFF


def roll(seed: int, i: int, salt: int) -> int:
    """One number 0..999 for slot i."""
    return scramble(mix(seed, i, salt)) % 1000


def piece(seed: int, i: int) -> dict | None:
    """What sits in course slot i (None: nothing), always the same for a seed."""
    if i < 0 or roll(seed, i, 1) < EMPTY:
        return None
    hazard = min(HAZARD_MAX, HAZARD_BASE + i * HAZARD_STEP)
    x = START + i * SLOT + roll(seed, i, 3) % (SLOT - GAP)
    if roll(seed, i, 2) < hazard:
        kind = HAZARDS[roll(seed, i, 4) % 2]
    else:
        g = roll(seed, i, 4) % 100
        kind = "pillow" if g < 26 else "teacup" if g < 44 else "yarn" if g < 62 else "wind" if g < 76 else "stardust" if g < 96 else "comet"
    y = 0
    if kind == "stardust":
        y = 120 + roll(seed, i, 5) % 240
    if kind == "comet":
        y = 100 + roll(seed, i, 5) % 160
    return {"i": i, "kind": kind, "x": x, "y": y, "w": SIZE[kind][0], "h": SIZE[kind][1]}


def new_shot(seed: int) -> dict:
    """One launch, waiting in the cannon."""
    return {"seed": seed, "phase": "aim", "t": 0, "phaseT": 0, "aim": 30, "aimDir": 1, "manual": False, "power": 0, "powerDir": 1,
            "x": MUZZLE_X, "y": MUZZLE_Y, "vx": 0, "vy": 0, "puffs": PUFFS, "dust": 0, "used": {}, "end": "", "events": []}


def _fire(s: dict) -> None:
    """Out of the cannon at the locked angle and power."""
    speed = SPEED_MIN + (SPEED_MAX - SPEED_MIN) * s["power"] / 100
    s["vx"] = speed * ANGLES[s["aim"]][0]
    s["vy"] = speed * ANGLES[s["aim"]][1]
    s["phase"] = "fly"; s["phaseT"] = 0
    s["events"].append({"type": "fire", "power": s["power"], "angle": AIM_LOW + s["aim"]})


def press(s: dict, button: str) -> None:
    """A press: "ok", "up" or "down" (anything else does nothing)."""
    if s["phase"] == "aim":
        if button in ("up", "down"):                    # a nudge stops the swing: the needle stays where you put it
            s["manual"] = True
            s["aim"] = max(0, min(AIM_STEPS, s["aim"] + (1 if button == "up" else -1)))
            s["events"].append({"type": "nudge"})
        elif button == "ok":
            s["phase"] = "power"; s["phaseT"] = 0; s["power"] = 0; s["powerDir"] = 1
            s["events"].append({"type": "lock", "angle": AIM_LOW + s["aim"]})
    elif s["phase"] == "power":
        if button == "ok":
            _fire(s)
    elif s["phase"] == "fly":
        if button == "ok" and s["puffs"] > 0:          # a sparkle puff
            s["puffs"] = s["puffs"] - 1
            s["vy"] = (s["vy"] if s["vy"] > 0 else 0) + PUFF_UP
            s["vx"] = s["vx"] + PUFF_FWD
            s["events"].append({"type": "puff", "left": s["puffs"]})


def _finish(s: dict, why: str) -> None:
    s["phase"] = "done"; s["end"] = why
    s["events"].append({"type": "stop", "why": why, "m": metres(s)})


def _touch(s: dict) -> None:
    """The course pieces around her."""
    here = math.floor((s["x"] - START) / SLOT)
    for k in range(here - 1, here + 2):
        p = piece(s["seed"], k)
        if not p or k in s["used"] or s["phase"] != "fly":
            continue
        if s["x"] < p["x"] or s["x"] > p["x"] + p["w"]:
            continue
        if p["kind"] in ("stardust", "comet"):         # floating: inside the box
            if s["y"] < p["y"] or s["y"] > p["y"] + p["h"]:
                continue
            s["used"][k] = 1
            if p["kind"] == "stardust":
                s["dust"] = s["dust"] + 1
                s["puffs"] = min(PUFFS, s["puffs"] + 1)
            else:
                s["vx"] = s["vx"] + COMET_FWD
                s["vy"] = COMET_UP
            s["events"].append({"type": p["kind"], "i": k})
            continue
        if s["y"] > p["h"]:                             # ground pieces: only when she's down at their height
            continue
        if p["kind"] == "wind":
            s["vy"] = s["vy"] + WIND
            if s["phaseT"] % 12 == 0:
                s["events"].append({"type": "wind", "i": k})
        elif p["kind"] == "pillow":
            if s["vy"] >= 0:
                continue
            s["y"] = p["h"]
            s["vy"] = max(-s["vy"] * PILLOW_KICK, PILLOW_MIN)
            s["vx"] = s["vx"] + PILLOW_FWD
            s["events"].append({"type": "pillow", "i": k})
        elif p["kind"] == "teacup":
            s["used"][k] = 1
            speed = s["vx"] if s["vx"] > CUP_SPEED else CUP_SPEED
            s["y"] = p["h"]
            s["vx"] = speed * CUP_COS
            s["vy"] = speed * CUP_SIN + CUP_UP
            s["events"].append({"type": "teacup", "i": k})
        elif p["kind"] == "yarn":
            s["used"][k] = 1
            s["vx"] = s["vx"] + YARN_FWD
            s["vy"] = max(s["vy"], YARN_UP)
            s["events"].append({"type": "yarn", "i": k})
        else:                                           # honey or nap: she stops right there
            s["vx"] = 0; s["vy"] = 0
            _finish(s, p["kind"])
            return


def step(s: dict) -> dict:
    """One frame (1/60 s); s["events"] says what happened."""
    s["events"] = []
    if s["phase"] == "done":
        return s
    s["t"] = s["t"] + 1
    s["phaseT"] = s["phaseT"] + 1
    if s["phase"] == "aim":
        if not s["manual"] and s["phaseT"] % AIM_EVERY == 0:
            s["aim"] = s["aim"] + s["aimDir"]
            if s["aim"] >= AIM_STEPS:
                s["aim"] = AIM_STEPS; s["aimDir"] = -1
            if s["aim"] <= 0:
                s["aim"] = 0; s["aimDir"] = 1
        if s["phaseT"] >= AIM_MAX:
            press(s, "ok")
        return s
    if s["phase"] == "power":
        s["power"] = s["power"] + s["powerDir"] * POWER_STEP
        if s["power"] >= 100:
            s["power"] = 100; s["powerDir"] = -1
        if s["power"] <= 0:
            s["power"] = 0; s["powerDir"] = 1
        if s["phaseT"] >= POWER_MAX:
            _fire(s)
        return s
    s["vy"] = s["vy"] - GRAVITY
    s["vx"] = s["vx"] * AIR
    if s["vx"] > VMAX:
        s["vx"] = VMAX
    s["x"] = s["x"] + s["vx"]
    s["y"] = s["y"] + s["vy"]
    _touch(s)
    if s["phase"] != "fly":
        return s
    if s["y"] <= 0:
        s["y"] = 0
        if s["vy"] < -BOUNCE_MIN:
            s["vy"] = -s["vy"] * BOUNCE
            s["vx"] = s["vx"] * SKID
            s["events"].append({"type": "bounce"})
        else:
            s["vy"] = 0
            s["vx"] = s["vx"] * ROLL
            if s["vx"] < STOP:
                _finish(s, "rest")
                return s
    if s["phaseT"] >= FLY_MAX:
        _finish(s, "time")
    return s


def metres(s: dict) -> int:
    """How far she is from the cannon, in whole metres."""
    return max(0, math.floor(s["x"] / UNIT))


def play_shot(seed: int, log: list) -> dict:
    """One launch from its presses: [[frame, button], ...] in order."""
    s = new_shot(seed)
    at = 0
    while s["phase"] != "done":
        while at < len(log) and log[at][0] <= s["t"]:
            press(s, log[at][1])
            at = at + 1
        step(s)
    return s


def replay(seed: int, shots: list) -> dict:
    """A whole go: what the server pays from."""
    out = {"shots": [], "total": 0, "frames": 0, "dust": 0}
    for n in range(min(len(shots), SHOTS)):
        s = play_shot(seed, shots[n])
        out["shots"].append({"m": metres(s), "dust": s["dust"], "end": s["end"], "frames": s["t"]})
        out["total"] = out["total"] + metres(s)
        out["frames"] = out["frames"] + s["t"]
        out["dust"] = out["dust"] + s["dust"]
    return out
