"""
Candle data fetching + local CSV caching.

One CSV per (pair, timeframe) under data/{PAIR}_{TIMEFRAME}.csv. The cache
always holds the *largest* candle count ever requested for that pair/
timeframe: if you ask for more candles than are cached, only the missing
older history is paginated from Binance and merged in; if you ask for fewer,
the cached file is just sliced — no request cache is ever discarded.
"""
from __future__ import annotations
import os
import time

import pandas as pd
import requests

import config

_SESSION = requests.Session()
_SESSION.headers.update({
    "Accept": "application/json",
    "User-Agent": "pine-strategy-simulator/1.0",
})


def _csv_path(pair: str, timeframe: str) -> str:
    return os.path.join(config.DATA_DIR, f"{pair}_{timeframe}.csv")


def _fetch_binance_page(pair: str, timeframe: str, end_time_ms: int | None, limit: int) -> list[dict]:
    params = {"symbol": pair, "interval": timeframe, "limit": limit}
    if end_time_ms is not None:
        params["endTime"] = end_time_ms
    for attempt in range(3):
        r = _SESSION.get(config.BINANCE_KLINES_URL, params=params, timeout=config.REQUEST_TIMEOUT)
        if r.status_code == 429:
            time.sleep(1.0 + attempt)
            continue
        r.raise_for_status()
        break
    else:
        r.raise_for_status()

    rows = r.json()
    candles = []
    for row in rows:
        candles.append({
            "time": int(row[6]) // 1000,   # candle close time, unix seconds
            "open": float(row[1]), "high": float(row[2]),
            "low": float(row[3]), "close": float(row[4]), "volume": float(row[5]),
        })
    return candles


def _fetch_older_than(pair: str, timeframe: str, before_time_sec: int | None, need: int, log) -> list[dict]:
    """Paginates backwards in time, collecting candles strictly older than
    `before_time_sec` (or the newest available, if None), until `need` are
    collected or Binance history is exhausted."""
    now = time.time()
    collected: list[dict] = []
    end_time_ms = (before_time_sec * 1000 - 1) if before_time_sec is not None else None

    while len(collected) < need:
        page = _fetch_binance_page(pair, timeframe, end_time_ms, config.BINANCE_MAX_LIMIT)
        if not page:
            break
        page = [c for c in page if c["time"] <= now]
        collected = page + collected
        log(f"Loaded {len(collected):,} candles...")
        oldest_open_ms = page[0]["time"] * 1000
        end_time_ms = oldest_open_ms - 1
        if len(page) < config.BINANCE_MAX_LIMIT:
            break   # exhausted available history
        time.sleep(config.PAGINATION_SLEEP_SEC)

    return collected


def _dedupe_sorted(candles: list[dict]) -> list[dict]:
    candles = sorted(candles, key=lambda c: c["time"])
    seen = set()
    out = []
    for c in candles:
        if c["time"] not in seen:
            seen.add(c["time"])
            out.append(c)
    return out


def load_candles(pair: str, timeframe: str, count: int, force_refresh: bool = False,
                  log=lambda msg: None) -> tuple[pd.DataFrame, bool, int]:
    """
    Returns (df tail-sliced to `count` rows, from_cache_only, cache_rows_before_this_call).
    from_cache_only is True only if the on-disk cache already had >= count
    rows and nothing new needed to be fetched.
    """
    path = _csv_path(pair, timeframe)
    existing = pd.DataFrame()
    if not force_refresh and os.path.exists(path):
        existing = pd.read_csv(path)

    cached_rows = len(existing)
    if cached_rows >= count and not force_refresh:
        return existing.tail(count).reset_index(drop=True), True, cached_rows

    log("Fetching candles...")
    oldest_cached_time = int(existing["time"].iloc[0]) if not existing.empty else None
    still_needed = count - cached_rows if not force_refresh else count

    if force_refresh or existing.empty:
        fetched = _fetch_older_than(pair, timeframe, None, count, log)
        merged = _dedupe_sorted(fetched)
    else:
        older = _fetch_older_than(pair, timeframe, oldest_cached_time, still_needed, log)
        merged = _dedupe_sorted(older + existing.to_dict("records"))

    if not merged:
        return pd.DataFrame(), False, cached_rows

    df = pd.DataFrame(merged)
    os.makedirs(config.DATA_DIR, exist_ok=True)
    df.to_csv(path, index=False)
    return df.tail(count).reset_index(drop=True), False, cached_rows


def cached_datasets() -> list[tuple[str, str, int]]:
    """(pair, timeframe, cached_row_count) for every locally cached CSV."""
    out = []
    if not os.path.isdir(config.DATA_DIR):
        return out
    for fname in sorted(os.listdir(config.DATA_DIR)):
        if not fname.endswith(".csv"):
            continue
        stem = fname[:-4]
        if "_" not in stem:
            continue
        pair, timeframe = stem.rsplit("_", 1)
        try:
            n = sum(1 for _ in open(os.path.join(config.DATA_DIR, fname))) - 1
        except OSError:
            n = 0
        out.append((pair, timeframe, max(n, 0)))
    return out
