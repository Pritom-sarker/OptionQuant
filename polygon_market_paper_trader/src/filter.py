# src/filter.py — market filtering logic (Stage 1)
from __future__ import annotations

import json
import re

import config
from src.utils import minutes_remaining


# ── Crypto detection ───────────────────────────────────────────────────────────

def _is_crypto_event(event: dict) -> bool:
    for tag in event.get("tags") or []:
        slug  = (tag.get("slug")  or "").lower()
        label = (tag.get("label") or "").lower()
        if slug in config.CRYPTO_TAG_SLUGS:
            return True
        if any(kw in label for kw in config.CRYPTO_KEYWORDS):
            return True

    haystack = " ".join(filter(None, [
        event.get("title")       or "",
        event.get("subtitle")    or "",
        event.get("description") or "",
        event.get("category")    or "",
        event.get("slug")        or "",
    ])).lower()
    return any(kw in haystack for kw in config.CRYPTO_KEYWORDS)


# ── Expiry type detection ──────────────────────────────────────────────────────

def _detect_expiry_type(event: dict, market: dict) -> str | None:
    # Tags first — Polymarket tags include "5M", "15M", "1H"
    for tag in event.get("tags") or []:
        label = (tag.get("label") or "").strip().upper()
        slug  = (tag.get("slug")  or "").lower()
        if label == "1H" or "1h" in slug:
            return "1h"
        if label == "15M" or "15m" in slug:
            return "15m"
        if label == "5M" or ("5m" in slug and "15m" not in slug):
            return "5m"

    # Slug / title pattern
    combined = ((event.get("slug") or "") + " " + (event.get("title") or "")).lower()

    if re.search(r'[-_\b]15m[-_\b]|15[-_]min', combined):
        return "15m"
    if re.search(r'[-_\b]1h[-_\b]|1[-_]hour', combined):
        return "1h"
    if re.search(r'[-_\b]5m[-_\b]|5[-_]min', combined):
        return "5m"

    # Fall back to duration from start/end dates
    end_str   = event.get("endDate")   or market.get("endDate")   or ""
    start_str = event.get("startDate") or market.get("startDate") or ""
    if end_str and start_str:
        from src.utils import parse_dt
        end   = parse_dt(end_str)
        start = parse_dt(start_str)
        if end and start:
            diff = (end - start).total_seconds() / 60
            if 3 <= diff <= 7:
                return "5m"
            if 12 <= diff <= 18:
                return "15m"
            if 55 <= diff <= 65:
                return "1h"

    return None


# ── Token helpers ──────────────────────────────────────────────────────────────

def _parse_token_ids(market: dict) -> list[str]:
    raw = market.get("clobTokenIds")
    if not raw:
        return []
    if isinstance(raw, list):
        return [str(x) for x in raw if x]
    try:
        return [str(x) for x in json.loads(raw) if x]
    except Exception:
        return [str(raw)] if raw else []


def _parse_outcomes(market: dict) -> list[str]:
    raw = market.get("outcomes") or "[]"
    if isinstance(raw, list):
        return [str(x) for x in raw]
    try:
        return [str(x) for x in json.loads(raw)]
    except Exception:
        return []


# ── Main filter ────────────────────────────────────────────────────────────────

def filter_events(events: list[dict]) -> tuple[list[dict], list[dict]]:
    """
    Returns (accepted, rejected).
    Each accepted item: {event, market, token_id, outcome, expiry_type, remaining_minutes}
    Each rejected item: {event, market, reason}
    """
    accepted: list[dict] = []
    rejected: list[dict] = []

    for event in events:
        markets = event.get("markets") or []

        if not _is_crypto_event(event):
            for mkt in markets:
                rejected.append({"event": event, "market": mkt,
                                  "reason": "Not a crypto market"})
            if not markets:
                rejected.append({"event": event, "market": {},
                                  "reason": "Not a crypto market"})
            continue

        for mkt in markets:
            # Tradeable checks
            if not mkt.get("active", False):
                rejected.append({"event": event, "market": mkt,
                                  "reason": "Market not active"})
                continue
            if mkt.get("closed", True):
                rejected.append({"event": event, "market": mkt,
                                  "reason": "Market is closed"})
                continue
            if not mkt.get("acceptingOrders", False):
                rejected.append({"event": event, "market": mkt,
                                  "reason": "Not accepting orders"})
                continue
            if not mkt.get("enableOrderBook", False):
                rejected.append({"event": event, "market": mkt,
                                  "reason": "Order book disabled"})
                continue

            # Expiry type
            expiry_type = _detect_expiry_type(event, mkt)
            if expiry_type is None:
                rejected.append({"event": event, "market": mkt,
                                  "reason": "Expiry type not 5m / 15m / 1h"})
                continue

            # Time remaining
            end_str   = event.get("endDate") or mkt.get("endDate") or ""
            remaining = minutes_remaining(end_str)
            if remaining is None:
                rejected.append({"event": event, "market": mkt,
                                  "reason": "Cannot determine time remaining"})
                continue

            lo, hi = config.EXPIRY_WINDOWS[expiry_type]
            if remaining < lo:
                rejected.append({"event": event, "market": mkt,
                                  "reason": f"Too little time remaining ({remaining:.1f}m < {lo}m)"})
                continue
            if remaining > hi:
                rejected.append({"event": event, "market": mkt,
                                  "reason": f"Too much time remaining ({remaining:.1f}m > {hi}m)"})
                continue

            # Token IDs
            token_ids = _parse_token_ids(mkt)
            if not token_ids:
                rejected.append({"event": event, "market": mkt,
                                  "reason": "No CLOB token IDs available"})
                continue

            outcomes = _parse_outcomes(mkt)
            token_id = token_ids[0]
            outcome  = outcomes[0] if outcomes else "Up"

            accepted.append({
                "event":             event,
                "market":            mkt,
                "token_id":          token_id,
                "outcome":           outcome,
                "expiry_type":       expiry_type,
                "remaining_minutes": remaining,
                "end_time":          end_str,
            })

    return accepted, rejected
