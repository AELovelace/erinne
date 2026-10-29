# Games

**Access → Settings → Entertainment → Games** puts games from another site (Lumi Web, Lumi Games, DOLL-OS…) into **Media → Games**. They are served from the **arcade**: an address of its own (`MNEMERYNNE_ARCADE_URL`, e.g. `https://arcade.beepsquad.wtf`) where the games site is proxied at the root. Nothing of Mnemerynne's exists on that address (no pages, no API, no connector), so a game's scripts can't reach Mnemerynne or anyone's session there.

## Setting it up

1. **The arcade's address:** a DNS name and a certificate for it in front of the box, and `MNEMERYNNE_ARCADE_URL` in the env file. It must be a different host from `MNEMERYNNE_PUBLIC_URL`; the app won't start otherwise. The deploy README (section 5l) has the web-server blocks.
2. **The games site:** the whole site, without a path, e.g. `https://sadgirlsclub.wtf`. `/lumibot/` on the arcade is `/lumibot/` on the site. The site has to accept being served from the arcade's address (its sign-in callbacks and trusted origins list it).
3. **Who may play:** AD or Entra group(s), separated by `;`. They need Media too. Empty: admins only.
4. **Games are on:** off hides Media → Games, and the arcade answers nothing.

## The games

Each game is a page on the site with a **name**, an **icon** (an emoji or a few letters), a short **description** and its **start page** (e.g. `/lumibot/`, `/games/`, `/dapp-web/emulator.html`). Media shows them in this list's order.

- **↑ ↓** reorder; **Edit** loads a game into the form below the list (its address in Media stays the same); **Turn off** hides it without removing it; **Remove** deletes it.
- **Test** asks the site for the start page as the arcade would, and shows the answer: a redirect to the game's own sign-in (e.g. `HTTP 302 → /lumibot/login…`) is a good sign; a refusal or no answer means the site, or the way to it, needs looking at.
- Every change is in the access log (`games.add`, `games.change`, `games.remove`, `settings.games`).

## How people get in

Media asks the server for a one-use ticket (a minute) for that game; the arcade spends it and sets its own sign-in cookie for 12 hours. Every request to the arcade checks again that the person is active, still signed in to Mnemerynne (signing out ends it) and still allowed to play (within a minute).

- **Screens (TVs)** play as their owner, never as an admin, and only while they are paired: removing a screen closes the arcade on it. A cast from a phone closes the game.
- **Admins** can always play from their own browser, even with no group set.
- The games' own sign-in (Discord for the Lumi games) happens inside the game and stays on the arcade's address.
