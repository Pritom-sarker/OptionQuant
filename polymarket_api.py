"""
Polymarket API interface — READ-ONLY, BTCUSD only.

Fetches the current BTC 5-minute Up/Down market and its price history.
No order book, no wallet, no private key, no order is ever placed.
"""
from __future__ import annotations
import json
import time
from datetime import datetime, timezone
from typing import Optional

import requests
import config

_SESSION = requests.Session()
_SESSION.headers.update({
    "Accept": "application/json",
    "User-Agent": "btcusd-polymarket-signal-viewer/1.0",
})


def _get(url: str, params: dict = None) -> dict | list | None:
    try:
        r = _SESSION.get(url, params=params, timeout=config.REQUEST_TIMEOUT)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print(f"[polymarket_api] GET {url} failed: {e}")
        return None


def _fetch_market_by_slug(slug: str) -> Optional[dict]:
    data = _get(f"{config.GAMMA_API}/markets", params={"slug": slug})
    if not data:
        return None
    m = data[0] if isinstance(data, list) else data
    return m or None


def _seconds_to_expiry(market: dict) -> float:
    end_raw = market.get("endDate") or ""
    if not end_raw:
        return -1.0
    try:
        end = datetime.fromisoformat(end_raw.replace("Z", "+00:00"))
        now = datetime.now(tz=timezone.utc)
        return (end - now).total_seconds()
    except Exception:
        return -1.0


def _get_token_ids(market: dict) -> tuple[str, str]:
    """Return (yes_token_id, no_token_id)."""
    raw_ids = market.get("clobTokenIds")
    if not raw_ids:
        return "", ""
    try:
        ids = json.loads(raw_ids) if isinstance(raw_ids, str) else list(raw_ids)
    except Exception:
        return "", ""
    if len(ids) < 2:
        return (str(ids[0]), "") if ids else ("", "")

    outcomes = market.get("outcomes") or []
    if isinstance(outcomes, str):
        try:
            outcomes = json.loads(outcomes)
        except Exception:
            outcomes = []

    yes_kw = {"yes", "up", "higher", "above", "true"}
    if len(outcomes) >= 2:
        if any(k in str(outcomes[0]).lower() for k in yes_kw):
            return str(ids[0]), str(ids[1])
        if any(k in str(outcomes[1]).lower() for k in yes_kw):
            return str(ids[1]), str(ids[0])

    return str(ids[0]), str(ids[1])


def _attach_market_meta(m: dict, slug: str, window_start_ts: int, tte: float) -> dict:
    yes_id, no_id = _get_token_ids(m)
    m["_tte"] = tte
    m["_yes_token_id"] = yes_id
    m["_no_token_id"] = no_id
    m["_slug"] = slug
    m["_window_start_ts"] = window_start_ts
    m["_market_url"] = f"{config.POLYMARKET_EVENT_URL_BASE}/{slug}"
    return m


def fetch_btcusd_market() -> Optional[dict]:
    """
    Return the currently active BTC 5-minute Up/Down market (the window
    closest to expiring right now), with token ids and time-to-expiry
    attached. Returns None if no active BTC 5-minute market is found.

    For general "what's currently active" queries only (e.g. Tab 2's
    observer). NEVER use this to pick which market a new trade candidate
    should trade — see fetch_market_for_window()'s docstring for why.

    IMPORTANT — slug semantics, verified directly against live Polymarket
    data: "btc-updown-5m-{ts}" encodes the window's START, not its end
    (confirmed via the market's own endDate field and question text, e.g.
    slug ...-1783389000 -> question "9:50PM-9:55PM ET" with
    endDate = 1783389000 + 300, not 1783389000 itself). A window is only
    truly expired once ts + 300 has passed — checking `ts <= now` instead
    (an earlier version of this function did) incorrectly rejects the
    genuinely-current, still-active window and forces selection of a later
    one, which is *worse* than the API-lag race it was meant to guard
    against. tte itself (from _seconds_to_expiry, using the market's own
    endDate) has always been correct, so the smallest-tte comparison below
    already naturally prefers the true current window on its own.
    """
    now = int(time.time())
    aligned = (now // 300) * 300
    best = None
    for i in range(config.WINDOWS_TO_CHECK):
        window_start_ts = aligned + i * 300
        if window_start_ts + 300 <= now:
            continue   # this window's real 5-minute duration has fully elapsed
        slug = f"{config.COIN}-updown-5m-{window_start_ts}"
        m = _fetch_market_by_slug(slug)
        if not m or not m.get("active") or m.get("closed"):
            continue
        tte = _seconds_to_expiry(m)
        if tte < config.MARKET_MIN_TTE_SEC or tte > config.MARKET_MAX_TTE_SEC:
            continue
        if best is None or tte < best["_tte"]:
            best = _attach_market_meta(m, slug, window_start_ts, tte)
    return best


def fetch_market_for_window(window_start_ts: int) -> Optional[dict]:
    """
    Fetches the EXACT market whose window starts at window_start_ts —
    always used when creating a trade candidate for a specific Tab 1
    signal (window_start_ts = that signal's own candle close time, which
    is exactly the next window's start).

    This must never be replaced with fetch_btcusd_market()'s "closest to
    expiry right now" heuristic for candidate creation: if there's any
    delay between a signal firing and its candidate actually being created
    (order-book fetch latency, a busy tick, etc.), "closest to expiry now"
    can have already rolled over to a *later* window than the one the
    signal is actually about — silently attaching the trade to the wrong
    contract and settling it against the wrong candle. Pinning directly to
    the signal's own timestamp makes that entire class of bug impossible.
    """
    slug = f"{config.COIN}-updown-5m-{window_start_ts}"
    m = _fetch_market_by_slug(slug)
    if not m or not m.get("active") or m.get("closed"):
        return None
    tte = _seconds_to_expiry(m)
    return _attach_market_meta(m, slug, window_start_ts, tte)


def fetch_btcusd_price_history(token_id: str, hours_back: float = None) -> list[dict]:
    """
    Fetch raw BTCUSD price ticks for the given token. Returns a list of
    {"t": unix_ts, "p": price} dicts, oldest first — timestamp/price points,
    not OHLC candles. candle_builder.py converts these into 5-minute candles.
    """
    if not token_id:
        return []
    hours_back = hours_back or config.LOOKBACK_HOURS
    now   = int(time.time())
    start = int(now - hours_back * 3600)
    data = _get(
        f"{config.CLOB_API}/prices-history",
        params={
            "market":   token_id,
            "startTs":  start,
            "endTs":    now,
            "fidelity": config.CANDLE_FIDELITY_MIN,
        },
    )
    if not data:
        return []
    return data.get("history", [])
