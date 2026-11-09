/* The phone's remote mode (/media/panel): the Erynne remote panel (the Mnemerynne-tab ESP32 firmware) on a phone.
   It is a real paired remote: it holds a remote key and uses the same /api/media/remote routes, so it can do
   what a panel can (a seat at a casino table included) and nothing more. Its timing follows the firmware too.
   Keep this in step with Mnemerynne-tab.ino when the panel changes. */
(function () {
  "use strict";

  const KEY_STORE = "mc-panel-key";         // localStorage: this phone's remote key
  const SCREEN_STORE = "mc-panel-screen";   // localStorage: the screen last chosen here
  const ROWS = 8;                           // rows on a Screens or Channels page, as on the panel
  const ACTIVE_MS = 60000;                  // "someone is using it" lasts this long after a tap
  const POLL_ACTIVE = 2000, POLL_IDLE = 10000, POLL_AFTER = 500;   // the panel's three poll intervals
  const VOLUME_MEMORY = 4000;               // quick volume taps build on the last one sent for this long
  const UNPAIR_MS = 5000;                   // Unpair's second tap must come within this
  const STATUS_MS = 4000;                   // a status message clears itself after this
  const TIMEOUT_MS = 7000;                  // the panel's HTTP timeout
  const CODE_ASK = 3000, CODE_RETRY = 10000;   // how often a code asks if it was claimed, and how long after a failure
  const KINDS = { idle: "Ready, nothing playing", video: "Film", live: "Live TV", youtube: "YouTube", audio: "Music",   // what the card calls each state
                  radio: "Radio", pictures: "Pictures", signage: "Signage", calibrating: "Adjusting screen size",
                  in_use: "Someone is using the TV", game: "Game" };

  const $ = function (id) { return document.getElementById(id); };   // shorthand for this page's fixed elements
  const signedIn = $("mp").dataset.signedIn === "1";                  // decides how this phone can pair

  let key = stored(KEY_STORE);               // this phone's remote key, or null while unpaired
  let chosen = Number(stored(SCREEN_STORE)) || null;   // the chosen screen's id
  let view = "pair";                         // which page shows: pair, remote, screens or channels
  let remote = { name: "", screens: [] };    // the last good answer from the server
  let stale = "";                            // why the last poll failed ("" when it worked)
  let receivedAt = 0;                        // when `remote` arrived, to move the progress bar between reports
  let touchedAt = Date.now();                // the last tap, which picks the poll interval
  let timer = null, polling = false, soon = false;   // the next poll, one in flight, and "poll quickly after this one"
  let sends = Promise.resolve();             // commands go one at a time, so quick taps arrive in order
  let volumeSent = 0.5, volumeSentAt = 0;    // the last volume sent, since the TV's report lags
  let screensPage = 0, unpairArmedAt = 0;    // the Screens page shown, and when Unpair was first tapped
  let channels = null, channelOffset = 0, channelSerial = 0;   // the Channels page, where it starts, and which request is newest
  let statusTimer = null, statusText = "";   // the temporary status line
  let padTimer = null, padLit = null;        // the D-pad key currently flashing
  let paintedRows = "";                      // what the list rows now show, so a poll doesn't rebuild them
  let codeSecret = null, codeTimer = null;   // the pairing code's secret, and its next step

  // ------------------------------------------------------------------ storage
  function stored(name) {   // localStorage can be blocked: the phone then stays paired only until the page closes
    try { return localStorage.getItem(name); } catch (e) { return null; }
  }
  function store(name, value) {   // saves a value, or removes it when value is null
    try { if (value === null) localStorage.removeItem(name); else localStorage.setItem(name, value); } catch (e) { /* blocked */ }
  }

  // ---------------------------------------------------------------- talking to the server
  function detail(data, fallback) {   // the server's message when it's readable text
    return data && typeof data.detail === "string" ? data.detail : fallback;
  }

  /* One request without cookies, with the panel's timeout: like a panel, this phone has no sign-in to send. */
  async function request(method, path, body, headers) {
    const stop = new AbortController();     // lets the timeout cancel the request
    const limit = setTimeout(function () { stop.abort(); }, TIMEOUT_MS);
    try {
      const response = await fetch(path, { method: method, credentials: "omit", signal: stop.signal,
                                           headers: Object.assign({}, headers, body ? { "Content-Type": "application/json" } : {}),
                                           body: body ? JSON.stringify(body) : undefined });
      const data = await response.json().catch(function () { return null; });   // an error page has no JSON
      return { response: response, data: data };
    } catch (err) {
      throw new Error("No answer from the server");   // offline, or the timeout fired
    } finally {
      clearTimeout(limit);
    }
  }

  /* One call with this phone's remote key. */
  async function call(method, path, body) {
    const asked = key;                      // the key this call used, in case the phone unpairs meanwhile
    const answer = await request(method, path, body, { Authorization: "Bearer " + asked });
    const response = answer.response, data = answer.data;
    if (response.status === 401 && asked === key) forget("This remote was removed. Pair it again.");   // like the panel, a refused key is erased
    if (!response.ok) throw new Error(detail(data, "The server refused that (" + response.status + ")"));
    return data;
  }

  /* Erases the key and goes back to the pair page. */
  function forget(message) {
    key = null;                     // later answers to the old key are ignored
    stale = "";                     // nothing is out of date on the pair page
    store(KEY_STORE, null);         // this phone is no longer a remote
    clearTimeout(timer);            // nothing left to poll
    remote = { name: "", screens: [] };   // shows nothing from the old pairing
    show("pair");
    setStatus(message);
  }

  /* Asks for the screens and what they are playing, then schedules the next ask. */
  async function poll() {
    if (polling || !key) { soon = true; return; }   // one at a time: a poll in flight picks this up when it ends
    polling = true;
    const asked = key;                              // the key this poll belongs to
    try {
      const data = await call("GET", "/api/media/remote");
      if (asked !== key) return;                    // unpaired meanwhile: drop the answer
      remote = data;                                // the new screens and states
      receivedAt = Date.now();                      // the progress bar moves on from here
      stale = "";                                   // the connection is good again
      if (!current() && remote.screens.length) choose(remote.screens[0].id);   // a removed screen falls back to the first
    } catch (err) {
      if (asked === key) stale = err.message;       // keeps the last screens: the buttons still work
    } finally {
      polling = false;
      if (key) {
        paint();
        schedule(soon ? POLL_AFTER : interval());   // quickly after a command, otherwise at the usual pace
        soon = false;
      }
    }
  }
  function interval() { return Date.now() - touchedAt < ACTIVE_MS ? POLL_ACTIVE : POLL_IDLE; }   // faster while in use
  function schedule(ms) {   // the next poll; none while the phone isn't showing this page
    clearTimeout(timer);
    timer = key && !document.hidden ? setTimeout(poll, ms) : null;
  }

  // ------------------------------------------------------------------ actions
  function current() {   // the chosen screen, if it is still one of the owner's
    return remote.screens.find(function (s) { return s.id === chosen; }) || null;
  }
  function choose(id) {   // chooses a screen, remembered across restarts as on the panel
    chosen = id;
    store(SCREEN_STORE, String(id));
  }

  /* Queues a command for the chosen screen. */
  function send(command) {
    const s = current();
    if (!s) { setStatus("Choose a screen first"); return; }                            // the panel's own message
    if (!s.online) { setStatus(s.name + " isn't connected: is Media open on it?"); return; }
    setStatus("Sending...");
    sends = sends.then(async function () {                                             // waits for the taps before this one
      try {
        await call("POST", "/api/media/remote/screens/" + s.id + "/command", command);
        setStatus("");                                                                 // sent: the card will show the result
        if (polling) soon = true; else schedule(POLL_AFTER);                           // shows the result quickly
      } catch (err) {
        if (key) setStatus(err.message);                                               // e.g. the 409 for a TV that went away
      }
    });
  }

  /* Vol - and Vol +: 10% steps, building on the last one sent because the TV's report lags. */
  function stepVolume(delta) {
    const s = current();
    const reported = s && s.state && typeof s.state.volume === "number" ? s.state.volume : 0.5;   // 0.5 if the TV hasn't said
    const base = Date.now() - volumeSentAt < VOLUME_MEMORY ? volumeSent : reported;               // the fresher of the two
    volumeSent = Math.min(1, Math.max(0, Math.round((base + delta) * 10) / 10));                  // keeps to whole 10% steps
    volumeSentAt = Date.now();
    send({ action: "volume", level: volumeSent });
  }

  function flash(button) {   // lights a D-pad key for a moment, as the panel does after a tap
    if (padLit) padLit.classList.remove("mp-lit");
    padLit = button;
    button.classList.add("mp-lit");
    clearTimeout(padTimer);
    padTimer = setTimeout(function () { button.classList.remove("mp-lit"); padLit = null; }, 200);
  }

  async function loadChannels(offset) {   // one page of channels; an answer to an older request is ignored
    const serial = ++channelSerial;
    channelOffset = offset;
    channels = null;                      // shows Loading until it arrives
    paint();
    let result;
    try {
      result = await call("GET", "/api/media/remote/channels?offset=" + offset + "&limit=" + ROWS);
    } catch (err) {
      result = { error: err.message };    // shown on the list's line, with Refresh to try again
    }
    if (serial !== channelSerial) return;
    channels = result;
    paint();
  }

  async function unpair() {   // removes this remote from the server, then erases the key here
    setStatus("Unpairing...");
    try {
      await call("DELETE", "/api/media/remote");
    } catch (err) {
      if (key) { setStatus(err.message); return; }   // still paired: says why and stays
    }
    if (key) forget("Unpaired.");
  }

  /* Pairing: the signed-in owner asks for a key for this phone. Uses the session, the only call here that does. */
  async function pair(name) {
    setStatus("Pairing...");
    let response, data = null;
    try {
      response = await fetch("/api/media/panel/pair", { method: "POST", credentials: "same-origin",   // the sign-in cookie proves who is pairing
                                                        headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name: name }) });
      data = await response.json().catch(function () { return null; });
    } catch (err) { setStatus("No answer from the server"); return; }
    if (!response.ok) { setStatus(detail(data, "Couldn't pair this phone")); return; }   // e.g. the name is taken
    key = data.key;                 // from now on this phone is known by its key
    store(KEY_STORE, key);
    setStatus("");
    show("remote");
    poll();
  }

  /* Pairing by code, for a phone with no sign-in: the panel's own flow, answered from any signed-in device. */
  function stopCode() {   // ends the code's asking, when this phone pairs or leaves the pair page
    clearTimeout(codeTimer);
    codeTimer = null;
    codeSecret = null;
  }
  function codeLater(step, ms) {   // the code's next step
    clearTimeout(codeTimer);
    codeTimer = setTimeout(step, ms);
  }
  function keyless(path, body) {   // a pairing call: answers the JSON, or throws the server's message
    return request("POST", path, body, {}).then(function (a) {
      if (!a.response.ok) throw new Error(detail(a.data, "The server refused that (" + a.response.status + ")"));
      return a.data;
    });
  }
  async function newCode() {   // asks for a code to show; a code that runs out comes back here
    if (view !== "pair") return;
    try {
      const p = await keyless("/media/remote/pair");
      if (view !== "pair") return;              // left the page while it was on its way
      codeSecret = p.secret;
      $("mp-code").textContent = p.code;
      $("mp-code-line").textContent = "Waiting for it to be claimed. This page changes by itself.";
      codeLater(askCode, CODE_ASK);
    } catch (err) {
      $("mp-code-line").textContent = "Can't get a code: " + err.message + ". Trying again...";
      codeLater(newCode, CODE_RETRY);
    }
  }
  async function askCode() {   // has someone claimed the code? Only while the phone is showing this page
    if (document.hidden) { codeLater(askCode, CODE_ASK); return; }
    const secret = codeSecret;                  // the code this answer is about
    try {
      const s = await keyless("/media/remote/pair/status", { secret: secret });
      if (secret !== codeSecret) return;        // replaced or stopped meanwhile
      if (s.state === "paired") {
        key = s.key;                            // the key comes once, and is kept
        store(KEY_STORE, key);
        show("remote");
        poll();
      } else if (s.state === "expired") newCode();
      else codeLater(askCode, CODE_ASK);        // still waiting
    } catch (err) {
      if (secret !== codeSecret) return;
      $("mp-code-line").textContent = "Can't reach the server. Trying again...";
      codeLater(askCode, CODE_RETRY);
    }
  }

  // ---------------------------------------------------------------- painting
  function setStatus(text) {   // a message that clears itself, or "" to clear it now
    statusText = text;
    clearTimeout(statusTimer);
    if (text) statusTimer = setTimeout(function () { setStatus(""); }, STATUS_MS);
    paintStatus();
  }
  function paintStatus() {   // the bottom line: a message, or STALE plus why the last poll failed
    const line = statusText || (stale ? (remote.screens.length ? "STALE: " + stale : stale) : remote.name ? remote.name + " · " + remote.owner : "");
    $("mp-status").textContent = line;
    $("mp-status").classList.toggle("mp-alert", Boolean(statusText || stale));
  }
  function kindLabel(s) {   // what a screen is doing, in the panel's words
    if (!s.online) return "Not connected";
    if (!s.state) return "Connected";
    return KINDS[s.state.kind] || s.state.kind || "Connected";
  }
  function clock(seconds) {   // 75 -> 1:15, 3675 -> 1:01:15
    const s = Math.max(0, Math.floor(seconds || 0));
    const h = Math.floor(s / 3600), m = Math.floor(s / 60) % 60, rest = String(s % 60).padStart(2, "0");
    return h ? h + ":" + String(m).padStart(2, "0") + ":" + rest : m + ":" + rest;
  }

  function show(name) {   // switches page: each one has its own section and title
    view = name;
    unpairArmedAt = 0;    // leaving Screens disarms Unpair
    const list = name === "screens" || name === "channels";
    $("mp-pair").hidden = name !== "pair";
    $("mp-remote").hidden = name !== "remote";
    $("mp-list").hidden = !list;
    if (name !== "pair") stopCode();                     // a code is only wanted on the pair page
    else if (!signedIn && !codeTimer) newCode();         // signed out: gets a code to show
    paint();
  }

  /* The moving part of the card: between reports it runs on from the last position. */
  function paintProgress() {
    const s = current();
    const st = s && s.online ? s.state : null;
    const has = st && typeof st.position === "number";
    $("mp-progress").hidden = !has;
    if (!has) return;
    const moving = !st.paused && ["video", "audio", "youtube"].includes(st.kind);   // only these advance by themselves
    const hasEnd = typeof st.duration === "number" && st.duration > 0;
    let position = st.position + (moving ? (Date.now() - receivedAt) / 1000 : 0);
    if (hasEnd) position = Math.min(position, st.duration);                          // never past the end
    $("mp-track").hidden = !hasEnd;                                                  // a live stream has no bar
    $("mp-fill").style.width = hasEnd ? (100 * position / st.duration) + "%" : "0";
    $("mp-time").textContent = hasEnd ? clock(position) + " / " + clock(st.duration) : clock(position) + "  LIVE";
    $("mp-time").classList.toggle("mp-live", !hasEnd);
  }

  function paintRemote() {   // the card and the Play button
    const s = current();
    const st = s && s.online ? s.state : null;                                       // what's shown only for a connected screen
    $("mp-title").textContent = "Remote";
    $("mp-dot").classList.toggle("mp-online", Boolean(s && s.online));
    $("mp-name").textContent = s ? s.name : remote.screens.length ? "Choose a screen" : "No screens: pair a TV on your phone";
    $("mp-state").textContent = s ? kindLabel(s) + (st && st.paused ? " - paused" : "") : "";
    $("mp-now").textContent = st && st.kind !== "idle" ? st.title || "" : "";
    $("mp-sub").textContent = st && st.kind !== "idle" ? st.sub || "" : "";
    let levels = "";                                                                 // "Vol 40%  Night 25%", or "Muted"
    if (st) levels = st.muted ? "Muted" : typeof st.volume === "number" ? "Vol " + Math.round(st.volume * 100) + "%" : "";
    if (st && st.night > 0.004) levels += "  Night " + Math.round(st.night * 100) + "%";
    $("mp-levels").textContent = levels;
    $("mp-play").textContent = st && !st.paused && st.kind !== "idle" ? "Pause" : "Play";
    paintProgress();
  }

  function row(title, sub, chosenRow, onTap) {   // one two-line row on a list page
    const b = document.createElement("button");
    b.type = "button";
    b.className = "mp-item" + (chosenRow ? " mp-chosen" : "");
    b.append(document.createTextNode(title));
    const small = document.createElement("small");
    small.textContent = sub;
    b.append(small);
    b.addEventListener("click", onTap);
    return b;
  }

  /* Rebuilds the rows only when they change: a rebuild under a finger would lose that tap. */
  function setRows(shown, build) {
    const signature = JSON.stringify(shown);          // everything the rows show
    if (signature === paintedRows) return;            // the same as what's there
    paintedRows = signature;
    $("mp-rows").replaceChildren(...build());
  }

  function paintScreens() {   // your screens, a page at a time, with Unpair
    const all = remote.screens;
    screensPage = Math.min(screensPage, Math.max(0, Math.ceil(all.length / ROWS) - 1));   // a shorter list can strand the page
    $("mp-title").textContent = "Choose a screen";
    const page = all.slice(screensPage * ROWS, (screensPage + 1) * ROWS).map(function (s) {
      const playing = s.online && s.state && s.state.kind !== "idle" && s.state.title;    // its title beats its kind
      return { id: s.id, name: s.name, sub: playing ? s.state.title : kindLabel(s), chosen: s.id === chosen };
    });
    setRows(["screens", page], function () {
      return page.map(function (s) { return row(s.name, s.sub, s.chosen, function () { choose(s.id); show("remote"); }); });
    });
    $("mp-line").textContent = all.length ? "" : "No screens: pair a TV on your phone";
    $("mp-prev").disabled = screensPage === 0;
    $("mp-next").disabled = (screensPage + 1) * ROWS >= all.length;
    const armed = unpairArmedAt && Date.now() - unpairArmedAt < UNPAIR_MS;                // two taps, so a stray touch can't unpair it
    $("mp-extra").textContent = armed ? "Tap again" : "Unpair";
    $("mp-extra").classList.toggle("mp-armed", Boolean(armed));
  }

  function paintChannels() {   // Live TV streams, then radio stations, a page at a time from the server
    const s = current();
    $("mp-title").textContent = "Play on " + (s ? s.name : "a screen");
    const ready = channels && !channels.error;
    const list = ready ? channels.channels : [];                  // nothing while loading or after an error
    setRows(["channels", list], function () {
      return list.map(function (c) {
        const station = c.kind === "station";
        return row(c.name, station ? "Radio" : "Live", false, function () {
          send(station ? { action: "play_station", key: c.key } : { action: "play_source", source_id: c.id });
          show("remote");
        });
      });
    });
    $("mp-line").textContent = !channels ? "Loading channels..." : channels.error ? channels.error
      : channels.total ? "Channels " + (channels.offset + 1) + "-" + (channels.offset + channels.channels.length) + " of " + channels.total
      : "No live streams or stations yet";
    $("mp-prev").disabled = !ready || channelOffset === 0;
    $("mp-next").disabled = !ready || channelOffset + ROWS >= channels.total;
    $("mp-extra").textContent = "Refresh";
    $("mp-extra").classList.remove("mp-armed");
  }

  function paintPair() {   // the name form when signed in, otherwise a way to sign in
    $("mp-title").textContent = "Pair this remote";
    $("mp-pair-form").hidden = !signedIn;
    $("mp-code-box").hidden = signedIn;
    $("mp-signin").hidden = signedIn;
  }

  function paint() {   // draws whichever page is showing
    if (view === "pair") paintPair();
    else if (view === "remote") paintRemote();
    else if (view === "screens") paintScreens();
    else paintChannels();
    paintStatus();
  }

  // ------------------------------------------------------------------- taps
  /* Any tap counts as use, so the poll speeds up; a tap after an idle spell polls at once. */
  document.addEventListener("pointerdown", function () {
    const wasIdle = Date.now() - touchedAt >= ACTIVE_MS;
    touchedAt = Date.now();
    if (wasIdle && key) schedule(0);
  });

  /* The buttons on the remote page say what they send in their data- attributes (media_panel.html). */
  $("mp-remote").addEventListener("click", function (event) {
    const b = event.target.closest("button");
    if (!b) return;
    if (b.dataset.pad) { flash(b); send({ action: "press", button: b.dataset.pad }); }   // an arrow or Enter
    else if (b.dataset.step) stepVolume(Number(b.dataset.step));                         // Vol - or Vol +
    else if (b.dataset.action) {
      const command = { action: b.dataset.action };
      if (b.dataset.by) command.by = Number(b.dataset.by);                               // -10s and +30s
      send(command);
    }
  });

  $("mp-open-screens").addEventListener("click", function () {   // opens Screens on the page holding the chosen one
    const at = remote.screens.findIndex(function (s) { return s.id === chosen; });
    screensPage = at > 0 ? Math.floor(at / ROWS) : 0;
    show("screens");
  });
  $("mp-open-channels").addEventListener("click", function () {   // Channels needs a screen to play on
    if (!current()) { setStatus("Choose a screen first"); return; }
    show("channels");
    loadChannels(0);
  });
  $("mp-back").addEventListener("click", function () { show("remote"); });
  $("mp-prev").addEventListener("click", function () {
    if (view === "screens") { screensPage -= 1; paint(); } else loadChannels(Math.max(0, channelOffset - ROWS));
  });
  $("mp-next").addEventListener("click", function () {
    if (view === "screens") { screensPage += 1; paint(); } else loadChannels(channelOffset + ROWS);
  });
  $("mp-extra").addEventListener("click", function () {   // Screens: Unpair (tap twice). Channels: Refresh.
    if (view === "channels") { loadChannels(channelOffset); return; }
    if (!unpairArmedAt || Date.now() - unpairArmedAt >= UNPAIR_MS) {
      unpairArmedAt = Date.now();                         // the first tap arms it
      paint();
      setTimeout(function () { if (view === "screens") paint(); }, UNPAIR_MS + 50);   // shows it disarming by itself
      return;
    }
    unpairArmedAt = 0;
    unpair();
  });
  $("mp-pair-form").addEventListener("submit", function (event) {
    event.preventDefault();                               // stays on this page
    pair($("mp-pair-name").value);
  });

  document.addEventListener("visibilitychange", function () {   // no polling in the background; fresh on return
    if (document.hidden) clearTimeout(timer); else if (key) { touchedAt = Date.now(); schedule(0); }
  });
  setInterval(function () { if (view === "remote" && !document.hidden) paintProgress(); }, 1000);   // the bar moves between polls

  // -------------------------------------------------------------------- start
  show(key ? "remote" : "pair");   // a paired phone opens straight on the remote
  if (key) poll();
}());
