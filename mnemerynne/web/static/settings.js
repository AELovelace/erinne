/* Personal settings: change your password, sign out other browsers.
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
