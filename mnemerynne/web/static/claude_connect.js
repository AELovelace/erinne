/* Connect page: Claude on the web and phone (claude.ai custom connectors) and
   Claude Code, connected through OAuth: the address to add, and the
   connections made, with "may edit" and Disconnect. Text via textContent only. */
(function () {
  "use strict";

  const { el, api } = window.Erynne;
  const box = document.getElementById("claude-connections");
  if (!box) return;

  function when(value) {
    return value ? value.slice(0, 16) + " UTC" : "never";
  }

  function render(data) {
    document.querySelectorAll(".mcp-address").forEach(function (n) { n.textContent = data.mcp_url; });
    box.replaceChildren();
    if (!data.connections.length) {
      box.appendChild(el("p", "muted small", "Nothing connected this way yet."));
      return;
    }
    const list = el("ul", "grants");
    data.connections.forEach(function (c) {
      const li = el("li");
      li.appendChild(el("span", null, "🔗 " + c.app + (c.name && c.name !== c.app ? " (" + c.name + ")" : "")));
      li.appendChild(el("span", "muted small", "connected " + when(c.created_at) + ", last used " + when(c.last_used_at)));
      const label = el("label", "check small");
      const edit = el("input");
      edit.type = "checkbox";
      edit.checked = c.can_edit;
      edit.addEventListener("change", async function () {
        try { await api("PATCH", "/api/oauth/connections/" + c.id, { can_edit: edit.checked }); }
        catch (e) { alert(e.message); }
        load();
      });
      label.append(edit, document.createTextNode(" may edit files"));
      li.appendChild(label);
      const off = el("button", "link danger small", "Disconnect");
      off.type = "button";
      off.addEventListener("click", async function () {
        if (!confirm("Disconnect " + c.app + "? It stops working with your documents straight away.")) return;
        try { await api("DELETE", "/api/oauth/connections/" + c.id); } catch (e) { alert(e.message); }
        load();
      });
      li.appendChild(off);
      list.appendChild(li);
    });
    box.appendChild(list);
  }

  async function load() {
    try { render(await api("GET", "/api/oauth/connections")); }
    catch (e) { box.replaceChildren(el("p", "error", e.message)); }
  }

  document.querySelectorAll(".copy-mcp").forEach(function (b) {
    b.addEventListener("click", async function () {
      try { await navigator.clipboard.writeText(document.querySelector(".mcp-address").textContent); b.textContent = "Copied"; }
      catch (e) { b.textContent = "Select and copy it"; }
    });
  });
  load();
})();
