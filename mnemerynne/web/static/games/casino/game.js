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
    slots: "Three reels × three rows. All three horizontal paylines play on every spin; your total cost is 3× the selected per-line bet. On each line, three stars return 24× the line bet, another triple 12×, and any pair 1×. Line returns add together. Columns and diagonals do not pay. Everyone has their own reels.",
  };
  const choices = game === "roulette" ? [["red", "Red"], ["black", "Black"], ["odd", "Odd"], ["even", "Even"], ["low", "1–18"], ["high", "19–36"]].concat(Array.from({ length: 37 }, (_, n) => [String(n), String(n)])) :
    game === "horse-racing" ? horses.map((name, i) => [String(i), name]) : [["", ""]];
  let state = null, busy = false, refreshing = false, amount = 50, pick = 0, signature = "", offset = 0, handPick = "hit", wheel = null;
  let resume = null, tvMode = false, inputQueue = Promise.resolve(), pendingInputs = 0;
  try {
    resume = sessionStorage.getItem("casino-room:" + game);
    tvMode = !!resume && sessionStorage.getItem("casino-view:" + game) === "tv";
  } catch (_) { /* A private browser can still play. */ }
  $("casino-rules").textContent = rules[game];
  $("casino-tv-rules").textContent = rules[game];
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
  function api(path, body) { return M.api(base + path, body); } // A bounded request prevents a lost connection from locking controls forever.
  function unit() { return state && state.couch ? "credits" : "coins"; } // Local multiplayer tables play on free credits; online tables on the account's coins.
  function remember(code) { // Refreshing a page resumes its existing seat instead of minting a new balance.
    try {
      if (code) { sessionStorage.setItem("casino-room:" + game, code); sessionStorage.setItem("casino-view:" + game, tvMode ? "tv" : "individual"); }
      else { sessionStorage.removeItem("casino-room:" + game); sessionStorage.removeItem("casino-view:" + game); }
    } catch (_) { /* Optional storage. */ }
  }
  function mine() { return state && state.players.find(p => p.id === state.you); } // Resolve only this client's authenticated seat.
  function wagerLines() { return game === "slots" ? (state ? state.slot_lines || 1 : 3) : 1; } // Legacy saved rounds retain their one-line cost.
  function stake(p) { return p.bet * wagerLines(); } // A slot bet reserves one stake for each active horizontal line.
  function betLimit(p) { return Math.min(500, Math.floor((p.chips + stake(p)) / (10 * wagerLines())) * 10); } // Only offer line bets the balance can cover in full.
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
    $("casino-controls").hidden = tvMode;
    $("casino-tv-guide").hidden = !tvMode;
    $("casino-table").classList.toggle("casino-tv", tvMode);
    $("gm").classList.toggle("casino-shared-tv", tvMode); // Reserve the TV's space for player cards, not a duplicate title/board.
    $("casino-leave").textContent = tvMode ? "End TV table" : "Leave table";
    $("casino-betting").hidden = !betting;
    $("casino-hand").hidden = !(p && game === "blackjack" && state.phase === "playing" && p.bet && !p.stood);
    $("casino-amount").textContent = amount;
    $("casino-stake-label").textContent = (state && state.couch ? "Credits" : "Coins") + (game === "slots" ? " per line" : " to play");
    $("casino-cost").hidden = game !== "slots";
    $("casino-cost").textContent = wagerLines() + " lines × " + amount + " = " + (wagerLines() * amount) + " " + unit() + " total";
    $("casino-pick").textContent = choices[pick][1];
    $("casino-bet").textContent = p && p.bet ? "Update bet" : "Place bet";
    $("casino-bet").disabled = busy || !p || amount > betLimit(p);
    $("casino-start").hidden = !state || !state.players.length || state.you !== state.players[0].id;
    $("casino-start").disabled = busy || !state || !state.players.some(p => p.bet);
    $("casino-refill").hidden = !(p && p.chips < 10 * wagerLines() && !p.bet);
    $("casino-refill").textContent = "Refill free " + unit();
    ["casino-create", "casino-create-tv", "casino-leave", "casino-hit", "casino-stand", "casino-refill"].forEach(id => { $(id).disabled = busy; });
    $("casino-less").disabled = busy || amount <= 10;
    $("casino-more").disabled = busy || amount >= (p ? betLimit(p) : 500);
    ["casino-pick-prev", "casino-pick-next"].forEach(id => { $(id).disabled = busy; });
    document.querySelectorAll("[data-room]").forEach(b => { b.disabled = busy || b.dataset.full === "true"; });
    $("casino-hit").classList.toggle("gm-primary", handPick === "hit");
    $("casino-stand").classList.toggle("gm-primary", handPick === "stand");
    $("casino-hit").setAttribute("aria-pressed", String(handPick === "hit"));
    $("casino-stand").setAttribute("aria-pressed", String(handPick === "stand"));
    $("casino-keys").textContent = !$("casino-hand").hidden ? "◀ ▶ Choose Hit or Stand · Enter confirms" :
      "◀ ▶ Change pick · ▲ ▼ Change bet · Enter sets bet · Play starts round (host) · Keyboard: P plays, Tab reaches buttons";
    if (wheel) wheel.setPick(tvMode ? "" : choices[pick][0]);
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
  function slotMachine(seat, large) { // Render each horizontal payline from server rows, with its own return and winning highlight.
    const machine = node("div", undefined, "casino-slot-machine" + (large ? " large" : ""));
    const saved = seat.reels || [], rows = saved.length && !Array.isArray(saved[0]) ? [saved] : saved;
    const settled = state.phase === "results" && seat.bet > 0;
    const returns = seat.line_payouts || (rows.length === 1 ? [seat.payout] : []);
    machine.setAttribute("aria-label", wagerLines() + " horizontal paylines across three reels");
    for (let line = 0; line < wagerLines(); line++) {
      const payout = returns[line] || 0;
      const row = node("div", undefined, "casino-slot-line" + (settled && payout > 0 ? " paid" : ""));
      row.dataset.line = line + 1;
      if (settled) row.dataset.payout = payout;
      const label = node("span", String(line + 1), "casino-line-number");
      label.setAttribute("aria-label", "Payline " + (line + 1));
      const reels = node("div", undefined, "casino-reels" + (state.phase === "playing" && seat.bet ? " rolling" : ""));
      (rows[line] || [null, null, null]).forEach(symbol => {
        const cell = node("span", symbols[symbol] || "—", "casino-slot-symbol");
        cell.setAttribute("aria-label", symbol || "Waiting for the spin"); reels.append(cell);
      });
      row.append(label, reels);
      if (settled) row.append(node("span", "Line " + (line + 1) + " · Return " + payout, "casino-line-return"));
      machine.append(row);
    }
    return machine;
  }
  function stage() { // Give each game a distinct board while all seats share the same server round.
    const el = $("casino-stage"), playing = state.phase === "playing", result = state.result;
    if (game === "roulette") {
      if (!wheel) { wheel = window.CasinoRoulette.create(M.reducedMotion); el.replaceChildren(wheel.element); }
      wheel.update(state); // Keep the rotor alive across polls, bets and joins, so animation never restarts.
      return;
    }
    el.replaceChildren();
    if (game === "blackjack") {
      el.append(node("h2", "Dealer"), cards(state.dealer));
      el.append(node("p", result ? "Dealer: " + result.dealer_total : playing ? "Hole card stays hidden until settlement" : "Place your bets for the next hand"));
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
      if (tvMode) {
        el.append(node("h2", playing ? "Everyone’s reels are rolling…" : "Your remote, your own three paylines"),
          node("p", "Each player's reels and returns appear on their card below."));
        return;
      }
      el.append(node("h2", playing ? "Reels rolling together…" : result ? "Your paylines" : "Three lines of luck"));
      el.append(slotMachine(mine(), true), node("p", wagerLines() === 3 ? "Top · Middle · Bottom — all three horizontal lines play" : "Saved single-line round · Three lines start next round"));
    }
  }
  function render(data) { // Accept monotonic snapshots so an older poll cannot undo a just-completed move.
    if (state && data.code === state.code && data.revision < state.revision) return;
    const old = state, focused = document.activeElement.id;
    state = data; offset = data.server_time * 1000 - Date.now(); remember(data.code);
    if (!old || old.round !== data.round || old.code !== data.code) handPick = "hit";
    $("casino-lobby").hidden = true; $("casino-table").hidden = false;
    $("casino-code").textContent = "Table " + data.code + " · Round " + data.round;
    $("casino-phase").textContent = { betting: "Place your bets", playing: game === "blackjack" ? "Play your hand" : "Round in progress", results: "Round complete" }[data.phase];
    const p = mine();
    $("casino-balance").textContent = p ? p.chips.toLocaleString() + " " + unit() : "Shared TV";
    $("casino-hint").textContent = !p ? "Each remote controls its own player card." : data.phase === "betting" ? (p.bet ? "Bet locked in: " + stake(p) + " " + unit() + " total. You can update it until the round starts." : "Choose your stake, then place a bet. The host or timer starts the round.") :
      data.phase === "results" ? (p.bet ? "Returned " + p.payout + " " + unit() + " · Net " + (p.payout - stake(p)) : "You watched this round. Join the next one!") :
      !p.bet ? "You’re watching this round. Betting reopens after the results." : p.stood ? "Your hand is finished. Waiting for the table." : "Bets are closed. Watch the table!";
    if (!old || old.revision !== data.revision || old.code !== data.code) {
      stage();
      const players = $("casino-players"); players.replaceChildren();
      data.players.forEach((seat, i) => {
        const box = node("article", undefined, "casino-player"); box.dataset.you = seat.id === data.you; box.dataset.seat = seat.id;
        box.append(node("h3", seat.name + (seat.id === data.you ? " · You" : "") + (i === 0 ? " · Host" : "")));
        box.append(node("p", seat.chips.toLocaleString() + " " + unit() + " · Bet " + seat.bet + (game === "slots" ? "/line · Total " + stake(seat) : "")));
        if (seat.choice && choices.length > 1) box.append(node("p", (choices.find(c => c[0] === seat.choice) || ["", seat.choice])[1]));
        if (seat.cards.length) {
          const value = handTotal(seat.cards);
          box.append(cards(seat.cards), node("p", "Total " + value + (value > 21 ? " · Bust" : seat.stood ? " · Standing" : "")));
        }
        if (game === "slots" && (seat.bet || tvMode)) {
          box.append(slotMachine(seat, false));
        }
        if (data.phase === "results" && seat.bet) box.append(node("p", "Return " + seat.payout + " · Net " + (seat.payout - stake(seat)), seat.payout > stake(seat) ? "casino-win" : ""));
        if (tvMode && seat.controls) {
          const draft = seat.controls;
          box.classList.add("casino-controller");
          box.dataset.player = i + 1;
          box.querySelector("h3").prepend(node("span", "P" + (i + 1) + " · ", "casino-seat-number"));
          if (data.phase === "betting") {
            const choice = choices[draft.pick][1];
            box.append(node("p", "Selected: " + (choice ? choice + " · " : "") + draft.amount + (game === "slots" ? "/line · " + draft.amount * wagerLines() + " total" : " " + unit()), "casino-draft"));
            box.append(node("p", seat.chips < 10 * wagerLines() && !seat.bet ? "Enter: refill free " + unit() :"Enter: " + (seat.bet ? "update bet" : "place bet"), "casino-controller-hint"));
          } else if (game === "blackjack" && data.phase === "playing" && seat.bet && !seat.stood) {
            box.append(node("p", "◀ ▶ " + (draft.hand === "hit" ? "[HIT] · Stand" : "Hit · [STAND]") + " · Enter confirms", "casino-draft"));
          } else if (data.phase === "playing" && !seat.bet) box.append(node("p", "Watching · play next round"));
          if (draft.error) box.append(node("p", draft.error, "casino-seat-error"));
        }
        if (tvMode) {
          const remove = node("button", "Remove player", "casino-remove"); remove.type = "button"; remove.id = "remove-" + seat.id;
          remove.setAttribute("aria-label", "Remove " + seat.name + " and forfeit their " + unit());
          remove.addEventListener("click", () => manageTV("remove", seat.id)); box.append(remove);
        }
        players.append(box);
      });
      if (tvMode && !data.players.length) players.append(node("p", "Ready for company. Press Enter on each remote to join; the first player becomes host.", "casino-waiting"));
      if (old && old.phase !== "results" && data.phase === "results") M.sound.tone(p && p.payout > stake(p) ? 660 : 220, .22);
    }
    controls(); tick(); restoreFocus(focused);
    M.report("Table " + data.code + " · " + data.players.length + "/8 players · " + (p ? p.chips + " " + unit() : "Each remote has its own seat") + " · " + data.phase);
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
      document.querySelectorAll(".rolling .casino-slot-symbol").forEach((reel, i) => { reel.textContent = faces[(step + i * 5) % faces.length]; });
    }
  }
  function lobby() { // Leave the seat view, clear its resume pointer, and reload the shared lobby.
    if (wheel) { wheel.destroy(); wheel = null; } // Stop animation and visibility listeners when leaving a table.
    state = null; resume = null; tvMode = false; signature = ""; remember(null);
    $("casino-table").hidden = true; $("casino-lobby").hidden = false;
    $("casino-rooms").replaceChildren(); $("casino-empty").hidden = false; $("casino-empty").textContent = "Looking for tables…";
    M.report("Choose a table to play together");
    M.showCoins("casino-coins");                         // leaving a table may have changed them
  }
  async function refresh() { // Poll only while visible; persisted state survives navigation and server restarts.
    if (busy || refreshing || document.hidden) return;
    refreshing = true;
    const code = state ? state.code : resume;
    try {
      if (code) {
        const data = await api("/" + code + (tvMode ? "/tv" : ""));
        if ((state && state.code === code) || (!state && resume === code)) { resume = null; render(data); }
      } else {
        const data = await api("");
        if (state || busy) return;
        const next = JSON.stringify(data.rooms);
        if (signature !== next) {
          signature = next;
          const focused = document.activeElement.id, rooms = $("casino-rooms"); rooms.replaceChildren();
          data.rooms.forEach(r => {
            const button = node("button", "Table " + r.code + "\n" + r.host + " · " + r.count + "/8 · " + r.phase + (r.tv ? " · Resume on this TV" : r.mine ? " · Resume" : ""));
            button.type = "button"; button.id = "room-" + r.code; button.dataset.room = r.code; button.dataset.full = r.count >= 8 && !r.mine;
            button.addEventListener("click", () => perform(r.tv ? "watch" : "join", r.code)); rooms.append(button);
          });
          $("casino-empty").hidden = !!data.rooms.length; $("casino-empty").textContent = "No tables yet. Create one and invite your friends.";
          controls(); restoreFocus(focused);
        }
      }
      if ($("casino-error").dataset.network) { error(""); delete $("casino-error").dataset.network; }
    } catch (e) {
      if (code && [403, 404].includes(e.status) && (!state || state.code === code)) lobby();
      error(e.status ? e.message : "Connection interrupted. Reconnecting… Your seat is kept for two minutes.");
      if (!e.status) $("casino-error").dataset.network = "true";
    } finally { refreshing = false; }
  }
  async function perform(action, code) { // Send one versioned intention; failed requests are never blindly replayed.
    if (busy) return;
    if (action === "leave" && tvMode) { await manageTV("close"); return; }
    M.sound.unlock();
    busy = true; controls(); error("");
    try {
      if (action === "watch") {
        const data = await api("/" + code + "/tv"); tvMode = true; render(data); return;
      }
      const creating = action === "create" || action === "create-tv";
      const body = creating ? { mode: action === "create-tv" ? "tv" : "individual" } : { action: action };
      if (!creating && action !== "join") body.revision = state.revision;
      if (action === "bet") { body.amount = amount; body.choice = choices[pick][0]; }
      const data = await api(creating ? "" : "/" + (code || state.code), body);
      if (creating || action === "join") tvMode = action === "create-tv";
      if (data.left) lobby(); else render(data);
    } catch (e) { error(e.status ? e.message : "Connection interrupted. Check the table before trying again."); }
    finally { busy = false; controls(); await refresh(); }
  }
  ["create", "create-tv", "leave", "bet", "start", "hit", "stand", "refill"].forEach(action => $("casino-" + action).addEventListener("click", () => perform(action)));

  async function manageTV(action, seat) { // Local TV controls manage seats explicitly; remote keys never activate these buttons.
    if (busy || !state || !tvMode) return;
    busy = true; controls(); error("");
    try {
      const data = await api("/" + state.code + "/tv/manage", { action: action, seat: seat || "", revision: state.revision });
      if (data.left) lobby(); else render(data);
    } catch (e) { error(e.message); }
    finally { busy = false; controls(); await refresh(); }
  }

  function remoteInput(token) { // Queue all controllers instead of dropping the second player's input while a request is in flight.
    if (!state || pendingInputs >= 128) { error("Remote inputs are catching up. Please press again in a moment."); return; }
    const code = state.code, round = state.round, phase = state.phase;
    pendingInputs++;
    inputQueue = inputQueue.then(async () => {
      if (!tvMode || !state || state.code !== code) return;
      try {
        const data = await api("/" + code + "/tv/input", { token: token, round: round, phase: phase });
        if (tvMode && state && state.code === code) { error(""); render(data); }
      } catch (e) { error(e.status ? e.message : "Remote connection interrupted. Check your card before pressing again."); }
    }).finally(() => { pendingInputs--; });
  }
  function changeBet(step) { // Remote and touch share ten-chip steps and the same available-balance limit.
    const p = mine();
    if (busy || !p || state.phase !== "betting") return;
    const limit = Math.max(10, betLimit(p));
    amount = Math.max(10, Math.min(limit, amount + step * 10)); controls();
  }
  function changePick(step) { // Left/right wraps through picks without moving focus to unrelated buttons.
    if (busy || !state || state.phase !== "betting") return;
    pick = (pick + step + choices.length) % choices.length; controls();
  }
  $("casino-less").addEventListener("click", () => changeBet(-1));
  $("casino-more").addEventListener("click", () => changeBet(1));
  $("casino-pick-prev").addEventListener("click", () => changePick(-1));
  $("casino-pick-next").addEventListener("click", () => changePick(1));
  M.onAction((action, controller) => { // The signed controller is authoritative in shared-TV mode; ordinary tables retain direct keys.
    if (tvMode && state) {
      if (controller) remoteInput(controller);
      else error("Use a paired remote to play your seat. Tab reaches the TV’s table-management buttons.");
      return;
    }
    if (busy) return;
    if (state) {
      const p = mine();
      if (state.phase === "betting") {
        if (action === "left" || action === "right") changePick(action === "left" ? -1 : 1);
        else if (action === "up" || action === "down") changeBet(action === "up" ? 1 : -1);
        else if (action === "enter") {
          if (p.chips < 10 * wagerLines() && !p.bet) perform("refill");
          else if (!$("casino-bet").disabled) perform("bet");
          else error("Lower your bet to fit your chip balance.");
        } else if (action === "play") {
          if (state.you !== state.players[0].id) error("The host starts the round. Your bet stays on the table.");
          else if (!state.players.some(p => p.bet)) error("Set a bet with Enter before pressing Play.");
          else perform("start");
        }
      } else if (game === "blackjack" && state.phase === "playing" && p.bet && !p.stood) {
        if (action === "left" || action === "right") { handPick = handPick === "hit" ? "stand" : "hit"; controls(); }
        else if (action === "enter") perform(handPick);
      }
      return; // Locked rounds cannot accidentally activate Leave or place another wager.
    }
    const buttons = focusable(); if (!buttons.length) return;
    const index = buttons.indexOf(document.activeElement);
    if (action === "enter") { (buttons[index] || buttons[0]).click(); return; }
    if (action === "play") return;
    const step = action === "left" || action === "up" ? -1 : 1;
    buttons[(index + step + buttons.length) % buttons.length].focus();
  }, { directEnter: () => !!state && !tvMode });
  async function poll() { await refresh(); setTimeout(poll, state ? 1000 : 3000); } // Schedule after completion to avoid overlapping polls.
  document.addEventListener("visibilitychange", () => { if (!document.hidden) refresh(); });
  setInterval(tick, 500);
  controls(); poll(); M.showCoins("casino-coins");
})();
