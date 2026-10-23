/* Settings → Claude in chat: your own Anthropic API key (write-only: the
   server never sends it back, only whether one is saved) and the Claude model
   your questions use. All server text goes through textContent. */
(function () {
  "use strict";

  const { el, api } = window.Erynne;
  const box = document.getElementById("claude-chat");
  if (!box) return;

  function render(data) {
    box.replaceChildren();
    const state = { "set": "✓ Your key is saved" + (data.updated_at ? " (" + data.updated_at.slice(0, 10) + ")" : "") + ".",
                    "missing": "No key saved yet.",
                    "needs re-entering": "⚠️ Your key can't be read any more (the server's secret key changed); paste it again." }[data.key];
    box.appendChild(el("p", "small" + (data.key === "set" ? "" : " muted"), state || data.key));
    if (!data.secret_key_set) {
      box.appendChild(el("p", "error small", "MNEMERYNNE_SECRET_KEY isn't set on the server, so keys can't be saved yet. Ask an admin."));
    }

    const form = el("form", "claude-key-form");
    const key = el("input");
    key.type = "password";
    key.autocomplete = "off";
    key.placeholder = data.key === "set" ? "Paste a new key to replace it" : "sk-ant-…";
    key.setAttribute("aria-label", "Anthropic API key");
    const model = el("select");
    model.setAttribute("aria-label", "Claude model");
    data.models.forEach(function (m) {
      const o = el("option", null, m.name);
      o.value = m.id;
      model.appendChild(o);
    });
    model.value = data.model;
    const save = el("button", "small", data.key === "set" ? "Save" : "Save key");
    save.type = "submit";
    form.append(key, model, save);
    if (data.key !== "missing") {
      const remove = el("button", "link danger small", "Remove key");
      remove.type = "button";
      remove.addEventListener("click", async function () {
        if (!confirm("Remove your Claude API key? Ask Claude stops working until you add one again.")) return;
        await run(function () { return api("DELETE", "/api/me/claude"); });
      });
      form.appendChild(remove);
    }
    form.addEventListener("submit", async function (e) {
      e.preventDefault();
      const body = { model: model.value };
      if (key.value.trim()) body.key = key.value.trim();
      const label = save.textContent;
      save.disabled = true;
      save.textContent = body.key ? "Checking…" : "Saving…";
      await run(function () { return api("PUT", "/api/me/claude", body); });
      save.disabled = false;   // still on the page only when saving failed
      save.textContent = label;
    });
    box.appendChild(form);
    box.appendChild(el("p", "muted small", "Create a key at console.anthropic.com → API keys. It's checked with " +
      "Anthropic when you save it, then stored encrypted and never shown again. Opus answers best; Sonnet and " +
      "Haiku cost less."));
    note = el("p", "error small");
    note.hidden = true;
    box.appendChild(note);
  }

  let note = null;

  async function run(fn) {
    if (note) note.hidden = true;
    try {
      render(await fn());
    } catch (e) {
      if (note) { note.textContent = e.message; note.hidden = false; }
    }
  }

  run(function () { return api("GET", "/api/me/claude"); });
})();
