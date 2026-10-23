/* Access → APIs: the internal systems Erynne may query (read-only), who may
   use each (one AD group), Claude's switch, credentials (write-only), the
   operations, a live test, imported documentation and recent calls.
   All server text goes through textContent, except description/spec HTML,
   which the server renders from Markdown with raw HTML escaped. */
(function () {
  "use strict";

  const { el, api } = window.Erynne;
  const $ = function (id) { return document.getElementById(id); };
  const errorNode = $("admin-error");
  const state = { list: null, selected: null, detail: null };

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
    if (!(opts && opts.multiline)) i.type = (opts && opts.type) || "text";
    if (opts && opts.rows) i.rows = opts.rows;
    if (opts && opts.list) i.setAttribute("list", opts.list);
    if (opts && opts.placeholder) i.placeholder = opts.placeholder;
    i.value = value === undefined || value === null ? "" : value;
    return i;
  }

  function select(options, value) {
    const s = el("select");
    options.forEach(function (o) {
      const opt = el("option", null, o[1]);
      opt.value = o[0];
      s.appendChild(opt);
    });
    s.value = value;
    return s;
  }

  function checkbox(labelText, checked) {
    const label = el("label", "check small");
    const box = el("input");
    box.type = "checkbox";
    box.checked = !!checked;
    label.append(box, document.createTextNode(" " + labelText));
    label.box = box;
    return label;
  }

  async function run(fn) {
    showError("");
    try { return await fn(); } catch (e) { showError(e.message); return null; }
  }

  // ---------------------------------------------------------------- list
  function credentialBadge(status) {
    return { "set": "🔑", "missing": "⚠️ no credential", "needs re-entering": "⚠️ re-enter credential",
             "not needed": "" }[status] || status;
  }

  function renderList() {
    const list = $("api-list");
    list.replaceChildren();
    if (!state.list.apis.length) list.appendChild(el("li", "muted small", "No APIs yet."));
    state.list.apis.forEach(function (a) {
      const li = el("li", "tree-row" + (state.selected === a.id ? " selected" : ""));
      li.setAttribute("role", "treeitem");
      const b = button((a.enabled ? "🔌 " : "⏸ ") + a.name, "link", function () { open(a.id); });
      li.appendChild(b);
      const bits = [a.ad_group ? "👥 " + a.ad_group : "no group yet", credentialBadge(a.credential),
                    a.claude_enabled ? "Claude" : ""].filter(Boolean);
      li.appendChild(el("div", "muted small", bits.join(" · ")));
      list.appendChild(li);
    });
    const presetSelect = $("new-api-preset");
    presetSelect.replaceChildren();
    [["", "Blank API…"]].concat(state.list.presets.map(function (p) { return [p.key, p.name + " (preset)"]; }))
      .forEach(function (o) { const opt = el("option", null, o[1]); opt.value = o[0]; presetSelect.appendChild(opt); });
    const groups = $("api-groups");
    groups.replaceChildren.apply(groups, state.list.groups.map(function (g) { const o = el("option"); o.value = g; return o; }));
  }

  async function load() {
    const data = await run(function () { return api("GET", "/api/admin/apis"); });
    if (!data) return;
    state.list = data;
    renderList();
    if (!data.secret_key_set) showError("MNEMERYNNE_SECRET_KEY isn't set on the server, so API credentials can't be stored yet.");
    if (state.selected) await open(state.selected);
  }

  $("new-api").addEventListener("submit", async function (e) {
    e.preventDefault();
    const key = $("new-api-preset").value;
    let body = { preset: key || null };
    if (!key) {
      const name = prompt("Name of the API (e.g. Hotel stats):");
      if (!name) return;
      const url = prompt("Its address (e.g. https://stats.example.com/api):");
      if (!url) return;
      body = { name: name.trim(), base_url: url.trim() };
    }
    const made = await run(function () { return api("POST", "/api/admin/apis", body); });
    if (made) { state.selected = made.id; await load(); }
  });

  // -------------------------------------------------------------- detail
  async function open(id) {
    const d = await run(function () { return api("GET", "/api/admin/apis/" + id); });
    if (!d) return;
    state.selected = id;
    state.detail = d;
    renderList();
    renderDetail(d);
  }

  function patch(changes) {
    return run(async function () {
      await api("PATCH", "/api/admin/apis/" + state.detail.id, changes);
      await load();
    });
  }

  function section(title) {
    const s = el("section", "api-section");
    s.appendChild(el("h3", "ax-h", title));
    return s;
  }

  function renderDetail(d) {
    const pane = $("api-pane");
    pane.replaceChildren();
    const head = el("div", "lib-head");
    head.appendChild(el("h2", null, "🔌 " + d.name));
    const enabled = checkbox("Enabled", d.enabled);
    enabled.box.addEventListener("change", function () { patch({ enabled: enabled.box.checked }); });
    // A preset with this API's name (e.g. Wazuh): bring in operations it gained since this API was made.
    const preset = (state.list && state.list.presets || []).find(function (p) {
      return p.name.toLowerCase() === d.name.toLowerCase();
    });
    if (preset) {
      head.appendChild(button("Add preset operations", "link small", async function () {
        const done = await run(function () {
          return api("POST", "/api/admin/apis/" + d.id + "/preset-operations", { preset: preset.key });
        });
        if (done !== null) {
          alert(done.added ? "Added " + done.added + " operation(s) from the " + preset.name + " preset."
                           : "This API already has all of the preset's operations.");
          await open(d.id);
        }
      }));
    }
    head.append(enabled, button("Rename", "link small", function () {
      const name = prompt("New name for " + d.name, d.name);
      if (name && name.trim() !== d.name) patch({ name: name.trim() });
    }), button("Delete", "link danger small", async function () {
      if (prompt("This removes “" + d.name + "”, its operations and its credential.\n\nType the name to confirm:") !== d.name) return;
      if (await run(function () { return api("DELETE", "/api/admin/apis/" + d.id); }) !== null) {
        state.selected = null; state.detail = null;
        $("api-pane").replaceChildren(el("p", "muted", "Deleted."));
        await load();
      }
    }));
    pane.appendChild(head);
    pane.append(accessSection(d), connectionSection(d), operationsSection(d), docsSection(d), callsSection(d));
  }

  function accessSection(d) {
    const s = section("Who can use it");
    const group = input(d.ad_group, { list: "api-groups", placeholder: "e.g. IT" });
    const claude = checkbox("Claude can query this too (responses are sent to Anthropic)", d.claude_enabled);
    s.append(field("AD group" + (state.list.domain ? " (" + state.list.domain + ")" : ""), group,
                   "People in this group (via their linked domain account) can ask Erynne about it. Empty: nobody."),
             claude, button("Save access", "small", function () {
               patch({ ad_group: group.value.trim(), claude_enabled: claude.box.checked });
             }));
    return s;
  }

  function connectionSection(d) {
    const s = section("Connection");
    const url = input(d.base_url);
    const auth = select([["none", "No credential"], ["bearer", "Bearer token"], ["header", "Key in a header"],
                         ["basic", "User name and password"]], d.auth_type);
    const header = input(d.auth_header, { placeholder: "X-API-Key" });
    const user = input(d.auth_user);
    const headerField = field("Header name", header);
    const userField = field("User name", user);
    function toggle() {
      headerField.hidden = auth.value !== "header";
      userField.hidden = auth.value !== "basic";
    }
    auth.addEventListener("change", toggle);
    toggle();
    s.append(field("Address", url), field("Authentication", auth), headerField, userField,
             button("Save connection", "small", function () {
               patch({ base_url: url.value.trim(), auth_type: auth.value, auth_header: header.value.trim(),
                       auth_user: user.value.trim() });
             }));

    if (d.auth_type !== "none") {
      const status = el("p", "small", "Credential: " + d.credential +
                                      (d.secret_changed_at ? " (changed " + d.secret_changed_at + " UTC)" : ""));
      const secret = input("", { type: "password", placeholder: d.credential === "set" ? "Replace…" : "Paste it here" });
      secret.autocomplete = "new-password";
      s.append(status, field(d.auth_type === "basic" ? "Password" : "Token or key", secret,
                             "Write-only: stored encrypted and never shown again."),
               button("Save credential", "small", function () {
                 if (!secret.value.trim()) return;
                 run(async function () {
                   await api("PUT", "/api/admin/apis/" + d.id + "/credential", { secret: secret.value });
                   secret.value = "";
                   await load();
                 });
               }),
               button("Clear", "link danger small", function () {
                 if (!confirm("Remove the stored credential for " + d.name + "?")) return;
                 run(async function () { await api("PUT", "/api/admin/apis/" + d.id + "/credential", { secret: "" }); await load(); });
               }));
    }

    const pem = input(d.ca_pem, { multiline: true, rows: 4, placeholder: "-----BEGIN CERTIFICATE----- (only for servers with a self-signed or internal certificate)" });
    const hostname = checkbox("Check the name in the certificate", d.tls_check_hostname);
    const fingerprint = el("p", "small mono");
    s.append(field("Trusted certificate", pem, "When set, only this certificate is trusted for this API."), hostname,
             fingerprint,
             button("Fetch from server", "small", function () {
               run(async function () {
                 const got = await api("POST", "/api/admin/apis/" + d.id + "/fetch-certificate");
                 pem.value = got.pem;
                 fingerprint.textContent = "SHA-256 " + got.fingerprint +
                   " — compare it with the server's real fingerprint before saving.";
               });
             }),
             button("Save certificate", "small", function () {
               patch({ ca_pem: pem.value.trim(), tls_check_hostname: hostname.box.checked });
             }));
    return s;
  }

  // ---------------------------------------------------------- operations
  function paramsSummary(params) {
    return params.map(function (p) {
      return p.name + (p.required ? "*" : "") + ": " + p.type + (p.options ? " [" + p.options.join("|") + "]" : "");
    }).join(", ") || "no parameters";
  }

  function operationsSection(d) {
    const s = section("Operations");
    s.appendChild(el("p", "muted small", "What Erynne may call. Example questions decide when a chat question goes " +
                                          "to an operation; add a few the way staff would ask."));
    d.operations.forEach(function (op) { s.appendChild(operationCard(d, op)); });
    s.appendChild(button("Add operation", "small", function () {
      s.insertBefore(operationEditor(d, null), s.lastChild);
    }));
    return s;
  }

  function operationCard(d, op) {
    const card = el("div", "api-op");
    const head = el("div", "lib-head");
    head.appendChild(el("strong", null, (op.enabled ? "" : "⏸ ") + op.name));
    head.appendChild(el("code", "small", op.method + " " + op.path));
    head.append(button("Edit", "link small", function () { card.replaceWith(operationEditor(d, op)); }),
                button("Test", "link small", function () { toggleTest(card, op); }));
    card.appendChild(head);
    if (op.description) card.appendChild(el("p", "small", op.description));
    card.appendChild(el("p", "muted small", "Parameters: " + paramsSummary(op.params)));
    const examples = op.example_questions.split("\n").filter(function (q) { return q.trim(); });
    card.appendChild(el("p", "muted small", examples.length ? "Asked like: “" + examples.slice(0, 3).join("”, “") + "”" +
                                                (examples.length > 3 ? " +" + (examples.length - 3) : "")
                                              : "⚠️ No example questions: chat never routes here."));
    return card;
  }

  function operationEditor(d, op) {
    const box = el("div", "api-op editing");
    const name = input(op ? op.name : "");
    const description = input(op ? op.description : "", { multiline: true, rows: 2 });
    const method = select([["GET", "GET"], ["POST", "POST (fixed body)"]], op ? op.method : "GET");
    const path = input(op ? op.path : "/", { placeholder: "/tickets/{ticketId}" });
    const body = input(op ? op.body : "", { multiline: true, rows: 4, placeholder: "{\"size\": 0}" });
    const params = input(JSON.stringify(op ? op.params : [], null, 1), { multiline: true, rows: 5 });
    const examples = input(op ? op.example_questions : "", { multiline: true, rows: 4,
                                                              placeholder: "One question per line" });
    const cache = input(op ? op.cache_seconds : 60, { type: "number" });
    const enabled = checkbox("Enabled", op ? op.enabled : true);
    const bodyField = field("Body (fixed; the model only fills {{name}} slots, with checked values)", body);
    function toggle() { bodyField.hidden = method.value !== "POST"; }
    method.addEventListener("change", toggle);
    toggle();
    box.append(field("Name", name), field("Description (helps the model fill parameters)", description),
               field("Method", method), field("Path", path, "Relative to the API address; {name} for path parameters."),
               bodyField,
               field("Parameters (JSON)", params,
                     'e.g. [{"name": "days", "type": "int", "in": "query", "min": 1, "max": 31, "default": 7, ' +
                     '"help": "How many days"}]. Types: string, int, date, enum (with "options"). "in": query, ' +
                     'path, or body (POST: write {{name}} in the body where the value goes).'),
               field("Example questions", examples), field("Cache (seconds)", cache), enabled);
    box.append(button("Save", "small", function () {
      let parsed;
      try { parsed = JSON.parse(params.value || "[]"); } catch (e) { showError("Parameters aren't valid JSON."); return; }
      const payload = { name: name.value.trim(), description: description.value, method: method.value,
                        path: path.value.trim(), body: method.value === "POST" ? body.value : "",
                        params: parsed, example_questions: examples.value, cache_seconds: Number(cache.value) || 0,
                        enabled: enabled.box.checked };
      run(async function () {
        if (op) await api("PUT", "/api/admin/apis/operations/" + op.id, payload);
        else await api("POST", "/api/admin/apis/" + d.id + "/operations", payload);
        await open(d.id);
      });
    }), button("Cancel", "link small", function () { open(d.id); }));
    if (op) {
      box.appendChild(button("Delete operation", "link danger small", function () {
        if (!confirm("Delete the operation " + op.name + "?")) return;
        run(async function () { await api("DELETE", "/api/admin/apis/operations/" + op.id); await open(d.id); });
      }));
    }
    return box;
  }

  function toggleTest(card, op) {
    const existing = card.querySelector(".api-test");
    if (existing) { existing.remove(); return; }
    const box = el("div", "api-test");
    const inputs = {};
    op.params.forEach(function (p) {
      const i = p.type === "enum" ? select([["", "(default)"]].concat((p.options || []).map(function (o) { return [o, o]; })), "")
                                  : input(p.default !== undefined ? p.default : "", { placeholder: p.help || p.type });
      inputs[p.name] = i;
      box.appendChild(field(p.name + (p.required ? " *" : ""), i));
    });
    const out = el("div");
    box.append(button("Run now", "small", function () {
      const params = {};
      Object.keys(inputs).forEach(function (k) { if (inputs[k].value !== "") params[k] = inputs[k].value; });
      out.replaceChildren(el("p", "muted small", "Calling…"));
      run(async function () {
        const r = await api("POST", "/api/admin/apis/operations/" + op.id + "/test", { params: params });
        out.replaceChildren();
        if (!r.ok) { out.appendChild(el("p", "error", "Failed: " + r.error)); return; }
        out.appendChild(el("p", "small", "✓ HTTP " + r.status + (r.truncated ? " (cut at 256 KB)" : "")));
        if (r.fields.length) {
          const t = el("table", "admin-table");
          const head = el("tr");
          head.append(el("th", null, "Field"), el("th", null, "Type"));
          t.appendChild(el("thead")).appendChild(head);
          const body = el("tbody");
          r.fields.forEach(function (f) {
            const tr = el("tr");
            tr.append(el("td", "mono small", f.path), el("td", "small", f.type));
            body.appendChild(tr);
          });
          t.appendChild(body);
          out.appendChild(t);
        }
        const pre = el("pre", "api-response");
        pre.textContent = r.response;
        out.appendChild(pre);
        await load();
      });
    }), out);
    card.appendChild(box);
  }

  // ------------------------------------------------------- documentation
  function docsSection(d) {
    const s = section("Documentation");
    if (d.description_html) {
      const desc = el("div", "doc small");
      desc.innerHTML = d.description_html;   // rendered server-side from Markdown, raw HTML escaped
      s.appendChild(desc);
    }
    const description = input(d.description, { multiline: true, rows: 4 });
    s.append(field("Description (Markdown)", description),
             button("Save description", "small", function () { patch({ description: description.value }); }));

    const spec = input(d.spec, { multiline: true, rows: 6,
                                 placeholder: "Paste the API's documentation: Markdown, or OpenAPI/Swagger JSON" });
    s.append(field("Imported documentation", spec), button("Save documentation", "small", function () {
      patch({ spec: spec.value });
    }));
    if (d.spec_html) {
      const doc = el("details", "api-spec");
      doc.appendChild(el("summary", "small", "Read the imported documentation"));
      const body = el("div", "doc small");
      body.innerHTML = d.spec_html;          // rendered server-side from Markdown, raw HTML escaped
      doc.appendChild(body);
      s.appendChild(doc);
    }
    if (d.spec_operations.length) {
      s.appendChild(el("p", "small", "Operations described in the OpenAPI document (GET only):"));
      const known = new Set(d.operations.map(function (o) { return o.path; }));
      const ul = el("ul", "grants");
      d.spec_operations.forEach(function (o) {
        const li = el("li");
        li.append(el("code", "small", "GET " + o.path), el("span", "small", " " + o.name + " · " + paramsSummary(o.params)));
        if (!known.has(o.path)) {
          li.appendChild(button("Add", "link small", function () {
            run(async function () {
              await api("POST", "/api/admin/apis/" + d.id + "/operations",
                        { name: o.name, description: o.description, path: o.path, params: o.params });
              await open(d.id);
            });
          }));
        }
        ul.appendChild(li);
      });
      s.appendChild(ul);
    }
    return s;
  }

  function callsSection(d) {
    const s = section("Recent calls");
    if (!d.calls.length) { s.appendChild(el("p", "muted small", "None yet.")); return s; }
    const t = el("table", "admin-table");
    const head = el("tr");
    ["When (UTC)", "Who", "Via", "Operation", "Result"].forEach(function (h) { head.appendChild(el("th", null, h)); });
    t.appendChild(el("thead")).appendChild(head);
    const body = el("tbody");
    d.calls.forEach(function (c) {
      const tr = el("tr");
      const result = c.ok ? "✓ " + (c.status_code || "") + (c.cached ? " cached" : "") + (c.ms ? " · " + c.ms + " ms" : "")
                          : "✗ " + (c.error || c.status_code);
      [c.at, c.username || "—", c.via, c.operation_name + (c.params && c.params !== "{}" ? " " + c.params : ""),
       result].forEach(function (v) { tr.appendChild(el("td", "small", v)); });
      body.appendChild(tr);
    });
    t.appendChild(body);
    s.appendChild(t);
    return s;
  }

  window.ErynneApis = { load: load };
  const panel = document.querySelector('[data-panel="apis"]');
  if (panel && !panel.hidden) load();
})();
