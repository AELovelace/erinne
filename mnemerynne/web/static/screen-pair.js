/* An unpaired TV (media_pair.html): show a pairing code, ask every 3 s whether someone claimed it, and
   open screen mode once they have (the answer carries the screen key as a cookie). A code that runs out
   is replaced; while the server can't be reached, keep trying. */
(function () {
  "use strict";
  const codeBox = document.getElementById("mc-pair-code");
  const qrBox = document.getElementById("mc-pair-qr");
  const state = document.getElementById("mc-pair-state");
  let timer = null;
  let failing = 0;
  // The screen box (deploy/screen) comes here as ?kiosk=1; Media's page remembers it (media.js).
  let kiosk = false;
  try {
    if (/[?&]kiosk=1\b/.test(location.search)) localStorage.setItem("mc-kiosk", "1");
    kiosk = localStorage.getItem("mc-kiosk") === "1";
  } catch (e) { /* private window */ }

  async function call(method) {
    const r = await fetch("/media/screen/pair", { method: method, credentials: "same-origin",
                                                   headers: { "Accept": "application/json" } });
    if (!r.ok) {
      let detail = "";
      try { detail = (await r.json()).detail || ""; } catch (e) { /* not JSON */ }
      throw new Error(detail || ("The server answered " + r.status + "."));
    }
    return r.json();
  }

  function later(fn, ms) {
    clearTimeout(timer);
    timer = setTimeout(fn, ms);
  }

  async function newCode() {
    try {
      const p = await call("POST");
      codeBox.textContent = p.code;
      qrBox.innerHTML = p.qr;                  // SVG made by the server from the code's own address
      state.textContent = "Waiting for your phone…";
      later(ask, 3000);
    } catch (err) {
      state.textContent = "Can't get a code: " + err.message + " Trying again…";
      failing += 1;
      if (kiosk && failing >= 12) { location.replace("http://127.0.0.1:9187/"); return; }
      later(newCode, 10000);
    }
  }

  async function ask() {
    try {
      const s = await call("GET");
      if (s.state === "paired") { state.textContent = "Paired. Starting…"; location.replace(s.url); return; }
      failing = 0;
      if (s.state === "expired") { newCode(); return; }
      later(ask, 3000);
    } catch (err) {
      state.textContent = "Can't reach the server. Trying again…";
      failing += 1;
      if (kiosk && failing >= 12) { location.replace("http://127.0.0.1:9187/"); return; }   // the box's own page
      later(ask, 10000);
    }
  }

  newCode();
})();
