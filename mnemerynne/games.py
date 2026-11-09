"""Built-in games: small games of our own that ship with Mnemerynne and open from Media → Games
(docs/stretch-goals/built-in-games.md). They replace the arcade (games proxied from another site), which is gone.

- A game is "installed" by being in GAMES; there is no table and no admin form.
- Its files are static, under web/static/games/<slug>/ (the casino games share games/casino/), with the shared
  web/static/games/common.js and common.css. web/games.py serves its page at /media/games/<slug>.
- They are Mnemerynne's own reviewed code, so they run on its origin: Media's game layer frames the page, and the
  TV page hands a remote panel's arrows and Enter to it (media.js games.key).
- Who may play: whoever may use Media, and a paired screen (the TV) as its owner, like the rest of Media.
- Coins are one purse per account, shared by every game and kept on this server (coins.py). Online casino tables bet them,
  local multiplayer tables use free-to-play credits instead, and Prism Drop and Lumi Pet pay small rewards the server has
  checked. Prism Drop's best score and Lumi Pet's memories stay on the device. Coins have no cash value.
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
    multiplayer: bool = False


GAMES: tuple[Game, ...] = (
    Game(slug="lumi-pet", name="Lumi Pet", icon="✨",
         description="A little neon companion. Share a snack, play, cuddle or curl up for a nap. Your first visit each day pays coins.",
         controls="◀ ▶ choose · Enter interact · ▲ glow colour · ▼ sound on or off"),
    Game(slug="prism-drop", name="Prism Drop", icon="🔮",
         description="Pick a pocket and drop a neon ball through blocks, bombs, bonus pegs and sliding platforms. Ten drops, and your score pays coins.",
         controls="◀ ▶ pick a pocket · Enter drops · ▲ replays the last drop · ▼ sound on or off"),
)

# Casino tables share one renderer and server rules; each still has its own Games card.
GAMES += tuple(Game(slug=slug, name=name, icon=icon, description=description,
                    controls="◀ ▶ pick · ▲ ▼ bet · Enter sets bet · Play starts round (host) · P on keyboard",
                    multiplayer=True) for slug, name, icon, description in (
    ("blackjack", "Blackjack", "🂡", "Share a table, hit or stand, and try to beat the dealer. Plays for coins online."),
    ("roulette", "Roulette", "🎡", "Pick a number or an outside bet and watch the wheel together. Plays for coins online."),
    ("horse-racing", "Horse Racing", "🏇", "Back one of six horses and cheer on the same race. Plays for coins online."),
    ("slots", "Slot Machines", "🎰", "Three reels, three horizontal paylines. Spin together and celebrate each winning line. Plays for coins online."),
))


def get(slug: str) -> Game | None:
    """The game with this slug, or None."""
    return next((g for g in GAMES if g.slug == slug), None)


def listing() -> list[dict]:
    """What Media → Games shows, in order."""
    return [asdict(g) for g in GAMES]
