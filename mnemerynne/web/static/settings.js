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

  // Themes switch at once (theme-neon.css is always loaded, scoped to data-theme), then save.
  function applyTheme(theme) {
    document.documentElement.dataset.theme = theme;
    const meta = document.querySelector('meta[name="color-scheme"]');
    if (meta) meta.content = theme === "neon" ? "dark" : "light dark";
  }

  document.querySelectorAll('input[name="theme"]').forEach(function (radio) {
    radio.addEventListener("change", async function () {
      const before = document.documentElement.dataset.theme;
      applyTheme(radio.value);
      note("theme-note", "");
      try {
        await api("PUT", "/api/me/theme", { theme: radio.value });
      } catch (err) {
        applyTheme(before);
        const old = document.querySelector('input[name="theme"][value="' + before + '"]');
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
