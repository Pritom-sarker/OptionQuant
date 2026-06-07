# src/trader.py — paper trade lifecycle
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from src import database as db
from src.utils import utcnow_str, minutes_remaining


# ── Entry ──────────────────────────────────────────────────────────────────────

def enter_trade(
    market_id:   str,
    token_id:    str,
    title:       str,
    side:        str,
    entry_price: float,
    order_size:  float,
    entry_score: float,
    expiry_type: str,
    end_time:    str,
) -> dict:
    contracts = order_size / entry_price if entry_price > 0 else 0.0

    trade = {
        "trade_id":    str(uuid.uuid4()),
        "market_id":   market_id,
        "token_id":    token_id,
        "title":       title,
        "side":        side,
        "entry_price": entry_price,
        "exit_price":  None,
        "contracts":   contracts,
        "order_size":  order_size,
        "entry_time":  utcnow_str(),
        "exit_time":   None,
        "pnl":         None,
        "status":      "open",
        "exit_reason": None,
        "entry_score": entry_score,
        "expiry_type": expiry_type,
        "end_time":    end_time,
    }

    db.insert_trade(trade)
    db.insert_log(
        "INFO", "trade_entry",
        f"PAPER ENTRY ▶ {side} [{expiry_type}] {title[:50]} "
        f"@ {entry_price:.4f} | contracts={contracts:.2f} | score={entry_score:.1f}"
    )
    return trade


# ── Exit ───────────────────────────────────────────────────────────────────────

def exit_trade(trade_id: str, exit_price: float, exit_reason: str) -> float:
    trade = db.get_trade(trade_id)
    if not trade:
        return 0.0

    pnl = trade["contracts"] * exit_price - trade["order_size"]

    db.update_trade(trade_id, {
        "exit_price":  exit_price,
        "exit_time":   utcnow_str(),
        "pnl":         pnl,
        "status":      "closed",
        "exit_reason": exit_reason,
    })
    db.insert_log(
        "INFO", "trade_exit",
        f"PAPER EXIT ◀ {trade['side']} {trade['title'][:50]} "
        f"@ {exit_price:.4f} | PnL={pnl:+.4f} | reason={exit_reason}"
    )
    return pnl


def close_at_expiry(trade_id: str) -> float:
    """Close a trade when its market expires. Resolve at current bid > 0.5."""
    trade = db.get_trade(trade_id)
    if not trade:
        return 0.0

    snap = db.get_latest_snapshot(trade["market_id"])
    last_bid = snap["best_bid"] if snap else 0.0

    side = trade.get("side", "UP")
    won  = (side == "UP" and last_bid >= 0.5) or (side == "DOWN" and last_bid < 0.5)

    if won:
        exit_price = 1.0
        reason     = "Market expired: WIN"
    else:
        exit_price = 0.0
        reason     = "Market expired: LOSS"

    return exit_trade(trade_id, exit_price, reason)


# ── Unrealized PnL ─────────────────────────────────────────────────────────────

def unrealized_pnl(trade: dict, current_bid: float) -> float:
    return trade["contracts"] * current_bid - trade["order_size"]


# ── Exit condition check ───────────────────────────────────────────────────────

def check_exit_conditions(
    trade: dict,
    latest_snap: dict,
    settings: dict,
) -> tuple[bool, str, float]:
    """
    Returns (should_exit, reason, exit_price).
    """
    exit_mode    = settings.get("exit_mode", "Hold Until Expiry")
    current_bid  = latest_snap.get("best_bid", 0.0)
    wp           = latest_snap.get("weighted_pressure", 0.0)
    order_size   = trade.get("order_size", 1.0)
    side         = trade.get("side", "UP")

    # Always close if market has expired
    end_time = trade.get("end_time") or ""
    remaining = minutes_remaining(end_time)
    if remaining is not None and remaining <= 0:
        return True, "Market expired", current_bid

    if exit_mode == "Hold Until Expiry":
        return False, "", current_bid

    pnl     = unrealized_pnl(trade, current_bid)
    pnl_pct = pnl / order_size if order_size > 0 else 0.0

    if exit_mode == "Take Profit / Stop Loss":
        tp = settings.get("take_profit", 0.20)
        sl = settings.get("stop_loss",   0.10)
        if pnl_pct >= tp:
            return True, f"Take profit ({pnl_pct:.1%})", current_bid
        if pnl_pct <= -sl:
            return True, f"Stop loss ({pnl_pct:.1%})", current_bid

    elif exit_mode == "Signal Flip Exit":
        if side == "UP"   and wp < -0.05:
            return True, f"Signal flip: pressure turned negative ({wp:+.4f})", current_bid
        if side == "DOWN" and wp >  0.05:
            return True, f"Signal flip: pressure turned positive ({wp:+.4f})", current_bid

    return False, "", current_bid
