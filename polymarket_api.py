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


def fetch_btcusd_market() -> Optional[dict]:
    """
    Return the currently active BTC 5-minute Up/Down market (the window
    closest to expiring right now), with token ids and time-to-expiry
    attached. Returns None if no active BTC 5-minute market is found.
    """
    now = int(time.time())
    aligned = (now // 300) * 300
    best = None
    for i in range(config.WINDOWS_TO_CHECK):
        window_end_ts = aligned + i * 300
        if window_end_ts <= now:
            # This window's own price-determining period has already fully
            # elapsed. Never rely solely on the API's active/closed flags or
            # endDate-derived TTE to catch this — both can lag the real clock
            # by a few seconds right after a candle closes, which would let an
            # already-resolved window still look "active" with a small
            # positive TTE and win as "soonest to expire". window_end_ts
            # itself is unambiguous, so check it directly first.
            continue
        slug = f"{config.COIN}-updown-5m-{window_end_ts}"
        m = _fetch_market_by_slug(slug)
        if not m or not m.get("active") or m.get("closed"):
            continue
        tte = _seconds_to_expiry(m)
        if tte < config.MARKET_MIN_TTE_SEC or tte > config.MARKET_MAX_TTE_SEC:
            continue
        if best is None or tte < best["_tte"]:
            yes_id, no_id = _get_token_ids(m)
            m["_tte"] = tte
            m["_yes_token_id"] = yes_id
            m["_no_token_id"] = no_id
            m["_slug"] = slug
            m["_window_end_ts"] = window_end_ts
            m["_market_url"] = f"{config.POLYMARKET_EVENT_URL_BASE}/{slug}"
            best = m
    return best


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
