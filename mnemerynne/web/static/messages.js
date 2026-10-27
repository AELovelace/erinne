/* Messages, shared by every place they appear: the fullscreen panel the top-right button opens
   (installs without Rooms), the Inbox entry on the Rooms page, and the /messages page. One mount
   builds two columns: send on the left (a note to a colleague, and what you sent), receive on the
   right (announcements meant for you, then your inbox; clicking a note marks it read). Every piece
   of text goes through textContent; an announcement's body is the server's rendered Markdown. */
(function () {
  "use strict";

  let counter = 0;
  const mounts = [];

  function el(tag, cls, text) {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text !== undefined && text !== null) n.textContent = text;
    return n;
  }

  async function api(method, url, body) {
    const opts = { method: method, headers: { "Accept": "application/json" } };
    if (body !== undefined) {
      opts.headers["Content-Type"] = "application/json";
      opts.body = JSON.stringify(body);
    }
    const resp = await fetch(url, opts);
    let data = null;
    try { data = await resp.json(); } catch (e) { /* empty body */ }
    if (!resp.ok) {
      let detail = data && data.detail;
      if (Array.isArray(detail)) detail = detail.map(function (d) { return d.msg; }).join("; ");
      throw new Error(detail || ("Request failed (" + resp.status + ")"));
    }
    return data;
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

  function notifyBar() { if (window.ErynneNotify) window.ErynneNotify.refresh(); }

  /* Build the two columns inside host and load them. Returns {refresh, focus}. */
  function mount(host, options) {
    const opts = options || {};
    const uid = "msg" + (++counter);
    host.classList.add("msg-columns");
    host.replaceChildren();

    // ------------------------------------------------------------- send
    const left = el("section", "msg-col msg-send");
    left.setAttribute("aria-label", "Send");
    left.appendChild(el("h2", "msg-title", "Leave a note"));
    const form = el("form", "settings-form");
    form.autocomplete = "off";
    const toLabel = el("label", "small", "To");
    toLabel.htmlFor = uid + "-to";
    const to = el("input");
    to.type = "text"; to.id = uid + "-to"; to.placeholder = "username"; to.maxLength = 64; to.required = true;
    to.setAttribute("list", uid + "-people");
    const people = el("datalist");
    people.id = uid + "-people";
    const textLabel = el("label", "small", "Note");
    textLabel.htmlFor = uid + "-text";
    const text = el("textarea");
    text.id = uid + "-text"; text.rows = 3; text.required = true;
    if (opts.maxChars) text.maxLength = opts.maxChars;
    const linkLabel = el("label", "small", "Link (optional)");
    linkLabel.htmlFor = uid + "-link";
    const link = el("input");
    link.type = "text"; link.id = uid + "-link"; link.maxLength = 500;
    link.placeholder = "/files/12/view, /wiki/40, /proposals#p7";
    const sendWrap = el("div");
    const send = el("button", null, "Send");
    send.type = "submit";
    sendWrap.appendChild(send);
    form.append(toLabel, to, people, textLabel, text, linkLabel, link, el("span"), sendWrap);
    left.appendChild(form);
    const note = el("p");
    note.hidden = true;
    note.setAttribute("aria-live", "polite");
    left.appendChild(note);
    if (opts.hint) left.appendChild(el("p", "muted small", opts.hint));
    left.appendChild(el("h2", "msg-title", "Sent"));
    const sent = el("div");
    sent.appendChild(el("p", "muted small", "Loading…"));
    left.appendChild(sent);

    // ---------------------------------------------------------- receive
    const right = el("section", "msg-col msg-receive");
    right.setAttribute("aria-label", "Inbox");
    const bar = el("div", "msg-bar");
    bar.appendChild(el("h2", "msg-title", "Inbox"));
    const readAll = el("button", "link small", "Mark all read");
    readAll.type = "button";
    bar.appendChild(readAll);
    right.appendChild(bar);
    const announcements = el("div");
    announcements.setAttribute("aria-live", "polite");
    right.appendChild(announcements);
    const inbox = el("div");
    inbox.setAttribute("aria-live", "polite");
    inbox.appendChild(el("p", "muted small", "Loading…"));
    right.appendChild(inbox);

    host.append(left, right);

    function say(message, ok) {
      note.textContent = message;
      note.className = ok ? "ok-note" : "error";
      note.hidden = !message;
    }

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
          notifyBar();
        }, { once: true });
      }
      return li;
    }

    function fill(target, items, box, empty) {
      target.replaceChildren();
      if (!items.length) { target.appendChild(el("p", "muted small", empty)); return; }
      const ul = el("ul", "message-list");
      items.forEach(function (m) { ul.appendChild(card(m, box)); });
      target.appendChild(ul);
    }

    function announcement(a) {
      const div = el("div", "notice glass" + (a.seen ? " seen" : ""));
      const head = el("div", "notice-head");
      head.append(el("span", "notice-icon", "📣"), el("strong", null, a.title), el("span", "muted small", " · " + when(a.created_at)));
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

    async function loadMessages() {
      try {
        const got = await api("GET", "/api/messages?box=inbox");
        fill(inbox, got.messages, "inbox", "Nothing yet.");
        const mine = await api("GET", "/api/messages?box=sent");
        fill(sent, mine.messages, "sent", "You haven't sent any notes.");
        const wanted = /^#m\d+$/.test(location.hash) && host.querySelector(location.hash);
        if (wanted) { wanted.classList.add("wanted"); wanted.scrollIntoView({ block: "center" }); }
      } catch (e) {
        inbox.replaceChildren(el("p", "error", e.message));
      }
    }

    async function loadAnnouncements() {
      try {
        const data = await api("GET", "/api/announcements");
        announcements.replaceChildren();
        if (!data.announcements.length) return;
        announcements.appendChild(el("h3", "msg-subtitle", "Announcements"));
        data.announcements.forEach(function (a) { announcements.appendChild(announcement(a)); });
      } catch (e) {
        announcements.replaceChildren(el("p", "error", e.message));
      }
    }

    async function loadPeople() {
      try {
        const data = await api("GET", "/api/people");
        people.replaceChildren();
        data.people.forEach(function (name) { const o = el("option"); o.value = name; people.appendChild(o); });
      } catch (e) { /* the box still takes a typed name */ }
    }

    form.addEventListener("submit", async function (e) {
      e.preventDefault();
      say("");
      send.disabled = true;
      try {
        await api("POST", "/api/messages", { to: to.value.trim(), text: text.value, link: link.value.trim() || null });
        text.value = "";
        link.value = "";
        say("Sent to " + to.value.trim() + ".", true);
        loadMessages();
      } catch (err) {
        say(err.message);
      } finally {
        send.disabled = false;
      }
    });

    readAll.addEventListener("click", async function () {
      try { await api("POST", "/api/messages/read-all"); } catch (e) { return; }
      loadMessages();
      notifyBar();
    });

    function refresh() { loadAnnouncements(); loadMessages(); }

    loadPeople();
    refresh();
    const handle = { host: host, refresh: refresh, focus: function () { to.focus(); } };
    mounts.push(handle);
    return handle;
  }

  function refreshAll() { mounts.forEach(function (m) { if (document.contains(m.host)) m.refresh(); }); }

  /* The top-right button and the fullscreen panel it grows into (installs without Rooms; with
     Rooms the button is a link to the Inbox there). */
  function wire() {
    const button = document.getElementById("notify-button");
    const modal = document.getElementById("msg-modal");
    if (!button || !modal || button.tagName !== "BUTTON") return;
    let mounted = null;
    function open() {
      if (modal.open) return;
      modal.showModal();
      const host = modal.querySelector(".msg-host");
      if (!mounted) mounted = mount(host, { maxChars: Number(modal.dataset.max) || 0 });
      else mounted.refresh();
      if (!window.matchMedia("(pointer: coarse)").matches) mounted.focus();   // not the keyboard, on a phone
    }
    button.addEventListener("click", open);
    const close = modal.querySelector("#msg-close");
    if (close) close.addEventListener("click", function () { modal.close(); });
    modal.addEventListener("click", function (e) { if (e.target === modal) modal.close(); });   // the backdrop
    modal.addEventListener("close", notifyBar);
    if (location.hash === "#messages" || (/^#m\d+$/.test(location.hash) && location.pathname !== "/messages")) open();
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", wire);
  else wire();

  window.ErynneMessages = { mount: mount, refresh: refreshAll };
})();
