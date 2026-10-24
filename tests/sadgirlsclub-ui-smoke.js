/* Browser-only Cockpit fixture and UI smoke tests. Never installed on the NAS. */
"use strict";
const fixture = {
  users: ["Administrator", "aedith"], groups: ["Domain Admins", "Family"], writes: [],
  memberships: {aedith: ["Family"], Administrator: ["Domain Admins"]}, disabled: new Set()
};
window.cockpit = {
  spawn(argv, options) {
    if (options.superuser !== "require" || argv.join(" ") !== "/usr/bin/python3 -I /usr/local/libexec/sadgirlsclub-admin") throw new Error("Unexpected bridge contract");
    let done;
    return {
      done(callback) { done = callback; }, fail() {},
      input(raw) {
        const request = JSON.parse(raw);
        let data;
        const {action, name, member} = request;
        if (action === "overview") data = {realm: "SADGIRLSCLUB.LOCAL", domain: "SADGIRLSCLUB", users: [...fixture.users], groups: [...fixture.groups], builtin_groups: ["Domain Admins"]};
        else if (action === "user" || action === "group") data = {
          name, protected: ["Administrator", "Domain Admins"].includes(name), disabled: fixture.disabled.has(name),
          dn: `CN=${name},CN=Users,DC=sadgirlsclub,DC=local`, display_name: name === "aedith" ? "Aedith" : "",
          memberships: fixture.memberships[name] || [],
          members: fixture.users.filter(user => (fixture.memberships[user] || []).includes(name))
        };
        else {
          fixture.writes.push(action); // Record operation names only, never fixture passwords.
          if (action === "create_user") { fixture.users.push(name); fixture.memberships[name] = []; }
          if (action === "create_group") fixture.groups.push(name);
          if (action === "add_member") fixture.memberships[member].push(name);
          if (action === "remove_member") fixture.memberships[member] = fixture.memberships[member].filter(group => group !== name);
          if (action === "disable") fixture.disabled.add(name);
          if (action === "enable") fixture.disabled.delete(name);
          if (action === "delete_user") fixture.users = fixture.users.filter(user => user !== name);
          if (action === "delete_group") fixture.groups = fixture.groups.filter(group => group !== name);
          data = {message: "Change saved."};
        }
        setTimeout(() => done(JSON.stringify({ok: true, data})), 1);
      }
    };
  }
};

window.addEventListener("load", async () => {
  const $ = id => document.getElementById(id);
  const assert = (condition, message) => { if (!condition) throw new Error(message); };
  const wait = async predicate => {
    for (let attempt = 0; attempt < 200; attempt++) {
      if (predicate()) return;
      await new Promise(resolve => setTimeout(resolve, 10));
    }
    throw new Error("Timed out waiting for UI state");
  };
  const settled = () => wait(() => !$("refresh").disabled);
  const choose = async name => {
    const entry = [...document.querySelectorAll(".object")].find(item => item.querySelector(".object-name").textContent === name);
    assert(entry, "Missing object: " + name); entry.click(); await settled();
  };
  const field = (name, value) => { document.querySelector(`[name="${name}"]`).value = value; };
  const action = text => [...document.querySelectorAll("#detail button")].find(item => item.textContent === text);
  const submit = async () => {
    $("form").requestSubmit(); await settled();
    if (!$("dialog").open) await wait(() => $("fields").children.length === 0);
  };
  try {
    await wait(() => $("user-count").textContent === "2" && !$("refresh").disabled);
    await choose("aedith");
    if (!location.search.includes("smoke")) {
      $("notice").textContent = "VISUAL PREVIEW · Sample directory data · No connection to your NAS";
      $("notice").hidden = false;
      return;
    }
    await choose("Administrator");
    assert([...document.querySelectorAll("#detail button")].every(item => item.disabled), "Built-in object was editable");
    $("create").click();
    field("name", "alice"); field("password", "Fixture Only 53!"); field("password_confirm", "different");
    await submit();
    assert(!$("form-error").hidden && fixture.writes.length === 0, "Mismatched password reached backend");
    field("password_confirm", "Fixture Only 53!"); await submit();
    assert(fixture.users.includes("alice") && $("detail").querySelector("h2").textContent === "alice", "User creation failed");
    assert(document.querySelectorAll("input[type=password]").length === 0, "Password fields were retained");
    action("+ Add to group").click(); field("choice", "Family"); await submit();
    assert(fixture.memberships.alice.includes("Family"), "Add membership failed");
    action("Disable account").click(); await submit();
    assert($("detail").querySelector(".badge").textContent === "DISABLED", "Disable status not updated");
    action("Enable account").click(); await submit();
    action("Reset password").click(); field("password", "Replacement 53!"); field("password_confirm", "Replacement 53!"); await submit();
    assert(fixture.writes.includes("reset_password"), "Password reset did not submit");
    $("groups-tab").click();
    const visibleNames = () => [...document.querySelectorAll(".object-name")].map(item => item.textContent);
    assert(!$("group-filter").hidden && $("hide-builtins").checked, "Group filter not enabled by default");
    assert(visibleNames().join(",") === "Family", "Built-in groups were not hidden");
    assert($("list-count").textContent.includes("1 built-in hidden") && $("group-count").textContent === "2", "Group counts lost hidden objects");
    $("search").value = "Domain"; $("search").dispatchEvent(new Event("input"));
    assert(visibleNames().length === 0, "Search bypassed the built-in filter");
    $("hide-builtins").click();
    assert(visibleNames().join(",") === "Domain Admins", "Show built-ins ignored search");
    await choose("Domain Admins");
    assert([...document.querySelectorAll("#detail button")].every(item => item.disabled), "Revealed built-in group became editable");
    $("hide-builtins").click();
    assert(!$("detail").querySelector(".detail-header"), "Hidden selected group left stale details");
    $("search").value = ""; $("search").dispatchEvent(new Event("input"));
    $("hide-builtins").click(); $("users-tab").click();
    assert($("group-filter").hidden && visibleNames().includes("Administrator"), "Group filter affected users");
    $("groups-tab").click(); $("refresh").click(); await settled();
    assert(!$("hide-builtins").checked && visibleNames().includes("Domain Admins"), "Refresh or tab switch reset the filter");
    $("hide-builtins").click();
    $("create").click(); field("name", "Archive"); await submit();
    action("+ Add member").click(); field("choice", "alice"); await submit();
    assert($("detail").querySelector(".chip").textContent.includes("alice"), "Member display failed");
    $("detail").querySelector(".chip button").click(); await submit();
    assert(!fixture.memberships.alice.includes("Archive"), "Remove membership failed");
    action("Delete group").click(); $("cancel").click();
    assert(fixture.groups.includes("Archive"), "Cancel deleted a group");
    await new Promise(resolve => setTimeout(resolve, 20));
    action("Delete group").click(); field("confirmation", "DELETE Archive"); await submit();
    assert(!fixture.groups.includes("Archive"), "Delete group failed");
    $("users-tab").click(); await choose("alice");
    action("Delete user").click(); field("confirmation", "DELETE alice"); await submit();
    assert(!fixture.users.includes("alice"), "Delete user failed");
    // Directory text must never become executable HTML, including in the list view.
    fixture.groups.push("<img src=x onerror=alert(1)>");
    $("refresh").click(); await settled(); $("groups-tab").click();
    assert(document.querySelectorAll("#object-list img").length === 0, "Directory text became HTML");
    $("search").value = "Family"; $("search").dispatchEvent(new Event("input"));
    assert(document.querySelectorAll(".object").length === 1, "Search did not filter");
    document.body.dataset.viewportWidth = String(window.innerWidth);
    document.body.dataset.contentWidth = String(document.documentElement.scrollWidth);
    assert(document.documentElement.scrollWidth <= window.innerWidth, "Page overflows the viewport");
    document.body.dataset.testResult = "passed";
    $("notice").hidden = false; $("notice").textContent = "UI SMOKE TESTS PASSED";
  } catch (error) {
    document.body.dataset.testResult = "failed";
    $("notice").hidden = false; $("notice").textContent = "UI SMOKE TEST FAILURE: " + error.message;
  }
});
