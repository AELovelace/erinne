/* Connect Claude: create, copy and revoke personal API tokens.
   All user-controlled text goes through textContent, never innerHTML. */
(function () {
  "use strict";

  const { el, api } = window.Erynne;
  const errorNode = document.getElementById("token-error");

  function showError(message) {
    errorNode.textContent = message || "";
    errorNode.hidden = !message;
  }

  function when(value) {
    return value ? value.slice(0, 16) + " UTC" : "never";
  }

  function render(data) {
    document.getElementById("mcp-url").textContent = data.mcp_url;

    const shared = document.getElementById("shared");
    shared.replaceChildren();
    if (!data.shared.length) shared.appendChild(el("li", "muted small", "Nothing yet. Ask an admin to share a library with Claude."));
    data.shared.forEach(function (c) {
      shared.appendChild(el("li", null, (c.kind === "private" ? "📥 " : "📚 ") + c.name));
    });

    const editable = document.getElementById("editable");
    editable.replaceChildren();
    if (!data.editable.length) editable.appendChild(el("li", "muted small", "Nowhere yet."));
    data.editable.forEach(function (p) {
      const li = el("li");
      li.appendChild(el("span", null, (p.folder === "Agents" ? "🤖 " : "📚 ") + p.library + (p.folder ? " / " + p.folder : "")));
      li.appendChild(el("span", "muted small", p.why));
      editable.appendChild(li);
    });

    const days = document.getElementById("token-days");
    if (!days.options.length) {
      data.expiry_days.forEach(function (d) {
        const o = el("option", null, "Expires in " + d + " days");
        o.value = String(d);
        days.appendChild(o);
      });
      days.value = "90";
    }

    const node = document.getElementById("tokens");
    if (!data.tokens.length) { node.replaceChildren(el("p", "muted", "No tokens yet.")); return; }
    const t = el("table", "admin-table");
    const head = el("tr");
    ["Name", "Starts with", "Created", "Expires", "Last used", "Can edit", ""].forEach(function (h) { head.appendChild(el("th", null, h)); });
    t.appendChild(el("thead")).appendChild(head);
    const body = el("tbody");
    data.tokens.forEach(function (tok) {
      const tr = el("tr", tok.active ? null : "muted");
      [tok.name, tok.prefix + "…", when(tok.created_at), when(tok.expires_at), when(tok.last_used_at)].forEach(function (v) {
        tr.appendChild(el("td", null, v));
      });
      const editCell = el("td");
      if (tok.active) {
        const label = el("label", "check small");
        const box = el("input");
        box.type = "checkbox";
        box.checked = tok.can_edit;
        box.setAttribute("aria-label", "Let Claude edit files with " + tok.name);
        box.addEventListener("change", async function () {
          showError("");
          try { await api("PATCH", "/api/tokens/" + tok.id, { can_edit: box.checked }); } catch (e) { showError(e.message); }
          load();
        });
        label.append(box, document.createTextNode(box.checked ? " yes" : " search only"));
        editCell.appendChild(label);
      } else {
        editCell.textContent = "—";
      }
      tr.appendChild(editCell);
      const cell = el("td");
      if (tok.active) {
        const revoke = el("button", "link danger small", "Revoke");
        revoke.type = "button";
        revoke.addEventListener("click", async function () {
          if (!confirm("Revoke “" + tok.name + "”? Claude stops working with it straight away.")) return;
          showError("");
          try { await api("DELETE", "/api/tokens/" + tok.id); } catch (e) { showError(e.message); }
          load();
        });
        cell.appendChild(revoke);
      } else {
        cell.textContent = tok.revoked_at ? "revoked" : "expired";
      }
      tr.appendChild(cell);
      body.appendChild(tr);
    });
    t.appendChild(body);
    node.replaceChildren(t);
  }

  async function load() {
    try { render(await api("GET", "/api/tokens")); } catch (e) { showError(e.message); }
  }

  document.getElementById("new-token").addEventListener("submit", async function (e) {
    e.preventDefault();
    showError("");
    try {
      const made = await api("POST", "/api/tokens", {
        name: document.getElementById("token-name").value.trim(),
        days: Number(document.getElementById("token-days").value),
        can_edit: document.getElementById("token-edit").checked,
      });
      document.getElementById("token-value").textContent = made.token;
      document.getElementById("token-reveal").hidden = false;
    } catch (err) { showError(err.message); }
    load();
  });

  document.getElementById("token-copy").addEventListener("click", async function () {
    const button = this;
    try {
      await navigator.clipboard.writeText(document.getElementById("token-value").textContent);
      button.textContent = "Copied";
    } catch (e) { button.textContent = "Select and copy it"; }
  });

  load();
})();
