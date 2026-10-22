/* Settings → Microsoft 365: the tenants people may link accounts in, each
   one's mode (synced from our AD, or separate staff), its consent and an
   optional own app registration. All server text goes through textContent. */
(function () {
  "use strict";

  const { el, api } = window.Erynne;
  const $ = function (id) { return document.getElementById(id); };
  const errorNode = $("admin-error");

  function showError(message) {
    errorNode.textContent = message || "";
    errorNode.hidden = !message;
    if (message) errorNode.scrollIntoView({ block: "nearest" });
  }

  function button(text, cls, onClick) {
    const b = el("button", cls, text);
    b.type = "button";
    b.addEventListener("click", onClick);
    return b;
  }

  function input(type, placeholder, value) {
    const i = el("input");
    i.type = type;
    i.placeholder = placeholder || "";
    i.value = value || "";
    return i;
  }

  function modeSelect(value) {
    const s = el("select");
    [["cloud", "Separate staff"], ["ad", "Synced from our AD"]].forEach(function (o) {
      const opt = el("option", null, o[1]);
      opt.value = o[0];
      s.appendChild(opt);
    });
    s.value = value || "cloud";
    return s;
  }

  async function change(method, url, body) {
    showError("");
    try { render(await api(method, url, body)); } catch (e) { showError(e.message); }
    await loadSharePoint();
    await loadMailboxes();
  }

  function tenantRow(t) {
    const tr = el("tr", t.enabled ? null : "muted");
    const name = el("td");
    name.append(el("strong", null, t.label), el("div", "muted small", (t.domain ? t.domain + " · " : "") + t.tenant_id));
    const mode = modeSelect(t.mode);
    mode.setAttribute("aria-label", "Who links accounts in " + t.label);
    mode.addEventListener("change", function () { change("PATCH", "/api/admin/m365/tenants/" + t.id, { mode: mode.value }); });

    const consent = el("td", "small");
    if (t.consent_ok_at) consent.appendChild(el("div", "ok-note", "✓ consented (checked " + t.consent_ok_at.slice(0, 16) + ")"));
    else if (t.consent_error) consent.appendChild(el("div", "error", t.consent_error));
    else consent.appendChild(el("div", "muted", "not checked yet"));
    if (t.consent_url) {
      const a = el("a", null, "Grant consent");
      a.href = t.consent_url; a.target = "_blank"; a.rel = "noopener";
      a.title = "An admin of " + t.label + " opens this once and accepts.";
      consent.appendChild(a);
      consent.appendChild(document.createTextNode(" · "));
    }
    consent.appendChild(button("Check", "link small", function () {
      change("POST", "/api/admin/m365/tenants/" + t.id + "/check");
    }));

    const app = el("td", "small", t.own_app ? "own app " + t.client_id : "shared app");
    const on = el("input");
    on.type = "checkbox";
    on.checked = t.enabled;
    on.setAttribute("aria-label", "Allow linking in " + t.label);
    on.addEventListener("change", function () { change("PATCH", "/api/admin/m365/tenants/" + t.id, { enabled: on.checked }); });
    const remove = button("Remove", "link danger small", function () {
      if (!confirm("Remove " + t.label + "? Every account linked in it (" + t.linked + ") is unlinked.")) return;
      change("DELETE", "/api/admin/m365/tenants/" + t.id);
    });
    [name, el("td"), consent, app, el("td", null, String(t.linked)), el("td"), el("td")].forEach(function (c) { tr.appendChild(c); });
    tr.children[1].appendChild(mode);
    tr.children[5].appendChild(on);
    tr.children[6].appendChild(remove);
    return tr;
  }

  function render(data) {
    const box = $("m365-settings");
    box.replaceChildren();
    box.appendChild(el("p", "muted", "People sign in to Mnemerynne as always, then link their Microsoft 365 accounts on " +
      "the Settings page, one per tenant. Linked accounts let Erynne reach SharePoint, and for the agent group mail and " +
      "calendar, with Microsoft's own permissions. Nothing is ever sent, deleted or moved."));
    if (!data.configured) {
      box.appendChild(el("p", "error", "The shared app registration isn't set on the server (MNEMERYNNE_M365_CLIENT_ID and " +
        "MNEMERYNNE_M365_CLIENT_SECRET). Only tenants with their own app can be linked until it is."));
    }
    const redirect = el("p", "small muted");
    redirect.append(document.createTextNode("Redirect URI to register: "), el("code", null, data.redirect_uri));
    box.appendChild(redirect);

    if (data.tenants.length) {
      const t = el("table", "admin-table");
      const head = el("tr");
      ["Tenant", "Who links", "Consent", "App", "Linked", "On", ""].forEach(function (h) { head.appendChild(el("th", null, h)); });
      t.appendChild(el("thead")).appendChild(head);
      const body = el("tbody");
      data.tenants.forEach(function (tn) { body.appendChild(tenantRow(tn)); });
      t.appendChild(body);
      const wrap = el("div", "table-wrap");
      wrap.appendChild(t);
      box.appendChild(wrap);
    } else {
      box.appendChild(el("p", "muted", "No tenants yet."));
    }

    const form = el("form", "ax-new");
    const ref = input("text", "Domain or tenant ID, e.g. gascofuel.com");
    ref.required = true;
    const label = input("text", "Name, e.g. Gasco");
    const mode = modeSelect("cloud");
    const clientId = input("text", "Own app client ID (optional)");
    const secret = input("password", "Own app secret");
    secret.autocomplete = "off";
    const add = el("button", "small", "Add tenant");
    add.type = "submit";
    [ref, label, mode, clientId, secret].forEach(function (i) { i.setAttribute("aria-label", i.placeholder || "Mode"); });
    form.append(ref, label, mode, clientId, secret, add);
    form.addEventListener("submit", function (e) {
      e.preventDefault();
      change("POST", "/api/admin/m365/tenants", { ref: ref.value.trim(), label: label.value.trim(), mode: mode.value,
                                                  client_id: clientId.value.trim(), client_secret: secret.value });
    });
    box.appendChild(form);
    box.appendChild(el("p", "small muted", "“Synced from our AD”: people may only link the account that matches their " +
      "linked domain account. “Separate staff”: anyone may link an account they can sign in to; create their " +
      "Mnemerynne user on Accounts first."));
  }

  // ---------------------------------------------------------- SharePoint
  function renderSharePoint(data) {
    let box = $("sharepoint-settings");
    if (!box) {
      box = el("div");
      box.id = "sharepoint-settings";
      $("m365-settings").appendChild(box);
    }
    box.replaceChildren(el("h3", "ax-h small", "SharePoint"));
    box.appendChild(el("p", "muted small", "Libraries indexed into Mnemerynne, read-only. Everyone with a linked " +
      "account in that tenant can find them; every file is checked against SharePoint for each person. A tenant " +
      "admin first grants Mnemerynne read access to the site (Sites.Selected; deploy guide 5i)."));
    if (data.libraries.length) {
      const t = el("table", "admin-table");
      const head = el("tr");
      ["Library", "Tenant", "Files", "Last sync", ""].forEach(function (h) { head.appendChild(el("th", null, h)); });
      t.appendChild(el("thead")).appendChild(head);
      const body = el("tbody");
      data.libraries.forEach(function (l) {
        const tr = el("tr");
        const name = el("td");
        const link = el("a", null, l.name);
        link.href = l.web_url || "#"; link.target = "_blank"; link.rel = "noopener";
        name.appendChild(link);
        if (l.status === "error") name.appendChild(el("div", "error small", l.error || "Sync failed"));
        if (l.status === "syncing" || l.status === "new") {
          name.appendChild(el("div", "muted small", "syncing… " + (l.files ? l.files + " files so far" : "starting")));
        }
        const actions = el("td");
        actions.append(button("Sync now", "link small", function () {
          change2("POST", "/api/admin/sharepoint/" + l.id + "/sync");
        }), document.createTextNode(" "), button("Remove", "link danger small", function () {
          if (!confirm("Stop syncing " + l.name + " and remove Mnemerynne's copy? SharePoint itself isn't touched.")) return;
          change2("DELETE", "/api/admin/sharepoint/" + l.id);
        }));
        [name, el("td", null, l.tenant), el("td", null, String(l.files)),
         el("td", "small", l.last_sync_at ? l.last_sync_at.slice(0, 16) + " UTC" : "not yet"), actions]
          .forEach(function (c) { tr.appendChild(c); });
        body.appendChild(tr);
      });
      t.appendChild(body);
      const wrap = el("div", "table-wrap");
      wrap.appendChild(t);
      box.appendChild(wrap);
    }
    if (!data.tenants.length) {
      box.appendChild(el("p", "muted small", "Add a tenant above first."));
      return;
    }
    const form = el("form", "ax-new");
    const tenant = el("select");
    data.tenants.forEach(function (tn) { const o = el("option", null, tn.label); o.value = tn.id; tenant.appendChild(o); });
    tenant.setAttribute("aria-label", "Tenant");
    const url = input("url", "https://contoso.sharepoint.com/sites/FrontOffice");
    url.required = true;
    url.setAttribute("aria-label", "Site address");
    const find = el("button", "small", "Find libraries");
    find.type = "submit";
    form.append(tenant, url, find);
    const picks = el("div");
    form.addEventListener("submit", async function (e) {
      e.preventDefault();
      showError("");
      picks.replaceChildren(el("p", "muted small", "Looking…"));
      let site;
      try { site = await api("POST", "/api/admin/sharepoint/resolve", { tenant: Number(tenant.value), url: url.value.trim() }); }
      catch (err) { picks.replaceChildren(); showError(err.message); return; }
      picks.replaceChildren(el("p", "small", site.name + ": pick the libraries to index."));
      const boxes = [];
      site.libraries.forEach(function (lib) {
        const label = el("label", "check small");
        const cb = el("input");
        cb.type = "checkbox";
        cb.value = lib.id;
        cb.disabled = lib.added;
        label.append(cb, document.createTextNode(" " + lib.name + (lib.added ? " (already added)" : "")));
        picks.appendChild(label);
        picks.appendChild(el("br"));
        boxes.push(cb);
      });
      if (!site.libraries.length) picks.appendChild(el("p", "muted small", "This site has no document libraries."));
      picks.appendChild(button("Add and start indexing", "small", async function () {
        const ids = boxes.filter(function (b) { return b.checked; }).map(function (b) { return b.value; });
        await change2("POST", "/api/admin/sharepoint/libraries", { tenant: Number(tenant.value), url: url.value.trim(),
                                                                   drive_ids: ids });
      }));
    });
    box.append(form, picks);
  }

  async function change2(method, path, body) {
    showError("");
    try { await api(method, path, body); } catch (e) { showError(e.message); }
    await loadSharePoint();
  }

  async function loadSharePoint() {
    try { renderSharePoint(await api("GET", "/api/admin/sharepoint")); }
    catch (e) { showError(e.message); }
  }

  // ----------------------------------------------------- shared mailboxes
  function renderMailboxes(data, tenants) {
    let box = $("mailbox-settings");
    if (!box) {
      box = el("div");
      box.id = "mailbox-settings";
      $("m365-settings").appendChild(box);
    }
    box.replaceChildren(el("h3", "ax-h small", "Shared mailboxes"));
    box.appendChild(el("p", "muted small", "Mailboxes Erynne's agent group may ask about (e.g. a front desk inbox), " +
      "read with each person's own Microsoft account: Exchange still decides who can open them. An optional group " +
      "narrows who sees them in Erynne."));
    if (data.mailboxes.length) {
      const list = el("ul", "grants");
      data.mailboxes.forEach(function (m) {
        const li = el("li");
        li.appendChild(el("span", null, "✉️ " + m.label + " · " + m.address + " · " + m.tenant));
        li.appendChild(el("span", "muted small", m.group ? "for " + m.group : "anyone Exchange lets in"));
        li.appendChild(button("Remove", "link danger small", async function () {
          showError("");
          try { renderMailboxes(await api("DELETE", "/api/admin/m365/mailboxes/" + m.id), tenants); }
          catch (e) { showError(e.message); }
        }));
        list.appendChild(li);
      });
      box.appendChild(list);
    }
    if (!tenants.length) return;
    const form = el("form", "ax-new");
    const tenant = el("select");
    tenants.forEach(function (tn) { const o = el("option", null, tn.label); o.value = tn.id; tenant.appendChild(o); });
    tenant.setAttribute("aria-label", "Tenant");
    const address = input("email", "frontdesk@contoso.com");
    const label = input("text", "Name, e.g. Front desk");
    const group = input("text", "Group (optional)");
    group.setAttribute("list", "domain-groups");
    [address, label, group].forEach(function (i) { i.setAttribute("aria-label", i.placeholder); });
    address.required = label.required = true;
    const add = el("button", "small", "Add mailbox");
    add.type = "submit";
    form.append(tenant, address, label, group, add);
    form.addEventListener("submit", async function (e) {
      e.preventDefault();
      showError("");
      try {
        renderMailboxes(await api("POST", "/api/admin/m365/mailboxes", { tenant: Number(tenant.value),
          address: address.value.trim(), label: label.value.trim(), group: group.value.trim() }), tenants);
      } catch (err) { showError(err.message); }
    });
    box.appendChild(form);
  }

  async function loadMailboxes() {
    try {
      const sp = await api("GET", "/api/admin/sharepoint");   // the enabled tenants
      renderMailboxes(await api("GET", "/api/admin/m365/mailboxes"), sp.tenants);
    } catch (e) { showError(e.message); }
  }

  async function load() {
    try { render(await api("GET", "/api/admin/m365")); }
    catch (e) { $("m365-settings").replaceChildren(el("p", "error", e.message)); return; }
    await loadSharePoint();
    await loadMailboxes();
  }

  window.ErynneM365 = { load: load };
  // admin.js shows the starting tab before this script runs: catch up if that was Settings.
  const panel = document.querySelector('[data-panel="settings"]');
  if (panel && !panel.hidden) load();
})();
