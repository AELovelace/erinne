/* Kiosk settings on a screen box's TV (media.html, screen mode; web/kiosk.py). The menu item shows only on
   the box (media.js remembers it as "mc-kiosk"). It asks the server for a code, shows it with a QR code for
   an admin's phone, and once approved opens the box's own settings page (deploy/screen/wifi-setup.py) with
   the one-time grant in the URL's fragment, which the box hands back to the server. Or, without a phone,
   the settings page's PIN. Escape or Backspace closes it. Nothing here uses innerHTML but the server's QR SVG. */
(function () {
  "use strict";
  const KIOSK_HOME = "http://127.0.0.1:9187/";
  const button = document.getElementById("mc-kiosk");
  if (!button) return;
  let onBox = false;
  try { onBox = localStorage.getItem("mc-kiosk") === "1"; } catch (e) { /* private window */ }
  button.hidden = !onBox;
  if (!onBox) return;

  let box = null, timer = null, secret = null;

  function el(tag, cls, text) {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text) n.textContent = text;
    return n;
  }

  function build() {
    box = el("div", "mc-splash mc-pair mc-kiosk-unlock");
    box.setAttribute("role", "dialog");
    box.setAttribute("aria-label", "Kiosk settings");
    box.style.zIndex = "96";                     // over signage and the player
    box.appendChild(el("div", "mc-splash-name", "⚙ Kiosk settings"));
    const row = el("div", "mc-pair-row");
    const qr = el("div", "mc-pair-qr");
    qr.id = "mc-kiosk-qr";
    qr.setAttribute("aria-hidden", "true");
    const steps = el("div", "mc-pair-steps");
    steps.appendChild(el("p", null, "An admin scans this with their phone, or opens " + location.host + "/media/kiosk and types:"));
    const code = el("div", "mc-pair-code", "…");
    code.id = "mc-kiosk-code";
    steps.appendChild(code);
    row.append(qr, steps);
    box.appendChild(row);
    const state = el("div", "mc-splash-state", "Getting a code…");
    state.id = "mc-kiosk-state";
    box.appendChild(state);
    const buttons = el("div", "mc-kiosk-buttons");
    const pin = el("button", "secondary", "Use the kiosk PIN");
    pin.type = "button";
    pin.addEventListener("click", function () { location.href = KIOSK_HOME + "settings"; });
    const cancel = el("button", "secondary", "Cancel");
    cancel.type = "button";
    cancel.addEventListener("click", close);
    buttons.append(pin, cancel);
    box.appendChild(buttons);
    document.body.appendChild(box);
  }

  async function call(method, query) {
    const r = await fetch("/media/screen/kiosk/unlock" + (query || ""), {
      method: method, credentials: "same-origin", headers: { "Accept": "application/json" } });
    const body = await r.json().catch(function () { return {}; });
    if (!r.ok) throw new Error(body.detail || ("The server answered " + r.status + "."));
    return body;
  }

  function later(fn, ms) { clearTimeout(timer); timer = setTimeout(fn, ms); }

  async function start() {
    try {
      const u = await call("POST");
      secret = u.secret;
      document.getElementById("mc-kiosk-code").textContent = u.code;
      document.getElementById("mc-kiosk-qr").innerHTML = u.qr;   // SVG made by the server from its own address
      document.getElementById("mc-kiosk-state").textContent = "Waiting for an admin…";
      later(ask, 2000);
    } catch (err) {
      document.getElementById("mc-kiosk-state").textContent = "Can't get a code: " + err.message;
    }
  }

  async function ask() {
    if (!box || box.hidden) return;
    try {
      const s = await call("GET", "?secret=" + encodeURIComponent(secret));
      if (s.state === "approved") {
        document.getElementById("mc-kiosk-state").textContent = "Approved by " + s.admin + ". Opening…";
        location.href = KIOSK_HOME + "settings#grant=" + encodeURIComponent(s.grant);
        return;
      }
      if (s.state === "expired") { start(); return; }
      later(ask, 2000);
    } catch (err) {
      document.getElementById("mc-kiosk-state").textContent = "Can't reach the server. Trying again…";
      later(ask, 5000);
    }
  }

  function open() {
    if (!box) build();
    box.hidden = false;
    box.querySelector("button").focus();
    start();
  }

  function close() {
    clearTimeout(timer);
    if (box) box.hidden = true;
    button.focus();
  }

  button.addEventListener("click", open);
  window.addEventListener("keydown", function (e) {
    if (!box || box.hidden) return;
    if (e.key === "Escape" || e.key === "Backspace") {
      e.preventDefault();
      e.stopImmediatePropagation();
      close();
    }
  }, true);
})();
