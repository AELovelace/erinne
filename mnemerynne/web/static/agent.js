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
    saved: "✓ Saved to your Drafts. Nothing was sent.",
    dismissed: "Dismissed; nothing was changed.",
    stale: "Not applied: the file changed since Erynne drafted this. Ask her to redo it.",
    rejected: "✗ Turned down by an admin; nothing was changed.",
    expired: "Expired before anyone decided; nothing was changed.",
  };

  function dateLabel(iso) {
    if (!iso) return "";
    const d = new Date(iso.replace(" ", "T") + "Z");
    return isNaN(d) ? iso : d.toLocaleString([], { dateStyle: "medium", timeStyle: "short" });
  }

  // A proposed edit or new file, with Apply / Dismiss while it is pending. One that waits for an admin
  // (approver "admin") can only be withdrawn here; adminProposal is the admins' view, with Approve / Reject.
  function proposal(p) {
    const card = el("div", "proposal");
    fill(card, p, {});
    return card;
  }

  function adminProposal(p) {
    const card = el("div", "proposal");
    fill(card, p, { admin: true });
    return card;
  }

  function decisionText(p) {
    if (p.approver === "admin" && p.decided_by && (p.status === "rejected" || p.status === "applied")) {
      return (p.status === "rejected" ? " Turned down by " : " Approved by ") + p.decided_by +
             (p.decision_note ? ": “" + p.decision_note + "”" : ".");
    }
    if (p.status === "dismissed" && p.decision_note) return " Note: “" + p.decision_note + "”";
    return "";
  }

  function fill(card, p, opts) {
    card.replaceChildren();
    card.dataset.status = p.status;
    const head = el("div", "proposal-head");
    const what = { create: "📄 New file ", edit: "📝 Change to ", mail: "✉️ Email draft: ",
                   api: "⚙️ Change in ", delete: "🗑️ Delete ", access: "🔓 Let the AI edit ", batch: "⚙️ " }[p.kind] || "";
    head.append(document.createTextNode(what), el("strong", null, p.file), el("span", "muted small", " · " + p.where));
    if (opts.admin && p.requester) head.appendChild(el("span", "muted small", " · asked by " + p.requester));
    else if (p.requested_via && p.requested_via !== "Erynne") head.appendChild(el("span", "muted small", " · asked by " + p.requested_via));
    card.appendChild(head);
    // The same sentence Claude or Erynne gave the person, so what they were told matches what they see.
    if (p.summary) card.appendChild(el("p", "proposal-summary", p.summary));
    const ticks = [];
    if (p.kind === "batch" && p.items) {
      // One line per change; while pending (and theirs to decide) each has a tick box.
      const ul = el("ul", "small proposal-changes proposal-items");
      p.items.forEach(function (item, i) {
        const li = el("li");
        if (p.status === "pending" && !opts.admin && p.approver !== "admin") {
          const label = el("label", "check");
          const box = el("input");
          box.type = "checkbox"; box.checked = true; box.dataset.index = String(i);
          ticks.push(box);
          label.append(box, document.createTextNode(" " + item.summary));
          li.appendChild(label);
        } else {
          const mark = { applied: "✓ ", declined: "– ", failed: "✗ ", pending: "" }[item.status] || "";
          li.appendChild(el("span", item.status === "failed" ? "error" : null, mark + item.summary));
          if (item.status === "failed" && item.error) li.appendChild(el("span", "muted", " (" + item.error + ")"));
          if (item.result && item.status === "applied") li.appendChild(el("span", "muted", " · " + item.result));
        }
        ul.appendChild(li);
      });
      card.appendChild(ul);
    } else if (p.changes && p.changes.length && !(p.kind === "create" && p.diff)) {
      const ul = el("ul", "small proposal-changes");
      p.changes.slice(0, 30).forEach(function (c) { ul.appendChild(el("li", null, c)); });
      if (p.changes.length > 30) ul.appendChild(el("li", "muted", "… and " + (p.changes.length - 30) + " more"));
      card.appendChild(ul);
    }
    if (p.diff) card.appendChild(diffView(p.diff));
    if (p.kind === "mail") card.appendChild(el("pre", "diff mail-body", p.body || ""));
    if (p.kind === "api" && p.body) card.appendChild(el("p", "muted small", p.body));
    const foot = el("div", "proposal-actions");
    if (p.status === "pending" && opts.admin) {
      const note = el("input", "small");
      note.type = "text"; note.maxLength = 1000; note.placeholder = "Note for them (optional)";
      note.setAttribute("aria-label", "Note for " + (p.requester || "the requester"));
      const approve = el("button", "small", "Approve");
      approve.type = "button";
      const reject = el("button", "link small", "Reject");
      reject.type = "button";
      const msg = el("span", "small");
      approve.addEventListener("click", function () { decide(card, p, "approve", [approve, reject], msg, opts, note.value); });
      reject.addEventListener("click", function () { decide(card, p, "reject", [approve, reject], msg, opts, note.value); });
      foot.append(note, approve, reject, msg);
    } else if (p.status === "pending" && p.approver === "admin") {
      const withdraw = el("button", "link small", "Withdraw");
      withdraw.type = "button";
      const msg = el("span", "small");
      withdraw.addEventListener("click", function () { decide(card, p, "dismiss", [withdraw], msg, opts); });
      foot.append(el("span", "small muted", "⏳ Waiting for a Mnemerynne admin to approve it."), withdraw, msg);
    } else if (p.status === "pending") {
      const apply = el("button", "small", { mail: "Save to Drafts", api: "Run it", delete: "Move to recycle bin",
                                            access: "Allow", batch: "Run the ticked ones" }[p.kind] || "Apply");
      apply.type = "button";
      const dismiss = el("button", "link small", "Dismiss");
      dismiss.type = "button";
      const msg = el("span", "small");
      apply.addEventListener("click", function () {
        const chosen = ticks.length ? ticks.filter(function (b) { return b.checked; }).map(function (b) { return Number(b.dataset.index); }) : null;
        if (chosen && !chosen.length) { msg.textContent = "Tick at least one change, or dismiss."; msg.className = "small error"; return; }
        decide(card, p, "apply", [apply, dismiss], msg, opts, null, chosen);
      });
      dismiss.addEventListener("click", function () {
        const why = prompt("Dismiss this? Add a reason if you like (shown to whoever proposed it):", "");
        if (why === null) return;
        decide(card, p, "dismiss", [apply, dismiss], msg, opts, why);
      });
      foot.append(apply, dismiss, msg);
      if (p.expires_at) foot.appendChild(el("span", "small muted", "Expires " + dateLabel(p.expires_at)));
    } else {
      const counts = p.counts || {};
      const said = p.status === "applied" && p.kind === "mail" ? STATUS.saved
                 : p.status === "applied" && p.kind === "batch"
                   ? "✓ " + (counts.applied || 0) + " applied, " + (counts.declined || 0) + " declined, " + (counts.failed || 0) + " failed."
                 : p.status === "applied" && p.kind === "api" ? "✓ Done."
                 : p.status === "applied" && p.kind === "delete" ? "✓ In the recycle bin."
                 : p.status === "applied" && p.kind === "access" ? "✓ The AI can edit here now." : STATUS[p.status] || p.status;
      foot.appendChild(el("span", "small " + (p.status === "stale" || p.status === "rejected" ? "error" : "muted"),
                          said + decisionText(p)));
    }
    if (p.url && p.status !== "dismissed") {
      const a = el("a", "small", { mail: "Open in Outlook", api: "See what it answered" }[p.kind] || "Open folder");
      a.href = p.url; a.target = "_blank"; a.rel = "noopener";
      foot.appendChild(a);
    }
    card.appendChild(foot);
  }

  async function decide(card, p, action, buttons, msg, opts, note, items) {
    buttons.forEach(function (b) { b.disabled = true; });
    msg.textContent = action === "apply" || action === "approve"
      ? ({ mail: "Saving…", api: "Running…", delete: "Deleting…", batch: "Running…" }[p.kind] || "Applying…") : "";
    msg.className = "small muted";
    try {
      const payload = opts.admin ? { note: note || "" } : action === "dismiss" && note ? { note: note }
                    : action === "apply" && items ? { items: items } : null;
      const r = payload
        ? await fetch((opts.admin ? "/api/admin/proposals/" : "/api/agent/proposals/") + p.id + "/" + action, {
            method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) })
        : await fetch("/api/agent/proposals/" + p.id + "/" + action, { method: "POST" });
      const data = await r.json().catch(function () { return {}; });
      if (r.ok) { fill(card, Object.assign({ requester: p.requester }, data), opts); return; }
      if (r.status === 409 && /changed since/.test(data.detail || "")) {
        fill(card, Object.assign({}, p, { status: "stale" }), opts);
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

  window.ErynneAgent = { tool: tool, proposal: proposal, adminProposal: adminProposal };
})();
