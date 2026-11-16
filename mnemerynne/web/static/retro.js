/* Media → Retro's library (templates/retro.html, web/retro.py /api/media/retro/games).
 *
 * Pages, flipped with a controller's LB / RB (retro-tv.js), the [ and ] or Page Up / Page Down keys, or the tabs:
 *  - ⭐ Top (Doll's redesign, 2026-11-15): Continue playing (the latest games this person played), then a row per
 *    console of its 50 most popular games on the share, scrolling left to right. Popularity is how many people
 *    played the game on RetroAchievements (media/popularity.py, "players"); games it doesn't know come after, A–Z.
 *    A row's last card opens that console's page.
 *  - a page per console (that has games): all of its games, A–Z.
 * A search looks through every console at once. A game is its box art (a picture beside the ROM with the same
 * name) or a cartridge drawn in the console's colours, and opens the player. The whole list comes in one answer.
 * The page shown is kept in the address (#nes), so coming back from a game lands on it again.
 */
(function () {
  "use strict";

  const $ = function (id) { return document.getElementById(id); };
  const root = $("rt");
  const RECENT = 12;             // games on the Continue playing row
  const TOP = 50;                // games on each console's row of the Top page
  let all = [], consoles = [], hasLibrary = true;
  let page = "top";              // "top", or a console's key

  function el(tag, cls, text) {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function showError(text) { const e = $("rt-error"); e.textContent = text || ""; e.hidden = !text; }

  function systemName(key) {
    const system = consoles.find(function (c) { return c.key === key; });
    return system ? system.name : key;
  }

  function players(n) {   // 12345 -> "12k", 1234 -> "1.2k"
    if (n >= 10000) return Math.round(n / 1000) + "k";
    if (n >= 1000) return (Math.round(n / 100) / 10) + "k";
    return String(n);
  }

  function card(game, opts) {   // one game: its art, title and console (or player count), opening the player
    const a = el("a", "rt-game");
    a.href = "/media/retro/play/" + game.id;
    a.dataset.console = game.console;
    a.title = game.name;
    const art = el("div", "rt-art");
    if (game.cover) {
      const img = el("img");
      img.src = game.cover; img.alt = ""; img.loading = "lazy";
      art.append(img);
    } else {   // a cartridge with the title on its label (a disc, for the PS1: retro.css)
      art.classList.add("rt-cart");
      art.append(el("span", "rt-label", game.title));
    }
    if (opts && opts.rank) art.append(el("span", "rt-rank", "#" + opts.rank));
    if (game.needs_bios) art.append(el("span", "rt-badge rt-badge-warn", "Needs a BIOS"));   // the player says which
    else if (game.can_continue) art.append(el("span", "rt-badge", "▶ Continue"));
    else if (game.has_save) art.append(el("span", "rt-badge", "💾 Saved"));
    const words = el("div", "rt-words");
    words.append(el("strong", "rt-name", game.title));
    const under = opts && opts.sameConsole ? "" : systemName(game.console);
    const count = typeof game.players === "number" ? "👥 " + players(game.players) : "";
    words.append(el("span", "muted small", [under, count].filter(Boolean).join(" · ") || " "));
    if (count) words.lastChild.title = game.players + " people played it on RetroAchievements";
    a.append(art, words);
    return a;
  }

  function byPopularity(a, b) {   // most played first; unranked ones after, A–Z
    const pa = typeof a.players === "number" ? a.players : -1, pb = typeof b.players === "number" ? b.players : -1;
    if (pa !== pb) return pb - pa;
    return a.title.localeCompare(b.title, undefined, { sensitivity: "base" });
  }

  function byTitle(a, b) { return a.title.localeCompare(b.title, undefined, { sensitivity: "base" }) || a.name.localeCompare(b.name); }

  function head(title, count) {   // a section's heading: its name first (tests read firstChild), then the count
    const h = el("h2", "rt-shelf-title", title);
    if (count !== undefined) h.append(el("span", "muted small", " " + count));
    return h;
  }

  function row(title, games, opts) {   // a row scrolling left to right (the Top page)
    const section = el("section", "rt-shelf");
    section.append(head(title, opts && opts.count));
    const strip = el("div", "rt-row");
    games.forEach(function (g, i) { strip.append(card(g, { rank: opts && opts.ranked ? i + 1 : 0, sameConsole: opts && opts.sameConsole })); });
    if (opts && opts.more) strip.append(opts.more);
    section.append(strip);
    return section;
  }

  function grid(title, games, opts) {   // a grid of every game (a console's page, search results)
    const section = el("section", "rt-shelf");
    section.append(head(title, games.length));
    const g = el("div", "rt-grid");
    games.forEach(function (game) { g.append(card(game, opts)); });
    section.append(g);
    return section;
  }

  function seeAll(c, total) {   // a row's last card: that console's page
    const b = el("button", "rt-game rt-more");
    b.type = "button";
    b.dataset.console = c.key;
    const art = el("div", "rt-art rt-more-art");
    art.append(el("span", "rt-more-arrow", "→"));
    const words = el("div", "rt-words");
    words.append(el("strong", "rt-name", "All " + total), el("span", "muted small", c.name + " A–Z"));
    b.append(art, words);
    b.addEventListener("click", function () { show(c.key, true); });
    return b;
  }

  function empty() {   // nothing at all: where games go, for whoever fills the folder
    const box = el("div", "rt-empty");
    const folder = root.dataset.folder;
    if (!hasLibrary) {
      box.append(el("p", "", "The " + folder + " folder isn't open to you on the share."));
      return box;
    }
    box.append(el("p", "", "No games yet."));
    const how = el("p", "muted");
    how.textContent = "Put your own ROMs in the " + folder + " folder on the share, one folder per console (" +
      consoles.map(function (c) { return c.folder; }).join(", ") + "), or drop them in its Unsorted folder and they " +
      "find their own way. A picture with the same name beside a game is its box art. Games appear after the next " +
      "share scan.";
    box.append(how);
    return box;
  }

  function pages() {   // the page order LB / RB go through: Top, then each console that has games
    const keys = ["top"];
    consoles.forEach(function (c) { if (all.some(function (g) { return g.console === c.key; })) keys.push(c.key); });
    return keys;
  }

  function tabs() {   // the tab per page, the current one marked
    const nav = $("rt-pages");
    nav.replaceChildren();
    if (!all.length) { nav.hidden = true; return; }
    nav.hidden = false;
    nav.append(el("span", "rt-shoulder", "LB"));
    pages().forEach(function (key) {
      const b = el("button", "rt-tab", key === "top" ? "⭐ Top" : systemName(key));
      b.type = "button";
      b.dataset.page = key;
      if (key === page) b.setAttribute("aria-current", "page");
      b.addEventListener("click", function () { show(key, false); });
      nav.append(b);
    });
    nav.append(el("span", "rt-shoulder", "RB"));
  }

  function draw() {
    const q = $("rt-q").value.trim().toLowerCase();
    const content = $("rt-content");
    content.replaceChildren();
    if (!pages().includes(page)) page = "top";
    tabs();
    if (!all.length) { content.append(empty()); return; }
    if (q) {   // a search: every console at once, A–Z
      const found = all.filter(function (g) { return g.title.toLowerCase().includes(q) || g.name.toLowerCase().includes(q); });
      if (!found.length) { content.append(el("p", "muted", "No game matches.")); return; }
      content.append(grid("Found", found.slice().sort(byTitle)));
      return;
    }
    if (page !== "top") {   // a console's page: all of its games, A–Z
      const here = all.filter(function (g) { return g.console === page; }).sort(byTitle);
      content.append(grid(systemName(page), here, { sameConsole: true }));
      return;
    }
    const recent = all.filter(function (g) { return g.played_at; })
      .sort(function (a, b) { return a.played_at < b.played_at ? 1 : -1; }).slice(0, RECENT);
    if (recent.length) content.append(row("Continue playing", recent));
    consoles.forEach(function (c) {
      const here = all.filter(function (g) { return g.console === c.key; });
      if (!here.length) return;
      const top = here.slice().sort(byPopularity).slice(0, TOP);
      const ranked = top.some(function (g) { return typeof g.players === "number"; });
      content.append(row(c.name, top, { ranked: ranked, sameConsole: true, count: here.length,
                                        more: here.length > top.length || here.length > 6 ? seeAll(c, here.length) : null }));
    });
  }

  // Show a page. `focusIt`: put the focus on its first game (a controller flipping pages keeps its place on the page).
  function show(key, focusIt) {
    page = key;
    try { history.replaceState(null, "", key === "top" ? location.pathname + location.search : "#" + key); } catch (e) { /* framed oddly */ }
    if ($("rt-q").value) $("rt-q").value = "";   // a page is a page: the search is cleared
    draw();
    window.scrollTo(0, 0);
    if (focusIt) {
      const first = $("rt-content").querySelector(".rt-game");
      if (first) first.focus({ preventScroll: true });
    }
  }

  // LB / RB (and the keys): the page before or after, round the end.
  function flip(step) {
    if (!all.length) return;
    const order = pages();
    const at = Math.max(0, order.indexOf(page));
    const inside = document.activeElement && $("rt-content").contains(document.activeElement);
    show(order[(at + step + order.length) % order.length], inside || document.activeElement === document.body);
  }

  async function load() {
    try {
      const response = await fetch("/api/media/retro/games", { credentials: "same-origin", cache: "no-store" });
      if (!response.ok) throw new Error("the server answered " + response.status);
      const data = await response.json();
      all = data.games; consoles = data.consoles; hasLibrary = data.library;
      showError("");
      const wanted = decodeURIComponent((location.hash || "").slice(1));
      if (wanted) page = wanted;
      draw();
    } catch (err) {
      showError("Couldn't load the games: " + err.message);
      $("rt-content").replaceChildren();
    }
  }

  $("rt-q").addEventListener("input", draw);
  document.addEventListener("keydown", function (e) {   // [ ] and Page Up / Down flip pages (not while typing)
    if (e.ctrlKey || e.altKey || e.metaKey) return;
    const typing = /^(INPUT|SELECT|TEXTAREA)$/.test((e.target && e.target.tagName) || "");
    if (typing) return;
    if (e.key === "[" || e.key === "PageUp") { e.preventDefault(); flip(-1); }
    else if (e.key === "]" || e.key === "PageDown") { e.preventDefault(); flip(1); }
  });
  window.RetroLibrary = { flip: flip, show: show, page: function () { return page; } };   // retro-tv.js: LB / RB
  load();
})();
