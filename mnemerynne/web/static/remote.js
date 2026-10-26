/* The Remote tab: a window (5% margin) that opens on a home page of resources, loads one in an
   iframe at /remote/<slug>/, and a bar on the right that drops down the list for switching.
   The resource list comes from the page (server-rendered); nothing here uses innerHTML. */
(function () {
  "use strict";

  const $ = function (id) { return document.getElementById(id); };
  const main = $("remote");
  if (!main) return;
  const apps = JSON.parse($("remote-apps").textContent || "[]");
  const bySlug = {};
  apps.forEach(function (a) { bySlug[a.slug] = a; });
  const frame = $("remote-frame"), home = $("remote-home-page"), bar = $("remote-bar"), handle = $("remote-handle"),
        list = $("remote-list"), name = $("remote-name"), path = $("remote-path"), reload = $("remote-reload"),
        open = $("remote-open"), full = $("remote-full"), windowEl = $("remote-window");
  let current = null;

  function mark() {
    list.querySelectorAll(".remote-item").forEach(function (b) {
      b.setAttribute("aria-current", (b.dataset.open || "") === (current || "") ? "page" : "false");
    });
  }

  function showHome(push) {
    current = null;
    frame.hidden = true;
    frame.src = "about:blank";
    home.hidden = false;
    name.textContent = "Home";
    path.textContent = " · resources";
    reload.hidden = true;
    open.hidden = true;
    document.title = "Remote · " + document.title.split(" · ").pop();
    if (push) history.pushState(null, "", "/remote");
    mark();
  }

  function show(slug, push) {
    const a = bySlug[slug];
    if (!a) { showHome(push); return; }
    if (!a.embed) { window.open(a.url, "_blank", "noopener"); return; }
    current = slug;
    home.hidden = true;
    frame.hidden = false;
    frame.src = a.url;
    name.textContent = a.name;
    path.textContent = " · " + a.url;
    reload.hidden = false;
    open.hidden = false;
    open.href = a.url;
    document.title = a.name + " · Remote";
    if (push) history.pushState(null, "", "/remote#" + slug);
    mark();
  }

  function collapse() {
    list.hidden = true;
    handle.setAttribute("aria-expanded", "false");
    bar.classList.remove("open");
  }

  function toggle() {
    const opening = list.hidden;
    list.hidden = !opening;
    handle.setAttribute("aria-expanded", String(opening));
    bar.classList.toggle("open", opening);
    try { localStorage.setItem("erynne.remote.bar", opening ? "open" : "closed"); } catch (e) { /* fine */ }
  }

  main.addEventListener("click", function (e) {
    const b = e.target.closest("[data-open]");
    if (!b) return;
    show(b.dataset.open, true);
    if (bar.contains(b)) collapse();
  });
  handle.addEventListener("click", toggle);
  $("remote-home").addEventListener("click", function () { showHome(true); });
  reload.addEventListener("click", function () { if (current) frame.src = bySlug[current].url; });
  full.addEventListener("click", function () {
    const on = windowEl.classList.toggle("full");
    full.setAttribute("aria-pressed", String(on));
  });
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape") collapse();
    else if (e.key === "r" && !e.ctrlKey && !e.metaKey && !e.altKey && document.activeElement === document.body) toggle();
  });
  window.addEventListener("popstate", function () {
    const slug = location.hash.slice(1);
    if (slug && bySlug[slug]) show(slug, false); else showHome(false);
  });

  try { if (localStorage.getItem("erynne.remote.bar") === "open") toggle(); } catch (e) { /* fine */ }
  const wanted = location.hash.slice(1);
  if (wanted && bySlug[wanted] && bySlug[wanted].embed) show(wanted, false); else showHome(false);
})();
