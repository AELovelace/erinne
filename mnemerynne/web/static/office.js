// The ONLYOFFICE editor page (/office/{id}, templates/office.html): fetch the signed config and start the editor.
// The Document Server does the rest; saves reach Mnemerynne through its callback (web/office.py), not this page.
(function () {
  "use strict";
  const page = document.getElementById("office");                       // the page's data-* settings
  if (!page) return;
  const statusEl = document.getElementById("office-status");            // the small status line in the bar
  const errorEl = document.getElementById("office-error");              // the box shown when it can't start

  function say(text) { statusEl.textContent = text; }                   // update the status line

  function fail(text) {                                                  // show why the editor couldn't open
    say("");
    errorEl.textContent = text;
    errorEl.hidden = false;
  }

  async function start() {
    if (typeof window.DocsAPI === "undefined") {                         // api.js didn't load: server down or off
      fail("The Office editor isn't answering right now. Try again in a minute, or use Download.");
      return;
    }
    let cfg;
    try {
      const resp = await fetch("/api/office/" + page.dataset.fileId + "/config", { credentials: "same-origin" });
      const data = await resp.json().catch(function () { return {}; });  // errors come back as {detail: …}
      if (!resp.ok) { fail(data.detail || "This file can't be opened in the editor."); return; }
      cfg = data;
    } catch (err) {
      fail("Couldn't reach " + location.host + ". Check your connection and reload.");
      return;
    }
    cfg.width = "100%";                                                  // fill the frame (sized by CSS)
    cfg.height = "100%";
    cfg.type = window.matchMedia("(pointer: coarse)").matches ? "mobile" : "desktop";   // phones get the mobile UI
    cfg.events = {
      onAppReady: function () { say(page.dataset.mode === "edit" ? "Ready" : "Read only"); },
      onDocumentStateChange: function (ev) { if (page.dataset.mode === "edit") say(ev.data ? "Editing…" : "Saved to the editor"); },
      onRequestClose: function () { location.href = page.dataset.back; },   // the editor's own Close button
      onError: function (ev) {                                           // e.g. download refused, version gone
        fail("The editor reported a problem" + (ev && ev.data && ev.data.errorDescription ? ": " + ev.data.errorDescription : ".")
             + " Reload to try again.");
      },
      onOutdatedVersion: function () { location.reload(); },             // the file changed: open the new one
    };
    // The signed `token` covers the fields above it; width/height/type/events aren't part of the signed document.
    new window.DocsAPI.DocEditor("oo-frame", cfg);
  }

  if (document.readyState === "complete") start();
  else window.addEventListener("load", start);                            // after api.js (deferred) has run
})();
