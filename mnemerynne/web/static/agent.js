/* Erynne's agent in chat: the steps she takes (tool calls) and the file
   changes she proposes, with Apply / Dismiss. Loaded before chat.js, which
   calls window.ErynneAgent. Everything is built as DOM text, never HTML. */
(function () {
  "use strict";

  function el(tag, cls, text) {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text !== undefined && text !== null) n.textContent = text;
    return n;
  }

  // One line per tool call, under a collapsible "Steps" heading.
  function tool(container, ev) {
    let details = container.querySelector("details.steps");
    if (!details) {
      details = el("details", "steps");
      details.appendChild(el("summary", "muted small", "Steps"));
      details.appendChild(el("ol", "step-list"));
      container.appendChild(details);
    }
    const list = details.querySelector(".step-list");
    let li = list.querySelector('[data-call="' + CSS.escape(ev.id) + '"]');
    if (!li) {
      li = el("li", "small");
      li.dataset.call = ev.id;
      list.appendChild(li);
    }
    li.className = "small step " + ev.state;
    li.textContent = ev.state === "running" ? (ev.label || ev.name) + "…" : (ev.summary || ev.name);
    details.querySelector("summary").textContent = "Steps (" + list.children.length + ")";
  }

  function diffView(text) {
    const pre = el("pre", "diff");
    text.split("\n").forEach(function (line) {
      const cls = line.startsWith("+") ? "add" : line.startsWith("-") ? "del" : line.startsWith("@@") ? "hunk" : "";
      pre.appendChild(el("span", cls, line + "\n"));
    });
    return pre;
  }

  const STATUS = {
    applied: "✓ Applied. The previous version is kept: Files → the file's info → Versions.",
    dismissed: "Dismissed; nothing was changed.",
    stale: "Not applied: the file changed since Erynne drafted this. Ask her to redo it.",
  };

  // A proposed edit or new file, with Apply / Dismiss while it is pending.
  function proposal(p) {
    const card = el("div", "proposal");
    fill(card, p);
    return card;
  }

  function fill(card, p) {
    card.replaceChildren();
    card.dataset.status = p.status;
    const head = el("div", "proposal-head");
    head.append(document.createTextNode(p.kind === "create" ? "📄 New file " : "📝 Change to "),
                el("strong", null, p.file), el("span", "muted small", " · " + p.where));
    card.appendChild(head);
    if (p.changes && p.changes.length && !(p.kind === "create" && p.diff)) {
      const ul = el("ul", "small proposal-changes");
      p.changes.slice(0, 30).forEach(function (c) { ul.appendChild(el("li", null, c)); });
      if (p.changes.length > 30) ul.appendChild(el("li", "muted", "… and " + (p.changes.length - 30) + " more"));
      card.appendChild(ul);
    }
    if (p.diff) card.appendChild(diffView(p.diff));
    const foot = el("div", "proposal-actions");
    if (p.status === "pending") {
      const apply = el("button", "small", "Apply");
      apply.type = "button";
      const dismiss = el("button", "link small", "Dismiss");
      dismiss.type = "button";
      const msg = el("span", "small");
      apply.addEventListener("click", function () { decide(card, p, "apply", [apply, dismiss], msg); });
      dismiss.addEventListener("click", function () { decide(card, p, "dismiss", [apply, dismiss], msg); });
      foot.append(apply, dismiss, msg);
    } else {
      foot.appendChild(el("span", "small " + (p.status === "stale" ? "error" : "muted"), STATUS[p.status] || p.status));
    }
    if (p.url && p.status !== "dismissed") {
      const a = el("a", "small", "Open folder");
      a.href = p.url; a.target = "_blank"; a.rel = "noopener";
      foot.appendChild(a);
    }
    card.appendChild(foot);
  }

  async function decide(card, p, action, buttons, msg) {
    buttons.forEach(function (b) { b.disabled = true; });
    msg.textContent = action === "apply" ? "Applying…" : "";
    msg.className = "small muted";
    try {
      const r = await fetch("/api/agent/proposals/" + p.id + "/" + action, { method: "POST" });
      const data = await r.json().catch(function () { return {}; });
      if (r.ok) { fill(card, data); return; }
      if (r.status === 409 && /changed since/.test(data.detail || "")) {
        fill(card, Object.assign({}, p, { status: "stale" }));
        return;
      }
      msg.textContent = data.detail || r.statusText;
      msg.className = "small error";
    } catch (e) {
      msg.textContent = "Couldn't reach Mnemerynne; try again.";
      msg.className = "small error";
    }
    buttons.forEach(function (b) { b.disabled = false; });
  }

  window.ErynneAgent = { tool: tool, proposal: proposal };
})();
