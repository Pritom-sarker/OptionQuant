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
import sys
import time
import traceback
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
        print("[btc_price_api] Binance returned an empty candle list.", file=sys.stderr)
    except requests.exceptions.ConnectionError as e:
        print(f"[btc_price_api] Binance connection error (network unreachable?): {e}", file=sys.stderr)
    except requests.exceptions.Timeout:
        print(
            f"[btc_price_api] Binance request timed out after {config.REQUEST_TIMEOUT}s.",
            file=sys.stderr,
        )
    except requests.exceptions.HTTPError as e:
        print(
            f"[btc_price_api] Binance HTTP error: {e.response.status_code} {e.response.reason} — {e.response.text[:200]}",
            file=sys.stderr,
        )
    except Exception as e:
        print(f"[btc_price_api] Binance failed with unexpected error: {type(e).__name__}: {e}", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)

    try:
        candles = _fetch_coinbase(limit)
        if candles:
            return candles
        print("[btc_price_api] Coinbase returned an empty candle list.", file=sys.stderr)
    except requests.exceptions.ConnectionError as e:
        print(f"[btc_price_api] Coinbase connection error (network unreachable?): {e}", file=sys.stderr)
    except requests.exceptions.Timeout:
        print(
            f"[btc_price_api] Coinbase request timed out after {config.REQUEST_TIMEOUT}s.",
            file=sys.stderr,
        )
    except requests.exceptions.HTTPError as e:
        print(
            f"[btc_price_api] Coinbase HTTP error: {e.response.status_code} {e.response.reason} — {e.response.text[:200]}",
            file=sys.stderr,
        )
    except Exception as e:
        print(f"[btc_price_api] Coinbase failed with unexpected error: {type(e).__name__}: {e}", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)

    print("[btc_price_api] Both Binance and Coinbase failed — returning empty list.", file=sys.stderr)
    return []
