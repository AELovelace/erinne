// Page width picker (_width_picker.html), on the file view and the narrow pages (Automator, Proposals, Messages,
// Access). The 50/75/100% radios already work from CSS alone (erynne.css .doc-width); this file remembers the pick
// in this browser and runs the Custom number box.
// It is the only script a read-only file view may run (web/files.py _view_csp_width), so it stays tiny.
(function () {
  "use strict";
  var bar = document.querySelector(".doc-width");
  if (!bar) return;                                             // no picker on this page: nothing to do
  var KEY = bar.getAttribute("data-key") || "erynne.docWidth";  // documents and pages remember their own pick:
                                                                // "50" | "75" | "100" | "custom:NN"
  bar.classList.add("dw-js");                                   // shows the Custom button (CSS hides it without us)
  var box = document.getElementById("dw-value");                // the Custom percent box

  function load() {                                             // read the saved pick; private windows may throw
    try { return localStorage.getItem(KEY) || ""; } catch (e) { return ""; }
  }
  function save(v) {                                            // remember the pick; fine if storage is blocked
    try { localStorage.setItem(KEY, v); } catch (e) { /* the pick still applies to this page */ }
  }
  function clamp(n) {                                           // keep Custom between 20% and 100%
    n = Math.round(Number(n));
    return isFinite(n) ? Math.min(100, Math.max(20, n)) : 60;
  }
  function setCustom(n) {                                       // feed the Custom width to the CSS (--doc-w-custom)
    document.documentElement.style.setProperty("--doc-w-custom", clamp(n) + "%");
  }

  // Put back what this browser picked last time.
  var saved = load();
  if (saved.indexOf("custom:") === 0) {                         // "custom:65" -> tick Custom, 65 in the box
    box.value = clamp(saved.slice(7));
    setCustom(box.value);
    document.getElementById("dw-custom").checked = true;
  } else if (saved && document.getElementById("dw-" + saved)) {
    document.getElementById("dw-" + saved).checked = true;      // one of the presets
  }

  // A preset or Custom clicked: apply and remember it.
  bar.addEventListener("change", function (e) {
    var t = e.target;
    if (t.name === "doc-width") {
      if (t.value === "custom") { setCustom(box.value); save("custom:" + clamp(box.value)); box.focus(); }
      else save(t.value);
    }
  });
  // The Custom box typed in or stepped: resize as you go and remember it.
  box.addEventListener("input", function () {
    if (!box.value) return;                                     // half-typed: wait for a number
    setCustom(box.value);
    save("custom:" + clamp(box.value));
  });
  box.addEventListener("change", function () { box.value = clamp(box.value); });   // tidy out-of-range on leave
})();
