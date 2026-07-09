"""
Real BTC/USD 5-minute OHLCV candles — READ-ONLY, no auth, no orders.

This is the actual BTC/USD spot price (e.g. ~108000), fetched from a real
crypto exchange. It is deliberately NOT sourced from Polymarket: Polymarket's
"5-minute Up/Down" markets only expose their YES/NO *prediction-contract*
price (a probability between 0 and 1), which is not BTC/USD OHLC data.

Binance is tried first (no auth, generous rate limits); Coinbase is a
fallback if Binance is unreachable.
"""
from __future__ import annotations
import time
from typing import Optional

import requests
import config

_SESSION = requests.Session()
_SESSION.headers.update({
    "Accept": "application/json",
    "User-Agent": "btcusd-polymarket-signal-viewer/1.0",
})


def _drop_forming_candle(candles: list[dict], limit: int) -> list[dict]:
    """
    Both Binance's and Coinbase's candle endpoints include the currently
    in-progress bucket as the last entry — its close time is in the future
    and its OHLC values are still changing tick by tick. That candle must
    never be treated as closed: it would let a signal fire before its own
    candle finishes, and let WIN/LOSS be evaluated against a still-moving
    "next candle" close. Drop anything not yet closed, then trim to `limit`.
    """
    now = time.time()
    closed = [c for c in candles if c["time"] <= now]
    return closed[-limit:] if len(closed) > limit else closed


def _fetch_binance(limit: int) -> list[dict]:
    # Over-fetch by 1: the most recent bar returned is often still forming
    # and gets dropped, so asking for exactly `limit` would leave us one short.
    r = _SESSION.get(
        config.BINANCE_KLINES_URL,
        params={"symbol": config.BINANCE_SYMBOL, "interval": "5m", "limit": limit + 1},
        timeout=config.REQUEST_TIMEOUT,
    )
    r.raise_for_status()
    rows = r.json()
    # Each row: [openTime, open, high, low, close, volume, closeTime, ...]
    candles = []
    for row in rows:
        candles.append({
            "time":   int(row[6]) // 1000,   # candle close time, unix seconds
            "open":   float(row[1]),
            "high":   float(row[2]),
            "low":    float(row[3]),
            "close":  float(row[4]),
            "volume": float(row[5]),
        })
    return _drop_forming_candle(candles, limit)


def _fetch_coinbase(limit: int) -> list[dict]:
    r = _SESSION.get(
        config.COINBASE_CANDLES_URL,
        params={"granularity": config.CANDLE_TIMEFRAME_MIN * 60},
        timeout=config.REQUEST_TIMEOUT,
    )
    r.raise_for_status()
    rows = r.json()
    # Each row: [time, low, high, open, close, volume] — newest first.
    candles = []
    for row in rows[: limit + 1]:
        t, low, high, open_, close, vol = row
        candles.append({
            "time":   int(t) + config.CANDLE_TIMEFRAME_MIN * 60,   # normalize to close time
            "open":   float(open_),
            "high":   float(high),
            "low":    float(low),
            "close":  float(close),
            "volume": float(vol),
        })
    candles.sort(key=lambda c: c["time"])
    return _drop_forming_candle(candles, limit)


def fetch_btcusd_candles(limit: Optional[int] = None) -> list[dict]:
    """
    Return the last `limit` real, fully-CLOSED BTC/USD 5-minute OHLCV
    candles, oldest first: {time, open, high, low, close, volume}. The
    currently in-progress candle is always excluded. Tries Binance first,
    falls back to Coinbase if Binance is unreachable. Returns [] if both fail
    — never fabricates data.
    """
    limit = limit or config.NUM_CANDLES_TARGET
    try:
        candles = _fetch_binance(limit)
        if candles:
            return candles
    except Exception as e:
        print(f"[btc_price_api] Binance failed: {e}")

    try:
        candles = _fetch_coinbase(limit)
        if candles:
            return candles
    except Exception as e:
        print(f"[btc_price_api] Coinbase fallback failed: {e}")

    return []


def fetch_forming_btcusd_candle() -> Optional[dict]:
    """
    Return the currently in-progress (not yet closed) BTC/USD 5-minute
    candle's live, still-moving OHLC — the exact bucket fetch_btcusd_candles
    always drops (see _drop_forming_candle's docstring for why that guard
    exists for the normal, closed-candle-only pipeline).

    Used only by the opt-in Early Entry feature: it deliberately trades away
    some certainty (the final seconds before close can still move price) for
    the ability to detect a matching pattern before the candle finishes,
    instead of only after. Returns None if the last bucket has already
    closed by the time this is read (race right at the boundary) or on any
    fetch failure — callers should just skip that tick, never fabricate data.
    """
    try:
        r = _SESSION.get(
            config.BINANCE_KLINES_URL,
            params={"symbol": config.BINANCE_SYMBOL, "interval": "5m", "limit": 1},
            timeout=config.REQUEST_TIMEOUT,
        )
        r.raise_for_status()
        rows = r.json()
        if not rows:
            return None
        row = rows[-1]
        close_time = int(row[6]) // 1000
        if close_time <= time.time():
            return None
        return {
            "time": close_time, "open": float(row[1]), "high": float(row[2]),
            "low": float(row[3]), "close": float(row[4]), "volume": float(row[5]),
        }
    except Exception as e:
        print(f"[btc_price_api] forming-candle fetch failed: {e}")
        return None
