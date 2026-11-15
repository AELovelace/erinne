/* Media → Retro's shelves (templates/retro.html, web/retro.py /api/media/retro/games).
 *
 * Continue playing on top (the latest games this person played), then a shelf per console. A game is its box
 * art (a picture beside the ROM with the same name) or a cartridge drawn in the console's colours, and opens the
 * player. The search and the console picker filter in the browser: the whole list comes in one answer.
 */
(function () {
  "use strict";

  const $ = function (id) { return document.getElementById(id); };
  const root = $("rt");
  const RECENT = 8;              // games on the Continue playing shelf
  let all = [], consoles = [], hasLibrary = true;

  function el(tag, cls, text) {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function showError(text) { const e = $("rt-error"); e.textContent = text || ""; e.hidden = !text; }

  function card(game) {   // one game: its art, title and console, opening the player
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
    if (game.needs_bios) art.append(el("span", "rt-badge rt-badge-warn", "Needs a BIOS"));   // the player says which
    else if (game.can_continue) art.append(el("span", "rt-badge", "▶ Continue"));
    else if (game.has_save) art.append(el("span", "rt-badge", "💾 Saved"));
    const words = el("div", "rt-words");
    words.append(el("strong", "rt-name", game.title));
    const system = consoles.find(function (c) { return c.key === game.console; });
    words.append(el("span", "muted small", system ? system.name : game.console));
    a.append(art, words);
    return a;
  }

  function shelf(title, games) {
    const section = el("section", "rt-shelf");
    const head = el("h2", "rt-shelf-title", title);
    head.append(el("span", "muted small", " " + games.length));
    const grid = el("div", "rt-grid");
    games.forEach(function (g) { grid.append(card(g)); });
    section.append(head, grid);
    return section;
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
      consoles.map(function (c) { return c.folder; }).join(", ") + "). A picture with the same name beside a game " +
      "is its box art. Games appear after the next share scan.";
    box.append(how);
    return box;
  }

  function draw() {
    const q = $("rt-q").value.trim().toLowerCase();
    const only = $("rt-console").value;
    const games = all.filter(function (g) {
      return (!only || g.console === only) && (!q || g.title.toLowerCase().includes(q) || g.name.toLowerCase().includes(q));
    });
    const content = $("rt-content");
    content.replaceChildren();
    if (!all.length) { content.append(empty()); return; }
    if (!games.length) { content.append(el("p", "muted", "No game matches.")); return; }
    if (!q) {   // Continue playing: only while not searching, so a search shows each game once
      const recent = games.filter(function (g) { return g.played_at; })
        .sort(function (a, b) { return a.played_at < b.played_at ? 1 : -1; }).slice(0, RECENT);
      if (recent.length) content.append(shelf("Continue playing", recent));
    }
    consoles.forEach(function (c) {
      const here = games.filter(function (g) { return g.console === c.key; });
      if (here.length) content.append(shelf(c.name, here));
    });
  }

  async function load() {
    try {
      const response = await fetch("/api/media/retro/games", { credentials: "same-origin", cache: "no-store" });
      if (!response.ok) throw new Error("the server answered " + response.status);
      const data = await response.json();
      all = data.games; consoles = data.consoles; hasLibrary = data.library;
      showError("");
      draw();
    } catch (err) {
      showError("Couldn't load the games: " + err.message);
      $("rt-content").replaceChildren();
    }
  }

  $("rt-q").addEventListener("input", draw);
  $("rt-console").addEventListener("change", draw);
  load();
})();
