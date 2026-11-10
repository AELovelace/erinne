/* The companion's controls and presentation. Lumi lives on the account (mnemerynne/lumipet.py): this page only says
   what the person chose (which snack, which toy) and draws what the server answers. Her needs run down while away. */
(function () {
  "use strict";
  const G = window.MGame, R = window.LumiPetRules, H = window.LumiPetHouse, E = window.LumiPetEnemies, Bt = window.LumiPetBattle, W = window.LumiPetCloset;
  const $ = function (id) { return document.getElementById(id); };
  const room = $("lp-room"), list = $("lp-list"), houseArt = $("lp-house"), tabButtons = Array.from(document.querySelectorAll("[data-lp-tab]"));
  const API = "/api/media/games/lumi-pet/", OLD_SAVE = "mnemerynne-game:lumi-pet:companion";
  const TABS = tabButtons.map(function (button) { return button.dataset.lpTab; }); // care, pantry, toys, closet, home, battle, shop, settings
  const MEMORIES = { cuddle: "A warm little cuddle", snack: "A shared snack", play: "Playtime together", rest: "A quiet moment together" };
  const NEEDS = { hunger: "Tummy rumbling. A snack would help.", energy: "So sleepy. A nap would help.", joy: "A little lonely. Stay and play?" };
  const BUSY = { nap: "napping", play: "playing" };
  const CUDDLES = ["Lumi leans into your hand. A tiny purr, just for you.", "Soft wings wrap around your fingers. We can stay like this a while."];
  let snap = null, offset = 0, tab = 0, picks = TABS.map(function () { return 0; }), busy = false, visited = false;
  let battleView = null, finished = null, lastFoe = ""; // Which Battle list is open, the fight that just ended, and the enemy last drawn.
  let lastOutfit = "";
  let homeSpot = null, lastHouse = ""; // The spot being decorated (null: the list of spots), and what the room last drew.
  let effectUntil = 0, lastReport = "", lastList = "", lastDone = null, timer = null, polled = 0, reminders = "unsupported";

  function serverNow() { return Date.now() + offset; } // The server's clock, so timers agree across devices.

  function take(data) { // Keep the server's answer and how far this device's clock is from the server's.
    snap = data; offset = data.now - Date.now(); polled = Date.now();
    $("lp-coins").textContent = data.coins.toLocaleString();
  }

  function say(text) { $("lp-message").textContent = text; } // The live region announces it to screen readers too.

  function effect(action, symbol) { // A brief expression in the room after an interaction.
    room.dataset.action = action; $("lp-effect").textContent = symbol; effectUntil = Date.now() + 4000;
    G.sound.unlock();
    G.sound.tone(action === "rest" ? 392 : 659.25, .22, 0, .09);
    G.sound.tone(action === "rest" ? 293.66 : 880, .3, .14, .06);
  }

  function item(id) { // Look an item up in the server's catalog.
    const c = snap.catalog;
    return c.snacks.concat(c.treats, c.toys).find(function (entry) { return entry.id === id; });
  }

  function sceneNow() { // Night follows this device's clock; naps and wakes come from the server, shifted onto this clock.
    return R.scene({ rest: snap.pet.rest, restUntil: snap.pet.restUntil - offset }, Date.now());
  }

  function rows() { // What the selected tab offers, as plain rows: the list and the remote both read from here.
    const pet = snap.pet, c = snap.catalog, name = TABS[tab], asleep = sceneNow().asleep;
    if (name === "care") return [
      { key: "cuddle", icon: "♡", name: "Cuddle", hint: "A little closeness", send: { action: "cuddle" } },
      asleep ? { key: "wake", icon: "☀", name: "Wake gently", hint: "A sleepy little hello", send: { action: "wake" } }
        : { key: "nap", icon: "☾", name: "Nap", hint: "Five minutes of star dreams · +35 energy", send: { action: "nap" } },
    ];
    if (name === "pantry") return c.snacks.concat(c.treats).filter(function (food) { return !food.price || pet.inventory[food.id]; }).map(function (food) {
      return { key: food.id, icon: food.icon, name: food.name, send: { action: "feed", item: food.id },
        hint: R.signed(food.fullness, "hunger") + " · " + R.signed(food.joy, "joy") + " · " + (food.price ? pet.inventory[food.id] + " left" : "always in the bowl") };
    });
    if (name === "toys") return c.toys.filter(function (toy) { return !toy.price || pet.toys.includes(toy.id); }).map(function (toy) {
      return { key: toy.id, icon: toy.icon, name: toy.name, send: { action: "play", item: toy.id },
        hint: toy.minutes + " min · " + R.signed(toy.joy, "joy") + " · " + R.signed(toy.energy, "energy") };
    });
    if (name === "closet") { // What she wears: the Star Capsule first, then every accessory, tried on as you move.
      const capsule = snap.closet.capsule;
      return [{ key: "capsule", icon: "🔮", name: "Star Capsule", hint: capsule.price + " coins · something new to wear, never a repeat", send: { action: "capsule" } }]
        .concat(snap.closet.slots.reduce(function (all, slot) {
          return all.concat(slot.items.map(function (piece) {
            const owned = pet.closet.includes(piece.id), worn = pet.outfit[slot.id] === piece.id;
            const row = { key: piece.id, icon: worn ? "✓" : owned ? "○" : piece.price ? "✧" : "🔒", name: slot.name + " · " + piece.name, wear: [slot.id, piece.id],
              hint: piece.rarity[0].toUpperCase() + piece.rarity.slice(1) + " · " + (worn ? "wearing it · Enter takes it off" : owned ? "hers · Enter puts it on" : piece.price ? piece.price + " coins" : "only from the Star Capsule") };
            if (owned || piece.price) row.send = { action: "wear", item: piece.id }; else row.note = piece.name + " only comes from the Star Capsule.";
            return row;
          }));
        }, []));
    }
    if (name === "home") { // Decorating: first the spots, then (inside one) its objects, previewed as you move.
      const spot = snap.house.spots.find(function (entry) { return entry.id === homeSpot; });
      if (!spot) return snap.house.spots.map(function (entry) {
        const now = entry.items.find(function (piece) { return piece.id === pet.house[entry.id]; });
        return { key: entry.id, icon: "⌂", name: entry.name, hint: now ? now.name : "", local: function () { openSpot(entry); } };
      });
      return [{ key: "back", icon: "‹", name: "Back to the house", hint: spot.name, local: function () { homeSpot = null; picks[tab] = Math.max(0, snap.house.spots.indexOf(spot)); } }].concat(spot.items.map(function (piece) {
        const owned = !piece.price || pet.decor.includes(piece.id), here = pet.house[spot.id] === piece.id;
        return { key: piece.id, icon: here ? "✓" : owned ? "○" : "✧", name: piece.name, preview: piece.id, send: here ? null : { action: "decorate", item: piece.id },
          hint: here ? "In the room now" : owned ? "Yours · Enter puts it out" : piece.price + " coins · Enter buys it" };
      }));
    }
    if (name === "battle") return Bt.rows({ snap: snap, view: battleView, finished: finished, now: serverNow(), countdown: R.countdown,
      go: function (view, pick, clear) { battleView = view; picks[tab] = pick || 0; if (clear) finished = null; } });
    if (name === "shop") return c.snacks.concat(c.treats, c.toys).filter(function (thing) { return thing.price && !pet.toys.includes(thing.id); }).map(function (thing) {
      const held = pet.inventory[thing.id] || 0, toy = c.toys.includes(thing);
      return { key: thing.id, icon: thing.icon, name: thing.name, send: { action: "buy", item: thing.id },
        hint: thing.price + " coins" + (toy ? " · a toy to keep" : held ? " · have " + held : "") };
    });
    return [ // settings
      { key: "glow", icon: "✦", name: "Glow: " + pet.glow[0].toUpperCase() + pet.glow.slice(1), hint: "Her colour, and her element in a fight: " + snap.battle.rules.glows[pet.glow], send: { action: "glow", glow: R.nextPalette(pet.glow) } },
      { key: "sound", icon: "♪", name: "Sound: " + (G.sound.isOn() ? "on" : "off"), hint: "Little chimes", local: function () { G.sound.toggle(); } },
    ].concat(reminders === "unsupported" ? [] : [ // Phones and computers only: a TV has nobody's pocket to buzz.
      { key: "reminders", icon: "✉", name: "Reminders: " + reminders, local: toggleReminders,
        hint: reminders === "blocked" ? "Allow notifications for this site in your browser first" : "A nudge on this device when Lumi needs you" },
    ]);
  }

  function openSpot(spot) { // Step into a spot, with its current object selected (row 0 is Back).
    homeSpot = spot.id;
    picks[tab] = 1 + Math.max(0, spot.items.findIndex(function (piece) { return piece.id === snap.pet.house[spot.id]; }));
  }

  function drawHouse(preview) { // Draw the room: what the server says is placed, with the highlighted object tried on.
    const house = Object.assign({}, snap.pet.house);
    if (preview) house[homeSpot] = preview;
    const signature = JSON.stringify(house);
    if (signature === lastHouse) return; // Redrawing would restart the window's scrolling, so only when it changed.
    lastHouse = signature;
    houseArt.innerHTML = H.render(house); // Markup authored in house.js; nothing a person typed goes into it.
  }

  function drawOutfit(preview) { // Dress her: what the server says she wears, with the highlighted accessory tried on.
    const outfit = Object.assign({}, snap.pet.outfit);
    if (preview) outfit[preview[0]] = preview[1];
    const signature = JSON.stringify(outfit);
    if (signature === lastOutfit) return;
    lastOutfit = signature;
    $("lp-acc-back").innerHTML = W.render(outfit, "back"); // Markup authored in closet.js.
    $("lp-acc-front").innerHTML = W.render(outfit, "front");
  }

  function drawFight() { // The fight scene over the room: the enemy, and both sides' bars. Shown for a fight in progress or just ended.
    const fight = snap.fight || finished;
    room.dataset.fight = String(Boolean(fight));
    $("lp-fight").hidden = !fight;
    if (!fight) { lastFoe = ""; return; }
    const foe = fight.enemy, bars = Bt.bars(fight), signature = foe.id + foe.colour;
    if (signature !== lastFoe) { lastFoe = signature; $("lp-foe").innerHTML = E.draw(foe.body, foe.colour); } // Art authored in enemies.js.
    $("lp-foe").dataset.down = String(foe.hp <= 0);
    $("lp-f-lumi").textContent = bars.lumi.label; $("lp-f-foe").textContent = bars.foe.label;
    $("lp-f-hp").style.width = bars.lumi.hp + "%"; $("lp-f-mp").style.width = bars.lumi.mp + "%"; $("lp-f-foehp").style.width = bars.foe.hp + "%";
  }

  function drawList(current) { // Rebuild the buttons only when the rows change, so focus and the remote's place survive a refresh.
    const signature = TABS[tab] + "|" + current.map(function (row) { return row.key + row.name + row.hint; }).join("|");
    if (signature !== lastList) {
      lastList = signature;
      list.replaceChildren.apply(list, current.map(function (row, index) {
        const button = document.createElement("button"), icon = document.createElement("span"), text = document.createElement("span"), hint = document.createElement("small");
        button.type = "button"; button.dataset.lpRow = row.key;
        icon.textContent = row.icon; icon.setAttribute("aria-hidden", "true");
        text.textContent = row.name; hint.textContent = row.hint; text.appendChild(hint);
        button.append(icon, text);
        button.addEventListener("click", function () { picks[tab] = index; choose(); }); // Mouse and touch act directly.
        return button;
      }));
    }
    Array.from(list.children).forEach(function (button, index) {
      const chosen = index === picks[tab];
      if (chosen && button.getAttribute("aria-pressed") !== "true" && list.scrollHeight > list.clientHeight) button.scrollIntoView({ block: "nearest" }); // Long lists follow the remote.
      button.setAttribute("aria-pressed", String(chosen));
    });
  }

  function render() { // Refresh the room, meters and list without flooding the remote's status line.
    if (!snap) return;
    const pet = snap.pet, now = Date.now(), scene = sceneNow(), mood = R.mood(pet), task = pet.task;
    room.dataset.palette = pet.glow;
    room.dataset.night = String(scene.night);
    room.dataset.sleep = String(scene.asleep || snap.pet.faint_until > serverNow());
    room.dataset.mood = mood.sad ? "sad" : "fine";
    $("lp-moments").textContent = pet.moments.toLocaleString();
    $("lp-memory").textContent = MEMORIES[pet.memory] || "Our first hello";
    $("lp-level").textContent = pet.level + (snap.progress.next ? " · " + pet.exp + "/" + snap.progress.next + " EXP" : " · the very top");
    const resting = pet.faint_until > serverNow(); // Napping off a lost fight: free in ten minutes.
    R.METERS.forEach(function (meter) { // Each meter is a bar and a number; low ones change colour.
      const bar = $("lp-" + meter), value = Math.round(pet[meter]);
      bar.style.width = value + "%"; bar.parentNode.setAttribute("aria-valuenow", String(value));
      bar.parentNode.dataset.low = String(pet[meter] < R.LOW);
      $("lp-" + meter + "-n").textContent = value;
    });
    $("lp-clock").textContent = scene.night ? "Moonlight hours" : "A little daytime glow";
    const left = task ? R.countdown(task.finishesAt - serverNow()) : "";
    $("lp-state").textContent = task ? (task.kind === "nap" ? "Napping · " + left : "Playing · " + left)
      : snap.fight ? "In a fight!" : resting ? "Napping off a fight · " + R.countdown(pet.faint_until - serverNow())
      : scene.asleep ? "Dreaming in good company" : mood.need ? NEEDS[mood.need] : "Happy to be here with you";
    $("lp-pet-title").textContent = scene.asleep ? "Lumi, a glowing fairy cat, sleeping peacefully" : "Lumi, a glowing fairy cat, awake";
    if (task && task.kind === "play") room.dataset.action = "play"; // She keeps playing for as long as the toy lasts.
    else if (now >= effectUntil) { delete room.dataset.action; $("lp-effect").textContent = scene.asleep ? "z z z" : mood.sad ? "…" : "♡"; }
    tabButtons.forEach(function (button, index) { button.setAttribute("aria-selected", String(index === tab)); });
    const current = rows();
    picks[tab] = Math.min(picks[tab], Math.max(0, current.length - 1)); // A list that shrank keeps a valid selection.
    drawList(current);
    const pick = current[picks[tab]];
    drawHouse(TABS[tab] === "home" && pick && pick.preview);
    drawOutfit(TABS[tab] === "closet" && pick && pick.wear);
    drawFight();
    const doing = snap.fight ? "fighting " + snap.fight.enemy.name : task ? BUSY[task.kind] : scene.asleep ? "dreaming" : mood.need ? "needs " + mood.need : "content";
    const report = "Lumi · " + doing + " · " + tabButtons[tab].textContent + ": " + (pick ? pick.name : "nothing yet") + " · Enter to choose";
    if (report !== lastReport) { G.report(report); lastReport = report; }
  }

  function announceDone() { // A nap or a play that finished (even while away) is mentioned once, when first seen.
    const done = snap.pet.done;
    if (!done || done.at === lastDone) { if (lastDone === null) lastDone = 0; return; }
    const first = lastDone === null; lastDone = done.at;
    if (first && serverNow() - done.at > 60000) return; // Old news on opening the page stays quiet.
    say(done.kind === "nap" ? "A stretch, a blink, a happy little hello. Lumi feels rested." : "What a game! Lumi flops down, very pleased with the " + item(done.item).name.toLowerCase() + ".");
    effect("cuddle", "✧ ♡");
  }

  function refresh() { // Ask the server how Lumi is; a failure keeps the last picture and tries again later.
    return G.api(API + "state").then(function (data) { take(data); announceDone(); render(); }).catch(function () {
      if (!snap) say("Lumi can't be reached just now. She'll be here when the connection is back.");
    });
  }

  function greet() { // A real moment with Lumi, not just opening the page, counts as today's visit.
    if (visited) return;
    visited = true;
    G.api(API + "visit", {}).then(function (data) {
      $("lp-coins").textContent = data.coins.toLocaleString(); snap.coins = data.coins;
      if (data.paid) say($("lp-message").textContent + " +" + data.paid + " coins for visiting today.");
    }).catch(function () { /* Lumi is just as happy either way */ });
  }

  function choose() { // Act on the selected row: one request at a time, and the server's words if it refuses.
    if (!snap || busy) return;
    const row = rows()[picks[tab]];
    if (!row) return;
    if (row.local) { row.local(); lastList = ""; render(); return; }
    if (!row.send) { say(row.note || "That's already in the room."); return; }
    busy = true;
    G.api(API + (row.url || "act"), row.send).then(function (data) {
      take(data);
      if (row.url) { // A fight began, or a turn was played: say what just happened, from the fight's own log.
        const fight = data.fight;
        battleView = null;
        say(fight.log.slice(row.url === "battle" ? 0 : -3).join(" "));
        if (fight.status !== "active") { finished = fight; snap.fight = null; picks[tab] = 0; }
        else if (row.url === "battle") picks[tab] = 0;
        effect(fight.status === "victory" ? "play" : fight.status === "defeat" ? "rest" : "cuddle", fight.status === "victory" ? "✧ ✦ ✧" : fight.status === "defeat" ? "z z z" : "✦");
        greet(); return;
      }
      const send = row.send, thing = send.item ? item(send.item) : null;
      if (send.action === "cuddle") { say(CUDDLES[data.pet.moments % CUDDLES.length]); effect("cuddle", "♡ ♡"); }
      else if (send.action === "feed") { say(thing.name + "! Lumi munches happily" + (thing.boost ? " and sparkles with a little " + thing.boost + " for her next adventure." : ".")); effect("snack", "✦"); }
      else if (send.action === "play") { say("Lumi pounces on the " + thing.name.toLowerCase() + ". " + thing.minutes + " minutes of play!"); effect("play", "✧ ✦"); }
      else if (send.action === "nap") { say("Lumi curls up on the cushion. Five minutes of little star dreams."); effect("rest", "z z z"); }
      else if (send.action === "wake") { say("A stretch, a blink, a happy little hello. Lumi settles beside you."); effect("rest", "☀"); }
      else if (send.action === "decorate") { say(data.said ? "Bought and put out: " + row.name + ". " + data.said.paid + " coins well spent." : row.name + " is out. Lumi approves."); effect("cuddle", "✧"); }
      else if (send.action === "capsule") { say("The capsule pops open: " + data.said.name + "! (" + data.said.rarity + ")"); effect("play", "✧ ✦ ✧"); picks[tab] = Math.max(0, rows().findIndex(function (r) { return r.key === data.said.capsule; })); }
      else if (send.action === "wear") { say(data.said ? "Bought: " + row.name + ". Lumi tries it on at once." : data.pet.outfit[row.wear[0]] === send.item ? "Lumi puts on the " + row.name.split(" · ")[1].toLowerCase() + "." : "Lumi takes it off."); effect("cuddle", "✧"); }
      else if (send.action === "learn") { say("Lumi learned " + row.name + "!"); effect("play", "✧ ✦"); }
      else if (send.action === "equip") { say(data.pet.moves.includes(send.item) ? row.name + " is ready for the next fight." : row.name + " is put away."); }
      else if (send.action === "mod") { say(send.item ? (data.said ? "Bought and fitted: " : "Fitted: ") + row.name + "." : "That slot is empty now."); effect("cuddle", "⚙"); }
      else if (send.action === "potions") { say("One potion, tucked away. Lumi carries " + data.pet.potions + "."); }
      else if (send.action === "heal") { say("A stretch and a shake. Lumi is up and ready."); effect("rest", "☀"); }
      else if (send.action === "respec") { say("Every skill point is back. Her trees start afresh."); battleView = null; }
      else if (send.action === "buy") { say("Bought: " + thing.name + ". " + data.said.paid + " coins well spent."); effect("cuddle", "✧"); }
      if (["cuddle", "feed", "play", "nap", "wake"].includes(send.action)) greet(); // Shopping and colours aren't a visit; time together is.
    }).catch(function (refused) { say(refused.message); }).then(function () { busy = false; render(); });
  }

  G.onAction(function (action) { // Keyboard, TV remote and buttons: ◀ ▶ change tab, ▲ ▼ move through the list, Enter chooses.
    if (!snap) return;
    if (action === "left" || action === "right") { tab = (tab + (action === "left" ? TABS.length - 1 : 1)) % TABS.length; homeSpot = null; battleView = null; }
    else if (action === "up" || action === "down") {
      const count = rows().length;
      if (count) picks[tab] = (picks[tab] + (action === "up" ? count - 1 : 1)) % count;
    } else if (action === "enter") { choose(); return; }
    render();
  }, { directEnter: function () { return true; } });

  tabButtons.forEach(function (button, index) { // Tabs can be tapped too.
    button.addEventListener("click", function () { tab = index; homeSpot = null; battleView = null; render(); });
  });

  function tick() { // Once a second: count timers down, collect a finished nap or play, and look in on her now and then.
    if (!snap) return;
    const task = snap.pet.task;
    if ((task && serverNow() >= task.finishesAt) || Date.now() - polled > 15000) { polled = Date.now(); refresh(); }
    render();
  }

  function visibility() { // Hidden frames stop timers and CSS motion; returning asks the server afresh.
    clearInterval(timer); timer = null;
    room.classList.toggle("lp-hidden", document.hidden);
    if (!document.hidden) { if (snap) refresh(); timer = setInterval(tick, 1000); }
  }
  document.addEventListener("visibilitychange", visibility);
  window.addEventListener("pagehide", function () { clearInterval(timer); });
  window.addEventListener("pageshow", visibility);

  // ---- Reminders: push notifications on this device (mnemerynne/push.py, static/sw.js). Each device opts in itself.
  function pushable() { // Needs a modern browser on HTTPS; paired TVs are left out.
    return "serviceWorker" in navigator && "PushManager" in window && "Notification" in window && window.isSecureContext &&
      !document.documentElement.classList.contains("tv");
  }

  function keyBytes(text) { // The server's key arrives as base64url; the browser wants bytes.
    const plain = atob(text.replace(/-/g, "+").replace(/_/g, "/"));
    return Uint8Array.from(plain, function (c) { return c.charCodeAt(0); });
  }

  function tell(subscription) { // Hand the server this device's subscription and its clock offset, for quiet hours.
    const json = subscription.toJSON();
    return G.api(API + "push", { endpoint: json.endpoint, p256dh: json.keys.p256dh, auth: json.keys.auth, utc_offset: -new Date().getTimezoneOffset() });
  }

  function setReminders(value) { reminders = value; lastList = ""; render(); }

  function checkReminders() { // On opening: is this device already subscribed? If so, refresh it quietly.
    if (!pushable()) return;
    if (Notification.permission === "denied") { setReminders("blocked"); return; }
    setReminders("off");
    navigator.serviceWorker.getRegistration("/").then(function (registration) {
      return registration ? registration.pushManager.getSubscription() : null;
    }).then(function (subscription) {
      if (subscription) return tell(subscription).then(function () { setReminders("on"); });
    }).catch(function () { /* reminders are a nicety; Lumi plays on without them */ });
  }

  function toggleReminders() { // Must start from a tap or key press, which is what lets the browser ask permission.
    if (reminders === "on") {
      navigator.serviceWorker.getRegistration("/").then(function (registration) {
        return registration ? registration.pushManager.getSubscription() : null;
      }).then(function (subscription) {
        if (!subscription) return;
        return G.api(API + "push/off", { endpoint: subscription.endpoint }).then(function () { return subscription.unsubscribe(); });
      }).then(function () { setReminders("off"); say("Reminders are off on this device."); })
        .catch(function () { say("Reminders couldn't be switched off just now. Try again in a moment."); });
      return;
    }
    Notification.requestPermission().then(function (answer) {
      if (answer !== "granted") { setReminders(answer === "denied" ? "blocked" : "off"); say("No reminders, then. Lumi will wait for you here."); return; }
      return Promise.all([navigator.serviceWorker.register("/sw.js", { scope: "/" }), G.api(API + "push")]).then(function (both) {
        return navigator.serviceWorker.ready.then(function (registration) {
          return registration.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: keyBytes(both[1].key) });
        });
      }).then(tell).then(function () {
        setReminders("on"); say("Reminders are on for this device. Lumi will nudge you when she needs you, never at night.");
      });
    }).catch(function (problem) { say(problem && problem.status ? problem.message : "Reminders couldn't be switched on in this browser."); });
  }

  function bringOldMemories() { // Happy moments counted in this browser before pets lived on the account move over, once.
    if (snap.pet.imported) return Promise.resolve();
    let old = null;
    try { old = JSON.parse(localStorage.getItem(OLD_SAVE)); } catch (_) { /* nothing to bring */ }
    const kept = R.legacy(old), body = { action: "import", moments: kept.moments };
    if (kept.glow) body.glow = kept.glow;
    return G.api(API + "act", body).then(take).catch(function () { /* she starts fresh; nothing else depends on it */ });
  }

  G.api(API + "state").then(function (data) { // First hello: load her, bring any old memories, then start the clock.
    take(data);
    return bringOldMemories();
  }).then(function () {
    if (snap.fight) { tab = TABS.indexOf("battle"); say(snap.fight.log.slice(-3).join(" ")); } // A fight was waiting: pick it up where it stopped.
    announceDone(); render();
  }).catch(function () {
    say("Lumi can't be reached just now. She'll be here when the connection is back.");
  });
  visibility();
  checkReminders();
})();
