"""
Converts raw Polymarket BTCUSD price ticks into 5-minute OHLC candles.
"""
from __future__ import annotations
import time

import config


def build_candles(raw_ticks: list[dict], timeframe_min: int = None) -> list[dict]:
    """
    Bucket raw {"t": unix_ts, "p": price} ticks into 5-minute OHLC candles.
    Only fully CLOSED candles are returned — a bucket whose close time is
    still in the future (the currently-forming candle) is dropped, since a
    signal must only ever be evaluated after its candle has closed.
    Returns a list of dicts (oldest first): {time, open, high, low, close}.
    """
    timeframe_min = timeframe_min or config.CANDLE_TIMEFRAME_MIN
    bucket_sec = timeframe_min * 60
    if not raw_ticks:
        return []

    now = int(time.time())
    buckets: dict[int, list[float]] = {}
    for tick in raw_ticks:
        try:
            t = int(tick["t"])
            p = float(tick["p"])
        except (KeyError, TypeError, ValueError):
            continue
        bucket_start = (t // bucket_sec) * bucket_sec
        buckets.setdefault(bucket_start, []).append(p)

    candles = []
    for bucket_start in sorted(buckets.keys()):
        close_time = bucket_start + bucket_sec
        if close_time > now:
            continue   # candle hasn't closed yet — exclude it
        prices = buckets[bucket_start]
        candles.append({
            "time":  close_time,
            "open":  prices[0],
            "high":  max(prices),
            "low":   min(prices),
            "close": prices[-1],
        })

    if len(candles) > config.NUM_CANDLES_TARGET:
        candles = candles[-config.NUM_CANDLES_TARGET:]
    return candles
