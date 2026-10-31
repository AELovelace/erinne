/* Shared table client: renders server snapshots and sends intentions, never outcomes or balances. */
(function () {
  "use strict";
  const $ = id => document.getElementById(id), game = $("gm").dataset.game, M = window.MGame;
  const base = "/api/media/games/" + encodeURIComponent(game) + "/rooms";
  const horses = ["Moonbeam", "Velvet Thunder", "Starlight", "Cherry Comet", "Lucky Clover", "Blue Ribbon"];
  const symbols = { cherry: "🍒", lemon: "🍋", bell: "🔔", diamond: "💎", clover: "🍀", star: "⭐" };
  const rules = {
    blackjack: "Beat the dealer without going over 21. Aces count as 1 or 11. Dealer stands on all 17s. Blackjack pays 3:2, wins 1:1, ties return your bet. Hit or stand only; no splits, doubles or insurance. Unfinished hands stand after 30 seconds.",
    roulette: "Single-zero wheel, 0–36. A straight number pays 35:1; red, black, odd, even, 1–18 and 19–36 pay 1:1. Zero loses all outside bets. One pick per player per round.",
    "horse-racing": "Six horses, each equally likely to win. Pick the winner for a 4:1 payout (5× returned including your stake). Everyone watches the same six-second race. Track movement is an animation; the server decides the finish.",
    slots: "Six equally likely symbols on each of three reels. Three stars return 24× your stake; any other triple returns 12×; any pair returns your stake. Everyone spins together, with separate reels per player.",
  };
  const choices = game === "roulette" ? [["red", "Red"], ["black", "Black"], ["odd", "Odd"], ["even", "Even"], ["low", "1–18"], ["high", "19–36"]].concat(Array.from({ length: 37 }, (_, n) => [String(n), String(n)])) :
    game === "horse-racing" ? horses.map((name, i) => [String(i), name]) : [["", ""]];
  let state = null, busy = false, refreshing = false, amount = 50, pick = 0, signature = "", offset = 0;
  let resume = null;
  try { resume = sessionStorage.getItem("casino-room:" + game); } catch (_) { /* A private browser can still play. */ }
  $("casino-rules").textContent = rules[game];
  $("casino-pick-controls").hidden = $("casino-pick-label").hidden = choices.length === 1;

  function node(tag, text, cls) { // All server-supplied names and results enter the page as text.
    const el = document.createElement(tag);
    if (text !== undefined) el.textContent = text;
    if (cls) el.className = cls;
    return el;
  }
  function error(message) { // Keep actionable errors visible without replacing the board.
    $("casino-error").textContent = message || "";
    $("casino-error").hidden = !message;
  }
  async function api(path, body) { // A bounded request prevents a lost connection from locking controls forever.
    const response = await fetch(base + path, { method: body === undefined ? "GET" : "POST", credentials: "same-origin",
      headers: body === undefined ? {} : { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body), signal: AbortSignal.timeout(10000), cache: "no-store" });
    const data = await response.json();
    if (!response.ok) {
      const failure = new Error(typeof data.detail === "string" ? data.detail : "This move could not be accepted.");
      failure.status = response.status;
      throw failure;
    }
    return data;
  }
  function remember(code) { // Refreshing a page resumes its existing seat instead of minting a new balance.
    try { if (code) sessionStorage.setItem("casino-room:" + game, code); else sessionStorage.removeItem("casino-room:" + game); } catch (_) { /* Optional storage. */ }
  }
  function mine() { return state && state.players.find(p => p.id === state.you); } // Resolve only this client's authenticated seat.
  function focusable() { // Match the controls visible to a TV remote, including native disabled and hidden states.
    return Array.from($("gm").querySelectorAll("button")).filter(b => !b.disabled && b.getClientRects().length);
  }
  function restoreFocus(id) { // Polling may hide an old action; preserve focus or move to the first usable action.
    const previous = $(id), buttons = focusable();
    if (previous && buttons.includes(previous)) previous.focus({ preventScroll: true });
    else if (!buttons.includes(document.activeElement) && buttons.length) buttons[0].focus({ preventScroll: true });
  }
  function controls() { // Enable only actions that the server can currently accept.
    const p = mine(), betting = state && state.phase === "betting";
    $("casino-betting").hidden = !betting;
    $("casino-hand").hidden = !(p && game === "blackjack" && state.phase === "playing" && p.bet && !p.stood);
    $("casino-amount").textContent = amount;
    $("casino-pick").textContent = choices[pick][1];
    $("casino-bet").textContent = p && p.bet ? "Update bet" : "Place bet";
    $("casino-bet").disabled = busy || !p || amount > p.chips + p.bet;
    $("casino-start").hidden = !state || state.you !== state.players[0].id;
    $("casino-start").disabled = busy || !state || !state.players.some(p => p.bet);
    $("casino-refill").hidden = !(p && p.chips < 10 && !p.bet);
    ["casino-create", "casino-leave", "casino-hit", "casino-stand", "casino-refill"].forEach(id => { $(id).disabled = busy; });
    $("casino-less").disabled = busy || amount <= 10;
    $("casino-more").disabled = busy || amount >= Math.min(500, p ? p.chips + p.bet : 500);
    ["casino-pick-prev", "casino-pick-next"].forEach(id => { $(id).disabled = busy; });
    document.querySelectorAll("[data-room]").forEach(b => { b.disabled = busy || b.dataset.full === "true"; });
  }
  function cards(hand) { // Decode the server's card IDs without exposing a hidden dealer card.
    const row = node("div", undefined, "casino-cards"), ranks = ["A", "2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K"], suits = ["♠", "♥", "♦", "♣"];
    hand.forEach(c => {
      const suit = c === null ? -1 : Math.floor(c / 13);
      row.append(node("span", c === null ? "?" : ranks[c % 13] + suits[suit], "casino-card" + (c === null ? " back" : suit === 1 || suit === 2 ? " red" : "")));
    });
    return row;
  }
  function handTotal(hand) { // A display-only count; the server independently checks every hand.
    let value = 0, aces = 0;
    hand.forEach(c => { const rank = c % 13; value += rank ? Math.min(rank + 1, 10) : 11; if (!rank) aces++; });
    while (value > 21 && aces > 0) { value -= 10; aces--; }
    return value;
  }
  function stage() { // Give each game a distinct board while all seats share the same server round.
    const el = $("casino-stage"), playing = state.phase === "playing", result = state.result;
    el.replaceChildren();
    if (game === "blackjack") {
      el.append(node("h2", "Dealer"), cards(state.dealer));
      el.append(node("p", result ? "Dealer: " + result.dealer_total : playing ? "Hole card stays hidden until settlement" : "Place your bets for the next hand"));
    } else if (game === "roulette") {
      el.append(node("div", result ? result.number : playing ? "◈" : "0–36", "casino-wheel " + (result ? result.color : playing ? "spinning" : "")));
      el.append(node("h2", result ? result.number + " · " + result.color : playing ? "The wheel is spinning…" : "Where will it land?"));
    } else if (game === "horse-racing") {
      el.append(node("h2", result ? horses[result.order[0]] + " wins!" : playing ? "And they’re off!" : "Meet the field"));
      const tracks = node("div", undefined, "casino-tracks");
      horses.forEach((name, i) => {
        const lane = node("div", undefined, "casino-lane"), track = node("div", undefined, "casino-track"), horse = node("span", "🏇", "casino-horse");
        horse.dataset.horse = i;
        horse.style.marginLeft = result ? (88 - result.order.indexOf(i) * 10) + "%" : "0%";
        lane.append(node("span", (i + 1) + ". " + name)); track.append(horse); lane.append(track); tracks.append(lane);
      });
      el.append(tracks);
      if (result) el.append(node("p", "Finish: " + result.order.map(i => horses[i]).join(" · ")));
    } else {
      el.append(node("h2", playing ? "Reels rolling together…" : result ? "Your table’s reels" : "A little luck, together"));
      const reels = node("div", undefined, "casino-reels");
      ["⭐", "🍒", "🔔"].forEach(s => reels.append(node("span", playing ? "?" : s)));
      el.append(reels, node("p", "Your own three reels · A shared round for the whole table"));
    }
  }
  function render(data) { // Accept monotonic snapshots so an older poll cannot undo a just-completed move.
    if (state && data.code === state.code && data.revision < state.revision) return;
    const old = state, focused = document.activeElement.id;
    state = data; offset = data.server_time * 1000 - Date.now(); remember(data.code);
    $("casino-lobby").hidden = true; $("casino-table").hidden = false;
    $("casino-code").textContent = "Table " + data.code + " · Round " + data.round;
    $("casino-phase").textContent = { betting: "Place your bets", playing: game === "blackjack" ? "Play your hand" : "Round in progress", results: "Round complete" }[data.phase];
    const p = mine();
    $("casino-balance").textContent = p.chips.toLocaleString() + " chips";
    $("casino-hint").textContent = data.phase === "betting" ? (p.bet ? "Bet locked in: " + p.bet + " chips. You can update it until the round starts." : "Choose your stake, then place a bet. The host or timer starts the round.") :
      data.phase === "results" ? (p.bet ? "Returned " + p.payout + " chips · Net " + (p.payout - p.bet) : "You watched this round. Join the next one!") :
      !p.bet ? "You’re watching this round. Betting reopens after the results." : p.stood ? "Your hand is finished. Waiting for the table." : "Bets are closed. Watch the table!";
    if (!old || old.revision !== data.revision || old.code !== data.code) {
      stage();
      const players = $("casino-players"); players.replaceChildren();
      data.players.forEach((seat, i) => {
        const box = node("article", undefined, "casino-player"); box.dataset.you = seat.id === data.you;
        box.append(node("h3", seat.name + (seat.id === data.you ? " · You" : "") + (i === 0 ? " · Host" : "")));
        box.append(node("p", seat.chips.toLocaleString() + " chips · Bet " + seat.bet));
        if (seat.choice && choices.length > 1) box.append(node("p", (choices.find(c => c[0] === seat.choice) || ["", seat.choice])[1]));
        if (seat.cards.length) {
          const value = handTotal(seat.cards);
          box.append(cards(seat.cards), node("p", "Total " + value + (value > 21 ? " · Bust" : seat.stood ? " · Standing" : "")));
        }
        if (game === "slots" && seat.bet) {
          const reels = node("div", undefined, "casino-reels" + (data.phase === "playing" ? " rolling" : ""));
          (seat.reels.length ? seat.reels : [null, null, null]).forEach(s => reels.append(node("span", symbols[s] || "?"))); box.append(reels);
        }
        if (data.phase === "results" && seat.bet) box.append(node("p", "Return " + seat.payout + " · Net " + (seat.payout - seat.bet), seat.payout > seat.bet ? "casino-win" : ""));
        players.append(box);
      });
      if (old && old.phase !== "results" && data.phase === "results") M.sound.tone(p.payout > p.bet ? 660 : 220, .22);
    }
    controls(); tick(); restoreFocus(focused);
    M.report("Table " + data.code + " · " + data.players.length + "/8 players · " + p.chips + " chips · " + data.phase);
  }
  function tick() { // Countdown and decorative horse movement use the server clock, never decide a winner.
    if (!state) return;
    const remaining = Math.max(0, state.deadline - (Date.now() + offset) / 1000);
    $("casino-clock").textContent = "· " + Math.ceil(remaining) + "s";
    if (game === "horse-racing" && state.phase === "playing" && !M.reducedMotion) {
      document.querySelectorAll("[data-horse]").forEach(h => {
        const i = Number(h.dataset.horse), progress = (1 - Math.min(6, remaining) / 6) * 72;
        h.style.marginLeft = Math.max(0, progress + Math.sin(remaining * 2 + i) * 7) + "%";
      });
    }
    if (game === "slots" && state.phase === "playing" && !M.reducedMotion) {
      const faces = Object.values(symbols), step = Math.floor(Date.now() / 140);
      document.querySelectorAll(".rolling span").forEach((reel, i) => { reel.textContent = faces[(step + i * 3) % faces.length]; });
    }
  }
  function lobby() { // Leave the seat view, clear its resume pointer, and reload the shared lobby.
    state = null; resume = null; signature = ""; remember(null);
    $("casino-table").hidden = true; $("casino-lobby").hidden = false;
    $("casino-rooms").replaceChildren(); $("casino-empty").hidden = false; $("casino-empty").textContent = "Looking for tables…";
    M.report("Choose a table to play together");
  }
  async function refresh() { // Poll only while visible; persisted state survives navigation and server restarts.
    if (busy || refreshing || document.hidden) return;
    refreshing = true;
    const code = state ? state.code : resume;
    try {
      if (code) {
        const data = await api("/" + code);
        if ((state && state.code === code) || (!state && resume === code)) { resume = null; render(data); }
      } else {
        const data = await api("");
        if (state || busy) return;
        const next = JSON.stringify(data.rooms);
        if (signature !== next) {
          signature = next;
          const focused = document.activeElement.id, rooms = $("casino-rooms"); rooms.replaceChildren();
          data.rooms.forEach(r => {
            const button = node("button", "Table " + r.code + "\n" + r.host + " · " + r.count + "/8 · " + r.phase + (r.mine ? " · Resume" : ""));
            button.type = "button"; button.id = "room-" + r.code; button.dataset.room = r.code; button.dataset.full = r.count >= 8 && !r.mine;
            button.addEventListener("click", () => perform("join", r.code)); rooms.append(button);
          });
          $("casino-empty").hidden = !!data.rooms.length; $("casino-empty").textContent = "No tables yet. Create one and invite your friends.";
          controls(); restoreFocus(focused);
        }
      }
      if ($("casino-error").dataset.network) { error(""); delete $("casino-error").dataset.network; }
    } catch (e) {
      if (code && [403, 404].includes(e.status) && (!state || state.code === code)) lobby();
      error(e.status ? e.message : "Connection interrupted. Reconnecting… Your table stays saved.");
      if (!e.status) $("casino-error").dataset.network = "true";
    } finally { refreshing = false; }
  }
  async function perform(action, code) { // Send one versioned intention; failed requests are never blindly replayed.
    if (busy) return;
    M.sound.unlock();
    busy = true; controls(); error("");
    try {
      const body = action === "create" ? {} : { action: action };
      if (action !== "create" && action !== "join") body.revision = state.revision;
      if (action === "bet") { body.amount = amount; body.choice = choices[pick][0]; }
      const data = await api(action === "create" ? "" : "/" + (code || state.code), body);
      if (data.left) lobby(); else render(data);
    } catch (e) { error(e.status ? e.message : "Connection interrupted. Check the table before trying again."); }
    finally { busy = false; controls(); await refresh(); }
  }
  ["create", "leave", "bet", "start", "hit", "stand", "refill"].forEach(action => $("casino-" + action).addEventListener("click", () => perform(action)));
  $("casino-less").addEventListener("click", () => { amount = Math.max(10, amount - 10); controls(); });
  $("casino-more").addEventListener("click", () => { const p = mine(); amount = Math.min(500, Math.floor((p.chips + p.bet) / 10) * 10, amount + 10); controls(); });
  $("casino-pick-prev").addEventListener("click", () => { pick = (pick + choices.length - 1) % choices.length; controls(); });
  $("casino-pick-next").addEventListener("click", () => { pick = (pick + 1) % choices.length; controls(); });
  M.onAction(action => { // Keyboard and remote use the same visible button order; no text entry is required.
    const buttons = focusable(); if (!buttons.length) return;
    const index = buttons.indexOf(document.activeElement);
    if (action === "enter") { (buttons[index] || buttons[0]).click(); return; }
    const step = action === "left" || action === "up" ? -1 : 1;
    buttons[(index + step + buttons.length) % buttons.length].focus();
  });
  async function poll() { await refresh(); setTimeout(poll, state ? 1000 : 3000); } // Schedule after completion to avoid overlapping polls.
  document.addEventListener("visibilitychange", () => { if (!document.hidden) refresh(); });
  setInterval(tick, 500);
  controls(); poll();
})();
