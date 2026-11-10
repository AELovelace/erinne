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
  checked. Breakout and Snake cost a run's entry and pay its checked score (arcade.py), solo or in showdowns (showdown.py). Prism Drop's best score stays on the device. Lumi Pet lives on the account (lumipet.py), where her shop
  spends coins. Coins have no cash value.
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
    track_keys: bool = False   # takes a remote's Previous and Next buttons itself (media.js); otherwise they stay the music's
    shelf: str = "Arcade"   # the heading it is listed under in Media → Games; shelves appear in the order their first game does
    mouse_keyboard: bool = False   # can't be played with a remote or touch: listed under "Mouse & keyboard", and only where there's a mouse (media.js viewGames)
    extras: tuple[str, ...] = ()   # more files under static/games/ its page loads before game.js (.css and .js), e.g. a shared shell


GAMES: tuple[Game, ...] = (
    Game(slug="lumi-pet", name="Lumi Pet", icon="✨",
         description="A little neon companion to feed, dress, fight beside and make a home for. She gets hungry, sleepy and lonely while you’re away. Your first visit each day pays coins.",
         controls="⏮ ⏭ change tab · ▲ ▼ choose · ◀ ▶ options · Enter does it · in the dungeon: arrows walk, ⏮ ⏭ choose, ⏯ potion", track_keys=True,
         shelf="Lumi Pet"),                               # a shelf of her own, first on the page
    Game(slug="prism-drop", name="Prism Drop", icon="🔮",
         description="Pick a pocket and drop a neon ball through blocks, bombs, bonus pegs and sliding platforms. Ten drops, and your score pays coins.",
         controls="◀ ▶ pick a pocket · Enter drops · ▲ replays the last drop · ▼ sound on or off"),
    # Ported from Doll's GameMaker game (python/export_stickbob.py makes its art and levels). It runs in the browser and
    # pays coins for what the page reports, checked against the run's time (runner.py), with an optional daily cap.
    Game(slug="stickbob-runner", name="StickBob Runner", icon="🏃",
         description="Run, jump, slide and shoot through StickBob's world for as long as you can last. Every 10 m and every kill pays a coin.",
         controls="A D run · W or Space jump · S slide · mouse aims · left click shoots · right click kicks · F knife · P pause · M sound",
         mouse_keyboard=True),
    # Ported from Doll's GameMaker arcade (python/export_roulette_arcade.py copies the art and sounds). Both share one shell
    # (games/arcade/): solo runs and showdown tables, where two race and five can watch and bet (arcade.py, showdown.py).
    Game(slug="breakout", name="Breakout", icon="🧱",
         description="Break every brick with a neon ball: catch power-ups, juggle multiball, clear level after level. Race someone in a showdown while others bet. 25 coins a run; every brick pays one.",
         controls="◀ ▶ move the bat · Enter or ▲ launches · P pauses · menus: arrows and Enter",
         extras=("arcade/arcade.css", "arcade/arcade.js")),
    Game(slug="snake", name="Snake", icon="🐍",
         description="Eat and grow without hitting the walls or yourself. Red is 1, yellow 5, aqua 10. Race someone in a showdown while others bet. 25 coins a run; every point pays one.",
         controls="Arrows turn · the first arrow starts · P pauses · menus: arrows and Enter",
         extras=("arcade/arcade.css", "arcade/arcade.js")),
    # Our own climber in the same shell (Doll's idea, 2026-11-10), with Lumi Pet as the climber.
    Game(slug="teacup-tower", name="Teacup Tower", icon="🫖",
         description="Lumi bounces up an endless tower of teacups: steer her, run at the walls to fly higher, string big hops into combos, and keep ahead as the tower rises. Race someone in a showdown. 25 coins a run; every floor pays one.",
         controls="◀ ▶ steer · she bounces by herself · faster sideways jumps higher · P pauses · menus: arrows and Enter",
         extras=("arcade/arcade.css", "arcade/arcade.js")),
)

# Casino tables share one renderer and server rules; each still has its own Games card.
GAMES += tuple(Game(slug=slug, name=name, icon=icon, description=description,
                    controls="◀ ▶ pick · ▲ ▼ bet · Enter sets bet · Play starts round (host) · P on keyboard",
                    multiplayer=True, shelf="Table games") for slug, name, icon, description in (
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
