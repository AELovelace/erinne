/* Settings page: link Microsoft 365 accounts (one per tenant) so Erynne can
   reach SharePoint and, for the agent group, mail and calendar as you.
   All text goes through textContent, never innerHTML. */
(function () {
  "use strict";

  const { el, api } = window.Erynne;
  const box = document.getElementById("m365");
  if (!box) return;

  function when(value) {
    return value ? value.slice(0, 16) + " UTC" : "never";
  }

  function linkButton(label, tenantId) {
    const a = el("a", "button-like small", label);
    a.href = "/m365/link" + (tenantId ? "?tenant=" + tenantId : "");
    return a;
  }

  function render(data) {
    const section = document.getElementById("m365-section");
    section.hidden = !data.available && !data.accounts.length;
    box.replaceChildren();

    // The result of a link that just finished (Microsoft sends people back here).
    const params = new URLSearchParams(window.location.search);
    if (params.get("m365")) {
      box.appendChild(el("p", params.get("m365") === "linked" ? "ok-note" : "error", params.get("note") || ""));
      history.replaceState(null, "", window.location.pathname + "#m365");
    }

    if (data.accounts.length) {
      const list = el("ul", "grants");
      data.accounts.forEach(function (a) {
        const li = el("li");
        li.appendChild(el("span", null, "🪟 " + a.upn + " · " + a.tenant));
        li.appendChild(el("span", "muted small", a.status === "relink"
          ? "Microsoft stopped accepting this link; link it again."
          : "linked " + when(a.linked_at) + ", last used " + when(a.last_used_at)));
        if (a.status === "relink") {
          const t = data.tenants.find(function (x) { return x.label === a.tenant; });
          li.appendChild(linkButton("Link again", t && t.own_app ? t.id : null));
        }
        const unlink = el("button", "link danger small", "Unlink");
        unlink.type = "button";
        unlink.addEventListener("click", async function () {
          if (!confirm("Unlink " + a.upn + "? Erynne stops reaching its SharePoint and mail.")) return;
          try { await api("DELETE", "/api/m365/accounts/" + a.id); } catch (e) { alert(e.message); }
          load();
        });
        li.appendChild(unlink);
        list.appendChild(li);
      });
      box.appendChild(list);
    } else {
      box.appendChild(el("p", "muted small", "No Microsoft 365 account linked yet."));
    }

    const actions = el("div", "grant-add");
    const shared = data.tenants.filter(function (t) { return !t.own_app; });
    if (shared.length) {
      actions.appendChild(linkButton(data.accounts.length ? "Link another Microsoft 365 account"
                                                          : "Link a Microsoft 365 account"));
    }
    data.tenants.filter(function (t) { return t.own_app; }).forEach(function (t) {
      actions.appendChild(linkButton("Link your " + t.label + " account", t.id));
    });
    if (actions.children.length) box.appendChild(actions);
  }

  async function load() {
    try { render(await api("GET", "/api/m365")); }
    catch (e) { box.replaceChildren(el("p", "error", e.message)); }
  }

  load();
})();
