"""
Order book pressure, spread, liquidity, and the entry decision tree for Tab 2.
No orders are ever sent from this module — it only returns a decision string.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Optional

import config


@dataclass
class OBMetrics:
    selected_side: str             # "YES" or "NO"
    selected_price: float          # best ask of the selected side (what we'd pay)
    yes_price: float                # best ask of YES (for display)
    no_price: float                  # best ask of NO (for display)
    best_bid: float                 # selected side's best bid
    best_ask: float                 # selected side's best ask
    spread: float
    liquidity_usd: float
    pressure: float                 # weighted bid/ask depth imbalance, -1..1
    weighted_bid_depth: float
    weighted_ask_depth: float


def compute_pressure(order_book: dict) -> tuple[float, float, float]:
    """
    Weighted top-5 bid/ask depth imbalance for one side's order book.
    pressure = (weighted_bid - weighted_ask) / (weighted_bid + weighted_ask)
    """
    weights = config.OB_WEIGHTS
    bids = order_book.get("bids", [])[: config.OB_LEVELS]
    asks = order_book.get("asks", [])[: config.OB_LEVELS]

    w_bid = sum(lv["size"] * weights[i] for i, lv in enumerate(bids) if i < len(weights))
    w_ask = sum(lv["size"] * weights[i] for i, lv in enumerate(asks) if i < len(weights))

    total = w_bid + w_ask
    pressure = (w_bid - w_ask) / total if total > 0 else 0.0
    return pressure, w_bid, w_ask


def compute_metrics(selected_side: str, selected_book: dict, other_book: dict) -> OBMetrics:
    """
    `selected_book` is the order book of the contract being watched (YES if
    the Tab 1 prediction is GREEN, NO if RED). `other_book` is the opposite
    side's book, fetched only so yes_price/no_price can both be displayed.
    """
    bids = selected_book.get("bids", [])
    asks = selected_book.get("asks", [])
    best_bid = bids[0]["price"] if bids else 0.0
    best_ask = asks[0]["price"] if asks else 1.0
    spread = best_ask - best_bid

    liquidity = sum(lv["price"] * lv["size"] for lv in bids[:config.OB_LEVELS])
    liquidity += sum(lv["price"] * lv["size"] for lv in asks[:config.OB_LEVELS])

    pressure, w_bid, w_ask = compute_pressure(selected_book)

    other_asks = other_book.get("asks", [])
    other_ask_price = other_asks[0]["price"] if other_asks else None

    yes_price = best_ask if selected_side == "YES" else other_ask_price
    no_price = best_ask if selected_side == "NO" else other_ask_price

    return OBMetrics(
        selected_side=selected_side,
        selected_price=best_ask,   # buying = pay the ask
        yes_price=yes_price if yes_price is not None else 0.0,
        no_price=no_price if no_price is not None else 0.0,
        best_bid=best_bid,
        best_ask=best_ask,
        spread=spread,
        liquidity_usd=liquidity,
        pressure=pressure,
        weighted_bid_depth=w_bid,
        weighted_ask_depth=w_ask,
    )


def profit_factor(entry_price: float) -> float:
    """(1 - price) / price — payout multiple if the contract resolves to 1."""
    if entry_price <= 0:
        return 0.0
    return (1.0 - entry_price) / entry_price


def pressure_trend(current_pressure: float, prev_pressure: Optional[float]) -> str:
    """Increasing / Decreasing / Flat, with a small epsilon to avoid noise flapping."""
    if prev_pressure is None:
        return "Flat"
    delta = current_pressure - prev_pressure
    if delta > config.PRESSURE_TREND_EPSILON:
        return "Increasing"
    if delta < -config.PRESSURE_TREND_EPSILON:
        return "Decreasing"
    return "Flat"


def check_entry(
    metrics: OBMetrics,
    candidate_valid: bool,
    candidate_expired: bool,
    prev_selected_price: Optional[float],
    local_low: Optional[float],
    prev_pressure: Optional[float],
    tte_seconds: float,
) -> tuple[str, str]:
    """
    Full entry decision tree. Returns (decision, reason) where decision is
    one of "ENTER", "WAIT", "SKIP".

    ENTER requires ALL of:
      - candidate still valid and not expired
      - enough time left before market expiry
      - selected price <= MAX_ENTRY_PRICE
      - expected profit factor >= MIN_PROFIT_FACTOR
      - spread and liquidity acceptable
      - price made a local low after the signal, and is now recovering above it
      - price ticking up vs the previous snapshot
      - order book pressure on the selected side is positive AND improving
    """
    if candidate_expired:
        return "SKIP", "Candidate expired."
    if not candidate_valid:
        return "SKIP", "Candidate was invalidated."

    if tte_seconds < 0:
        return "SKIP", "Market has expired — no time to settle safely."

    price = metrics.selected_price

    if price > config.MAX_ENTRY_PRICE:
        return "WAIT", "Waiting for cheaper contract."

    pf = profit_factor(price)
    if pf < config.MIN_PROFIT_FACTOR:
        return "WAIT", f"Expected profit {pf:.2f}x is below the required {config.MIN_PROFIT_FACTOR:.2f}x."

    if metrics.spread > config.MAX_SPREAD:
        return "WAIT", f"Spread {metrics.spread:.3f} too wide (> {config.MAX_SPREAD:.2f})."

    if metrics.liquidity_usd < config.MIN_LIQUIDITY_USD:
        return "WAIT", f"Liquidity ${metrics.liquidity_usd:.2f} below minimum ${config.MIN_LIQUIDITY_USD:.2f}."

    if local_low is None:
        return "WAIT", "Waiting for a local low to form since the signal."

    if price <= local_low:
        return "WAIT", "Contract still falling."

    if prev_selected_price is not None and price <= prev_selected_price:
        return "WAIT", "Waiting for price to tick up from its low."

    if metrics.pressure <= config.MIN_OB_PRESSURE:
        return "WAIT", "Pressure not improving."

    if prev_pressure is not None and metrics.pressure <= prev_pressure:
        return "WAIT", "Pressure not improving."

    reason = (
        f"Recovery confirmed. Pressure increasing. Expected profit above {config.MIN_PROFIT_FACTOR:.0f}x. "
        f"({metrics.selected_side} recovered from low {local_low:.3f} to {price:.3f}; "
        f"pressure {metrics.pressure:.3f}; spread {metrics.spread:.3f}; liquidity ${metrics.liquidity_usd:.2f}; "
        f"profit factor {pf:.2f}x.)"
    )
    return "ENTER", reason


# ─────────────────────────────────────────────────────────────────────────────
# Always-on dual-sided observation (Tab 2's independent order-book panel).
# Unlike OBMetrics/check_entry above (single "selected" side, time-boxed
# candidate), this always computes metrics for BOTH YES and NO — Tab 2 must
# keep observing regardless of whether Tab 1 has a GREEN/RED signal yet.
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class SideMetrics:
    price: float                # best ask — what buying this side would pay
    best_bid: float
    best_ask: float
    mid: float
    spread: float
    top5_bid_depth: float       # unweighted sum of top-5 bid sizes
    top5_ask_depth: float
    weighted_bid_depth: float
    weighted_ask_depth: float
    pressure: float
    liquidity_usd: float


def compute_side_metrics(order_book: dict) -> SideMetrics:
    """One side's (YES or NO) full order book snapshot, independent of
    whether that side is currently "selected" by a Tab 1 signal."""
    bids = order_book.get("bids", [])
    asks = order_book.get("asks", [])
    best_bid = bids[0]["price"] if bids else 0.0
    best_ask = asks[0]["price"] if asks else 1.0
    mid = (best_bid + best_ask) / 2.0
    spread = best_ask - best_bid

    top5_bids = bids[: config.OB_LEVELS]
    top5_asks = asks[: config.OB_LEVELS]
    top5_bid_depth = sum(lv["size"] for lv in top5_bids)
    top5_ask_depth = sum(lv["size"] for lv in top5_asks)
    liquidity = sum(lv["price"] * lv["size"] for lv in top5_bids)
    liquidity += sum(lv["price"] * lv["size"] for lv in top5_asks)

    pressure, w_bid, w_ask = compute_pressure(order_book)

    return SideMetrics(
        price=best_ask, best_bid=best_bid, best_ask=best_ask, mid=mid, spread=spread,
        top5_bid_depth=top5_bid_depth, top5_ask_depth=top5_ask_depth,
        weighted_bid_depth=w_bid, weighted_ask_depth=w_ask,
        pressure=pressure, liquidity_usd=liquidity,
    )


def check_confirmation(
    metrics: SideMetrics,
    local_low: Optional[float],
    prev_price: Optional[float],
    prev_pressure: Optional[float],
) -> tuple[str, str]:
    """
    Simplified confirmation check for the currently-selected side of Tab 2's
    always-on observer. No candidate-expiry concept here (Tab 2 tracks the
    selected side until Tab 1's signal itself changes) and no real order is
    ever placed — this only reports "READY" (order book confirms the entry
    conditions) or "WAIT", plus a short plain-English clause explaining why.
    """
    price = metrics.price

    if price > config.MAX_ENTRY_PRICE:
        return "WAIT", f"price {price:.3f} is above {config.MAX_ENTRY_PRICE:.2f}, so no entry confirmation yet"

    pf = profit_factor(price)
    if pf < config.MIN_PROFIT_FACTOR:
        return "WAIT", f"expected profit {pf:.2f}x is below the required {config.MIN_PROFIT_FACTOR:.2f}x"

    if metrics.spread > config.MAX_SPREAD:
        return "WAIT", f"spread {metrics.spread:.3f} is too wide (> {config.MAX_SPREAD:.2f})"

    if metrics.liquidity_usd < config.MIN_LIQUIDITY_USD:
        return "WAIT", f"liquidity ${metrics.liquidity_usd:.2f} is below the minimum ${config.MIN_LIQUIDITY_USD:.2f}"

    if local_low is None:
        return "WAIT", "waiting for a local low to form since the signal"

    if price <= local_low:
        return "WAIT", f"price is still falling (local low {local_low:.3f}), so no entry confirmation yet"

    if prev_price is not None and price <= prev_price:
        return "WAIT", f"price {price:.3f} is not yet ticking up from its low"

    if metrics.pressure <= config.MIN_OB_PRESSURE:
        return "WAIT", "pressure is not positive yet, so no entry confirmation"

    if prev_pressure is not None and metrics.pressure <= prev_pressure:
        return "WAIT", "pressure is positive but not improving yet, so no entry confirmation"

    return "READY", (f"price dropped to {local_low:.3f} and recovered to {price:.3f}. "
                      f"Pressure is positive ({metrics.pressure:.3f}) and improving. "
                      f"Order book confirms the entry.")
