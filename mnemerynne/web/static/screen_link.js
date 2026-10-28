/* A screen box's TV on any page but the TV page (base.html; the TV page is media.js's screenMode): it still
   hears the remote. A command from a phone gives the person using the TV TAKEOVER seconds to keep it (Keep
   using the TV, Escape or Backspace); then, or at once when an admin overrides, open documents are saved as
   they are (the "mnemerynne:takeover" event, mdeditor.js) and the TV goes back to the TV page, which signs
   out whoever was signed in on it and carries the command out (handed over in sessionStorage). The screen
   answers how each command went (web/media.py screen_answer), which the phone shows. Nothing here uses
   innerHTML. */
(function () {
  "use strict";
  const TAKEOVER = 10;                 // seconds: media/screens.py TAKEOVER_SECONDS
  const HANDOVER = "mc-takeover";      // sessionStorage: the command the TV page carries out (media.js)
  const SAVE_WAIT_MS = 8000;           // the longest wait for open documents to save
  let pending = null, timer = null, left = 0, box = null, going = false, before = null;

  function el(tag, cls, text) {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text) n.textContent = text;
    return n;
  }

  function send(method, url, body) {
    return fetch(url, { method: method, credentials: "same-origin", body: JSON.stringify(body),
                        headers: { "Content-Type": "application/json", "Accept": "application/json" } })
      .catch(function () { /* the server is away: the phone stops asking after a while */ });
  }

  function answer(cmd, state, seconds) {
    if (!cmd || !cmd.id) return;
    send("POST", "/media/screen/commands/" + encodeURIComponent(cmd.id),
         seconds === undefined ? { state: state } : { state: state, seconds: seconds });
  }

  // What the phone's remote shows for this screen while it's on another page.
  function report() { send("PUT", "/api/media/screen/state", { kind: "in_use", title: "Someone is using the TV" }); }

  // ------------------------------------------------------------ the countdown
  function build() {
    box = el("div", "tv-takeover");
    box.setAttribute("role", "alertdialog");
    box.setAttribute("aria-labelledby", "tv-takeover-title");
    box.setAttribute("aria-describedby", "tv-takeover-text");
    const card = el("div", "tv-takeover-card glass");
    const title = el("h2");
    title.id = "tv-takeover-title";
    const text = el("p", "muted");
    text.id = "tv-takeover-text";
    const count = el("div", "tv-count");
    count.id = "tv-takeover-count";
    const keep = el("button", null, "Keep using the TV");
    keep.type = "button";
    keep.id = "tv-takeover-keep";
    keep.addEventListener("click", keepIt);
    card.append(title, text, count, keep);
    box.appendChild(card);
    document.body.appendChild(box);
  }

  function show(title, text, count, canKeep) {
    const fresh = !box;
    if (fresh) build();
    if (fresh || box.hidden) before = document.activeElement;   // focus goes back there when it's kept
    document.getElementById("tv-takeover-title").textContent = title;
    document.getElementById("tv-takeover-text").textContent = text;
    document.getElementById("tv-takeover-count").textContent = count;
    const keep = document.getElementById("tv-takeover-keep");
    keep.hidden = !canKeep;
    box.hidden = false;
    if (canKeep) keep.focus();
  }

  function who(cmd) { return cmd.sender ? cmd.sender + "'s phone" : "A phone"; }

  function ask(cmd) {
    clearInterval(timer);                // a newer command takes the place of one still counting down
    pending = cmd;
    left = TAKEOVER;
    show("📱 " + who(cmd) + " wants the TV", "The TV goes back to its page in a moment, unless you keep it.",
         String(left), true);
    answer(cmd, "waiting", left);
    timer = setInterval(function () {
      left -= 1;
      if (left > 0) { document.getElementById("tv-takeover-count").textContent = String(left); return; }
      clearInterval(timer);
      go(pending);
    }, 1000);
  }

  function keepIt() {
    if (!pending || going) return;
    clearInterval(timer);
    answer(pending, "kept");
    pending = null;
    box.hidden = true;
    if (before && before.isConnected && before.focus) before.focus();
  }

  async function go(cmd) {
    if (going || !cmd) return;
    going = true;
    clearInterval(timer);
    pending = null;
    show(cmd.override ? "📱 " + who(cmd) + " took over the TV" : "Going back to the TV…", "Saving what's open…", "", false);
    // Open documents save themselves as they are (mdeditor.js); a stuck save doesn't keep the TV here.
    const waits = [];
    window.dispatchEvent(new CustomEvent("mnemerynne:takeover", { detail: { wait: function (p) { waits.push(p); } } }));
    if (waits.length) {
      await Promise.race([Promise.allSettled(waits), new Promise(function (r) { setTimeout(r, SAVE_WAIT_MS); })]);
    }
    try { sessionStorage.setItem(HANDOVER, JSON.stringify(cmd)); } catch (e) { /* the TV page can't carry it out */ }
    location.href = "/media/screen";
  }

  function onCommand(cmd) {
    const a = cmd.action;
    if (a === "unpaired") { location.replace("/media/screen"); return; }   // it shows its pairing code
    if (a === "signage") return;          // the programme changed: the TV page loads it when it's back
    if (a === "override") {
      if (pending && pending.id === cmd.id) go(Object.assign({}, pending, { override: true, sender: cmd.sender || pending.sender }));
      return;
    }
    if (going) return;
    if (cmd.override) go(cmd); else ask(cmd);
  }

  window.addEventListener("keydown", function (e) {
    if (!box || box.hidden) return;
    // While it counts down, Escape or Backspace keeps the TV (Backspace would go back a page otherwise).
    if (e.key === "Escape" || e.key === "Backspace" || going) {
      e.preventDefault();
      e.stopImmediatePropagation();
      if (!going) keepIt();
    }
  }, true);

  const es = new EventSource("/media/screen/stream");
  es.addEventListener("hello", report);
  es.addEventListener("cmd", function (e) {
    let cmd;
    try { cmd = JSON.parse(e.data); } catch (err) { return; }
    onCommand(cmd);
  });
  setInterval(report, 5000);
})();
