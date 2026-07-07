"""
Tab 3 — Trading Engine. SIMULATION ONLY: no wallet, no order placement, no
real Polymarket trading, ever. Every "BUY" below is a simulated paper entry.

Consumes Tab 1's signal (GREEN/RED) and independently-fetched order books
(the caller — app.py — does all network I/O, exactly like candidate_manager
already does for Tab 2; this module is pure state + decision logic).

Entry philosophy (see the Tab 3 spec): the goal is not the cheapest price,
it's capturing good trades. A strong, improving order book justifies buying
even near the hard cap; a weak/falling one means waiting for a better price.
Three modes:
  Mode 1 — Immediate Entry: buyers already in control, buy now.
  Mode 2 — Wait: pressure balanced, no edge yet, keep observing.
  Mode 3 — Deep Wait: pressure negative; wait for it to recover, then buy
           ("Recovery Entry").
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional
import time

import config
import orderbook_engine as obe
import trade_db


def _clean(x):
    """NaN (from pandas ATR/body-ratio columns) -> None, for SQLite storage."""
    try:
        if x != x:   # NaN != NaN
            return None
    except Exception:
        pass
    return x


@dataclass
class TradeCandidate:
    db_id: int
    signal_time: int
    direction: int              # 1 = GREEN/UP (watch YES), -1 = RED/DOWN (watch NO)
    prediction: str              # "GREEN" or "RED"
    signal_open: float
    signal_high: float
    signal_low: float
    signal_close: float
    atr: Optional[float]
    body: float
    body_atr_ratio: Optional[float]
    reason: str
    selected_side: str           # "YES" | "NO"
    market_slug: str
    expiry_time: float
    # Snapshotted at creation and reused for this candidate's whole lifetime
    # — even if the *current* active market rolls over to a new 5-minute
    # window mid-candidate, order book polling keeps targeting the window
    # this candidate was actually created against (never a later market's
    # tokens; that would corrupt monitoring and make settlement impossible).
    yes_token_id: str = ""
    no_token_id: str = ""
    created_at: float = field(default_factory=time.time)
    status: str = "OBSERVING"    # OBSERVING | ENTERED | SKIPPED | EXPIRED

    snapshot_history: list = field(default_factory=list)
    local_low: Optional[float] = None
    prev_price: Optional[float] = None
    prev_pressure: Optional[float] = None
    prev_bid_depth: Optional[float] = None
    prev_ask_depth: Optional[float] = None
    pressure_changes: list = field(default_factory=list)
    pressure_positive_streak: int = 0
    pressure_negative_streak: int = 0

    last_mode: Optional[str] = None
    last_decision: str = "WAIT"
    last_reason: str = "Candidate created — collecting order book snapshots."

    # Simulated resting limit order — price = local_low since the signal
    # (confirmed rule: "buy if/when price comes back down to where it
    # bottomed"). limit_touched = current price is at/below that level.
    limit_price: Optional[float] = None
    limit_touched: bool = False

    chart_path: Optional[str] = None   # saved candle+limit chart, refreshed every tick
    f1_trend: str = ""
    f2_volatility: str = ""
    f3_close_location: str = ""
    f4_continuation: str = ""
    f5_anti_chop: str = ""

    def is_expired(self) -> bool:
        return time.time() > self.expiry_time


@dataclass
class ActiveTrade:
    db_id: int
    candidate_db_id: int
    market_slug: str
    direction: int
    prediction: str
    selected_side: str
    entry_time: float
    entry_price: float
    stake: float
    entry_mode: str
    entry_reason: str
    expiry_time: float
    yes_token_id: str = ""       # inherited from the candidate — same market for its whole lifetime
    no_token_id: str = ""
    status: str = "OPEN"         # OPEN | EARLY_EXIT | SETTLED

    snapshot_history: list = field(default_factory=list)
    prev_pressure: Optional[float] = None
    pressure_negative_streak: int = 0

    exit_time: Optional[float] = None
    exit_price: Optional[float] = None
    exit_reason: Optional[str] = None
    final_result: Optional[str] = None
    pnl: Optional[float] = None
    return_pct: Optional[float] = None
    report_text: Optional[str] = None

    # Saved report images (paths) — same files the Active Trade Block shows
    # live, refreshed each tick; whatever they contain at settlement is what
    # View Log shows, so no separate "freeze" step is needed.
    candle_chart_path: Optional[str] = None
    pressure_chart_path: Optional[str] = None
    depth_chart_path: Optional[str] = None
    pnl_chart_path: Optional[str] = None


def create_candidate(tab1_row: dict, market: dict) -> TradeCandidate:
    """Tab 1 produced a GREEN/RED signal — create and persist a new candidate."""
    direction = 1 if tab1_row["predicted_next"] == "GREEN" else -1
    selected_side = "YES" if direction == 1 else "NO"
    expiry_time = time.time() + market["_tte"]

    row = {
        "signal_time": int(tab1_row["time"]), "direction": direction,
        "prediction": tab1_row["predicted_next"],
        "signal_open": tab1_row["open"], "signal_high": tab1_row["high"],
        "signal_low": tab1_row["low"], "signal_close": tab1_row["close"],
        "atr": _clean(tab1_row.get("atr")), "body": tab1_row["body"],
        "body_atr_ratio": _clean(tab1_row.get("body_atr_ratio")),
        "reason": tab1_row.get("reason", ""), "selected_side": selected_side,
        "market_slug": market["_slug"],
        "f1_trend": tab1_row.get("f1_trend", ""), "f2_volatility": tab1_row.get("f2_volatility", ""),
        "f3_close_location": tab1_row.get("f3_close_location", ""),
        "f4_continuation": tab1_row.get("f4_continuation", ""),
        "f5_anti_chop": tab1_row.get("f5_anti_chop", ""),
    }
    db_id = trade_db.insert_candidate(row)

    return TradeCandidate(
        db_id=db_id, signal_time=row["signal_time"], direction=direction,
        prediction=row["prediction"], signal_open=row["signal_open"],
        signal_high=row["signal_high"], signal_low=row["signal_low"],
        signal_close=row["signal_close"], atr=row["atr"], body=row["body"],
        body_atr_ratio=row["body_atr_ratio"], reason=row["reason"],
        selected_side=selected_side, market_slug=row["market_slug"],
        yes_token_id=market["_yes_token_id"], no_token_id=market["_no_token_id"],
        expiry_time=expiry_time, f1_trend=row["f1_trend"], f2_volatility=row["f2_volatility"],
        f3_close_location=row["f3_close_location"], f4_continuation=row["f4_continuation"],
        f5_anti_chop=row["f5_anti_chop"],
    )


def supersede_candidate(candidate: TradeCandidate, reason: str = "Superseded by a new Tab 1 signal") -> None:
    if candidate.status == "OBSERVING":
        candidate.status = "SKIPPED"
        candidate.last_reason = reason
        trade_db.update_candidate_status(candidate.db_id, "SKIPPED")


def expire_candidate(candidate: TradeCandidate) -> None:
    if candidate.status == "OBSERVING":
        candidate.status = "EXPIRED"
        candidate.last_reason = "Market expired before any entry condition was met."
        trade_db.update_candidate_status(candidate.db_id, "EXPIRED")


def _decide_entry(candidate: TradeCandidate, calc: dict, settings: dict) -> tuple[Optional[str], str, str]:
    """
    Returns (mode, decision, reason). mode is one of IMMEDIATE / WAIT /
    DEEP_WAIT / None (hard-blocked); decision is BUY / WAIT / SKIP.
    """
    price = calc["price"]

    if settings.get("immediate_mode"):
        reason = (f"Immediate Entry & Exit mode is ON — entering immediately at the current market price "
                   f"{price:.3f} with no order-book conditions (pressure, profit factor, spread, liquidity) "
                   f"and no waiting. Will hold until the market expires — no early exit.")
        return "IMMEDIATE", "BUY", reason

    if price > settings["hard_block_price"]:
        return None, "SKIP", (f"Price {price:.3f} is above the hard block price "
                               f"{settings['hard_block_price']:.2f} — never entered, no exceptions.")

    price_ok = price <= settings["max_entry_price"]
    pf_ok = calc["profit_factor"] >= settings["min_profit_factor"]
    spread_ok = calc["spread"] <= settings["max_spread"]
    liquidity_ok = calc["liquidity"] >= settings["min_liquidity"]

    # ── Mode 1 — Immediate Entry: buyers are already taking control ────────
    pressure_strong = calc["pressure"] >= settings["pressure_threshold"]
    slope_positive = calc["pressure_slope"] > 0
    bid_increasing = calc["bid_depth_change"] > 0
    ask_stable = abs(calc["ask_depth_change"]) <= settings["depth_stable_tolerance"] * max(calc["ask_depth"], 1.0)

    if (price_ok and pf_ok and pressure_strong and slope_positive and bid_increasing
            and ask_stable and spread_ok and liquidity_ok):
        reason = (f"Pressure is strong ({calc['pressure']:.2f}) and rising, bid depth increasing, "
                   f"ask depth stable, spread/liquidity acceptable. Waiting increases risk of missing "
                   f"the trade — immediate simulated market entry at {price:.3f}.")
        return "IMMEDIATE", "BUY", reason

    # ── Mode 3 — Deep Wait: pressure was negative, watch for recovery ──────
    if candidate.pressure_negative_streak >= 1:
        if slope_positive and price_ok and pf_ok and spread_ok and liquidity_ok:
            reason = (f"Recovery entry — pressure was negative and is now recovering "
                       f"(slope {calc['pressure_slope']:.3f}); price {price:.3f} has probably found support.")
            return "DEEP_WAIT", "BUY", reason
        return "DEEP_WAIT", "WAIT", ("Pressure is negative/recovering but not confirmed yet — "
                                      "continuing to observe for a recovery entry.")

    # ── Mode 2 — Wait: pressure balanced, no edge yet ───────────────────────
    if abs(calc["pressure"]) < settings["pressure_threshold"]:
        return "WAIT", "WAIT", "Pressure is balanced — market has no clear direction yet, still observing."

    return "WAIT", "WAIT", "Order book is not yet confirming an entry (price, profit factor, spread, or liquidity)."


def record_candidate_snapshot(candidate: TradeCandidate, yes_book: dict, no_book: dict,
                               settings: dict) -> dict:
    """One pre-entry snapshot: metrics, pressure slope, decision — persisted, never overwritten."""
    selected_book = yes_book if candidate.selected_side == "YES" else no_book
    metrics = obe.compute_side_metrics(selected_book)
    now = time.time()
    price = metrics.price

    pressure_change = (metrics.pressure - candidate.prev_pressure) if candidate.prev_pressure is not None else 0.0
    candidate.pressure_changes.append(pressure_change)
    candidate.pressure_changes = candidate.pressure_changes[-settings["pressure_confirm_count"]:]
    pressure_slope = sum(candidate.pressure_changes) / len(candidate.pressure_changes)

    bid_depth_change = (metrics.weighted_bid_depth - candidate.prev_bid_depth) \
        if candidate.prev_bid_depth is not None else 0.0
    ask_depth_change = (metrics.weighted_ask_depth - candidate.prev_ask_depth) \
        if candidate.prev_ask_depth is not None else 0.0

    if candidate.local_low is None or price < candidate.local_low:
        candidate.local_low = price
    recovering = candidate.prev_price is not None and candidate.prev_price > candidate.local_low

    # Simulated limit order — rests at the local low; "touched" means the
    # current price has come back down to (or below) that resting price.
    candidate.limit_price = candidate.local_low
    candidate.limit_touched = price <= candidate.limit_price

    calc = {
        "price": price, "pressure": metrics.pressure, "pressure_slope": pressure_slope,
        "bid_depth_change": bid_depth_change, "ask_depth_change": ask_depth_change,
        "ask_depth": metrics.weighted_ask_depth, "spread": metrics.spread,
        "liquidity": metrics.liquidity_usd, "profit_factor": obe.profit_factor(price),
    }
    mode, decision, reason = _decide_entry(candidate, calc, settings)

    snap = {
        "ts": now, "best_bid": metrics.best_bid, "best_ask": metrics.best_ask, "mid": metrics.mid,
        "spread": metrics.spread,
        "top5_bids": selected_book.get("bids", [])[:config.OB_LEVELS],
        "top5_asks": selected_book.get("asks", [])[:config.OB_LEVELS],
        "weighted_bid_depth": metrics.weighted_bid_depth, "weighted_ask_depth": metrics.weighted_ask_depth,
        "pressure": metrics.pressure, "pressure_change": pressure_change, "pressure_slope": pressure_slope,
        "bid_depth_change": bid_depth_change, "ask_depth_change": ask_depth_change,
        "limit_price": candidate.limit_price, "limit_touched": candidate.limit_touched,
        "selected_price": price, "local_low": candidate.local_low, "recovering": recovering,
        "decision": decision, "mode": mode or "", "reason": reason,
    }
    trade_db.insert_candidate_snapshot(candidate.db_id, snap)
    candidate.snapshot_history.append(snap)
    if len(candidate.snapshot_history) > config.TAB3_SNAPSHOT_HISTORY_MAX:
        candidate.snapshot_history = candidate.snapshot_history[-config.TAB3_SNAPSHOT_HISTORY_MAX:]

    if metrics.pressure > 0:
        candidate.pressure_positive_streak += 1
        candidate.pressure_negative_streak = 0
    elif metrics.pressure < 0:
        candidate.pressure_negative_streak += 1
        candidate.pressure_positive_streak = 0
    else:
        candidate.pressure_positive_streak = 0
        candidate.pressure_negative_streak = 0

    candidate.prev_price = price
    candidate.prev_pressure = metrics.pressure
    candidate.prev_bid_depth = metrics.weighted_bid_depth
    candidate.prev_ask_depth = metrics.weighted_ask_depth
    candidate.last_mode = mode
    candidate.last_decision = decision
    candidate.last_reason = reason

    return snap


def enter_trade(candidate: TradeCandidate, snap: dict, settings: dict) -> ActiveTrade:
    """Simulated entry only — no real order is ever sent."""
    row = {
        "candidate_id": candidate.db_id, "market_slug": candidate.market_slug,
        "direction": candidate.direction, "prediction": candidate.prediction,
        "entry_time": time.time(), "entry_price": snap["selected_price"],
        "stake": settings["stake"], "entry_mode": candidate.last_mode or "",
        "entry_reason": candidate.last_reason, "expiry_time": candidate.expiry_time,
    }
    db_id = trade_db.insert_trade(row)
    trade_db.update_candidate_status(candidate.db_id, "ENTERED")
    candidate.status = "ENTERED"

    return ActiveTrade(
        db_id=db_id, candidate_db_id=candidate.db_id, market_slug=row["market_slug"],
        direction=row["direction"], prediction=row["prediction"], selected_side=candidate.selected_side,
        entry_time=row["entry_time"], entry_price=row["entry_price"], stake=row["stake"],
        entry_mode=row["entry_mode"], entry_reason=row["entry_reason"], expiry_time=row["expiry_time"],
        yes_token_id=candidate.yes_token_id, no_token_id=candidate.no_token_id,
    )


def record_trade_snapshot(trade: ActiveTrade, yes_book: dict, no_book: dict) -> dict:
    """Active-trade monitoring tick — mark-to-market PnL off the current best bid (exit value)."""
    selected_book = yes_book if trade.selected_side == "YES" else no_book
    metrics = obe.compute_side_metrics(selected_book)
    now = time.time()

    shares = trade.stake / trade.entry_price if trade.entry_price > 0 else 0.0
    unrealized_value = shares * metrics.best_bid
    pnl = unrealized_value - trade.stake
    pnl_pct = pnl / trade.stake if trade.stake else 0.0

    pressure_trend = obe.pressure_trend(metrics.pressure, trade.prev_pressure)
    time_remaining = max(0.0, trade.expiry_time - now)

    snap = {
        "ts": now, "price": metrics.price, "pnl": pnl, "pnl_pct": pnl_pct,
        "pressure": metrics.pressure, "pressure_trend": pressure_trend, "spread": metrics.spread,
        "liquidity": metrics.liquidity_usd, "bid_depth": metrics.weighted_bid_depth,
        "ask_depth": metrics.weighted_ask_depth, "time_remaining": time_remaining,
    }
    trade_db.insert_trade_snapshot(trade.db_id, snap)
    trade.snapshot_history.append(snap)
    if len(trade.snapshot_history) > config.TAB3_SNAPSHOT_HISTORY_MAX:
        trade.snapshot_history = trade.snapshot_history[-config.TAB3_SNAPSHOT_HISTORY_MAX:]

    if metrics.pressure < 0:
        trade.pressure_negative_streak += 1
    else:
        trade.pressure_negative_streak = 0
    trade.prev_pressure = metrics.pressure

    return snap


def check_early_exit(trade: ActiveTrade, settings: dict) -> tuple[bool, str]:
    """
    Early exit ONLY to cut a collapsing trade — never just because profit
    shrank. Every condition below must hold: real loss, sustained negative
    pressure, negative slope, bid depth falling, ask depth rising, spread
    widening.
    """
    if settings.get("immediate_mode"):
        return False, ""   # Immediate Entry & Exit mode: hold until settle_at_expiry, never exit early.

    n = settings["pressure_confirm_count"]
    if len(trade.snapshot_history) < n:
        return False, ""

    latest = trade.snapshot_history[-1]
    if latest["pnl_pct"] > -settings["early_exit_loss_pct"]:
        return False, ""
    if trade.pressure_negative_streak < n:
        return False, ""

    recent = trade.snapshot_history[-n:]
    changes = [recent[i]["pressure"] - recent[i - 1]["pressure"] for i in range(1, len(recent))]
    slope = sum(changes) / len(changes) if changes else 0.0
    bid_falling = recent[-1]["bid_depth"] < recent[0]["bid_depth"]
    ask_increasing = recent[-1]["ask_depth"] > recent[0]["ask_depth"]
    spread_widening = recent[-1]["spread"] > recent[0]["spread"]

    if slope < 0 and bid_falling and ask_increasing and spread_widening:
        reason = (f"Early exit — loss {latest['pnl_pct'] * 100:.1f}%, pressure negative for {n} "
                  f"consecutive snapshots and falling, bid depth falling, ask depth rising, "
                  f"spread widening. Order book shows the trade collapsing.")
        return True, reason
    return False, ""


def settle_early_exit(trade: ActiveTrade, reason: str) -> dict:
    latest = trade.snapshot_history[-1]
    return _finalize(trade, status="EARLY_EXIT", exit_time=latest["ts"], exit_price=latest["price"],
                      exit_reason=reason, pnl=latest["pnl"], return_pct=latest["pnl_pct"])


def parse_window_start_ts(market_slug: str) -> int:
    """market_slug is always '{coin}-updown-5m-{window_start_ts}' — verified
    directly against live Polymarket data (see polymarket_api.fetch_btcusd_market's
    docstring): the embedded timestamp is the window's START, not its end.
    The window's real end/resolution is start + 300."""
    return int(market_slug.rsplit("-", 1)[1])


def settle_at_expiry(trade: ActiveTrade, window_candle: Optional[dict]) -> Optional[dict]:
    """
    Attempts settlement only once the market has actually expired. These
    Polymarket 5-minute markets resolve on "BTC price at window close vs
    window open" — resolved here directly from the real BTC candle spanning
    that exact window (window_candle, looked up by the caller from
    btc_price_api.py's already-fetched candles), NOT from Polymarket's own
    order book. That matters: once a market closes, its order book empties
    out completely (no bids, no asks), and there is no way to distinguish a
    WIN from a LOSS from an empty book — the old mid-price heuristic would
    stay permanently ambiguous and the trade would never settle.

    window_candle is None until that specific candle appears in fetched data
    (retried next tick) — {open, close} is all that's needed.
    """
    if time.time() < trade.expiry_time:
        return None
    if window_candle is None:
        return None   # candle hasn't shown up in fetched data yet — retry next tick

    open_, close_ = window_candle["open"], window_candle["close"]
    if close_ > open_:
        btc_direction = 1
    elif close_ < open_:
        btc_direction = -1
    else:
        btc_direction = 0   # exact tie — vanishingly rare; Polymarket itself has to break it somehow,
                             # we treat it as a loss for the side that needed a strict move.

    won = btc_direction == trade.direction
    shares = trade.stake / trade.entry_price if trade.entry_price > 0 else 0.0
    exit_price = 1.0 if won else 0.0
    pnl = shares * exit_price - trade.stake
    return_pct = pnl / trade.stake if trade.stake else 0.0
    move = "closed UP" if btc_direction == 1 else ("closed DOWN" if btc_direction == -1 else "was flat")
    reason = (f"Held to market expiry. BTC {move} over the window ({open_:.2f} -> {close_:.2f}). "
              + ("WIN." if won else "LOSS."))
    return _finalize(trade, status="SETTLED", exit_time=time.time(), exit_price=exit_price,
                      exit_reason=reason, pnl=pnl, return_pct=return_pct)


def _build_report_text(trade: ActiveTrade, final_result: str, return_pct: float) -> str:
    candidate_row = trade_db.fetch_candidate(trade.candidate_db_id)
    signal_reason = candidate_row["reason"] if candidate_row else ""
    return (
        f"Tab 1 signaled {trade.prediction} ({signal_reason}). Tab 3 selected {trade.selected_side} "
        f"and entered via {trade.entry_mode} at {trade.entry_price:.3f} — {trade.entry_reason} "
        f"The trade exited at {trade.exit_price:.3f} — {trade.exit_reason} "
        f"Final result: {final_result} ({return_pct * 100:+.1f}% return)."
    )


def _finalize(trade: ActiveTrade, status: str, exit_time: float, exit_price: float,
              exit_reason: str, pnl: float, return_pct: float) -> dict:
    final_result = "WIN" if pnl > 0 else "LOSS"
    trade.status = status
    trade.exit_time = exit_time
    trade.exit_price = exit_price
    trade.exit_reason = exit_reason
    trade.final_result = final_result
    trade.pnl = pnl
    trade.return_pct = return_pct
    trade.report_text = _build_report_text(trade, final_result, return_pct)
    trade_db.update_trade_settlement(trade.db_id, status, exit_time, exit_price, exit_reason,
                                      final_result, pnl, return_pct, trade.report_text)
    return {"status": status, "exit_time": exit_time, "exit_price": exit_price, "exit_reason": exit_reason,
            "final_result": final_result, "pnl": pnl, "return_pct": return_pct}


def build_report(trade_row: dict) -> dict:
    """DB-driven so it works for both the live trade and any historical one."""
    candidate_row = trade_db.fetch_candidate(trade_row["candidate_id"])
    candidate_snapshots = trade_db.fetch_candidate_snapshots(trade_row["candidate_id"])
    trade_snapshots = trade_db.fetch_trade_snapshots(trade_row["id"])
    return {
        "candidate": candidate_row, "trade": trade_row,
        "candidate_snapshots": candidate_snapshots, "trade_snapshots": trade_snapshots,
    }
