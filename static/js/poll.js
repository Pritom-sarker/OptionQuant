// Two independent, decoupled "live" mechanisms:
//
// 1. startPolling(url, targetId, intervalMs, tabKey) — fetches an HTML partial on
//    its own interval and swaps it into the target element. Each page calls
//    this once with its own URL/interval, completely independent of every
//    other page (navigating away tears down the interval entirely). This is
//    what makes values (numbers, status, tables, rows) update live without
//    a manual page reload — including a trade vanishing from Tab 3 the
//    moment it settles, and appearing in Tab 5 on its next poll.
//
//    tabKey (optional) ties this poll loop to a LIVE/DISCONNECTED status dot
//    and a "last update" timestamp already present in that tab's caption —
//    see live-dot-{tabKey}/live-text-{tabKey}/live-updated-{tabKey} in each
//    tabN.html. After 3 consecutive failed fetches the dot flips to
//    DISCONNECTED (still retrying every interval); one success flips it
//    straight back to LIVE.
//
// 2. startImageRefresh(intervalMs) — a single shared timer, independent of
//    any value-polling above, that just re-fetches every chart <img> on the
//    page (identified by a data-live attribute holding the real chart URL)
//    on a fixed cadence. Charts refresh on this same steady cadence on every
//    page regardless of how often that page's values happen to poll.

const DISCONNECT_AFTER_FAILURES = 3;

function _setLiveStatus(tabKey, connected) {
  if (!tabKey) return;
  const dot = document.getElementById(`live-dot-${tabKey}`);
  const text = document.getElementById(`live-text-${tabKey}`);
  if (dot) dot.classList.toggle("disconnected", !connected);
  if (text) text.textContent = connected ? "LIVE" : "DISCONNECTED";
}

function _setLastUpdated(tabKey) {
  if (!tabKey) return;
  const el = document.getElementById(`live-updated-${tabKey}`);
  if (el) el.textContent = "last update: " + new Date().toLocaleTimeString();
}

function startPolling(url, targetId, intervalMs, tabKey) {
  const target = document.getElementById(targetId);
  if (!target) return;

  let consecutiveFailures = 0;

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
        consecutiveFailures = 0;
        _setLiveStatus(tabKey, true);
        _setLastUpdated(tabKey);
      } else {
        throw new Error(`HTTP ${res.status}`);
      }
    } catch (e) {
      // Network hiccup or a backend error — never disturb the DOM (leave the
      // last-good render in place), just surface it and keep retrying.
      consecutiveFailures += 1;
      console.warn(`poll failed for ${targetId} (attempt ${consecutiveFailures}):`, e);
      if (consecutiveFailures >= DISCONNECT_AFTER_FAILURES) {
        _setLiveStatus(tabKey, false);
      }
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

// Any chart <img data-live="..."> that fails to load (e.g. a transient 500
// from the chart route) shows up in the console instead of failing silently.
document.addEventListener(
  "error",
  (e) => {
    if (e.target.tagName === "IMG" && e.target.hasAttribute("data-live")) {
      console.warn("chart image failed to load:", e.target.getAttribute("data-live"));
    }
  },
  true
);
