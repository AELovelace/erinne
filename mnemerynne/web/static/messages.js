/* The Messages page: announcements meant for you (with their state), a note to a colleague,
   your inbox (unread first; clicking marks read) and what you sent. Every piece of text goes
   through textContent; an announcement's body is the server's rendered Markdown. */
(function () {
  "use strict";

  const { el, api } = window.Erynne;
  const $ = function (id) { return document.getElementById(id); };

  function note(text, ok) {
    const n = $("compose-note");
    n.textContent = text;
    n.className = ok ? "ok-note" : "error";
    n.hidden = !text;
  }

  function when(iso) {
    const d = new Date(iso.replace(" UTC", "Z").replace(" ", "T"));
    return isNaN(d) ? iso : d.toLocaleString([], { dateStyle: "medium", timeStyle: "short" });
  }

  function linkTo(href, text) {
    const a = el("a", "small", text || href);
    a.href = href;
    return a;
  }

  // ------------------------------------------------------------ messages
  function card(m, box) {
    const li = el("li", "message" + (m.read || box === "sent" ? "" : " unread") + (m.kind === "system" ? " system" : ""));
    li.id = "m" + m.message_id;
    const head = el("div", "message-head small muted");
    head.append(el("strong", null, box === "sent" ? "To " + m.to : m.from), document.createTextNode(" · " + when(m.at)));
    if (!m.read && box !== "sent") head.appendChild(el("span", "badge unread", "new"));
    li.appendChild(head);
    li.appendChild(el("p", "message-text", m.text));
    if (m.link) li.appendChild(linkTo(m.link, "Open " + m.link));
    if (!m.read && box !== "sent") {
      li.addEventListener("click", async function () {
        try { await api("POST", "/api/messages/" + m.message_id + "/read"); } catch (e) { return; }
        li.classList.remove("unread");
        const b = head.querySelector(".badge");
        if (b) b.remove();
        if (window.ErynneNotify) window.ErynneNotify.refresh();
      }, { once: true });
    }
    return li;
  }

  function fill(id, items, box, empty) {
    const host = $(id);
    host.replaceChildren();
    if (!items.length) { host.appendChild(el("p", "muted small", empty)); return; }
    const ul = el("ul", "message-list");
    items.forEach(function (m) { ul.appendChild(card(m, box)); });
    host.appendChild(ul);
  }

  async function loadMessages() {
    try {
      const inbox = await api("GET", "/api/messages?box=inbox");
      fill("inbox", inbox.messages, "inbox", "Nothing yet.");
      const sent = await api("GET", "/api/messages?box=sent");
      fill("sent", sent.messages, "sent", "You haven't sent any notes.");
      const wanted = location.hash && document.getElementById(location.hash.slice(1));
      if (wanted) { wanted.classList.add("wanted"); wanted.scrollIntoView({ block: "center" }); }
    } catch (e) {
      $("inbox").replaceChildren(el("p", "error", e.message));
    }
  }

  // -------------------------------------------------------- announcements
  function announcement(a) {
    const div = el("div", "notice glass" + (a.seen ? " seen" : ""));
    const head = el("div", "notice-head");
    head.append(el("strong", null, a.title), el("span", "muted small", " · " + when(a.created_at)));
    div.appendChild(head);
    if (a.html) {
      const body = el("div", "notice-body");
      body.innerHTML = a.html;   // the server's rendered Markdown (raw HTML in it is escaped there)
      div.appendChild(body);
    }
    const foot = el("div", "notice-actions");
    if (a.link) foot.appendChild(linkTo(a.link, "Open"));
    if (a.must_ack) foot.appendChild(el("span", "small " + (a.acked ? "ok-note" : "error"), a.acked ? "Acknowledged" : "Needs your acknowledgement"));
    else foot.appendChild(el("span", "small muted", a.seen ? "Seen" : "New"));
    div.appendChild(foot);
    return div;
  }

  async function loadAnnouncements() {
    try {
      const data = await api("GET", "/api/announcements");
      const host = $("announcements");
      host.replaceChildren();
      if (!data.announcements.length) { host.appendChild(el("p", "muted small", "No announcements right now.")); return; }
      data.announcements.forEach(function (a) { host.appendChild(announcement(a)); });
    } catch (e) {
      $("announcements").replaceChildren(el("p", "error", e.message));
    }
  }

  // ---------------------------------------------------------------- compose
  async function loadPeople() {
    try {
      const data = await api("GET", "/api/people");
      const list = $("people");
      list.replaceChildren();
      data.people.forEach(function (name) { const o = el("option"); o.value = name; list.appendChild(o); });
    } catch (e) { /* the box still takes a typed name */ }
  }

  $("compose").addEventListener("submit", async function (e) {
    e.preventDefault();
    note("");
    const button = this.querySelector("button[type=submit]");
    button.disabled = true;
    try {
      await api("POST", "/api/messages", { to: $("note-to").value.trim(), text: $("note-text").value,
                                           link: $("note-link").value.trim() || null });
      $("note-text").value = "";
      $("note-link").value = "";
      note("Sent to " + $("note-to").value.trim() + ".", true);
      loadMessages();
    } catch (err) {
      note(err.message);
    } finally {
      button.disabled = false;
    }
  });

  $("read-all").addEventListener("click", async function () {
    try { await api("POST", "/api/messages/read-all"); } catch (e) { return; }
    loadMessages();
    if (window.ErynneNotify) window.ErynneNotify.refresh();
  });

  loadPeople();
  loadAnnouncements();
  loadMessages();
})();
