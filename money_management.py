"""
Tab 6 — Money Management Simulator. Ported from
pine_strategy_simulator/money_management.py's run_simulation() — identical
sizing formula, loss-basket recovery mechanics, dynamic recovery tiers, and
reset modes. See config.py's "Tab 6" section for the one deliberate change:
WIN payoff here uses each trade's own dynamic profit factor
(orderbook_engine.profit_factor(entry_price)) instead of a static profit
factor of 1.

This replays the app's own REAL settled trades (trade_db, chronological,
oldest -> newest) rather than a historical Pine-strategy backtest sweep —
Tab 1/Tab 3 already generate the real signals/trades live, so there is no
separate strategy-combination step here, just the money-management bookkeeping
layered on top of what actually happened.
"""
from __future__ import annotations

import orderbook_engine as obe

DYNAMIC_TIERS = [
    (5, 0.25),    # loss basket <= 5x base trade -> 25%
    (10, 0.15),   # <= 10x base trade -> 15%
    (20, 0.10),   # <= 20x base trade -> 10%
]
DYNAMIC_FLOOR_PCT = 0.05   # > 20x base trade -> 5%


def _recovery_pct_for(loss_basket: float, base_trade: float, dynamic_mode: bool, fixed_pct: float) -> float:
    if not dynamic_mode:
        return fixed_pct
    for multiple, pct in DYNAMIC_TIERS:
        if loss_basket <= multiple * base_trade:
            return pct
    return DYNAMIC_FLOOR_PCT


def run_simulation(trades: list[dict], money: dict) -> dict:
    """
    trades: real settled trades, chronological (oldest -> newest), each
    {"time": entry_time, "result": "WIN"|"LOSS", "entry_price": float,
    "direction": "YES"|"NO"} — exactly trade_db's settled rows, reversed.

    money: {starting_balance, base_trade_amount, max_trade_amount,
    recovery_percent (0-1, fixed), dynamic_mode (bool),
    profit_split_recovery_pct (0-1), reset_mode ("never"|"on_zero"|
    "after_n_wins"), reset_after_n_wins (int)}

    Sizing (never martingale — no doubling on loss):
      recovery_addon = loss_basket * recovery_pct
      trade_amount = min(base_trade_amount + recovery_addon, max_trade_amount)

    LOSS: realized_profit -= trade_amount; loss_basket += trade_amount; balance -= trade_amount
    WIN:  profit_factor = orderbook_engine.profit_factor(entry_price)  -- DYNAMIC, per real trade
          gross_win = trade_amount * profit_factor
          balance += gross_win; recovery_part = gross_win * profit_split_recovery_pct
          profit_part = gross_win - recovery_part; loss_basket = max(0, loss_basket - recovery_part)
          realized_profit += profit_part; recovered_profit += recovery_part

    Returns {"summary": dict, "trade_log": list[dict], "curves": {"time": [...],
    "balance": [...], "loss_basket": [...], "trade_amount": [...]}}
    """
    starting_balance = float(money["starting_balance"])
    base_trade = float(money["base_trade_amount"])
    max_trade_amount = float(money["max_trade_amount"])
    fixed_recovery_pct = float(money["recovery_percent"])
    dynamic_mode = bool(money["dynamic_mode"])
    profit_split_recovery_pct = float(money["profit_split_recovery_pct"])
    reset_mode = money["reset_mode"]
    reset_after_n_wins = int(money.get("reset_after_n_wins", 0))

    balance = starting_balance
    loss_basket = 0.0
    realized_profit = 0.0
    recovered_profit = 0.0
    peak_balance = starting_balance
    max_drawdown_pct = 0.0
    consecutive_losses = 0
    max_consecutive_losses = 0
    consecutive_wins = 0
    max_consecutive_wins = 0
    wins_since_reset = 0
    biggest_loss_basket = 0.0
    max_trade_amount_used = 0.0
    trade_amounts: list[float] = []

    wins = losses = 0
    trade_rows = []
    curve_time, curve_balance, curve_basket, curve_trade_amt = [], [], [], []
    bankrupt = False
    bankrupt_trade_num = None
    bankrupt_time = None

    for t in trades:
        result = t["result"]
        if result not in ("WIN", "LOSS"):
            continue   # real trades only ever settle WIN/LOSS (see trade_engine._finalize) — defensive skip

        recovery_pct = _recovery_pct_for(loss_basket, base_trade, dynamic_mode, fixed_recovery_pct)
        recovery_addon = loss_basket * recovery_pct
        trade_amount = min(base_trade + recovery_addon, max_trade_amount)

        balance_before = balance
        loss_basket_before = loss_basket
        trade_profit_factor = None

        if result == "LOSS":
            losses += 1
            realized_profit -= trade_amount
            loss_basket += trade_amount
            balance -= trade_amount
            consecutive_losses += 1
            max_consecutive_losses = max(max_consecutive_losses, consecutive_losses)
            consecutive_wins = 0
            recovered_this_trade = 0.0
            profit_added_this_trade = -trade_amount
        else:
            wins += 1
            trade_profit_factor = obe.profit_factor(t["entry_price"])
            gross_win = trade_amount * trade_profit_factor
            balance += gross_win
            recovery_part = gross_win * profit_split_recovery_pct
            profit_part = gross_win - recovery_part
            loss_basket = max(0.0, loss_basket - recovery_part)
            realized_profit += profit_part
            recovered_profit += recovery_part
            consecutive_wins += 1
            max_consecutive_wins = max(max_consecutive_wins, consecutive_wins)
            consecutive_losses = 0
            wins_since_reset += 1
            recovered_this_trade = recovery_part
            profit_added_this_trade = profit_part

        if reset_mode == "after_n_wins" and reset_after_n_wins > 0 and wins_since_reset >= reset_after_n_wins:
            loss_basket = 0.0
            wins_since_reset = 0
        # "on_zero" and "never" both rely on the max(0, ...) floor above — no
        # separate forced-reset event for either; "after_n_wins" is the only
        # mode that forces an early reset before the basket organically pays down.

        biggest_loss_basket = max(biggest_loss_basket, loss_basket)
        max_trade_amount_used = max(max_trade_amount_used, trade_amount)
        trade_amounts.append(trade_amount)

        peak_balance = max(peak_balance, balance)
        drawdown_pct = ((peak_balance - balance) / peak_balance * 100.0) if peak_balance > 0 else 0.0
        max_drawdown_pct = max(max_drawdown_pct, drawdown_pct)

        trade_rows.append({
            "trade_num": len(trade_rows) + 1, "time": t["time"], "direction": t.get("direction"),
            "result": result, "entry_price": t.get("entry_price"), "profit_factor": trade_profit_factor,
            "base_trade_amount": base_trade, "recovery_addon": recovery_addon, "trade_amount": trade_amount,
            "balance_before": balance_before, "balance_after": balance, "pnl": balance - balance_before,
            "loss_basket_before": loss_basket_before, "loss_basket_after": loss_basket,
            "recovery_pct_used": recovery_pct, "recovered_amount": recovered_this_trade,
            "realized_profit_added": profit_added_this_trade,
        })

        curve_time.append(t["time"])
        curve_balance.append(balance)
        curve_basket.append(loss_basket)
        curve_trade_amt.append(trade_amount)

        # 100%+ drawdown = balance has hit zero or gone negative — stop
        # replaying further trades (there is no money left to fund another
        # one) and flag it clearly for the summary, exactly like the
        # original simulator.
        if balance <= 0:
            bankrupt = True
            bankrupt_trade_num = trade_rows[-1]["trade_num"]
            bankrupt_time = int(t["time"])
            break

    total_trades = wins + losses
    summary = {
        "starting_balance": starting_balance, "ending_balance": balance,
        "net_pnl": balance - starting_balance,
        "roi_pct": ((balance - starting_balance) / starting_balance * 100.0) if starting_balance else 0.0,
        "total_trades": total_trades, "wins": wins, "losses": losses,
        "win_rate": (wins / total_trades * 100.0) if total_trades else 0.0,
        "max_consecutive_losses": max_consecutive_losses, "max_consecutive_wins": max_consecutive_wins,
        "current_loss_streak": consecutive_losses,
        "biggest_loss_basket": biggest_loss_basket, "final_loss_basket": loss_basket,
        "max_trade_amount_used": max_trade_amount_used,
        "average_trade_amount": (sum(trade_amounts) / len(trade_amounts)) if trade_amounts else 0.0,
        "total_recovered_amount": recovered_profit, "total_realized_profit": realized_profit,
        "max_drawdown_pct": max_drawdown_pct,
        "bankrupt": bankrupt, "bankrupt_trade_num": bankrupt_trade_num, "bankrupt_time": bankrupt_time,
    }
    curves = {"time": curve_time, "balance": curve_balance, "loss_basket": curve_basket,
              "trade_amount": curve_trade_amt}
    return {"summary": summary, "trade_log": trade_rows, "curves": curves}


def hourly_balance_curve(trade_log: list[dict], starting_balance: float) -> list[dict]:
    """
    Balance bucketed to the hour, carried forward through any empty hours —
    "how the balance is changing every hour". Mirrors the original
    simulator's time_bucketed_breakdown(), simplified to hourly-only and to
    the balance line (Tab 6 doesn't need the weekly/monthly trade-count view).
    """
    if not trade_log:
        return []
    buckets: dict[int, float] = {}
    for row in trade_log:
        hour = int(row["time"] // 3600 * 3600)
        buckets[hour] = row["balance_after"]   # rows are chronological — last write per hour wins

    hours = sorted(buckets)
    out = []
    last_balance = starting_balance
    h = hours[0]
    while h <= hours[-1]:
        if h in buckets:
            last_balance = buckets[h]
        out.append({"hour": h, "balance": last_balance})
        h += 3600
    return out
