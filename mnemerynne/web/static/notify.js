/* On every signed-in page: the unread badge in the top bar and the announcement cards under it.
   Polls /api/notify on load, every minute and when the tab comes back. An announcement's body is
   the server's rendered Markdown (raw HTML escaped there); everything else is textContent. */
(function () {
  "use strict";

  const badge = document.getElementById("nav-unread");
  const host = document.getElementById("notices");
  if (!badge && !host) return;
  const shown = new Set();
  let timer = null;

  function el(tag, cls, text) {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text !== undefined && text !== null) n.textContent = text;
    return n;
  }

  function setBadge(n) {
    if (!badge) return;
    badge.textContent = n > 99 ? "99+" : String(n);
    badge.hidden = !n;
  }

  async function mark(a, card, button) {
    button.disabled = true;
    try {
      const r = await fetch("/api/announcements/" + a.id + "/mark", {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ ack: !!a.must_ack }) });
      if (!r.ok) throw new Error(r.statusText);
      card.remove();
      shown.delete(a.id);
      if (host && !host.childElementCount) host.hidden = true;
    } catch (e) {
      button.disabled = false;
    }
  }

  function card(a) {
    const div = el("div", "notice glass" + (a.must_ack ? " must-ack" : ""));
    div.dataset.id = String(a.id);
    const head = el("div", "notice-head");
    head.append(el("span", "notice-icon", "📣"), el("strong", null, a.title));
    div.appendChild(head);
    if (a.html) {
      const body = el("div", "notice-body");
      body.innerHTML = a.html;
      div.appendChild(body);
    }
    const foot = el("div", "notice-actions");
    if (a.link) {
      const open = el("a", "small", "Read more");
      open.href = a.link;
      foot.appendChild(open);
    }
    const ok = el("button", "small", a.must_ack ? "I acknowledge" : "Got it");
    ok.type = "button";
    ok.addEventListener("click", function () { mark(a, div, ok); });
    foot.appendChild(ok);
    div.appendChild(foot);
    return div;
  }

  async function refresh() {
    try {
      const r = await fetch("/api/notify", { headers: { "Accept": "application/json" } });
      if (r.status === 401) { clearInterval(timer); return; }   // signed out meanwhile
      if (!r.ok) return;
      const data = await r.json();
      setBadge(data.unread || 0);
      if (!host) return;
      const pending = data.announcements || [];
      const ids = new Set(pending.map(function (a) { return a.id; }));
      Array.from(host.children).forEach(function (c) { if (!ids.has(Number(c.dataset.id))) { c.remove(); shown.delete(Number(c.dataset.id)); } });
      pending.forEach(function (a) {
        if (shown.has(a.id)) return;
        shown.add(a.id);
        host.appendChild(card(a));
      });
      host.hidden = !host.childElementCount;
    } catch (e) { /* offline: try again next time */ }
  }

  window.ErynneNotify = { refresh: refresh };
  refresh();
  timer = setInterval(refresh, 60000);
  document.addEventListener("visibilitychange", function () { if (!document.hidden) refresh(); });
})();
