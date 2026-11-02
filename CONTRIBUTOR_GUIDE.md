# Contributing to the built-in games

The current repository is Mnemerynne/Erynne. Built-in games open from Media → Games and the set-top-box Media page.

- Register games in `mnemerynne/games.py`. The `multiplayer` flag selects the common casino template and assets; Prism Drop retains its own files.
- Keep authoritative rules in `mnemerynne/casino.py`. API requests contain intentions only. `web/games.py` derives each seat from a signed-in account or authenticated paired screen and applies Media's existing access checks.
- `casino_rooms` (migration 0061) persists full private state. Always use the public snapshot builder for responses: never expose the deck, actor identifiers or hidden dealer card. Mutations run under `BEGIN IMMEDIATE`, and client revisions prevent duplicate moves.
- Keep server text in `textContent`, scripts in static files, and controls usable through `MGame.onAction`. Preserve focus during polling, reduced-motion support, responsive layouts and remote state reports.
- Casino table controls are direct: Left/Right picks, Up/Down adjusts ten chips, Enter submits, and Play starts for the host. `common.js` maps media keys and P to `play`; `media.js` routes the remote's existing `toggle` command into the game. Lobby arrows and explicit keyboard Tab navigation retain button access. Do not turn table controls back into focus traversal.
- `static/games/casino/roulette.js` owns the persistent SVG wheel, its 37-pocket geometry and ball landing. Reuse it across revisions, honor reduced motion, stop its animation when hidden or destroyed, and always derive the landing from the server result.
- Add explanatory comments around rules, lifecycle transitions and identity handling. Put standalone Python utilities in `python/` and PowerShell utilities in `ps/`.

There is no `game_editor_gui.py` in this repository. These games use the existing registry, templates and rule constants; no separate modding GUI is shipped by this change. Update the player/help and tuning/testing guides when those interfaces change.

See [Testing](TESTING_GUIDE.md) and [Tuning](GENERATION_TUNING_GUIDE.md).
