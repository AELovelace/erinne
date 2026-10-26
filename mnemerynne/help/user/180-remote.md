# The Remote tab

**Remote** shows web pages that normally live only on the organisation's own network: a monitoring dashboard, the balancer's control page, a NAS, a server console. Erynne's server fetches them for you, so you can open them from wherever you can sign in to Erynne. The tab only appears when an admin has listed at least one resource you may open.

## The window

The tab is a small browser. A window sits in the middle of the page with a margin around it, like a terminal:

- It opens on the **home page**: a card for each resource you may open, with a short description and **Open**.
- Opening a resource loads it inside the window. The title bar shows its name and address, with **Home** (⌂), **Reload** (↻), **Open in a new tab** (⧉) and **Fullscreen** (⛶, which removes the margin; press it again to bring the margin back).
- Some resources are marked to open in a new tab instead of the window; their card and bar entry say so.

The address bar follows what you open (`/remote#balancer`), so you can bookmark a resource or send a colleague the link; they need access to it too.

## The bar on the right

A slim **Resources** handle sits on the right edge. Click it (or press `R` when nothing is focused) and the list drops down: Home and every resource, with the current one marked. Pick one and the bar folds away; press Escape to close it. The bar remembers whether you left it open.

## Signing in

Resources use your Erynne sign-in: if you open a resource's address while signed out, you sign in and land back on it. The resource itself may have its own sign-in on top; Erynne never passes your Erynne password to it.

## What to expect

- The page inside the window is the resource's own; what you do there is up to that system, and Erynne doesn't log it beyond who opened what.
- Resources that were not written to live under another address can misbehave (links that lead nowhere, missing images). Tell your admin; they can switch on link rewriting for it, or open it in a new tab instead.
- If a resource "didn't answer", the server behind it is down or unreachable from Erynne's server, not a problem with your account.
