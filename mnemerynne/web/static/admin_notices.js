/* Access → Announcements: post a notice for everyone or a group, see who saw and acknowledged
   each one, and remove them. All text through textContent. */
(function () {
  "use strict";

  const { el, api } = window.Erynne;
  const $ = function (id) { return document.getElementById(id); };
  const errorNode = $("admin-error");

  function showError(message) {
    errorNode.textContent = message || "";
    errorNode.hidden = !message;
  }

  function fillGroups(groups) {
    const list = $("announcement-groups");
    list.replaceChildren();
    (groups || []).forEach(function (g) { const o = el("option"); o.value = g; list.appendChild(o); });
  }

  function when(iso) {
    if (!iso) return "";
    const d = new Date(iso.replace(" ", "T") + (iso.length > 10 && !/Z$/.test(iso) ? "Z" : ""));
    return isNaN(d) ? iso : d.toLocaleString([], { dateStyle: "medium", timeStyle: "short" });
  }

  function row(a) {
    const li = el("li", "notice-admin" + (a.expired ? " muted" : ""));
    const head = el("div", "ax-bar");
    const title = el("strong", null, a.title);
    const meta = el("span", "muted small",
      " · " + (a.group || "everyone") + (a.must_ack ? " · must acknowledge" : "") +
      (a.expires_at ? (a.expired ? " · expired " : " · until ") + when(a.expires_at) : "") +
      " · seen by " + a.seen + (a.must_ack ? ", acknowledged by " + a.acked : "") +
      " · by " + (a.author || "?") + " " + when(a.created_at));
    const remove = el("button", "link danger small", "Remove");
    remove.type = "button";
    remove.addEventListener("click", async function () {
      if (!confirm("Remove “" + a.title + "”?")) return;
      showError("");
      try { render(await api("DELETE", "/api/admin/announcements/" + a.id)); } catch (e) { showError(e.message); }
    });
    head.append(title, meta, remove);
    li.appendChild(head);
    if (a.body) li.appendChild(el("p", "small", a.body));
    if (a.link) { const link = el("a", "small", a.link); link.href = a.link; li.appendChild(link); }
    return li;
  }

  function render(data) {
    fillGroups(data.groups);
    const box = $("announcements-admin");
    box.replaceChildren();
    if (!data.announcements.length) { box.appendChild(el("p", "muted", "No announcements yet.")); return; }
    const ul = el("ul", "grants");
    data.announcements.forEach(function (a) { ul.appendChild(row(a)); });
    box.appendChild(ul);
  }

  async function load() {
    try { render(await api("GET", "/api/admin/announcements")); }
    catch (e) { $("announcements-admin").replaceChildren(el("p", "error", e.message)); }
  }

  const form = $("new-announcement");
  if (form) form.addEventListener("submit", async function (e) {
    e.preventDefault();
    showError("");
    const button = form.querySelector("button[type=submit]");
    button.disabled = true;
    try {
      const data = await api("POST", "/api/admin/announcements", {
        title: $("an-title").value.trim(), body: $("an-body").value, link: $("an-link").value.trim(),
        group: $("an-group").value.trim(), must_ack: $("an-ack").checked, expires_at: $("an-expires").value || null,
      });
      form.reset();
      render(data);
    } catch (err) {
      showError(err.message);
    } finally {
      button.disabled = false;
    }
  });

  window.ErynneNotices = { load: load };
})();
