/* Sadgirlsclub Directory: a small Cockpit UI, with directory text rendered as text only. */
"use strict";
(() => {
  const $ = id => document.getElementById(id);
  const state = {kind: "user", users: [], groups: [], builtinGroups: new Set(), selected: null, info: null, busy: false};
  let modalTask = null;

  function node(tag, text, className) { // Creates safe DOM nodes without interpolating directory data into HTML.
    const element = document.createElement(tag);
    if (text !== undefined) element.textContent = text;
    if (className) element.className = className;
    return element;
  }

  function button(text, handler, className = "", locked = false) { // Attaches actions and preserves permanent read-only restrictions.
    const element = node("button", text, className);
    element.type = "button";
    element.dataset.locked = locked ? "yes" : "";
    element.disabled = state.busy || locked;
    element.addEventListener("click", handler);
    return element;
  }

  function busy(value) { // Prevents duplicate submissions and navigation during a pending command.
    state.busy = value;
    document.querySelectorAll("button").forEach(element => { element.disabled = value || element.dataset.locked === "yes"; });
    $("search").disabled = value;
    $("hide-builtins").disabled = value;
    $("detail").setAttribute("aria-busy", String(value));
  }

  function notice(message = "", error = false) { // Reports success and transport errors in a persistent live region.
    $("notice").textContent = message;
    $("notice").className = "notice" + (error ? " error" : "");
    $("notice").hidden = !message;
  }

  function api(action, values = {}) { // Uses Cockpit's authenticated privileged bridge, never an HTTP admin endpoint.
    if (!window.cockpit) return Promise.reject(new Error("Open this page through Cockpit on your NAS, then enable Administrative access."));
    return new Promise((resolve, reject) => {
      const process = cockpit.spawn(["/usr/bin/python3", "-I", "/usr/local/libexec/sadgirlsclub-admin"], {
        superuser: "require", err: "message"
      });
      process.done(output => {
        try {
          const response = JSON.parse(output);
          if (response.ok) resolve(response.data);
          else reject(new Error(response.error || "The directory change failed."));
        } catch (error) { reject(new Error("The helper returned an invalid response. Check its installation.")); }
      });
      process.fail(error => reject(new Error(error.message || "Enable Administrative access in Cockpit and try again.")));
      process.input(JSON.stringify({action, ...values})); // The pipe closes after this request; passwords stay out of argv.
    });
  }

  function hiddenGroup(name) { // Uses authoritative identity metadata, never English group names.
    return state.kind === "group" && $("hide-builtins").checked && state.builtinGroups.has(name.toLowerCase());
  }

  function renderList() { // Combines name search with the built-in filter and clears newly hidden selections.
    const names = state.kind === "user" ? state.users : state.groups;
    const query = $("search").value.toLocaleLowerCase();
    const hiddenCount = names.filter(hiddenGroup).length;
    const filtered = names.filter(name => !hiddenGroup(name) && name.toLocaleLowerCase().includes(query));
    if (state.selected && hiddenGroup(state.selected)) {
      state.selected = null; state.info = null; welcome();
    }
    $("group-filter").hidden = state.kind !== "group";
    $("object-list").replaceChildren();
    for (const name of filtered) {
      const entry = button("", () => select(name), "object" + (state.selected === name ? " selected" : ""));
      entry.setAttribute("aria-pressed", String(state.selected === name));
      entry.append(node("span", state.kind === "user" ? name.slice(0, 2).toUpperCase() : "#", "avatar"),
        node("span", name, "object-name"), node("span", "›", "arrow"));
      $("object-list").append(entry);
    }
    if (!filtered.length) $("object-list").append(node("p", hiddenCount ? "No matches. Try another name or show built-in groups." : "No matches. Try another name.", "empty"));
    $("list-count").textContent = `${filtered.length} of ${names.length} ${state.kind === "user" ? "users" : "groups"}` +
      (hiddenCount ? ` · ${hiddenCount} built-in hidden` : "");
    $("users-tab").classList.toggle("active", state.kind === "user");
    $("groups-tab").classList.toggle("active", state.kind === "group");
    $("users-tab").setAttribute("aria-pressed", String(state.kind === "user"));
    $("groups-tab").setAttribute("aria-pressed", String(state.kind === "group"));
    $("list-heading").textContent = state.kind === "user" ? "PEOPLE" : "GROUPS";
    $("create").textContent = state.kind === "user" ? "+ New user" : "+ New group";
    $("search").placeholder = state.kind === "user" ? "Find someone…" : "Find a group…";
  }

  function welcome() { // Shows a useful empty state when there is no selected directory object.
    const content = node("div", undefined, "welcome");
    content.append(node("span", "✧", "orb"), node("p", "A LITTLE ORDER. A LOT OF HEART.", "eyebrow"),
      node("h2", "Everyone belongs somewhere."), node("p", "Select a user or group to manage their place in your domain."));
    $("detail").replaceChildren(content);
  }

  function openForm(title, description, fields, submitLabel, task, dangerous = false) { // Builds accessible dialogs with explicit confirmation for changes.
    $("form").reset();
    $("fields").replaceChildren();
    $("dialog-title").textContent = title;
    $("dialog-description").textContent = description;
    $("form-error").hidden = true;
    $("submit").textContent = submitLabel;
    $("submit").className = dangerous ? "danger" : "";
    for (const field of fields) {
      const label = node("label", undefined, field.type === "checkbox" ? "check" : "field");
      let control;
      if (field.options) {
        control = node("select");
        control.append(new Option("Choose…", ""));
        field.options.forEach(value => control.append(new Option(value, value)));
      } else {
        control = node("input");
        control.type = field.type || "text";
        control.maxLength = field.maxLength || 200;
        control.autocomplete = field.type === "password" ? "new-password" : "off";
        if (field.type === "checkbox") control.checked = Boolean(field.checked);
        if (field.pattern) control.pattern = field.pattern;
      }
      control.name = field.name;
      control.id = "field-" + field.name;
      control.required = Boolean(field.required);
      if (field.placeholder) control.placeholder = field.placeholder;
      label.htmlFor = control.id;
      if (field.type === "checkbox") label.append(control, document.createTextNode(field.label));
      else label.append(document.createTextNode(field.label), control);
      $("fields").append(label);
    }
    modalTask = task;
    $("dialog").showModal();
    $("fields").querySelector("input,select")?.focus();
  }

  function passwordFields() { // Uses separate password and confirmation fields; Samba enforces domain password policy.
    return [{name: "password", label: "Password", type: "password", required: true, maxLength: 256},
      {name: "password_confirm", label: "Confirm password", type: "password", required: true, maxLength: 256},
      {name: "must_change", label: "Require a password change at next Windows sign-in", type: "checkbox", checked: false}];
  }

  function closeDialog() { // Clears secrets and callbacks synchronously, before the next dialog can open.
    $("dialog").close();
    $("form").reset(); $("fields").replaceChildren(); modalTask = null;
  }

  function create() { // Creates ordinary domain users or global security groups, without granting administrator rights.
    const kind = state.kind;
    const fields = [{name: "name", label: kind === "user" ? "Username" : "Group name", required: true,
      maxLength: kind === "user" ? 20 : 100, ...(kind === "user" ? {pattern: "[A-Za-z0-9][A-Za-z0-9._-]{0,19}"} : {})}];
    if (kind === "user") fields.push({name: "given_name", label: "First name"}, {name: "surname", label: "Last name"},
      {name: "email", label: "Email address (optional)", type: "email"}, ...passwordFields());
    openForm(kind === "user" ? "Welcome someone new." : "Bring people together.",
      kind === "user" ? "Create a Windows domain account. You can add group memberships afterward."
        : "Create a security group for organizing access. Folder permissions are assigned separately.",
      fields, kind === "user" ? "Create user" : "Create group", async values => {
        await api("create_" + kind, values);
        return {message: `${values.name} was created.`, select: values.name};
      });
  }

  function resetPassword() { // Sends the new secret only through the authenticated Cockpit input pipe.
    const name = state.selected;
    openForm("Set a new password.", `Change the domain password for ${name}.`, passwordFields(), "Set password", async values => {
      await api("reset_password", {name, ...values});
      return {message: `Password updated for ${name}.`, select: name};
    });
  }

  function confirmAction(action, title, explanation) { // Requires explicit approval; deletion additionally requires the exact target name.
    const name = state.selected;
    const deleting = action.startsWith("delete_");
    openForm(title, explanation, deleting ? [{name: "confirmation", label: `Type DELETE ${name}`, required: true, maxLength: 270}] : [],
      deleting ? "Delete permanently" : "Confirm", async values => {
        await api(action, {name, ...values});
        return {message: deleting ? `${name} was deleted.` : `Updated ${name}.`, select: deleting ? null : name};
      }, deleting || action === "disable");
  }

  function addMembership() { // Lets the admin select a user or group instead of memorizing command syntax.
    const isUser = state.kind === "user";
    const name = state.selected;
    const existing = isUser ? state.info.memberships : state.info.members;
    const candidates = (isUser ? state.groups : state.users).filter(value => !existing.some(item => item.toLowerCase() === value.toLowerCase()));
    openForm(isUser ? "Add to a group." : "Add a member.",
      "Built-in accounts and critical groups are read-only. Select an ordinary user and group.",
      [{name: "choice", label: isUser ? "Group" : "User", required: true, options: candidates}], "Add membership", async values => {
        await api("add_member", {name: isUser ? values.choice : name, member: isUser ? name : values.choice});
        return {message: "Group membership added.", select: name};
      });
  }

  function removeMembership(group, member) { // Confirms removal without deleting either directory object.
    const selected = state.selected;
    openForm("Remove membership?", `Remove ${member} from ${group}? The user and group will remain in the directory.`, [],
      "Remove membership", async () => {
        await api("remove_member", {name: group, member});
        return {message: "Group membership removed.", select: selected};
      }, true);
  }

  function renderDetail() { // Renders account properties and actions, with read-only treatment for protected objects.
    const info = state.info;
    if (!info) return welcome();
    const isUser = state.kind === "user";
    const locked = info.protected || info.name.endsWith("$");
    const header = node("div", undefined, "detail-header");
    const heading = node("div");
    heading.append(node("p", isUser ? "DOMAIN USER" : "SECURITY / DISTRIBUTION GROUP", "eyebrow"), node("h2", info.name));
    if (info.display_name) heading.append(node("p", info.display_name, "description"));
    header.append(heading, node("span", locked ? "BUILT-IN · READ ONLY" : isUser ? (info.disabled ? "DISABLED" : "ENABLED") : "GROUP", "badge" + (info.disabled ? " disabled" : "")));
    const facts = node("dl", undefined, "facts");
    for (const [label, value] of [["Domain", $("domain").textContent], ["Location", info.dn], ["Email", info.email], ["Description", info.description]]) {
      if (value) facts.append(node("dt", label), node("dd", value));
    }
    const actions = node("div", undefined, "actions");
    if (isUser) {
      actions.append(button("Reset password", resetPassword, "", locked),
        button(info.disabled ? "Enable account" : "Disable account", () => confirmAction(info.disabled ? "enable" : "disable",
          info.disabled ? "Enable this account?" : "Disable this account?", `Change whether ${info.name} can sign in to the domain.`), "secondary", locked),
        button("Unlock account", () => confirmAction("unlock", "Unlock this account?", "Clear a lockout caused by failed sign-in attempts."), "secondary", locked));
    }
    actions.append(button("Delete " + state.kind, () => confirmAction("delete_" + state.kind, "Delete " + info.name + "?",
      "This permanently removes the directory object. Recreating the same name will not restore its identity or permissions."), "danger", locked));
    const section = node("div", undefined, "section-title");
    section.append(node("h3", isUser ? "Member of" : "Members"), button(isUser ? "+ Add to group" : "+ Add member", addMembership, "small-button", locked));
    const chips = node("div", undefined, "chips");
    const items = isUser ? info.memberships : info.members;
    for (const item of items) {
      const chip = node("span", undefined, "chip");
      chip.append(node("span", item));
      if (!locked) {
        const remove = button("×", () => removeMembership(isUser ? item : info.name, isUser ? info.name : item));
        remove.setAttribute("aria-label", `Remove ${isUser ? info.name : item} from ${isUser ? item : info.name}`);
        chip.append(remove);
      }
      chips.append(chip);
    }
    if (!items.length) chips.append(node("p", "No memberships to show.", "hint"));
    $("detail").replaceChildren(header, facts, actions, section, chips,
      node("p", "Group changes may require signing out and back in on Windows. Folder access is managed separately.", "hint"));
  }

  async function select(name) { // Loads one selected object and leaves the directory usable after a read error.
    if (state.busy) return;
    state.selected = name;
    busy(true);
    renderList();
    try { state.info = await api(state.kind, {name}); renderDetail(); }
    catch (error) { state.info = null; welcome(); notice(error.message, true); }
    finally { busy(false); }
  }

  async function reload(selected = state.selected) { // Re-reads authoritative state after each successful change.
    const data = await api("overview");
    state.users = data.users;
    state.groups = data.groups;
    state.builtinGroups = new Set((data.builtin_groups || []).map(name => name.toLowerCase()));
    $("domain").textContent = data.realm;
    $("user-count").textContent = data.users.length;
    $("group-count").textContent = data.groups.length;
    const names = state.kind === "user" ? state.users : state.groups;
    state.selected = names.find(name => selected && name.toLowerCase() === selected.toLowerCase()) || null;
    state.info = state.selected ? await api(state.kind, {name: state.selected}) : null;
    renderList(); renderDetail();
  }

  async function refresh() { // Supports an explicit retry after enabling Cockpit administrative access.
    if (state.busy) return;
    busy(true); notice();
    try { await reload(); }
    catch (error) { notice(error.message, true); $("domain").textContent = "CONNECTION NEEDED"; }
    finally { busy(false); }
  }

  function switchKind(kind) { // Keeps the user and group views separate and resets stale object details.
    if (state.busy) return;
    state.kind = kind; state.selected = null; state.info = null;
    $("search").value = "";
    renderList(); welcome();
  }

  $("form").addEventListener("submit", async event => { // Submits once, clears secrets promptly, and keeps failed operations visible.
    event.preventDefault();
    if (state.busy || !modalTask) return;
    const values = Object.fromEntries(new FormData($("form")));
    $("fields").querySelectorAll("input[type=checkbox]").forEach(input => { values[input.name] = input.checked; });
    if (values.password !== undefined && values.password !== values.password_confirm) {
      $("form-error").textContent = "The two passwords do not match."; $("form-error").hidden = false; return;
    }
    busy(true); $("form-error").hidden = true;
    let result;
    try { result = await modalTask(values); }
    catch (error) { $("form-error").textContent = error.message; $("form-error").hidden = false; }
    finally {
      delete values.password; delete values.password_confirm;
      $("fields").querySelectorAll("input[type=password]").forEach(input => { input.value = ""; });
    }
    if (result) {
      closeDialog();
      notice(result.message);
      try { await reload(result.select); }
      catch (error) { notice(`${result.message} Refresh failed: ${error.message} Do not repeat the change.`, true); }
    }
    busy(false);
  });
  $("cancel").addEventListener("click", () => { if (!state.busy) closeDialog(); });
  $("dialog").addEventListener("cancel", event => { event.preventDefault(); if (!state.busy) closeDialog(); });
  $("users-tab").addEventListener("click", () => switchKind("user"));
  $("groups-tab").addEventListener("click", () => switchKind("group"));
  $("search").addEventListener("input", renderList);
  $("hide-builtins").addEventListener("change", renderList);
  $("refresh").addEventListener("click", refresh);
  $("create").addEventListener("click", create);
  refresh();
})();
