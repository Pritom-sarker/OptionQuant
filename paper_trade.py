"""
Paper trading for Tab 2. SIMULATION ONLY — never sends a real order, never
touches a wallet, never uses a private key.

Entry: records the trade at the confirmed order-book entry price.
Hold: never exits early — always waits for market expiry.
Settlement: reads the post-expiry order book mid-price on the selected side
as a proxy for the resolved outcome (>=0.95 -> WIN, <=0.05 -> LOSS); still
ambiguous mid-prices are retried on a later refresh.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Optional
import time

import config
import orderbook_api as obapi
import orderbook_engine as obe


@dataclass
class PaperTrade:
    market_id: str
    direction: int             # 1 = GREEN/UP (bought YES), -1 = RED/DOWN (bought NO)
    prediction: str            # "GREEN" or "RED"
    entry_time: float
    entry_price: float
    stake: float
    pressure_at_entry: float
    expected_profit_factor: float
    expiry_time: float         # market's own expiry, unix seconds
    status: str = "OPEN"       # OPEN | SETTLED
    final_result: Optional[str] = None   # WIN | LOSS
    profit: Optional[float] = None


def enter_trade(candidate, metrics: "obe.OBMetrics", market_id: str, expiry_time: float) -> PaperTrade:
    """Records a SIMULATED entry. No real order is ever sent."""
    pf = obe.profit_factor(metrics.selected_price)
    trade = PaperTrade(
        market_id=market_id,
        direction=candidate.direction,
        prediction=candidate.prediction,
        entry_time=time.time(),
        entry_price=metrics.selected_price,
        stake=config.DEFAULT_STAKE,
        pressure_at_entry=metrics.pressure,
        expected_profit_factor=pf,
        expiry_time=expiry_time,
    )
    candidate.mark_entered(metrics.selected_price)
    return trade


def settle_trade(trade: PaperTrade, yes_token_id: str, no_token_id: str) -> bool:
    """
    Settles `trade` in place if its market has expired and a clear resolution
    is available. Returns True if settlement happened this call.
    """
    if trade.status != "OPEN":
        return False
    if time.time() < trade.expiry_time:
        return False   # never settle before the market has actually expired

    selected_token = yes_token_id if trade.direction == 1 else no_token_id
    book = obapi.fetch_order_book(selected_token)
    best_bid = obapi.get_best_bid(book)
    best_ask = obapi.get_best_ask(book)
    if best_bid is None or best_ask is None:
        return False   # can't resolve yet, retry next refresh

    mid = (best_bid + best_ask) / 2.0
    if mid >= 0.95:
        won = True
    elif mid <= 0.05:
        won = False
    else:
        return False   # still ambiguous, retry next refresh

    profit = trade.stake * obe.profit_factor(trade.entry_price) if won else -trade.stake
    trade.status = "SETTLED"
    trade.final_result = "WIN" if won else "LOSS"
    trade.profit = profit
    return True
