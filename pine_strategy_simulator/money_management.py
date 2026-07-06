"""
Money Management Simulator — candle-by-candle balance/loss-basket replay.

This is deliberately NOT martingale: a losing trade never doubles the next
trade. The next trade's size is base_trade_amount plus a small percentage of
the *currently outstanding* loss basket (recovery_addon), always capped at
max_trade_amount. See run_simulation()'s docstring for the exact formulas.

Reuses pine_logic.detect_pattern/compute_filters/compute_active_signal (the
same Pine-exact per-strategy math as the rest of this project) — this module
only adds: (1) priority-based combination across multiple selected
strategies into one signal per candle, and (2) the sequential money
management bookkeeping, which by nature can't be vectorized (balance and
loss basket are running state that depends on every prior trade in order).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

import pine_logic

DYNAMIC_TIERS = [
    (5, 0.25),    # loss basket <= 5x base trade -> 25%
    (10, 0.15),   # <= 10x base trade -> 15%
    (20, 0.10),   # <= 20x base trade -> 10%
]
DYNAMIC_FLOOR_PCT = 0.05   # > 20x base trade -> 5%


def combine_signals(df: pd.DataFrame, priority_order: list[str], filters_per_strategy: dict,
                     atr_mult: float) -> pd.DataFrame:
    """
    Runs every selected strategy's own detect_pattern/compute_filters/
    compute_active_signal completely independently (unmodified per-strategy
    Pine math), then resolves one winning strategy per candle by
    priority_order: the first strategy in that list (i.e. the order the user
    chose) whose own signal fired on that candle wins — "only one trade per
    candle" regardless of how many selected strategies fired that candle.
    """
    per_strategy = {}
    for name in priority_order:
        pat_dir = pine_logic.detect_pattern(df, name, atr_mult)
        filters = pine_logic.compute_filters(df, pat_dir)
        act_ok = pine_logic.compute_active_signal(pat_dir, filters, filters_per_strategy[name])
        per_strategy[name] = {"pat_dir": pat_dir, "act_ok": act_ok}

    n = len(df)
    winning_strategy = [None] * n
    winning_dir = np.zeros(n, dtype=int)
    for pos in range(n):
        i = df.index[pos]
        for name in priority_order:
            if bool(per_strategy[name]["act_ok"].loc[i]):
                winning_strategy[pos] = name
                winning_dir[pos] = int(per_strategy[name]["pat_dir"].loc[i])
                break

    return pd.DataFrame({"strategy": winning_strategy, "direction": winning_dir}, index=df.index)


def _recovery_pct_for(loss_basket: float, base_trade: float, dynamic_mode: bool, fixed_pct: float) -> float:
    if not dynamic_mode:
        return fixed_pct
    for multiple, pct in DYNAMIC_TIERS:
        if loss_basket <= multiple * base_trade:
            return pct
    return DYNAMIC_FLOOR_PCT


def run_simulation(df: pd.DataFrame, priority_order: list[str], filters_per_strategy: dict,
                    atr_mult: float, money: dict) -> dict:
    """
    money: {
        starting_balance, base_trade_amount, max_trade_amount,
        recovery_percent (fixed, 0-1), dynamic_mode (bool),
        profit_split_recovery_pct (0-1), reset_mode ("never"|"on_zero"|"after_n_wins"),
        reset_after_n_wins (int),
    }

    Walks the dataset oldest -> newest exactly once. A signal on candle N is
    scored against candle N+1's own open/close only (never candle N, never
    an unfinished candle):
      UP:   WIN if close[N+1] > open[N+1], LOSS if close[N+1] < open[N+1]
      DOWN: WIN if close[N+1] < open[N+1], LOSS if close[N+1] > open[N+1]
      close[N+1] == open[N+1] -> NEUTRAL (no balance/loss-basket change)

    Sizing (never martingale — no doubling on loss):
      recovery_addon = loss_basket * recovery_pct
      trade_amount = min(base_trade_amount + recovery_addon, max_trade_amount)

    LOSS:  realized_profit -= trade_amount; loss_basket += trade_amount; balance -= trade_amount
    WIN:   balance += trade_amount (gross_win); recovery_part = gross_win * profit_split_recovery_pct;
           profit_part = gross_win - recovery_part; loss_basket = max(0, loss_basket - recovery_part);
           realized_profit += profit_part; recovered_profit += recovery_part

    Returns {"summary": dict, "trade_log": DataFrame, "strategy_breakdown": DataFrame,
             "curves": {"balance": [...], "loss_basket": [...], "trade_amount": [...], "drawdown": [...]}}
    """
    combined = combine_signals(df, priority_order, filters_per_strategy, atr_mult)
    n = len(df)
    total_candles = n

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

    wins = losses = neutrals = 0
    trade_rows = []
    curve_balance, curve_basket, curve_trade_amt, curve_drawdown = [], [], [], []
    bankrupt = False
    bankrupt_trade_num = None
    bankrupt_time = None

    for pos in range(n - 1):   # last row has no N+1 to score against
        i = df.index[pos]
        direction = int(combined["direction"].loc[i])
        if direction == 0:
            continue

        strategy = combined["strategy"].loc[i]
        signal_time = df["time"].iloc[pos]
        signal_close = df["close"].iloc[pos]
        result_time = df["time"].iloc[pos + 1]
        result_open = df["open"].iloc[pos + 1]
        result_close = df["close"].iloc[pos + 1]

        if result_close == result_open:
            result = "NEUTRAL"
        elif (direction == 1) == (result_close > result_open):
            result = "WIN"
        else:
            result = "LOSS"

        base_row = {
            "trade_num": len(trade_rows) + 1, "signal_time": signal_time, "result_time": result_time,
            "strategy": strategy, "direction": "UP" if direction == 1 else "DOWN",
            "signal_close": signal_close, "result_open": result_open, "result_close": result_close,
            "result": result,
        }

        if result == "NEUTRAL":
            neutrals += 1
            trade_rows.append({
                **base_row, "base_trade_amount": None, "recovery_addon": None, "trade_amount": None,
                "balance_before": balance, "balance_after": balance, "pnl": 0.0,
                "loss_basket_before": loss_basket, "loss_basket_after": loss_basket,
                "recovery_pct_used": None, "recovered_amount": 0.0, "realized_profit_added": 0.0,
            })
            continue

        recovery_pct = _recovery_pct_for(loss_basket, base_trade, dynamic_mode, fixed_recovery_pct)
        recovery_addon = loss_basket * recovery_pct
        trade_amount = min(base_trade + recovery_addon, max_trade_amount)

        balance_before = balance
        loss_basket_before = loss_basket

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
            gross_win = trade_amount
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
        # "on_zero" and "never" both rely on the max(0, ...) floor above — there is
        # no separate forced-reset event for either, since the basket can never
        # go negative regardless of mode; "after_n_wins" is the only mode that
        # forces an *early* reset before the basket has organically paid down.

        biggest_loss_basket = max(biggest_loss_basket, loss_basket)
        max_trade_amount_used = max(max_trade_amount_used, trade_amount)
        trade_amounts.append(trade_amount)

        peak_balance = max(peak_balance, balance)
        drawdown_pct = ((peak_balance - balance) / peak_balance * 100.0) if peak_balance > 0 else 0.0
        max_drawdown_pct = max(max_drawdown_pct, drawdown_pct)

        trade_rows.append({
            **base_row, "base_trade_amount": base_trade, "recovery_addon": recovery_addon,
            "trade_amount": trade_amount, "balance_before": balance_before, "balance_after": balance,
            "pnl": balance - balance_before, "loss_basket_before": loss_basket_before,
            "loss_basket_after": loss_basket, "recovery_pct_used": recovery_pct,
            "recovered_amount": recovered_this_trade, "realized_profit_added": profit_added_this_trade,
        })

        curve_balance.append(balance)
        curve_basket.append(loss_basket)
        curve_trade_amt.append(trade_amount)
        curve_drawdown.append(drawdown_pct)

        # 100%+ drawdown = balance has hit zero or gone negative — the account
        # is wiped out and cannot fund another trade. Stop simulating further
        # trades here (continuing would be fiction: there is no money left to
        # risk) and flag it clearly for the summary/warnings.
        if balance <= 0:
            bankrupt = True
            bankrupt_trade_num = base_row["trade_num"]
            bankrupt_time = int(result_time)
            break

    trade_log = pd.DataFrame(trade_rows)
    total_trades = wins + losses + neutrals
    resolved = wins + losses

    strategy_breakdown = _strategy_breakdown(trade_log, priority_order)
    if not strategy_breakdown.empty:
        ranked = strategy_breakdown.sort_values("net_pnl", ascending=False)
        best_strategy = ranked.iloc[0]["strategy"]
        worst_strategy = ranked.iloc[-1]["strategy"]
    else:
        best_strategy = worst_strategy = "—"

    summary = {
        "starting_balance": starting_balance, "ending_balance": balance,
        "net_pnl": balance - starting_balance,
        "roi_pct": ((balance - starting_balance) / starting_balance * 100.0) if starting_balance else 0.0,
        "total_trades": total_trades, "wins": wins, "losses": losses, "neutrals": neutrals,
        "win_rate": (wins / resolved * 100.0) if resolved else 0.0,
        "loss_rate": (losses / resolved * 100.0) if resolved else 0.0,
        "signal_frequency": (total_trades / total_candles * 100.0) if total_candles else 0.0,
        "max_consecutive_losses": max_consecutive_losses, "max_consecutive_wins": max_consecutive_wins,
        "current_loss_streak": consecutive_losses,
        "biggest_loss_basket": biggest_loss_basket, "final_loss_basket": loss_basket,
        "max_trade_amount_used": max_trade_amount_used,
        "average_trade_amount": float(np.mean(trade_amounts)) if trade_amounts else 0.0,
        "total_recovered_amount": recovered_profit, "total_realized_profit": realized_profit,
        "max_drawdown_pct": max_drawdown_pct,
        "best_strategy": best_strategy, "worst_strategy": worst_strategy,
        "bankrupt": bankrupt, "bankrupt_trade_num": bankrupt_trade_num, "bankrupt_time": bankrupt_time,
    }

    curves = {"balance": curve_balance, "loss_basket": curve_basket,
              "trade_amount": curve_trade_amt, "drawdown": curve_drawdown}

    return {"summary": summary, "trade_log": trade_log, "strategy_breakdown": strategy_breakdown, "curves": curves}


# ─── Time-bucketed breakdowns (weekly trade count / balance, monthly balance) ─

def _period_label(period, freq: str) -> str:
    start = period.start_time
    if freq == "W":
        week_of_month = (start.day - 1) // 7 + 1
        return f"{start.strftime('%b')} W{week_of_month}"
    return start.strftime("%b %Y")


def time_bucketed_breakdown(trade_log: pd.DataFrame, starting_balance: float, freq: str) -> pd.DataFrame:
    """
    freq="W" -> weekly (trade count + ending balance per week, labeled "Sep W1", "Sep W2", ...).
    freq="M" -> monthly (ending balance per month, labeled "Sep 2026").
    Periods with no trades still appear, with trade_count=0 and the balance
    carried forward from the last period that had one (so the chart/table
    reads as a continuous account timeline, not just the weeks with activity).
    """
    empty = pd.DataFrame(columns=["period", "label", "trade_count", "ending_balance"])
    if trade_log.empty:
        return empty

    df = trade_log.copy()
    df["dt"] = pd.to_datetime(df["result_time"], unit="s")
    df["period"] = df["dt"].dt.to_period(freq)

    grouped = df.groupby("period").agg(
        trade_count=("trade_num", "count"), ending_balance=("balance_after", "last")
    )

    full_range = pd.period_range(grouped.index.min(), grouped.index.max(), freq=freq)
    grouped = grouped.reindex(full_range)
    grouped["trade_count"] = grouped["trade_count"].fillna(0).astype(int)
    grouped["ending_balance"] = grouped["ending_balance"].ffill()
    grouped["ending_balance"] = grouped["ending_balance"].fillna(starting_balance)

    grouped = grouped.reset_index().rename(columns={"index": "period"})
    grouped["label"] = grouped["period"].apply(lambda p: _period_label(p, freq))
    return grouped[["period", "label", "trade_count", "ending_balance"]]


def _strategy_breakdown(trade_log: pd.DataFrame, priority_order: list[str]) -> pd.DataFrame:
    if trade_log.empty:
        return pd.DataFrame(columns=["strategy", "trades", "wins", "losses", "neutrals", "win_rate",
                                      "net_pnl", "average_trade_amount", "max_consecutive_losses"])
    rows = []
    for name in priority_order:
        sub = trade_log[trade_log["strategy"] == name]
        if sub.empty:
            continue
        wins = int((sub["result"] == "WIN").sum())
        losses = int((sub["result"] == "LOSS").sum())
        neutrals = int((sub["result"] == "NEUTRAL").sum())
        resolved = wins + losses
        non_neutral = sub[sub["result"] != "NEUTRAL"]
        max_loss, cur = 0, 0
        for r in sub["result"]:
            if r == "LOSS":
                cur += 1
                max_loss = max(max_loss, cur)
            elif r == "WIN":
                cur = 0
        rows.append({
            "strategy": name, "trades": len(sub), "wins": wins, "losses": losses, "neutrals": neutrals,
            "win_rate": (wins / resolved * 100.0) if resolved else 0.0,
            "net_pnl": float(sub["pnl"].sum()),
            "average_trade_amount": float(non_neutral["trade_amount"].mean()) if len(non_neutral) else 0.0,
            "max_consecutive_losses": max_loss,
        })
    return pd.DataFrame(rows)
