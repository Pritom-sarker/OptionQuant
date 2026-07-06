// Two independent, decoupled "live" mechanisms:
//
// 1. startPolling(url, targetId, intervalMs) — fetches an HTML partial on
//    its own interval and swaps it into the target element. Each page calls
//    this once with its own URL/interval, completely independent of every
//    other page (navigating away tears down the interval entirely). This is
//    what makes values (numbers, status, tables, rows) update live without
//    a manual page reload — including a trade vanishing from Tab 3 the
//    moment it settles, and appearing in Tab 5 on its next poll.
//
// 2. startImageRefresh(intervalMs) — a single shared timer, independent of
//    any value-polling above, that just re-fetches every chart <img> on the
//    page (identified by a data-live attribute holding the real chart URL)
//    on a fixed cadence. Charts refresh on this same steady cadence on every
//    page regardless of how often that page's values happen to poll.

function startPolling(url, targetId, intervalMs) {
  const target = document.getElementById(targetId);
  if (!target) return;

  async function tick() {
    try {
      // Swapping innerHTML wholesale would collapse any <details data-id="...">
      // (e.g. Tab 5's "View Log" reports) the user currently has open — record
      // which ids are open first, then re-open the matching elements after.
      const openIds = Array.from(target.querySelectorAll("details[open][data-id]"))
        .map((d) => d.dataset.id);

      const res = await fetch(url, { cache: "no-store" });
      if (res.ok) {
        target.innerHTML = await res.text();
        openIds.forEach((id) => {
          const el = target.querySelector(`details[data-id="${id}"]`);
          if (el) el.open = true;
        });
      }
    } catch (e) {
      // Network hiccup — just try again next interval, no need to surface it.
      console.warn("poll failed", url, e);
    }
  }

  tick();
  setInterval(tick, intervalMs);
}

function startImageRefresh(intervalMs) {
  function bump() {
    document.querySelectorAll("img[data-live]").forEach((img) => {
      const base = img.getAttribute("data-live");
      img.src = base + (base.includes("?") ? "&" : "?") + "t=" + Date.now();
    });
  }
  bump();
  setInterval(bump, intervalMs);
}
