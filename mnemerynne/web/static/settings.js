/* Preferences: theme, password, sign out other browsers, and the memories Erynne keeps.
   (Microsoft 365 linking on this page is m365.js.) */
(function () {
  "use strict";

  const { api } = window.Erynne;
  const $ = function (id) { return document.getElementById(id); };

  function note(id, text, ok) {
    const node = $(id);
    node.textContent = text;
    node.className = ok ? "ok-note" : "error";
    node.hidden = !text;
  }

  // Themes switch at once (the base stylesheets are always loaded, scoped to data-theme; a theme's own
  // stylesheet and attributes come from its radio, base.html's shape), then save.
  const LOOK_ATTRS = ["data-scene", "data-border", "data-flat", "data-texture", "data-fx"];
  function currentLook() {
    const html = document.documentElement;
    const link = document.getElementById("look-css");
    const attrs = {};
    LOOK_ATTRS.forEach(function (a) { if (html.hasAttribute(a)) attrs[a] = html.getAttribute(a); });
    return { slug: html.dataset.look || "", base: html.dataset.theme, css: link ? link.getAttribute("href") : "", attrs: attrs };
  }
  function applyLook(look) {
    const html = document.documentElement;
    html.dataset.theme = look.base;
    if (look.slug) html.dataset.look = look.slug; else delete html.dataset.look;
    LOOK_ATTRS.forEach(function (a) { html.removeAttribute(a); });
    Object.keys(look.attrs || {}).forEach(function (a) { html.setAttribute(a, look.attrs[a]); });
    let link = document.getElementById("look-css");
    if (look.css) {
      if (!link) { link = document.createElement("link"); link.rel = "stylesheet"; link.id = "look-css"; document.head.appendChild(link); }
      if (link.getAttribute("href") !== look.css) link.setAttribute("href", look.css);
    } else if (link) link.remove();
    const meta = document.querySelector('meta[name="color-scheme"]');
    if (meta) meta.content = look.base === "neon" ? "dark" : "light dark";
  }
  function lookOf(radio) {
    let attrs = {};
    try { attrs = JSON.parse(radio.dataset.attrs || "{}"); } catch (e) { /* none */ }
    return { slug: radio.value, base: radio.dataset.base, css: radio.dataset.css || "", attrs: attrs };
  }

  document.querySelectorAll('input[name="theme"]').forEach(function (radio) {
    radio.addEventListener("change", async function () {
      const before = currentLook();
      applyLook(lookOf(radio));
      note("theme-note", "");
      try {
        await api("PUT", "/api/me/theme", { theme: radio.value });
      } catch (err) {
        applyLook(before);
        const old = document.querySelector('input[name="theme"][value="' + before.slug + '"]');
        if (old) old.checked = true;
        note("theme-note", err.message);
      }
    });
  });

  $("password-form").addEventListener("submit", async function (e) {
    e.preventDefault();
    note("password-note", "");
    if ($("pw-new").value !== $("pw-again").value) {
      note("password-note", "The two new passwords don't match.");
      $("pw-again").focus();
      return;
    }
    const button = this.querySelector("button[type=submit]");
    button.disabled = true;
    try {
      await api("POST", "/api/me/password", { current: $("pw-current").value, new: $("pw-new").value });
      this.reset();
      note("password-note", "Password changed. Your other browsers have been signed out.", true);
    } catch (err) {
      note("password-note", err.message);
    } finally {
      button.disabled = false;
    }
  });

  // Memories: forget one or all; the list is rendered by the server, so reload after a change.
  async function forget(url) {
    const r = await fetch(url, { method: "DELETE" });
    if (!r.ok) {
      note("memories-note", "Couldn't forget that: " + r.statusText);
      return;
    }
    location.reload();
  }
  document.querySelectorAll("#memories-list .forget").forEach(function (b) {
    b.addEventListener("click", function () { forget("/api/memories/" + encodeURIComponent(b.closest("li").dataset.id)); });
  });
  const forgetAll = $("forget-all");
  if (forgetAll) forgetAll.addEventListener("click", function () {
    if (confirm("Forget everything Erynne remembers about you?")) forget("/api/memories");
  });

  // The agents' scratchpad: one Clear button wipes every board (the notes are the agents' own, not files).
  const clearPad = $("clear-scratchpad");
  if (clearPad) clearPad.addEventListener("click", async function () {
    if (!confirm("Clear every note your agents left on the scratchpad? This can't be undone.")) return;
    const r = await fetch("/api/scratchpad", { method: "DELETE" });
    if (!r.ok) {
      note("scratchpad-note", "Couldn't clear it: " + r.statusText);
      return;
    }
    location.reload();   // the summary is rendered by the server
  });

  $("sign-out-others").addEventListener("click", async function () {
    if (!confirm("Sign out of every other browser? You stay signed in here.")) return;
    try {
      await api("POST", "/api/me/sign-out-others");
      note("sessions-note", "Done. Every other browser has to sign in again.", true);
    } catch (err) {
      note("sessions-note", err.message);
    }
  });
})();
