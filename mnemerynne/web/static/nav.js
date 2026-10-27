/* The top bar on every page: measures how much of the screen the bar and the announcement cards
   take (--chrome, which the full-height pages subtract from the viewport, phone or not), scrolls
   the current tab into view when the tabs are a strip, runs the ☰ menu on phones, and closes the
   collapsible side lists (details.ax-side) on narrow screens. Nothing here uses innerHTML. */
(function () {
  "use strict";

  const root = document.documentElement;
  const bar = document.querySelector(".topbar");
  const notices = document.getElementById("notices");
  const narrow = window.matchMedia("(max-width: 760px)");

  // --chrome: the bar's height plus the notices block (0 when hidden), as the layout sees it.
  function measure() {
    let px = 0;
    if (bar) px += bar.getBoundingClientRect().height;
    if (notices && !notices.hidden) px += notices.getBoundingClientRect().height + 8;   // its top margin
    root.style.setProperty("--chrome", Math.ceil(px) + "px");
  }
  if (bar && "ResizeObserver" in window) {
    const watch = new ResizeObserver(measure);
    watch.observe(bar);
    if (notices) watch.observe(notices);
  }
  measure();
  window.addEventListener("load", measure);
  if (notices) new MutationObserver(measure).observe(notices, { attributes: true, attributeFilter: ["hidden"], childList: true });

  // The current tab in view when the tabs scroll sideways.
  const current = document.querySelector(".tabs a[aria-current='page']");
  if (current && narrow.matches && typeof current.scrollIntoView === "function") {
    current.scrollIntoView({ block: "nearest", inline: "center" });
  }

  // ☰: the username, Help and Sign out, in a menu on phones (inline on a desktop).
  const menuButton = document.getElementById("nav-menu");
  const more = document.getElementById("nav-more");
  if (menuButton && more) {
    function setOpen(open) {
      more.classList.toggle("open", open);
      menuButton.setAttribute("aria-expanded", String(open));
    }
    menuButton.addEventListener("click", function (e) {
      e.stopPropagation();
      setOpen(!more.classList.contains("open"));
    });
    document.addEventListener("click", function (e) {
      if (more.classList.contains("open") && !more.contains(e.target)) setOpen(false);
    });
    document.addEventListener("keydown", function (e) { if (e.key === "Escape") setOpen(false); });
    narrow.addEventListener("change", function () { setOpen(false); });
  }

  // Side lists (wiki pages, help contents, Access trees) start closed on a phone; a desktop shows them open.
  function foldSides() {
    document.querySelectorAll("details.ax-side").forEach(function (d) { d.open = !narrow.matches; });
  }
  foldSides();
  narrow.addEventListener("change", foldSides);
})();
