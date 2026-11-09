# Contributing to the built-in games

The current repository is Mnemerynne/Erynne. Built-in games open from Media → Games and the set-top-box Media page.

- Register games in `mnemerynne/games.py`. The `multiplayer` flag selects the common casino template and assets; Prism Drop retains its own files.
- Keep authoritative rules in `mnemerynne/casino.py`. API requests contain intentions only. `web/games.py` derives each seat from a signed-in account or authenticated paired screen and applies Media's existing access checks.
- `casino_rooms` (migration 0061) persists full private state. Always use the public snapshot builder for responses: never expose the deck, actor identifiers or hidden dealer card. Mutations run under `BEGIN IMMEDIATE`, and client revisions prevent duplicate moves.
- Coins go through `mnemerynne/coins.py` only, inside the caller's transaction. A casino seat with an `account` plays from that purse: `room()` loads the balance into `chips`, the rules run unchanged, and `_pay_purses` writes the difference. Couch tables have no accounts and use table-only credits. Never accept an amount or score from a browser: Prism Drop's rules live in `mnemerynne/prism.py` for this reason, and any new reward must be something the server saw.
- Keep server text in `textContent`, scripts in static files, and controls usable through `MGame.onAction`. Preserve focus during polling, reduced-motion support, responsive layouts and remote state reports.
- Casino table controls are direct: Left/Right picks, Up/Down adjusts the stake by ten, Enter submits, and Play starts for the host. `common.js` maps media keys and P to `play`; `media.js` routes the remote's existing `toggle` command into the game. Lobby arrows and explicit keyboard Tab navigation retain button access. Do not turn table controls back into focus traversal.
- `static/games/casino/roulette.js` owns the persistent SVG wheel, its 37-pocket geometry and ball landing. Reuse it across revisions, honor reduced motion, stop its animation when hidden or destroyed, and always derive the landing from the server result.
- Slots store three horizontal rows and their separate returns. `bet` is per line; use the round's `slot_lines` when reserving, replacing, validating or showing a stake and when computing net results. `wager_lines()` treats pre-update rounds without that field as one line until the next reset. Preserve their original charge and settlement.
- Add explanatory comments around rules, lifecycle transitions and identity handling. Put standalone Python utilities in `python/` and PowerShell utilities in `ps/`.

There is no `game_editor_gui.py` in this repository. These games use the existing registry, templates and rule constants; no separate modding GUI is shipped by this change. Update the player/help and tuning/testing guides when those interfaces change.

See [Testing](TESTING_GUIDE.md) and [Tuning](GENERATION_TUNING_GUIDE.md).

## Lumi Pet

`static/games/lumi-pet/rules.js` owns the pure save/clock rules; `game.js` handles controls and storage, `game.css` styles the native SVG companion in `templates/games/lumi-pet.html`. It uses the existing non-multiplayer game template and Media access guard. Saves belong to this browser profile, not an account, and never leave the device; only the day's first visit reaches the server, which takes no numbers from the page. Use explanatory inline comments and preserve the no-neglect design: absence cannot reduce happy moments or cause distress. There is no game editor GUI in this repository; these assets and the tuning guide are the editing interface. The ASCII neon fireplace is a design-list item only.

## One TV, multiple casino remotes

The casino lobby on a paired, non-speaker TV offers **One TV, multiple remotes**. It creates a display-only host surface, leaving all eight player slots available. Each paired remote's first Enter joins its own seat; subsequent keys affect only its persisted `controls` draft. The first player is host. Normal account/device tables and guests joining their code retain the original controls.

`casino_remote.py` signs the authenticated panel's exact key for the destination screen and current device pairings. `web/media.py` adds this proof to press/Play commands; `media.js` and `games/common.js` preserve it through the iframe. `web/games.py` verifies signature, 90-second expiry, current pairings, ownership and screen access before `casino.couch()` processes it. Never trust a browser-supplied remote ID or use a remote name as identity. Device fingerprints and input receipts stay out of public snapshots.

`casino.couch()` serializes each key, draft, join and bet in one database transaction. Receipts suppress replay across TV refreshes, duplicate stream listeners and workers. Round/phase checks prevent a delayed betting Enter from becoming Hit. The client queues remote inputs independently of the ordinary button busy flag. Keep the viewer spectator-only, show every player's draft/hand/results, and preserve host transfer after explicit seat removal. There is no new schema migration: display and controller metadata live in the existing room JSON.

## TV game scaling

`web/app.py` gives built-in game iframe requests the TV's text-size context, with zero extra underscan and no kiosk toolbar/listener. Other embedded pages retain their existing behavior. Games reuse `erynne.css`'s root `--ui-scale`, including automatic 1×/1.5×/2× sizing and saved manual sizes. `media.js` sends the current scale on iframe load, window resize and live Text size adjustments; `games/common.js` accepts it only from its same-origin parent on a TV, then asks canvas games to resize. Use rem for layout caps (casino 100rem; Lumi Pet 112.5rem wide, room 47.5rem tall) so controls and artwork grow together. Keep SVG viewBox/canvas world coordinates fixed; redraw canvas pixels from its CSS size and device pixel ratio.
