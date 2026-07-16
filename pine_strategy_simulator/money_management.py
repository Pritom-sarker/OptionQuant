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

from typing import Optional

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


# ─── Tiered Money Management (cycle-based recovery, separate from the ────────
# fixed/dynamic loss-basket model above). Two distinct pools:
#
#   temporary_cycle_loss  — losses from the CURRENT consecutive-loss cycle.
#                            Never touches permanent_loss_pool until the
#                            cycle ends (a win occurs).
#   permanent_loss_pool   — the portion of a *finished* cycle that the
#                            cycle-ending win did not fully recover. Paid
#                            down gradually by loss_pool_recovery_percentage
#                            on every subsequent order (independent of
#                            whatever cycle is currently running).
#
# Recovery tiers map "which order number inside the current cycle" (1st loss
# retry, 2nd, 3rd, ...) to a recovery percentage of temporary_cycle_loss.
# Tiers must contiguously cover 1..maximum_cycle_orders with no gaps/overlaps
# — see validate_tiers(). Positions beyond maximum_cycle_orders are handled
# by fallback_mode ("stop" | "continue" | "manual").
# ─────────────────────────────────────────────────────────────────────────────

def validate_tiers(tiers: list[dict], maximum_cycle_orders: int) -> list[str]:
    """
    tiers: list of {"start": int, "end": int, "pct": float (0-1)}.
    Returns a list of human-readable error strings; empty list = valid.
    Tiers must be positive-integer ranges, start <= end, pct in [0, 1],
    non-overlapping, and must contiguously cover loss position 1 through
    maximum_cycle_orders with no gaps (so every position in that range has
    exactly one recovery percentage, and fallback_mode alone governs
    anything past maximum_cycle_orders).
    """
    errors = []
    if not tiers:
        return ["At least one recovery tier is required."]

    for idx, t in enumerate(tiers, start=1):
        start, end, pct = t.get("start"), t.get("end"), t.get("pct")
        if start is None or end is None or pct is None:
            errors.append(f"Tier {idx}: start, end, and recovery % are all required.")
            continue
        try:
            start_i, end_i = int(start), int(end)
        except (TypeError, ValueError):
            errors.append(f"Tier {idx}: start and end must be whole numbers.")
            continue
        if float(start_i) != float(start) or float(end_i) != float(end):
            errors.append(f"Tier {idx}: start ({start}) and end ({end}) must be whole numbers.")
            continue
        if start_i <= 0 or end_i <= 0:
            errors.append(f"Tier {idx}: start and end must be positive integers (got {start_i}-{end_i}).")
        if start_i > end_i:
            errors.append(f"Tier {idx}: start ({start_i}) cannot be greater than end ({end_i}).")
        if not (0.0 <= float(pct) <= 1.0):
            errors.append(f"Tier {idx}: recovery percentage must be between 0% and 100% (got {float(pct) * 100:.0f}%).")

    if errors:
        return errors

    sorted_tiers = sorted(tiers, key=lambda t: int(t["start"]))
    if int(sorted_tiers[0]["start"]) != 1:
        errors.append(f"The first tier must start at loss position 1 (starts at {int(sorted_tiers[0]['start'])}).")

    for prev, cur in zip(sorted_tiers, sorted_tiers[1:]):
        prev_end, cur_start = int(prev["end"]), int(cur["start"])
        if cur_start <= prev_end:
            errors.append(f"Tiers overlap: {int(prev['start'])}-{prev_end} and {cur_start}-{int(cur['end'])} "
                           f"both cover position {cur_start}.")
        elif cur_start != prev_end + 1:
            errors.append(f"Gap between tiers: position {prev_end + 1} to {cur_start - 1} "
                           f"has no assigned recovery percentage.")

    last_end = int(sorted_tiers[-1]["end"])
    if not errors and last_end != int(maximum_cycle_orders):
        errors.append(f"The last tier must end exactly at Maximum Cycle Orders ({int(maximum_cycle_orders)}) — "
                       f"it currently ends at {last_end}. Either extend the last tier or lower Maximum Cycle Orders.")

    return errors


def _tier_for_position(position: int, sorted_tiers: list[dict]) -> tuple[Optional[int], Optional[float], Optional[str]]:
    for idx, t in enumerate(sorted_tiers, start=1):
        if int(t["start"]) <= position <= int(t["end"]):
            return idx, float(t["pct"]), f"Tier {idx} ({int(t['start'])}-{int(t['end'])})"
    return None, None, None


def run_tiered_simulation(df: pd.DataFrame, priority_order: list[str], filters_per_strategy: dict,
                           atr_mult: float, money: dict, tiers: list[dict]) -> dict:
    """
    money: {starting_balance, base_stake, net_profit_ratio, static_lp_pct, max_first_order_stake,
            maximum_cycle_orders, fallback_mode ("stop"|"continue"|"manual"),
            win_pool_contribution_pct, win_pool_lp_coverage_pct}
    tiers: validated via validate_tiers() before calling this.

    Per-order sizing — the LP tax applies ONLY to order 1 of each cycle (not every order,
    which is what caused runaway compounding in the first version of this engine):
      position == 1:
        lp_addon = (permanent_loss_pool * static_lp_pct) / net_profit_ratio
        raw_stake = base_stake + lp_addon
        final_stake = min(raw_stake, max_first_order_stake) if max_first_order_stake else raw_stake
        (base_or_cycle_stake_component / loss_pool_extra_stake_component are the base/LP split of
        final_stake, clipped proportionally if the cap bit)
      position > 1:
        recovery_stake = (temporary_cycle_loss * tier_pct) / net_profit_ratio
        final_stake = max(base_stake, recovery_stake)      -- no LP component at all.

      Because temporary_cycle_loss accumulates the ACTUAL (possibly capped) order-1 stake lost,
      a capped order 1 (e.g. $3 instead of an uncapped $6) naturally makes order 2's recovery
      target start from that $3, not from base_stake — no special-casing needed.

    LOSS: temporary_cycle_loss += final_stake; cycle_order_number += 1; balance -= final_stake.
          permanent_loss_pool is untouched (an order-1 LP component that loses becomes part of
          the temporary cycle loss, so it is never double counted).

    WIN:  actual_payout = final_stake * net_profit_ratio.
          win_pool_contribution = actual_payout * win_pool_contribution_pct is skimmed off the
          top into win_pool first. The remainder is split proportionally between the
          cycle-stake and LP-stake components (only order 1 ever has a nonzero LP component):
          the cycle share first pays down temporary_cycle_loss (remainder transferred to
          permanent_loss_pool), the LP share pays down permanent_loss_pool directly. Finally,
          after every winning trade, win_pool opportunistically pays down whatever permanent_loss_pool
          remains: payment = min(win_pool, permanent_loss_pool * win_pool_lp_coverage_pct).
          temporary_cycle_loss then resets to 0 and a new cycle begins at order 1.

    Position > maximum_cycle_orders is handled by fallback_mode:
      "continue" -> keeps using the last tier's percentage indefinitely (the cycle just keeps
      growing past the max, still chasing 100% recovery via the normal tier formula).
      "stop" -> does NOT place another order. Instead the maxed-out cycle is force-closed right
      there: cycle_timeout_lp_pct of its still-unresolved temporary_cycle_loss is transferred to
      permanent_loss_pool, the rest is written off (never chased again), and a fresh cycle starts
      at order 1 on the next signal. The backtest keeps running — this is the only fallback that
      does not halt the simulation.
      "manual" -> halts the simulation at that point (a backtest replay has no human in the loop
      to grant "manual" confirmation, so this is the one fallback that truly stops the run and
      surfaces a warning; it exists for a future live-trading integration where a person could
      actually click to resume).
    """
    combined = combine_signals(df, priority_order, filters_per_strategy, atr_mult)
    n = len(df)

    starting_balance = float(money["starting_balance"])
    base_stake = float(money["base_stake"])
    net_profit_ratio = float(money["net_profit_ratio"])
    static_lp_pct = float(money["static_lp_pct"])
    max_first_order_stake = money.get("max_first_order_stake")
    max_first_order_stake = float(max_first_order_stake) if max_first_order_stake else None
    maximum_cycle_orders = int(money["maximum_cycle_orders"])
    fallback_mode = money["fallback_mode"]
    win_pool_contribution_pct = float(money["win_pool_contribution_pct"])
    win_pool_lp_coverage_pct = float(money["win_pool_lp_coverage_pct"])
    cycle_timeout_lp_pct = float(money["cycle_timeout_lp_pct"])

    sorted_tiers = sorted(tiers, key=lambda t: int(t["start"]))
    last_tier_idx = len(sorted_tiers)
    last_tier_pct = float(sorted_tiers[-1]["pct"])
    last_tier_label = f"Tier {last_tier_idx} ({int(sorted_tiers[-1]['start'])}-{int(sorted_tiers[-1]['end'])}, continued)"

    balance = starting_balance
    cycle_id = 1
    cycle_order_number = 1
    temporary_cycle_loss = 0.0
    permanent_loss_pool = 0.0
    win_pool = 0.0
    consecutive_losses = 0
    max_consecutive_losses = 0
    peak_balance = starting_balance
    max_drawdown_pct = 0.0
    wins = losses = neutrals = cycle_timeouts = 0
    trade_rows = []
    curve_balance, curve_temp_loss, curve_perm_pool, curve_stake, curve_win_pool = [], [], [], [], []
    bankrupt = False
    bankrupt_trade_num = None
    bankrupt_time = None
    halted = False
    halt_reason = None

    for pos in range(n - 1):
        i = df.index[pos]
        direction = int(combined["direction"].loc[i])
        if direction == 0:
            continue

        strategy = combined["strategy"].loc[i]
        result_time = df["time"].iloc[pos + 1]
        result_open = df["open"].iloc[pos + 1]
        result_close = df["close"].iloc[pos + 1]

        if result_close == result_open:
            neutrals += 1
            continue

        result = "WIN" if (direction == 1) == (result_close > result_open) else "LOSS"

        position_used = cycle_order_number
        cycle_id_used = cycle_id
        temp_loss_before = temporary_cycle_loss
        perm_pool_before = permanent_loss_pool
        win_pool_before = win_pool
        balance_before = balance

        tier_idx, tier_pct, tier_label = _tier_for_position(position_used, sorted_tiers)
        if tier_idx is None:
            if fallback_mode == "continue":
                tier_pct, tier_label = last_tier_pct, last_tier_label
            elif fallback_mode == "stop":
                # No order 11 is placed. Force-close the maxed-out cycle right here: only
                # cycle_timeout_lp_pct of its unresolved loss becomes permanent-pool debt to be
                # chased later, the rest is written off — then a fresh cycle starts at order 1
                # on the next signal. The backtest keeps running (nothing else about the
                # recovery/tier/win-pool equations changes).
                cycle_timeouts += 1
                transferred_to_pool = temp_loss_before * cycle_timeout_lp_pct
                permanent_loss_pool = perm_pool_before + transferred_to_pool
                temporary_cycle_loss = 0.0
                cycle_order_number = 1
                cycle_id = cycle_id_used + 1
                consecutive_losses = 0

                trade_rows.append({
                    "trade_id": len(trade_rows) + 1, "timestamp": int(result_time), "cycle_id": cycle_id_used,
                    "order_number_in_cycle": position_used, "strategy": strategy,
                    "direction": "UP" if direction == 1 else "DOWN", "result": "CYCLE_TIMEOUT",
                    "recovery_tier": "MAX CYCLE ORDERS REACHED", "recovery_percentage": cycle_timeout_lp_pct,
                    "temporary_loss_before": temp_loss_before, "base_or_cycle_stake": 0.0,
                    "permanent_pool_before": perm_pool_before, "pool_recovery_stake": 0.0,
                    "final_stake": 0.0, "actual_payout": 0.0, "net_profit_or_loss": 0.0,
                    "temporary_loss_after": 0.0, "recovered_from_cycle": 0.0,
                    "transferred_to_pool": transferred_to_pool, "recovered_from_pool": 0.0,
                    "permanent_pool_after": permanent_loss_pool,
                    "win_pool_before": win_pool_before, "win_pool_contribution": 0.0,
                    "win_pool_lp_payment": 0.0, "win_pool_after": win_pool,
                    "balance_before": balance_before, "balance_after": balance_before,
                })
                curve_balance.append(balance_before)
                curve_temp_loss.append(0.0)
                curve_perm_pool.append(permanent_loss_pool)
                curve_stake.append(0.0)
                curve_win_pool.append(win_pool)
                continue
            else:
                halted = True
                halt_reason = (f"Cycle order #{position_used} exceeds Maximum Cycle Orders "
                                f"({maximum_cycle_orders}) and fallback mode is 'Reset only after manual "
                                f"confirmation'. Simulation halted before trade #{len(trade_rows) + 1} — cycle "
                                f"#{cycle_id_used} left with ${temp_loss_before:.2f} of unresolved temporary "
                                f"cycle loss.")
                break

        if position_used == 1:
            lp_addon = (perm_pool_before * static_lp_pct) / net_profit_ratio
            raw_stake = base_stake + lp_addon
            if max_first_order_stake and raw_stake > max_first_order_stake:
                final_stake = max_first_order_stake
            else:
                final_stake = raw_stake
            base_component = min(base_stake, final_stake)
            lp_component = final_stake - base_component
        else:
            recovery_stake = (temp_loss_before * tier_pct) / net_profit_ratio
            final_stake = max(base_stake, recovery_stake)
            base_component = final_stake
            lp_component = 0.0

        recovered_from_cycle = transferred_to_pool = recovered_from_pool = actual_payout = 0.0
        win_pool_contribution = win_pool_lp_payment = 0.0

        if result == "LOSS":
            losses += 1
            temporary_cycle_loss = temp_loss_before + final_stake
            balance = balance_before - final_stake
            net_profit_or_loss = -final_stake
            cycle_order_number = position_used + 1
            consecutive_losses += 1
            max_consecutive_losses = max(max_consecutive_losses, consecutive_losses)
        else:
            wins += 1
            actual_payout = final_stake * net_profit_ratio

            win_pool_contribution = actual_payout * win_pool_contribution_pct
            win_pool = win_pool_before + win_pool_contribution
            remaining_payout = actual_payout - win_pool_contribution

            cycle_share = (base_component / final_stake) if final_stake > 0 else 1.0
            cycle_profit = remaining_payout * cycle_share
            lp_profit = remaining_payout - cycle_profit

            recovered_from_cycle = min(temp_loss_before, cycle_profit)
            transferred_to_pool = max(0.0, temp_loss_before - recovered_from_cycle)
            pool_after_transfer = perm_pool_before + transferred_to_pool

            recovered_from_pool = min(pool_after_transfer, lp_profit)
            permanent_loss_pool = max(0.0, pool_after_transfer - recovered_from_pool)

            # Win pool opportunistically pays down whatever LP remains, after every winning trade.
            win_pool_lp_payment = min(win_pool, permanent_loss_pool * win_pool_lp_coverage_pct)
            permanent_loss_pool = max(0.0, permanent_loss_pool - win_pool_lp_payment)
            win_pool = max(0.0, win_pool - win_pool_lp_payment)

            balance = balance_before + actual_payout
            net_profit_or_loss = actual_payout
            temporary_cycle_loss = 0.0
            cycle_order_number = 1
            cycle_id = cycle_id_used + 1
            consecutive_losses = 0

        peak_balance = max(peak_balance, balance)
        drawdown_pct = ((peak_balance - balance) / peak_balance * 100.0) if peak_balance > 0 else 0.0
        max_drawdown_pct = max(max_drawdown_pct, drawdown_pct)

        trade_rows.append({
            "trade_id": len(trade_rows) + 1, "timestamp": int(result_time), "cycle_id": cycle_id_used,
            "order_number_in_cycle": position_used, "strategy": strategy,
            "direction": "UP" if direction == 1 else "DOWN", "result": result,
            "recovery_tier": tier_label, "recovery_percentage": tier_pct,
            "temporary_loss_before": temp_loss_before, "base_or_cycle_stake": base_component,
            "permanent_pool_before": perm_pool_before, "pool_recovery_stake": lp_component,
            "final_stake": final_stake, "actual_payout": actual_payout,
            "net_profit_or_loss": net_profit_or_loss, "temporary_loss_after": temporary_cycle_loss,
            "recovered_from_cycle": recovered_from_cycle, "transferred_to_pool": transferred_to_pool,
            "recovered_from_pool": recovered_from_pool, "permanent_pool_after": permanent_loss_pool,
            "win_pool_before": win_pool_before, "win_pool_contribution": win_pool_contribution,
            "win_pool_lp_payment": win_pool_lp_payment, "win_pool_after": win_pool,
            "balance_before": balance_before, "balance_after": balance,
        })
        curve_balance.append(balance)
        curve_temp_loss.append(temporary_cycle_loss)
        curve_perm_pool.append(permanent_loss_pool)
        curve_stake.append(final_stake)
        curve_win_pool.append(win_pool)

        if balance <= 0:
            bankrupt = True
            bankrupt_trade_num = len(trade_rows)
            bankrupt_time = int(result_time)
            halted = True
            halt_reason = (f"Account balance reached ${balance:.2f} (<= $0) on trade #{bankrupt_trade_num} — "
                            f"no money left to fund another order. Simulation stopped here.")
            break

    trade_log = pd.DataFrame(trade_rows)
    resolved = wins + losses
    total_trades = wins + losses + neutrals

    # Preview of the NEXT order the live status panel would place, given the state the walk ended in.
    next_position = cycle_order_number
    next_tier_idx, next_tier_pct, next_tier_label = _tier_for_position(next_position, sorted_tiers)
    if next_tier_idx is None:
        if fallback_mode == "continue":
            next_tier_pct, next_tier_label = last_tier_pct, last_tier_label
        else:
            # Only reachable with fallback_mode == "manual" — "stop" always resolves and resets
            # cycle_order_number back to 1 within the same iteration, so it never ends a walk stuck > max.
            next_tier_label = "MAX REACHED — halted, awaiting manual reset"

    if halted and fallback_mode != "continue" and next_tier_idx is None:
        next_base_component = next_lp_component = next_final_stake = None
    elif next_position == 1:
        next_lp_addon = (permanent_loss_pool * static_lp_pct) / net_profit_ratio
        next_raw_stake = base_stake + next_lp_addon
        next_final_stake = min(next_raw_stake, max_first_order_stake) \
            if max_first_order_stake and next_raw_stake > max_first_order_stake else next_raw_stake
        next_base_component = min(base_stake, next_final_stake)
        next_lp_component = next_final_stake - next_base_component
    else:
        next_final_stake = max(base_stake, (temporary_cycle_loss * next_tier_pct) / net_profit_ratio)
        next_base_component = next_final_stake
        next_lp_component = 0.0

    live_status = {
        "cycle_order_number": cycle_order_number, "consecutive_losses": consecutive_losses,
        "temporary_cycle_loss": temporary_cycle_loss, "active_recovery_tier": next_tier_label,
        "active_recovery_percentage": next_tier_pct, "base_or_cycle_stake": next_base_component,
        "permanent_loss_pool": permanent_loss_pool, "static_lp_pct": static_lp_pct,
        "loss_pool_extra_stake": next_lp_component, "final_stake": next_final_stake,
        "max_first_order_stake": max_first_order_stake,
        "maximum_cycle_orders": maximum_cycle_orders, "fallback_mode": fallback_mode,
        "win_pool": win_pool, "win_pool_contribution_pct": win_pool_contribution_pct,
        "win_pool_lp_coverage_pct": win_pool_lp_coverage_pct,
        "halted": halted, "halt_reason": halt_reason,
    }

    loss_streaks = trade_log[trade_log["result"] == "LOSS"].groupby("cycle_id").size().tolist() \
        if not trade_log.empty else []

    summary = {
        "starting_balance": starting_balance, "ending_balance": balance, "net_pnl": balance - starting_balance,
        "roi_pct": ((balance - starting_balance) / starting_balance * 100.0) if starting_balance else 0.0,
        "total_trades": total_trades, "wins": wins, "losses": losses, "neutrals": neutrals,
        "cycle_timeouts": cycle_timeouts,
        "win_rate": (wins / resolved * 100.0) if resolved else 0.0,
        "max_consecutive_losses": max_consecutive_losses,
        "final_temporary_cycle_loss": temporary_cycle_loss, "final_permanent_loss_pool": permanent_loss_pool,
        "final_win_pool": win_pool,
        "max_drawdown_pct": max_drawdown_pct,
        "bankrupt": bankrupt, "bankrupt_trade_num": bankrupt_trade_num, "bankrupt_time": bankrupt_time,
        "halted": halted, "halt_reason": halt_reason,
    }

    curves = {"balance": curve_balance, "temporary_cycle_loss": curve_temp_loss,
              "permanent_loss_pool": curve_perm_pool, "final_stake": curve_stake, "win_pool": curve_win_pool}

    return {"summary": summary, "trade_log": trade_log, "curves": curves, "live_status": live_status,
            "loss_streaks": loss_streaks}
