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

# Diagnostic only (see the n+1 pre-positioning gap investigation) — tracks
# which tier last served fetch_forming_btcusd_candle() so a silent, permanent
# fall-through to Coinbase (laggy, often has no genuinely-forming bucket —
# see that function's docstring) shows up in the logs instead of looking
# identical to Binance working fine.
_last_forming_source: Optional[str] = None


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


def _fetch_binance_klines(base_url: str, symbol: str, limit: int) -> list[dict]:
    # Over-fetch by 1: the most recent bar returned is often still forming
    # and gets dropped, so asking for exactly `limit` would leave us one short.
    r = _SESSION.get(
        base_url,
        params={"symbol": symbol, "interval": "5m", "limit": limit + 1},
        timeout=config.REQUEST_TIMEOUT,
    )
    r.raise_for_status()
    rows = r.json()
    # Each row: [openTime, open, high, low, close, volume, closeTime, ...]
    candles = []
    for row in rows:
        candles.append({
            # Binance's closeTime is always the NEXT candle's openTime minus 1ms
            # (e.g. 1784574899999, not 1784574900000) — floor-dividing that by
            # 1000 alone lands one full second BEFORE the true 300-aligned
            # boundary every single time (1784574899, not 1784574900). Polymarket's
            # own slugs use the clean boundary, so without the +1 here,
            # fetch_market_for_window(signal_time) was never able to find a
            # matching market: it doesn't exist at signal_time, only at
            # signal_time + 1. This was silent — the caller just logged
            # "no matching Polymarket market available yet" and retried forever.
            "time":   int(row[6]) // 1000 + 1,   # candle close time, unix seconds
            "open":   float(row[1]),
            "high":   float(row[2]),
            "low":    float(row[3]),
            "close":  float(row[4]),
            "volume": float(row[5]),
        })
    return _drop_forming_candle(candles, limit)


def _fetch_binance(limit: int) -> list[dict]:
    return _fetch_binance_klines(config.BINANCE_KLINES_URL, config.BINANCE_SYMBOL, limit)


def _fetch_binance_us(limit: int) -> list[dict]:
    return _fetch_binance_klines(config.BINANCE_US_KLINES_URL, config.BINANCE_US_SYMBOL, limit)


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
    currently in-progress candle is always excluded. Tries binance.com, then
    binance.us, then Coinbase — see fetch_forming_btcusd_candle's docstring
    for why binance.us specifically sits ahead of Coinbase (not blocked from
    US-based cloud egress the way binance.com is, unlike Coinbase whose own
    lag matters less here since this path only ever needs already-closed
    candles). Returns [] if all three fail — never fabricates data.
    """
    limit = limit or config.NUM_CANDLES_TARGET
    for fetch, name in ((_fetch_binance, "Binance"), (_fetch_binance_us, "Binance.US")):
        try:
            candles = fetch(limit)
            if candles:
                return candles
        except Exception as e:
            print(f"[btc_price_api] {name} failed: {e}")

    try:
        candles = _fetch_coinbase(limit)
        if candles:
            return candles
    except Exception as e:
        print(f"[btc_price_api] Coinbase fallback failed: {e}")

    return []


def _fetch_forming_binance() -> Optional[dict]:
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
    close_time = int(row[6]) // 1000 + 1   # see _fetch_binance's comment — closeTime is always boundary-1ms
    if close_time <= time.time():
        return None
    return {
        "time": close_time, "open": float(row[1]), "high": float(row[2]),
        "low": float(row[3]), "close": float(row[4]), "volume": float(row[5]),
    }


def _fetch_forming_binance_us() -> Optional[dict]:
    r = _SESSION.get(
        config.BINANCE_US_KLINES_URL,
        params={"symbol": config.BINANCE_US_SYMBOL, "interval": "5m", "limit": 1},
        timeout=config.REQUEST_TIMEOUT,
    )
    r.raise_for_status()
    rows = r.json()
    if not rows:
        return None
    row = rows[-1]
    close_time = int(row[6]) // 1000 + 1   # same closeTime-is-boundary-minus-1ms convention as binance.com
    if close_time <= time.time():
        return None
    return {
        "time": close_time, "open": float(row[1]), "high": float(row[2]),
        "low": float(row[3]), "close": float(row[4]), "volume": float(row[5]),
    }


def _fetch_forming_coinbase() -> Optional[dict]:
    r = _SESSION.get(
        config.COINBASE_CANDLES_URL,
        params={"granularity": config.CANDLE_TIMEFRAME_MIN * 60},
        timeout=config.REQUEST_TIMEOUT,
    )
    r.raise_for_status()
    rows = r.json()
    if not rows:
        return None
    t, low, high, open_, close, vol = rows[0]   # newest-first — row 0 is the most recent bucket
    close_time = int(t) + config.CANDLE_TIMEFRAME_MIN * 60
    if close_time <= time.time():
        return None
    return {
        "time": close_time, "open": float(open_), "high": float(high),
        "low": float(low), "close": float(close), "volume": float(vol),
    }


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

    Three tiers, in order: binance.com, then binance.us, then Coinbase.
    binance.com geo-blocks most US-based cloud/datacenter egress IPs
    (Railway's included) for regulatory reasons — that would otherwise make
    Early Entry silently do nothing forever (every tick hits the except
    branch and returns None) while the ordinary closed-candle path still
    works fine off the Coinbase fallback in fetch_btcusd_candles — exactly
    the "signals only ever fire at candle close, never ahead of time"
    symptom this was added to fix. binance.us is the separate US-compliant
    entity and isn't blocked the same way, so it's tried before Coinbase,
    whose own candle feed lags real time by tens of seconds and often
    doesn't expose a genuinely still-forming bucket at all.
    """
    global _last_forming_source
    for fetch, name in ((_fetch_forming_binance, "Binance"),
                        (_fetch_forming_binance_us, "Binance.US")):
        try:
            result = fetch()
            if result is not None:
                if _last_forming_source != name:
                    print(f"[btc_price_api] DIAG forming-candle source switched to {name} "
                          f"(was {_last_forming_source})")
                    _last_forming_source = name
                return result
        except Exception as e:
            print(f"[btc_price_api] {name} forming-candle fetch failed: {e}")

    try:
        result = _fetch_forming_coinbase()
        if result is not None and _last_forming_source != "Coinbase":
            print(f"[btc_price_api] DIAG forming-candle source switched to Coinbase "
                  f"(was {_last_forming_source})")
            _last_forming_source = "Coinbase"
        return result
    except Exception as e:
        print(f"[btc_price_api] Coinbase forming-candle fallback failed: {e}")
        return None
