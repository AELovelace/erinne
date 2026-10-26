/* Access → Remote: the LAN apps served through Mnemerynne. A list on the left, the chosen app's
   settings on the right: address, who may open it, the certificate for a self-signed server,
   link rewriting, how the Remote tab shows it. All text through textContent. */
(function () {
  "use strict";

  const { el, api } = window.Erynne;
  const $ = function (id) { return document.getElementById(id); };
  const errorNode = $("admin-error");
  const state = { list: null, groups: [], selected: null };

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

  function field(labelText, input, hint) {
    const label = el("label", "api-field");
    label.appendChild(el("span", "small", labelText));
    label.appendChild(input);
    if (hint) label.appendChild(el("span", "muted small", hint));
    return label;
  }

  function input(value, opts) {
    const i = el(opts && opts.multiline ? "textarea" : "input");
    if (!(opts && opts.multiline)) i.type = "text";
    if (opts && opts.rows) i.rows = opts.rows;
    if (opts && opts.list) i.setAttribute("list", opts.list);
    if (opts && opts.placeholder) i.placeholder = opts.placeholder;
    i.value = value === undefined || value === null ? "" : value;
    return i;
  }

  function checkbox(labelText, checked) {
    const label = el("label", "check small");
    const box = el("input");
    box.type = "checkbox";
    box.checked = !!checked;
    label.append(box, document.createTextNode(" " + labelText));
    return { label: label, box: box };
  }

  function fillGroups(groups) {
    const list = $("remote-groups");
    list.replaceChildren();
    (groups || []).forEach(function (g) { const o = el("option"); o.value = g; list.appendChild(o); });
  }

  async function save(app, changes) {
    showError("");
    try {
      render(await api("PATCH", "/api/admin/remote/" + app.id, changes));
    } catch (e) { showError(e.message); }
  }

  function renderList() {
    const ul = $("remote-list-admin");
    ul.replaceChildren();
    if (!state.list.length) { ul.appendChild(el("li", "muted small", "No remote apps yet.")); return; }
    state.list.forEach(function (a) {
      const li = el("li");
      const row = el("div", "tree-row" + (state.selected === a.id ? " current" : ""));
      const b = el("button", "tree-label", (a.enabled ? "" : "⏸ ") + a.name);
      b.type = "button";
      b.addEventListener("click", function () { state.selected = a.id; renderList(); renderDetail(); });
      row.appendChild(b);
      li.appendChild(row);
      ul.appendChild(li);
    });
  }

  function renderDetail() {
    const pane = $("remote-pane");
    pane.replaceChildren();
    const a = state.list.find(function (x) { return x.id === state.selected; });
    if (!a) {
      pane.appendChild(el("p", "muted", "Pick an app on the left, or add one: a short slug (its address becomes " +
        "/remote/<slug>/), a name, and where it lives on the network."));
      return;
    }
    const head = el("div", "ax-bar");
    head.appendChild(el("h2", null, a.name));
    const openLink = el("a", "small", "Open " + a.url);
    openLink.href = a.url; openLink.target = "_blank"; openLink.rel = "noopener";
    head.appendChild(openLink);
    pane.appendChild(head);
    pane.appendChild(el("p", "muted small", "Served at " + a.url + ". The app's scripts run on this site's address " +
      "for whoever opens it, so list only the organisation's own tools, for the groups that should have them."));

    const form = el("div", "api-form");
    const name = input(a.name), description = input(a.description, { multiline: true, rows: 2 }),
          url = input(a.base_url, { placeholder: "http://10.0.5.20:9180" }),
          group = input(a.group, { list: "remote-groups", placeholder: "Empty: admins only" });
    form.append(field("Name", name), field("Description", description, "Shown on the Remote home page."),
                field("Address on the network", url, "http(s)://host[:port][/base], the way this server reaches it."),
                field("Who may open it", group, "An AD or Entra group (several with ;). Admins always can."));
    const embed = checkbox("Show it in the Remote window (otherwise it opens in a new tab)", a.embed);
    const rewrite = checkbox("Rewrite absolute links in its pages (for apps that can't be told their base path)", a.rewrite_html);
    const enabled = checkbox("Enabled", a.enabled);
    form.append(embed.label, rewrite.label, enabled.label);
    form.appendChild(button("Save", null, function () {
      save(a, { name: name.value, description: description.value, base_url: url.value, group: group.value,
                embed: embed.box.checked, rewrite_html: rewrite.box.checked, enabled: enabled.box.checked });
    }));
    pane.appendChild(form);

    pane.appendChild(el("h3", "ax-h", "Certificate"));
    pane.appendChild(el("p", "muted small", "Self-signed server? Fetch its certificate, compare the fingerprint with " +
      "the real one, and pin it: only that certificate is then trusted for this app. Currently: " +
      (a.certificate === "pinned" ? "pinned." : "the system's trusted roots.")));
    const pem = input("", { multiline: true, rows: 5, placeholder: "-----BEGIN CERTIFICATE-----" });
    const fingerprint = el("p", "small muted");
    const hostname = checkbox("Check the name in the certificate (untick for a server reached by IP)", a.tls_check_hostname);
    const certForm = el("div", "api-form");
    certForm.append(field("PEM", pem), fingerprint, hostname.label);
    const actions = el("div", "ax-bar");
    actions.append(
      button("Fetch from server", "small", async function () {
        showError("");
        try {
          const got = await api("POST", "/api/admin/remote/" + a.id + "/fetch-certificate");
          pem.value = got.ca_pem;
          fingerprint.textContent = "SHA-256 " + got.fingerprint + " — compare it with the server's real fingerprint before saving.";
        } catch (e) { showError(e.message); }
      }),
      button("Save certificate", "small", function () { save(a, { ca_pem: pem.value, tls_check_hostname: hostname.box.checked }); }),
      button("Use the system's roots", "link small", function () { save(a, { ca_pem: "", tls_check_hostname: true }); }));
    certForm.appendChild(actions);
    pane.appendChild(certForm);

    pane.appendChild(el("h3", "ax-h", "Remove"));
    pane.appendChild(button("Delete this app", "link danger small", async function () {
      if (!confirm("Delete “" + a.name + "”? People lose its address at once.")) return;
      showError("");
      try { state.selected = null; render(await api("DELETE", "/api/admin/remote/" + a.id)); } catch (e) { showError(e.message); }
    }));
  }

  function render(data) {
    state.list = data.apps;
    state.groups = data.groups || [];
    fillGroups(state.groups);
    if (state.selected !== null && !state.list.some(function (a) { return a.id === state.selected; })) state.selected = null;
    renderList();
    renderDetail();
  }

  async function load() {
    try { render(await api("GET", "/api/admin/remote")); }
    catch (e) { $("remote-pane").replaceChildren(el("p", "error", e.message)); }
  }

  const form = $("new-remote");
  if (form) form.addEventListener("submit", async function (e) {
    e.preventDefault();
    showError("");
    try {
      const data = await api("POST", "/api/admin/remote", {
        slug: $("new-remote-slug").value.trim(), name: $("new-remote-name").value.trim(), base_url: $("new-remote-url").value.trim() });
      form.reset();
      state.selected = data.id;
      render(data);
    } catch (err) { showError(err.message); }
  });

  window.ErynneRemote = { load: load };
})();
