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
      "the Connect page, one per tenant. Linked accounts let Erynne reach SharePoint, and for the agent group mail and " +
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

  async function load() {
    try { render(await api("GET", "/api/admin/m365")); }
    catch (e) { $("m365-settings").replaceChildren(el("p", "error", e.message)); }
  }

  window.ErynneM365 = { load: load };
})();
