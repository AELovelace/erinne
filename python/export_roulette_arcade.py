"""Copy Breakout's and Snake's art and sounds out of Doll's GameMaker arcade into Mnemerynne's games.

    python python/export_roulette_arcade.py [path to the Roulette project]

Default project: C:/Users/langley/GameMakerProjects/Roulette. Writes (and overwrites) only:
    mnemerynne/web/static/games/breakout/assets/   power-up-0..4.png, banner.png, bounce-2..9.wav, ball-lost.wav
    mnemerynne/web/static/games/snake/assets/      eat.wav
The ball, bat and bricks are plain shapes, so the game draws those itself (static/games/breakout/game.js). Sounds are
copied as they are; the ball-lost clip keeps its sound, under a plainer file name (Doll's choice).
"""

from __future__ import annotations

import json
import re
import shutil
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
GAMES = REPO / "mnemerynne" / "web" / "static" / "games"


def read_yy(path: Path) -> dict:
    """A GameMaker .yy file: JSON with trailing commas."""
    return json.loads(re.sub(r",(\s*[}\]])", r"\1", path.read_text(encoding="utf-8")))   # drop the trailing commas


def frames(project: Path, sprite: str) -> list[Path]:
    """A sprite's frame images, in the order the sprite plays them."""
    folder = project / "sprites" / sprite
    data = read_yy(folder / f"{sprite}.yy")
    return [folder / f"{frame['name']}.png" for frame in data["frames"]]


def main() -> None:
    project = Path(sys.argv[1] if len(sys.argv) > 1 else "C:/Users/langley/GameMakerProjects/Roulette")
    breakout, snake = GAMES / "breakout" / "assets", GAMES / "snake" / "assets"
    breakout.mkdir(parents=True, exist_ok=True)
    snake.mkdir(parents=True, exist_ok=True)
    for i, frame in enumerate(frames(project, "sprPowerups")):                 # wide bat, multiball, big ball, aim, slow
        shutil.copyfile(frame, breakout / f"power-up-{i}.png")
    shutil.copyfile(frames(project, "banner_square_sad")[0], breakout / "banner.png")   # the arena's backdrop
    for n in range(2, 10):                                                      # the eight bounce shots
        shutil.copyfile(project / "sounds" / f"DT_0{n}_Perc_Shot" / f"DT_0{n}_Perc_Shot.wav", breakout / f"bounce-{n}.wav")
    shutil.copyfile(project / "sounds" / "i_fucked_ur_mum" / "i_fucked_ur_mum.wav", breakout / "ball-lost.wav")
    shutil.copyfile(project / "sounds" / "ding" / "ding.wav", snake / "eat.wav")
    print(f"Copied into {breakout} and {snake}.")


if __name__ == "__main__":
    main()
