/* Inbox: list files per collection, upload, delete, retry, live status.
   All user-controlled text goes through textContent, never innerHTML. */
(function () {
  "use strict";

  const STATUS_LABEL = { queued: "Queued", processing: "Processing", ready: "Ready", failed: "Failed" };
  const sections = new Map();
  document.querySelectorAll(".collection").forEach(function (el) {
    sections.set(Number(el.dataset.collectionId), el);
  });
  let pollTimer = null;

  function el(tag, cls, text) {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined && text !== null) node.textContent = text;
    return node;
  }

  function sizeLabel(bytes) {
    if (bytes >= 1048576) return (bytes / 1048576).toFixed(1) + " MB";
    return Math.max(1, Math.round(bytes / 1024)) + " KB";
  }

  function renderFile(f) {
    const li = el("li", "file status-" + f.status);
    const name = el("a", "file-name", f.name);
    name.href = f.view_url;
    name.target = "_blank";
    name.rel = "noopener";
    li.appendChild(name);

    const meta = [f.kind.toUpperCase(), sizeLabel(f.size)];
    if (f.pages) meta.push(f.pages + " pages");
    li.appendChild(el("span", "file-meta muted", meta.join(" · ")));
    li.appendChild(el("span", "badge " + f.status, STATUS_LABEL[f.status] || f.status));

    const detail = f.error || f.progress;
    if (detail) li.appendChild(el("div", "file-detail" + (f.error ? " error" : " muted"), detail));
    if (f.notes) li.appendChild(el("div", "file-detail note", f.notes));

    if (f.can_delete) {
      const actions = el("span", "file-actions");
      if (f.status === "failed") {
        const retry = el("button", "link", "Retry");
        retry.type = "button";
        retry.addEventListener("click", function () { act("POST", "/api/files/" + f.id + "/retry"); });
        actions.appendChild(retry);
      }
      const del = el("button", "link danger", "Delete");
      del.type = "button";
      del.addEventListener("click", function () {
        if (confirm("Delete " + f.name + "? This can't be undone.")) act("DELETE", "/api/files/" + f.id);
      });
      actions.appendChild(del);
      li.appendChild(actions);
    }
    return li;
  }

  async function refresh() {
    let data;
    try {
      const resp = await fetch("/api/files");
      if (resp.status === 401) { window.location.href = "/login"; return; }
      data = await resp.json();
    } catch (err) {
      schedule(5000);
      return;
    }
    const byCollection = new Map();
    data.files.forEach(function (f) {
      if (!byCollection.has(f.collection_id)) byCollection.set(f.collection_id, []);
      byCollection.get(f.collection_id).push(f);
    });
    sections.forEach(function (section, id) {
      const list = section.querySelector(".files");
      const files = byCollection.get(id) || [];
      list.replaceChildren.apply(list, files.map(renderFile));
      section.querySelector(".empty").hidden = files.length > 0;
    });
    const busy = data.files.some(function (f) { return f.status === "queued" || f.status === "processing"; });
    schedule(busy ? 2000 : null);
  }

  function schedule(ms) {
    clearTimeout(pollTimer);
    if (ms) pollTimer = setTimeout(refresh, ms);
  }

  async function act(method, url) {
    const resp = await fetch(url, { method: method });
    if (!resp.ok) {
      const body = await resp.json().catch(function () { return {}; });
      alert(body.detail || resp.statusText);
    }
    refresh();
  }

  async function upload(section, fileList) {
    const list = section.querySelector(".files");
    for (const file of fileList) {
      const pending = el("li", "file status-uploading");
      pending.appendChild(el("span", "file-name", file.name));
      pending.appendChild(el("span", "badge processing", "Uploading"));
      list.prepend(pending);
      section.querySelector(".empty").hidden = true;

      const form = new FormData();
      form.append("file", file);
      form.append("collection_id", section.dataset.collectionId);
      try {
        const resp = await fetch("/api/files", { method: "POST", body: form });
        if (!resp.ok) {
          const body = await resp.json().catch(function () { return {}; });
          pending.replaceChildren(
            el("span", "file-name", file.name),
            el("span", "badge failed", "Rejected"),
            el("div", "file-detail error", body.detail || resp.statusText));
          continue;
        }
      } catch (err) {
        pending.appendChild(el("div", "file-detail error", "Upload failed: " + err.message));
        continue;
      }
      pending.remove();
    }
    refresh();
  }

  sections.forEach(function (section) {
    const input = section.querySelector("input[type=file]");
    if (!input) return;
    input.addEventListener("change", function () {
      upload(section, Array.from(input.files));
      input.value = "";
    });
    const zone = section.querySelector(".dropzone");
    section.addEventListener("dragover", function (e) { e.preventDefault(); zone.hidden = false; });
    section.addEventListener("dragleave", function (e) {
      if (!section.contains(e.relatedTarget)) zone.hidden = true;
    });
    section.addEventListener("drop", function (e) {
      e.preventDefault();
      zone.hidden = true;
      upload(section, Array.from(e.dataTransfer.files));
    });
  });

  refresh();
})();
