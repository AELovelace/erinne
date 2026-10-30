"""Built-in games: small games of our own that ship with Mnemerynne and open from Media → Games
(docs/stretch-goals/built-in-games.md). They replace the arcade (games proxied from another site), which is gone.

- A game is "installed" by being in GAMES; there is no table and no admin form.
- Its files are static, under web/static/games/<slug>/ (rules.js, game.js, game.css), with the shared
  web/static/games/common.js and common.css. web/games.py serves its page at /media/games/<slug>.
- They are Mnemerynne's own reviewed code, so they run on its origin: Media's game layer frames the page, and the
  TV page hands a remote panel's arrows and Enter to it (media.js games.key).
- Who may play: whoever may use Media, and a paired screen (the TV) as its owner, like the rest of Media.
- Scores stay on the device that played (the browser's storage): nothing is sent or worth anything.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class Game:
    slug: str           # its address (/media/games/<slug>) and its folder under static/games
    name: str
    icon: str           # an emoji, shown on its card and in the game bar
    description: str    # one line for its card
    controls: str       # how to play with the TV remote's arrows and Enter (or a keyboard)


GAMES: tuple[Game, ...] = (
    Game(slug="prism-drop", name="Prism Drop", icon="🔮",
         description="Pick a pocket and drop a neon ball through blocks, bombs and bonus pegs. Ten drops, best score wins.",
         controls="◀ ▶ pick a pocket · Enter drops · ▲ replays the last drop · ▼ sound on or off"),
)


def get(slug: str) -> Game | None:
    """The game with this slug, or None."""
    return next((g for g in GAMES if g.slug == slug), None)


def listing() -> list[dict]:
    """What Media → Games shows, in order."""
    return [asdict(g) for g in GAMES]
