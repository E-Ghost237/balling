/* Progressive presentation enhancements only. No API calls or prediction logic. */
(() => {
  const tabs = [...document.querySelectorAll("[data-market]")];
  function selectMarket(tab, focus = false) {
    tabs.forEach((item) => {
      const selected = item === tab;
      item.setAttribute("aria-selected", String(selected));
      item.tabIndex = selected ? 0 : -1;
      document.getElementById(item.getAttribute("aria-controls")).hidden =
        !selected;
    });
    if (focus) tab.focus();
  }
  tabs.forEach((tab, index) => {
    tab.addEventListener("click", () => selectMarket(tab));
    tab.addEventListener("keydown", (event) => {
      let next;
      if (event.key === "ArrowRight") next = (index + 1) % tabs.length;
      if (event.key === "ArrowLeft")
        next = (index - 1 + tabs.length) % tabs.length;
      if (event.key === "Home") next = 0;
      if (event.key === "End") next = tabs.length - 1;
      if (next !== undefined) {
        event.preventDefault();
        selectMarket(tabs[next], true);
      }
    });
  });

  const nav = document.getElementById("mobile-nav");
  const toggle = document.getElementById("mobile-nav-toggle");
  function closeNav(returnFocus = false) {
    if (!nav || nav.classList.contains("hidden")) return;
    nav.classList.add("hidden");
    toggle?.setAttribute("aria-expanded", "false");
    if (returnFocus) toggle?.focus();
  }
  nav
    ?.querySelectorAll("a")
    .forEach((link) => link.addEventListener("click", () => closeNav()));
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") closeNav(true);
  });
  document.addEventListener("click", (event) => {
    if (!event.target.closest(".site-header")) closeNav();
  });
  document.querySelectorAll(".desktop-nav a, #mobile-nav a").forEach((link) => {
    const target = new URL(link.href, location.origin);
    if (target.pathname === location.pathname && !target.hash)
      link.setAttribute("aria-current", "page");
  });
  // Bring a newly generated result into view, including after an HTMX swap.
  // This is scoped to the simulator; opening saved history never jumps the page.
  function revealPrediction() {
    const result = document.querySelector("#simulate-page .result-stack");
    if (!result) return;
    requestAnimationFrame(() => {
      result.focus({ preventScroll: true });
      result.scrollIntoView({ block: "start", behavior: "instant" });
    });
  }
  document.addEventListener("htmx:afterSettle", (event) => {
    if (event.target.id === "simulate-page") revealPrediction();
  });
  if (document.querySelector("#simulate-page .result-stack")) {
    window.addEventListener("load", revealPrediction, { once: true });
  }
})();
