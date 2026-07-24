"""
Tab 6 — Money Management. Tiered cycle/win-pool sizing, live-wired into every
real trade Tab 3 places (see background_worker._tick_tab3 and
next_trade_amount_tiered's docstring). Ported from
pine_strategy_simulator/money_management.py::run_tiered_simulation — same
two-pool model, same win-pool mechanics, same "stop" fallback on hitting
Maximum Cycle Orders — see that module's docstrings for the full mechanics
writeup; this file only adapts it to replay real settled trades instead of a
candle-by-candle backtest walk.

Two deliberate adaptations for live trades:
  1. WIN payout/recovery accounting uses each trade's own REAL payout
     multiple, orderbook_engine.profit_factor(entry_price) — known only once
     a trade has actually settled, exactly like the old fixed/dynamic system
     this replaces already did for its own WIN payoff.
  2. Stake SIZING (recovery_stake / lp_addon, for a trade that hasn't
     happened yet) never divides by a payout ratio — an upcoming trade's own
     entry price/PF is unknowable in advance, so sizing works directly off
     the loss/pool amounts, matching how the old system's sizing formula
     already never referenced a profit factor either (only its WIN-payoff
     step did). Under the validated net_profit_ratio=1.0 examples this
     produces identical numbers to the backtest version, since dividing by
     1.0 changes nothing.
"""
from __future__ import annotations

from typing import Optional

import orderbook_engine as obe


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


def trades_from_db_rows(rows: list[dict]) -> list[dict]:
    """
    Converts trade_db rows (as returned by trade_db.fetch_all_trades() or
    fetch_all_trades_with_signal_time(), newest-first) into the chronological
    (oldest -> newest) {"time","result","entry_price","direction","signal_time"}
    list replay_tiered() expects. The single shared conversion — both Tab 6's
    display and Tab 3's live sizing (see next_trade_amount_tiered()) call this
    so they can never drift out of sync with each other. signal_time is
    display-only metadata (None if rows came from fetch_all_trades(), which
    doesn't join it in) — it never affects sizing/recovery math.
    """
    return [
        {"time": r["entry_time"], "result": r["final_result"], "entry_price": r["entry_price"],
         "direction": "YES" if r["direction"] == 1 else "NO", "signal_time": r.get("signal_time")}
        for r in reversed(rows)
    ]


def replay_tiered(trades: list[dict], money: dict, tiers: list[dict]) -> dict:
    """
    Walks real settled trades (chronological, oldest -> newest) through the
    tiered cycle/win-pool state machine — see this module's docstring and
    pine_strategy_simulator/money_management.py::run_tiered_simulation for
    the full mechanics writeup (temporary_cycle_loss / permanent_loss_pool /
    win_pool, order-1-only LP tax capped by max_first_order_stake, the "stop"
    fallback force-closing a maxed-out cycle instead of halting).

    trades: trades_from_db_rows()'s output.
    money: {starting_balance (display only), base_stake, static_lp_pct,
            max_first_order_stake, maximum_cycle_orders, fallback_mode
            ("stop"|"continue"|"manual"), cycle_timeout_lp_pct,
            win_pool_contribution_pct, win_pool_lp_coverage_pct}
    tiers: validated via validate_tiers() before calling this.

    Returns {"live_status": {...state for the NEXT order, including
    final_stake...}, "trade_log": [...one row per real trade or forced cycle
    timeout...], "summary": {...}}.
    """
    starting_balance = float(money.get("starting_balance", 0.0))
    base_stake = float(money["base_stake"])
    static_lp_pct = float(money["static_lp_pct"])
    max_first_order_stake = money.get("max_first_order_stake")
    max_first_order_stake = float(max_first_order_stake) if max_first_order_stake else None
    maximum_cycle_orders = int(money["maximum_cycle_orders"])
    fallback_mode = money["fallback_mode"]
    cycle_timeout_lp_pct = float(money["cycle_timeout_lp_pct"])
    win_pool_contribution_pct = float(money["win_pool_contribution_pct"])
    win_pool_lp_coverage_pct = float(money["win_pool_lp_coverage_pct"])

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
    wins = losses = cycle_timeouts = 0
    trade_rows = []
    halted = False
    halt_reason = None

    for t in trades:
        result = t["result"]
        if result not in ("WIN", "LOSS"):
            continue   # real trades only ever settle WIN/LOSS — defensive skip

        if halted:
            break

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
                cycle_timeouts += 1
                transferred_to_pool = temp_loss_before * cycle_timeout_lp_pct
                permanent_loss_pool = perm_pool_before + transferred_to_pool
                temporary_cycle_loss = 0.0
                cycle_order_number = 1
                cycle_id = cycle_id_used + 1
                consecutive_losses = 0

                trade_rows.append({
                    "time": t["time"], "signal_time": t.get("signal_time"),
                    "cycle_id": cycle_id_used, "order_number_in_cycle": position_used,
                    "result": "CYCLE_TIMEOUT", "direction": t.get("direction"),
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
                continue
            else:   # "manual" — halts; a live redeploy of money-management state requires
                     # a human to change the tiers/max-orders config, not an automatic reset.
                halted = True
                halt_reason = (f"Cycle order #{position_used} exceeds Maximum Cycle Orders "
                                f"({maximum_cycle_orders}) and fallback mode is 'Reset only after manual "
                                f"confirmation'. No further sizing is possible until the tier configuration "
                                f"or Maximum Cycle Orders is changed — cycle #{cycle_id_used} left with "
                                f"${temp_loss_before:.2f} of unresolved temporary cycle loss.")
                break

        if position_used == 1:
            lp_addon = perm_pool_before * static_lp_pct
            raw_stake = base_stake + lp_addon
            final_stake = min(raw_stake, max_first_order_stake) if max_first_order_stake and raw_stake > max_first_order_stake else raw_stake
            base_component = min(base_stake, final_stake)
            lp_component = final_stake - base_component
        else:
            recovery_stake = temp_loss_before * tier_pct
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
            entry_price = t.get("entry_price")
            trade_profit_factor = obe.profit_factor(entry_price) if entry_price else 0.0
            actual_payout = final_stake * trade_profit_factor

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

            win_pool_lp_payment = min(win_pool, permanent_loss_pool * win_pool_lp_coverage_pct)
            permanent_loss_pool = max(0.0, permanent_loss_pool - win_pool_lp_payment)
            win_pool = max(0.0, win_pool - win_pool_lp_payment)

            balance = balance_before + actual_payout
            net_profit_or_loss = actual_payout
            temporary_cycle_loss = 0.0
            cycle_order_number = 1
            cycle_id = cycle_id_used + 1
            consecutive_losses = 0

        trade_rows.append({
            "time": t["time"], "signal_time": t.get("signal_time"),
            "cycle_id": cycle_id_used, "order_number_in_cycle": position_used,
            "result": result, "direction": t.get("direction"),
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

    # Preview of the NEXT order this configuration would place, given the state the walk ended in.
    next_position = cycle_order_number
    next_tier_idx, next_tier_pct, next_tier_label = _tier_for_position(next_position, sorted_tiers)
    if next_tier_idx is None:
        if fallback_mode == "continue":
            next_tier_pct, next_tier_label = last_tier_pct, last_tier_label
        else:
            next_tier_label = "MAX REACHED — halted, awaiting manual reset"

    if halted and fallback_mode != "continue" and next_tier_idx is None:
        next_base_component = next_lp_component = next_final_stake = None
    elif next_position == 1:
        next_lp_addon = permanent_loss_pool * static_lp_pct
        next_raw_stake = base_stake + next_lp_addon
        next_final_stake = min(next_raw_stake, max_first_order_stake) \
            if max_first_order_stake and next_raw_stake > max_first_order_stake else next_raw_stake
        next_base_component = min(base_stake, next_final_stake)
        next_lp_component = next_final_stake - next_base_component
    else:
        next_final_stake = max(base_stake, temporary_cycle_loss * next_tier_pct)
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

    resolved = wins + losses
    summary = {
        "starting_balance": starting_balance, "ending_balance": balance, "net_pnl": balance - starting_balance,
        "roi_pct": ((balance - starting_balance) / starting_balance * 100.0) if starting_balance else 0.0,
        "total_trades": resolved, "wins": wins, "losses": losses, "cycle_timeouts": cycle_timeouts,
        "win_rate": (wins / resolved * 100.0) if resolved else 0.0,
        "max_consecutive_losses": max_consecutive_losses,
        "final_temporary_cycle_loss": temporary_cycle_loss, "final_permanent_loss_pool": permanent_loss_pool,
        "final_win_pool": win_pool,
    }

    return {"live_status": live_status, "trade_log": trade_rows, "summary": summary}


def hourly_balance_curve(trade_log: list[dict], starting_balance: float) -> list[dict]:
    """
    Balance bucketed to the hour, carried forward through any empty hours —
    "how the balance is changing every hour". Tab 6's chart wants this
    instead of one point per trade, same as before the tiered rewrite —
    trade_log rows still carry "time"/"balance_after", so this needs no
    changes for the new tiered trade_log shape.
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


def drawdown_curve(hourly: list[dict]) -> list[dict]:
    """
    Percent drawdown from the running peak balance at each hourly bucket —
    "how far below its own high-water mark the balance currently is". 0% at
    a new peak, negative otherwise. Takes hourly_balance_curve()'s own output
    so the two charts always share the same time axis/bucketing.
    """
    out = []
    peak = None
    for row in hourly:
        bal = row["balance"]
        peak = bal if peak is None else max(peak, bal)
        dd_pct = ((bal - peak) / peak * 100.0) if peak else 0.0
        out.append({"hour": row["hour"], "drawdown_pct": dd_pct})
    return out


def daily_pnl_curve(trade_log: list[dict]) -> list[dict]:
    """
    Net P/L summed per calendar day (UTC-aligned, matching trade_log's own
    unix timestamps), oldest first. trade_log is already chronological so
    buckets come out in day order without needing a separate sort.
    """
    buckets: dict[int, float] = {}
    order: list[int] = []
    for row in trade_log:
        day = int(row["time"] // 86400 * 86400)
        if day not in buckets:
            buckets[day] = 0.0
            order.append(day)
        buckets[day] += row["net_profit_or_loss"]
    return [{"day": d, "net_pnl": buckets[d]} for d in order]


def project_future_balance(trade_log: list[dict], current_balance: float,
                            horizons_days: tuple = (7, 14, 30)) -> dict:
    """
    Simple linear projection: the average daily P/L rate observed since the
    first settled trade, extended forward. Not a forecast of any individual
    future trade — just "if the average pace so far holds, where does that
    put the balance N days from now". Needs at least 2 trades to have any
    elapsed time to measure a rate over; returns a flat (no-growth)
    projection otherwise rather than dividing by a near-zero time span.
    """
    if len(trade_log) < 2:
        return {"daily_rate": 0.0, "elapsed_days": 0.0,
                "projections": {d: current_balance for d in horizons_days}}
    elapsed_days = max((trade_log[-1]["time"] - trade_log[0]["time"]) / 86400.0, 1.0 / 24)
    total_pnl = sum(r["net_profit_or_loss"] for r in trade_log)
    daily_rate = total_pnl / elapsed_days
    return {
        "daily_rate": daily_rate, "elapsed_days": elapsed_days,
        "projections": {d: current_balance + daily_rate * d for d in horizons_days},
    }


def next_trade_amount_tiered(db_rows: list[dict], money: dict, tiers: list[dict]) -> dict:
    """
    The exact dollar stake Tab 3's trade engine should use for its NEXT real
    trade, plus the full cycle state — the same tiered sizing formula
    replay_tiered() applies to every replayed trade, evaluated against the
    cycle/pool state left behind by every trade settled so far. Like the
    system it replaces, this needs no separately-tracked balance/cycle state
    — it's derived fresh, the same way, every time (see replay_tiered's
    docstring). Returns replay_tiered()'s full result dict; callers that
    just need the stake read result["live_status"]["final_stake"] (None if
    fallback_mode is "manual" and the cycle is halted awaiting a config
    change — callers must treat that as "do not enter").
    """
    trades = trades_from_db_rows(db_rows)
    return replay_tiered(trades, money, tiers)
