# Themes

**Access → Settings → Themes** (the full-width section under the two columns) is where the box's looks are kept. Every look is a *theme*; people pick one under Preferences from the themes you have turned on, unless you set one theme for everyone.

## Themes and bases

A theme starts from one of three **bases**, the stylesheets shipped with the app:

- **Sakura**: blossoms over Mt Fuji, following the device's light or dark mode.
- **Neon**: hot pink on black, chromatic fringes and bloom, hard edges, drifting cities. Always dark.
- **Plain**: nothing behind the pages; the brand's own colours, gradients and background. Light and dark.

Four themes come built in: **Sakura** and **Neon** (stock, on), **Corporate** (the plain base with a neutral blue palette, off until you turn it on) and **Sad Girls Club** (white on black, deep pink, a VCR font, ridge borders, scanlines, flicker and text jitter: the look of sadgirlsclub.wtf on the neon base; off until you turn it on). You can edit a built-in like any other theme; **Reset to stock** takes it back to how it shipped.

**New theme from** Sakura, Neon or Plain makes your own; **Duplicate** copies any theme, built-in or not. Each card has two switches: **On** (off: nobody gets it, and it can't be the default) and **People may pick it** (off: only reachable as the default or the enforced theme).

## Who gets what

- **Default theme**: new accounts, people who never chose, and the sign-in page in a browser that hasn't signed in before.
- **Who gets it**: *People choose from the themes that are on*, or *Everyone gets* one theme. Enforcing a theme hides the picker on Preferences and overrides what people chose; turning it back to *People choose* restores their choices.

Pages remember the theme per account and in a cookie, so the sign-in page matches the last person who used that browser.

## The editor

**Edit** opens a theme. With **Preview on this page** ticked (the default), every change is drawn on the Access page itself as you go, saved or not; **Save** keeps it, **Revert** drops the draft, **‹ Back** leaves the editor. Only what differs from the base is stored, so a theme you save without changing stays stock.

- **Scene**: what is behind the pages: the base's own (blossoms or cities), nothing, or a **background picture** you upload (JPEG, PNG or WebP up to 8 MB; it is re-encoded and shrunk to 2560 px). The *tint* slider darkens or lightens the picture under the pages.
- **Palette**: light and dark (one palette on the neon base, which is always dark). The tokens, in plain words: **Page** (behind everything), **Primary** (buttons, links, the active tab) and its **hover**, **Text** and **Muted text**, **Surface** and **Surface, strong** (panels; these can be see-through), **Lines** (borders), **Highlight** and **Highlight, soft** (selected rows, hovered tabs), **Error**, and the **OK / Bad / Busy** pairs (status badges). *Derive the rest from Primary* fills the hover, highlights, lines and surfaces from one colour; *Copy light → dark* flips the light palette for the dark one. Neon-based themes also have the **Neon extras**: the second colour, the two fringes and the glow.
- **Gradients**: the page background, the top bar and the buttons are each solid or a gradient of two to four stops with an angle, per palette. Text on a gradient bar or button is white when the gradient is dark and the palette's text colour when it is light.
- **Shape and type**: corner radius, frosted glass or flat surfaces, borders (soft, hard 2 px or ridge), a **panel texture** (a small tile drawn under panels and the top bar), and the heading and body fonts: system font stacks, the bundled VCR OSD Mono, or a `.woff2` you upload for either.
- **Effects**: an old monitor's tricks over every page: **scanlines**, **flicker**, **text jitter** (headings carry a blue and a red shadow that wander) and **pulse** (the page breathes between two colours). **Intensity** scales them all. They stop for anyone whose device asks for reduced motion.
- **Advanced CSS**: appended last to the theme's stylesheet, for what the controls don't cover (20 KB at most; `@import` is refused). It is served as part of the theme, same-origin, so treat it like configuration: whoever can edit themes can style every page.

## Theme packs

**Download** on a card saves the theme as `<slug>.theme.json`: its name, base, settings and its own files (background, texture, fonts) inline. **Upload a theme pack…** under *New theme from* makes a theme from one, so a look made on one box can move to another or be shared. A pack never holds the branding (that is the organisation's) and is checked like any upload: the same size and type limits apply.

## Branding

Branding is organisation-wide and shows on every theme:

- **Product name**: in the top bar, the sign-in card, page titles and the home-screen app, and how the box introduces itself to Claude and ChatGPT through the connector ([Claude](claude.md#which-mnemerynne-this-is)). The assistant is still called Erynne in chat.
- **Sign-in tagline**: the line under the name on the sign-in card.
- **Logo** (PNG, JPEG, WebP or SVG up to 2 MB) with a **height** slider, replacing the 🌸; an optional **logo for dark surfaces** used on dark themes and in dark mode. SVGs with scripts, event handlers or outside links are refused, and they are served sandboxed.
- **Mark**: a square PNG for the browser tab's icon and the home-screen icons (192, 512 and a maskable 512 on the default theme's page colour, 180 for iOS). Without one the 🌸 and the built-in icons stay.

## Behind the scenes

The themes live in the `themes` table, their files under `data/theme/<slug>/` and the brand's under `data/theme/brand/`. Each theme's stylesheet is generated at `/theme/<slug>.css?v=<version>` and cached by browsers for a year; every save bumps the version, so people see the change on their next page. Every change is logged under Recent changes as `settings.theme` or `settings.brand`.
