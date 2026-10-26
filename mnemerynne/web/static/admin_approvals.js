/* Access → Approvals: requests only an admin can decide (a whole library's AI edit asked for by someone
   who isn't an admin, and API actions marked "needs an admin's approval"). Waiting ones first, with
   Approve / Reject and a note for the requester; then the ones decided in the last week. The cards are
   agent.js's, built as DOM text. */
(function () {
  "use strict";

  const box = document.getElementById("approvals");

  function note(text, cls) {
    const p = document.createElement("p");
    p.className = cls || "muted";
    p.textContent = text;
    return p;
  }

  async function load() {
    try {
      const r = await fetch("/api/admin/proposals");
      if (!r.ok) throw new Error(r.statusText);
      const data = await r.json();
      box.replaceChildren();
      if (!data.requests.length) { box.appendChild(note("Nothing waiting, and nothing decided this week.")); return; }
      const waiting = data.requests.filter(function (p) { return p.status === "pending"; }).length;
      box.appendChild(note(waiting ? waiting + " waiting for an admin." : "Nothing waiting."));
      data.requests.forEach(function (p) {
        const card = window.ErynneAgent.adminProposal(p);
        card.id = "p" + p.id;
        box.appendChild(card);
      });
    } catch (e) {
      box.replaceChildren(note("Couldn't load the requests; reload the page.", "error"));
    }
  }

  window.ErynneApprovals = { load: load };
})();
