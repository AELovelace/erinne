/* Access administration: libraries and their grants, Claude sharing, groups,
   a who-can-see-what overview and the change log.
   All user-controlled text goes through textContent, never innerHTML. */
(function () {
  "use strict";

  const { el, api } = window.Erinne;
  const libsNode = document.getElementById("libs");
  const groupsNode = document.getElementById("groups");
  const accessNode = document.getElementById("access");
  const logNode = document.getElementById("log");
  const activityNode = document.getElementById("activity");
  const errorNode = document.getElementById("admin-error");
  let principals = { users: [], groups: [] };

  function showError(message) {
    errorNode.textContent = message || "";
    errorNode.hidden = !message;
    if (message) errorNode.scrollIntoView({ block: "nearest" });
  }

  /* Run a change, show its error if any, then reload everything. */
  async function change(method, url, body) {
    showError("");
    try { await api(method, url, body); } catch (e) { showError(e.message); }
    await load();
  }

  function sizeLabel(bytes) {
    if (bytes >= 1073741824) return (bytes / 1073741824).toFixed(1) + " GB";
    if (bytes >= 1048576) return (bytes / 1048576).toFixed(1) + " MB";
    return Math.round(bytes / 1024) + " KB";
  }

  function accessSelect(value, withNone) {
    const select = el("select");
    [["read", "Can read"], ["write", "Can edit"]].concat(withNone ? [["none", "Remove"]] : []).forEach(function (o) {
      const opt = el("option", null, o[1]);
      opt.value = o[0];
      select.appendChild(opt);
    });
    select.value = value;
    return select;
  }

  function checkbox(labelText, checked, onChange, title) {
    const label = el("label", "check small");
    const input = el("input");
    input.type = "checkbox";
    input.checked = checked;
    input.addEventListener("change", function () { onChange(input.checked); });
    label.append(input, document.createTextNode(" " + labelText));
    if (title) label.title = title;
    return label;
  }

  function table(headers, rows) {
    const t = el("table", "admin-table");
    const head = el("tr");
    headers.forEach(function (h) { head.appendChild(el("th", null, h)); });
    t.appendChild(el("thead")).appendChild(head);
    const body = el("tbody");
    rows.forEach(function (cells) {
      const tr = el("tr");
      cells.forEach(function (c) {
        const td = el("td");
        if (c instanceof Node) td.appendChild(c); else td.textContent = c;
        tr.appendChild(td);
      });
      body.appendChild(tr);
    });
    t.appendChild(body);
    return t;
  }

  function principalOptions(select, skip) {
    principals.users.forEach(function (u) {
      if (skip.has("user:" + u.id)) return;
      const o = el("option", null, "👤 " + u.name);
      o.value = "user:" + u.id;
      select.appendChild(o);
    });
    principals.groups.forEach(function (g) {
      if (skip.has("group:" + g.id)) return;
      const o = el("option", null, "👥 " + g.name);
      o.value = "group:" + g.id;
      select.appendChild(o);
    });
  }

  // ------------------------------------------------------------- libraries
  function renderLibrary(lib) {
    const grantsUrl = "/api/admin/libraries/" + lib.id + "/grants";
    const section = el("section", "lib");
    const head = el("div", "lib-head");
    head.appendChild(el("h3", null, "📚 " + lib.name));
    head.appendChild(el("span", "muted small", lib.files + (lib.files === 1 ? " file" : " files") + " · " + sizeLabel(lib.bytes)));
    const open = el("a", "small", "Open in Files");
    open.href = "/files#c=" + lib.id;
    head.appendChild(open);
    const rename = el("button", "link small", "Rename");
    rename.type = "button";
    rename.addEventListener("click", function () {
      const name = prompt("New name for " + lib.name, lib.name);
      if (!name || name.trim() === lib.name) return;
      change("PATCH", "/api/admin/libraries/" + lib.id, { name: name.trim() });
    });
    const del = el("button", "link danger small", "Delete");
    del.type = "button";
    del.addEventListener("click", function () {
      const typed = prompt("This deletes “" + lib.name + "” and all " + lib.files + " of its files for good, for everyone.\n\nType the library name to confirm:");
      if (typed === null) return;
      if (typed.trim() !== lib.name) { showError("The name didn't match, so nothing was deleted."); return; }
      change("DELETE", "/api/admin/libraries/" + lib.id);
    });
    head.append(rename, del);
    section.appendChild(head);

    const claudeRow = el("div", "claude-row");
    claudeRow.appendChild(checkbox("Shared with Claude", lib.claude_enabled, function (on) {
      change("PUT", "/api/admin/libraries/" + lib.id + "/claude", { enabled: on });
    }, "Master switch: when off, nobody can search this library from Claude"));
    if (lib.claude_changed_by) {
      claudeRow.appendChild(el("span", "muted small",
        "last changed by " + lib.claude_changed_by + ", " + (lib.claude_changed_at || "").slice(0, 16) + " UTC"));
    }
    section.appendChild(claudeRow);

    const list = el("ul", "grants");
    if (!lib.grants.length) list.appendChild(el("li", "muted small", "Nobody has access yet."));
    lib.grants.forEach(function (g) {
      const li = el("li");
      li.appendChild(el("span", null, (g.type === "group" ? "👥 " : "👤 ") + g.name));
      const claude = checkbox("Claude", g.claude, function (on) {
        change("PUT", grantsUrl, { type: g.type, id: g.id, access: g.access, claude: on });
      }, lib.claude_enabled ? "May search this library from Claude" : "The library isn't shared with Claude, so this has no effect yet");
      if (!lib.claude_enabled) claude.classList.add("muted");
      const select = accessSelect(g.access, true);
      select.setAttribute("aria-label", "Access for " + g.name);
      select.addEventListener("change", function () {
        change("PUT", grantsUrl, { type: g.type, id: g.id, access: select.value });
      });
      li.append(claude, select);
      list.appendChild(li);
    });
    section.appendChild(list);

    // Add someone
    const add = el("div", "grant-add");
    const who = el("select");
    who.setAttribute("aria-label", "Person or group to add");
    const blank = el("option", null, "Add a person or group…");
    blank.value = "";
    who.appendChild(blank);
    principalOptions(who, new Set(lib.grants.map(function (g) { return g.type + ":" + g.id; })));
    const access = accessSelect("read", false);
    access.setAttribute("aria-label", "Access to give");
    const go = el("button", "small", "Add");
    go.type = "button";
    go.addEventListener("click", function () {
      if (!who.value) return;
      const parts = who.value.split(":");
      change("PUT", grantsUrl, { type: parts[0], id: Number(parts[1]), access: access.value });
    });
    add.append(who, access, go);
    section.appendChild(add);
    return section;
  }

  // ---------------------------------------------------------------- groups
  function renderGroup(group) {
    const membersUrl = "/api/admin/groups/" + group.id + "/members";
    const memberIds = group.members.map(function (m) { return m.id; });
    const section = el("section", "lib");
    const head = el("div", "lib-head");
    head.appendChild(el("h3", null, "👥 " + group.name));
    head.appendChild(el("span", "muted small", group.members.length + (group.members.length === 1 ? " member" : " members")));
    const rename = el("button", "link small", "Rename");
    rename.type = "button";
    rename.addEventListener("click", function () {
      const name = prompt("New name for " + group.name, group.name);
      if (!name || name.trim() === group.name) return;
      change("PATCH", "/api/admin/groups/" + group.id, { name: name.trim() });
    });
    const del = el("button", "link danger small", "Delete");
    del.type = "button";
    del.addEventListener("click", function () {
      if (!confirm("Delete the group “" + group.name + "”? Its members lose any library access that came only from this group.")) return;
      change("DELETE", "/api/admin/groups/" + group.id);
    });
    head.append(rename, del);
    section.appendChild(head);

    const list = el("ul", "grants");
    if (!group.members.length) list.appendChild(el("li", "muted small", "No members yet."));
    group.members.forEach(function (m) {
      const li = el("li");
      li.appendChild(el("span", null, "👤 " + m.name));
      const remove = el("button", "link small", "Remove");
      remove.type = "button";
      remove.addEventListener("click", function () {
        change("PUT", membersUrl, { members: memberIds.filter(function (id) { return id !== m.id; }) });
      });
      li.appendChild(remove);
      list.appendChild(li);
    });
    section.appendChild(list);

    const add = el("div", "grant-add");
    const who = el("select");
    who.setAttribute("aria-label", "Person to add to " + group.name);
    const blank = el("option", null, "Add a person…");
    blank.value = "";
    who.appendChild(blank);
    principals.users.forEach(function (u) {
      if (memberIds.indexOf(u.id) !== -1) return;
      const o = el("option", null, "👤 " + u.name);
      o.value = String(u.id);
      who.appendChild(o);
    });
    const go = el("button", "small", "Add");
    go.type = "button";
    go.addEventListener("click", function () {
      if (!who.value) return;
      change("PUT", membersUrl, { members: memberIds.concat([Number(who.value)]) });
    });
    add.append(who, go);
    section.appendChild(add);
    return section;
  }

  // -------------------------------------------------------------- overview
  function personCell(p) {
    const cell = el("div");
    cell.appendChild(el("div", null, p.name + (p.role === "admin" ? " (admin)" : "")));
    if (p.tokens) {
      const revoke = el("button", "link danger small", "Revoke " + p.tokens + (p.tokens === 1 ? " Claude token" : " Claude tokens"));
      revoke.type = "button";
      revoke.addEventListener("click", function () {
        if (!confirm("Revoke all of " + p.name + "'s Claude tokens? Claude stops working for them straight away.")) return;
        change("DELETE", "/api/admin/users/" + p.id + "/tokens");
      });
      cell.appendChild(revoke);
    }
    return cell;
  }

  function renderActivity(calls) {
    activityNode.replaceChildren(calls.length
      ? table(["When (UTC)", "Who", "Tool", "Asked", "Files returned"], calls.map(function (c) {
          const asked = Object.keys(c.args).filter(function (k) { return c.args[k] !== null && c.args[k] !== ""; })
            .map(function (k) { return k + ": " + c.args[k]; }).join(", ");
          return [c.at.slice(0, 16), c.user || "(deleted)", c.tool + (c.ok ? "" : " (failed)"), asked, c.files.join(", ")];
        }))
      : el("p", "muted", "No Claude searches yet."));
  }

  function renderAccess(people) {
    const rows = [];
    people.forEach(function (p) {
      p.libraries.forEach(function (c, i) {
        let claude;
        if (c.kind === "private") {
          claude = checkbox(c.claude ? "yes" : "no", c.claude, function (on) {
            change("PUT", "/api/admin/users/" + p.id + "/inbox-claude", { enabled: on });
          }, "May search their own inbox from Claude");
        } else {
          claude = c.claude ? "yes" : "no";
        }
        rows.push([i === 0 ? personCell(p) : "",
                   c.kind === "private" ? "Inbox" : "📚 " + c.name,
                   c.access === "write" ? "edit" : "read", claude, c.via.join(", ")]);
      });
    });
    accessNode.replaceChildren(rows.length
      ? table(["Person", "Library", "Access", "Claude", "Through"], rows)
      : el("p", "muted", "No active users."));
  }

  function renderLog(entries) {
    logNode.replaceChildren(entries.length
      ? table(["When (UTC)", "Who", "Change", "On", "Details"], entries.map(function (e) {
          return [e.at.slice(0, 16), e.actor_name, e.action, e.target, e.detail];
        }))
      : el("p", "muted", "No changes yet."));
  }

  async function load() {
    try {
      const [libs, people, groups, access, log, activity] = await Promise.all([
        api("GET", "/api/admin/libraries"), api("GET", "/api/admin/principals"), api("GET", "/api/admin/groups"),
        api("GET", "/api/admin/access"), api("GET", "/api/admin/log?limit=50"),
        api("GET", "/api/admin/claude-activity?limit=50")]);
      principals = people;
      libsNode.replaceChildren();
      if (!libs.libraries.length) libsNode.appendChild(el("p", "muted", "No libraries yet."));
      libs.libraries.forEach(function (lib) { libsNode.appendChild(renderLibrary(lib)); });
      groupsNode.replaceChildren();
      if (!groups.groups.length) groupsNode.appendChild(el("p", "muted", "No groups yet."));
      groups.groups.forEach(function (g) { groupsNode.appendChild(renderGroup(g)); });
      renderAccess(access.users);
      renderLog(log.entries);
      renderActivity(activity.calls);
    } catch (e) { showError(e.message); }
  }

  function createForm(formId, inputId, url) {
    document.getElementById(formId).addEventListener("submit", async function (e) {
      e.preventDefault();
      const input = document.getElementById(inputId);
      showError("");
      try {
        await api("POST", url, { name: input.value.trim() });
        input.value = "";
      } catch (err) { showError(err.message); }
      await load();
    });
  }

  createForm("new-lib", "new-lib-name", "/api/admin/libraries");
  createForm("new-group", "new-group-name", "/api/admin/groups");
  load();
})();
