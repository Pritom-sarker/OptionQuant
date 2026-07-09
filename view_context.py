"""
Builds the Jinja2 context dicts for each tab — shared between the full-page
routes (routes/pages.py) and the live-partial routes (routes/api.py) so
there is exactly one place defining what data each tab's template sees.
"""
from __future__ import annotations
import time

import pandas as pd

import config
import money_management as mm
import orderbook_engine as obe
import trade_db
import trade_engine
from engine_state import state


def _fmt(v, nd=2):
    return round(v, nd) if v is not None and pd.notna(v) else "—"


def build_tab1_context() -> dict:
    with state.lock:
        prediction = dict(state.tab1_prediction) if state.tab1_prediction else None
        settings = dict(state.tab1_settings)
        computed = state.tab1_computed
        df = state.tab1_df
        backfill_rows = list(state.backfill_rows)
        backfill_total = state.backfill_total

    if prediction is not None:
        prediction["time_str"] = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(prediction["time"]))

    if df is None or computed is None:
        return {"candles_ok": False, "prediction": prediction, "settings": settings}

    last = df.iloc[-1]
    stats = computed["stats"]
    min_needed = max(settings["atr_length"], settings["atr_sma_length"]) + settings["atr_length"]
    # Sourced from computed["enabled_pattern_names"] (the same background tick that
    # produced the breakdown tables below) rather than the live current settings —
    # settings apply instantly on save, but tab1_computed only refreshes once per
    # ~15s tick, so reading live settings here could show a pattern set that
    # disagrees with what the tables actually reflect for up to that long.
    enabled_patterns = computed.get("enabled_pattern_names", [])
    enabled_patterns_label = " + ".join(enabled_patterns) if enabled_patterns else "None enabled"

    breakdown_groups = [
        {"pattern": g["pattern"], "rows": [
            {"Condition": b["condition"], "Actual": b["actual"], "Required": b["required"], "Status": b["status"]}
            for b in g["rows"]
        ]}
        for g in computed["breakdown"]
    ]

    last_n_rows = [{
        "Time": time.strftime("%H:%M:%S", time.localtime(r["time"])),
        "Open": round(r["open"], 2), "High": round(r["high"], 2),
        "Low": round(r["low"], 2), "Close": round(r["close"], 2),
        "Pattern": r["pattern_name"], "Predicted Next": r["predicted_next"],
        "Next Close (actual)": f"{r['next_close']:,.2f}" if r["next_close"] is not None else "pending",
        "Result": r["result"], "Reason": r["reason"],
    } for r in reversed(computed["last_n_rows"])]

    backfill = None
    if backfill_total:
        entries = [r for r in backfill_rows if r["predicted_next"] in ("GREEN", "RED")]
        green = [r for r in entries if r["predicted_next"] == "GREEN"]
        red = [r for r in entries if r["predicted_next"] == "RED"]
        rows = [{
            "Time": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(r["time"])),
            "Open": round(r["open"], 2), "High": round(r["high"], 2),
            "Low": round(r["low"], 2), "Close": round(r["close"], 2),
            "ATR": _fmt(r["atr"]), "Body": round(r["body"], 2), "Body/ATR": _fmt(r["body_atr_ratio"]),
            "Pattern Matched": r["pattern_name"], "Prediction": r["predicted_next"], "Reason": r["reason"],
        } for r in reversed(entries)]
        backfill = {
            "total_checked": backfill_total, "entries": len(entries), "green": len(green), "red": len(red),
            "entry_rate": f"{len(entries) / backfill_total * 100:.1f}%" if backfill_total else "—",
            "latest_entry_time": (time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(entries[-1]["time"]))
                                   if entries else "—"),
            "shown": len(rows), "rows": rows,
        }

    return {
        "candles_ok": True, "prediction": prediction, "settings": settings, "stats": stats,
        "enabled_patterns_label": enabled_patterns_label,
        "last_close": last["close"], "last_atr": last["atr"] if pd.notna(last["atr"]) else None,
        "last_time_str": time.strftime("%H:%M:%S", time.localtime(last["time"])),
        "last_refresh_str": time.strftime("%H:%M:%S", time.localtime(computed["last_refreshed"])),
        "candles_count": len(df), "min_needed": min_needed,
        "breakdown_groups": breakdown_groups, "last_n_rows": last_n_rows, "last_n_count": config.LAST_N_CANDLES_TABLE,
        "backfill": backfill,
    }


def build_tab2_context() -> dict:
    with state.lock:
        market = state.tab2_market
        observer = state.tab2_observer
        prediction = state.tab1_prediction

    if market is None or observer is None:
        return {"market_ok": False}

    predicted_label = prediction.get("predicted_next", "UNKNOWN") if prediction else "UNKNOWN"
    # market['_window_start_ts'] is the *current* window's start (slugs are
    # keyed by start, not end — see polymarket_api.fetch_btcusd_market's
    # docstring) — the next window's own start is exactly 300s later.
    next_slug = f"{config.COIN}-updown-5m-{market['_window_start_ts'] + 300}"
    next_url = f"{config.POLYMARKET_EVENT_URL_BASE}/{next_slug}"
    expiry_time = time.time() + market["_tte"]

    final_decision = {"OBSERVE": "OBSERVE", "WAIT": "WAIT", "READY": "READY FOR PAPER ENTRY"}[observer.last_decision]
    decision_icon = {"OBSERVE": "⚪", "WAIT": "🟡", "READY": "🟢"}.get(observer.last_decision, "")

    if observer.selected_side is None:
        explanation = observer.last_reason
    elif observer.last_decision == "READY":
        explanation = f"Tab 1 predicts {predicted_label}. {observer.last_reason}"
    else:
        explanation = (f"Tab 1 predicts {predicted_label}, so Tab 2 is watching {observer.selected_side}. "
                        f"{observer.selected_side} {observer.last_reason}.")

    def side_rows(side: str) -> list[dict]:
        metrics = observer.last_yes_metrics if side == "YES" else observer.last_no_metrics
        if metrics is None:
            return [{"Field": "Status", "Value": "Waiting for the first order book snapshot..."}]
        hist = observer.yes_pressure_history if side == "YES" else observer.no_pressure_history
        change = (hist[-1]["pressure"] - hist[-2]["pressure"]) if len(hist) >= 2 else None
        is_selected = observer.selected_side == side
        local_low = observer.selected_side_local_low if is_selected else None
        recovering = observer.is_recovering() if is_selected else False
        return [
            {"Field": "Best Bid", "Value": f"{metrics.best_bid:.4f}"},
            {"Field": "Best Ask", "Value": f"{metrics.best_ask:.4f}"},
            {"Field": "Mid Price", "Value": f"{metrics.mid:.4f}"},
            {"Field": "Spread", "Value": f"{metrics.spread:.4f}"},
            {"Field": "Weighted Bid Depth", "Value": f"{metrics.weighted_bid_depth:.2f}"},
            {"Field": "Weighted Ask Depth", "Value": f"{metrics.weighted_ask_depth:.2f}"},
            {"Field": "Pressure", "Value": f"{metrics.pressure:.3f}"},
            {"Field": "Pressure Change", "Value": f"{change:+.3f}" if change is not None else "—"},
            {"Field": "Pressure Trend", "Value": observer.yes_trend if side == "YES" else observer.no_trend},
            {"Field": "Liquidity ($)", "Value": f"{metrics.liquidity_usd:.2f}"},
            {"Field": "Local Low After Signal", "Value": f"{local_low:.4f}" if local_low is not None else "—"},
            {"Field": "Recovery Status", "Value": ("Recovering" if recovering else "Not yet recovering")
                                                    if is_selected else "—"},
            {"Field": "Decision", "Value": observer.last_decision if is_selected else "—"},
            {"Field": "Reason", "Value": observer.last_reason if is_selected else "Not the selected side."},
        ]

    return {
        "market_ok": True, "market": market, "predicted_label": predicted_label,
        "expiry_str": time.strftime("%H:%M:%S", time.localtime(expiry_time)),
        "next_slug": next_slug, "next_url": next_url, "tte": int(market["_tte"]),
        "yes_price": f"{observer.last_yes_metrics.price:.3f}" if observer.last_yes_metrics else "—",
        "no_price": f"{observer.last_no_metrics.price:.3f}" if observer.last_no_metrics else "—",
        "selected_side": observer.selected_side or "NONE",
        "yes_pressure": f"{observer.last_yes_metrics.pressure:.3f}" if observer.last_yes_metrics else "—",
        "no_pressure": f"{observer.last_no_metrics.pressure:.3f}" if observer.last_no_metrics else "—",
        "yes_trend": observer.yes_trend, "no_trend": observer.no_trend,
        "decision_icon": decision_icon, "final_decision": final_decision, "explanation": explanation,
        "yes_rows": side_rows("YES"), "no_rows": side_rows("NO"),
    }


def _entry_status_label(candidate, trade) -> str:
    labels = {"OPEN": "active", "EARLY_EXIT": "early exit", "SETTLED": "settled"}
    if trade is not None:
        return labels.get(trade.status, trade.status.lower())
    if candidate is None:
        return "—"
    return "limit placed" if candidate.limit_price is not None else "waiting"


def build_tab3_context() -> dict:
    """
    One "item" per active slot — multiple can run concurrently (a fresh
    signal's candidate/trade is never blocked by a previous one still being
    open), so the template loops over `items` instead of assuming a single
    trade. Each item's signal/side comes from *that slot's own* candidate
    (its actual originating signal), not the live global Tab 1 prediction,
    which may have already moved on to a different, unrelated candle.
    """
    with state.lock:
        slots = list(state.tab3_slots)
        market_ok = state.tab3_market_ok
        settings = dict(state.tab3_settings)

    items = []
    for slot in slots:
        candidate, trade = slot["candidate"], slot["trade"]
        latest_cand = candidate.snapshot_history[-1] if candidate.snapshot_history else None
        latest_trade = trade.snapshot_history[-1] if trade and trade.snapshot_history else None
        pressure = (latest_trade["pressure"] if latest_trade else
                    (latest_cand["pressure"] if latest_cand else None))
        items.append({
            "id": candidate.db_id,
            "signal_side": f"{candidate.prediction} / {candidate.selected_side}",
            "status": _entry_status_label(candidate, trade),
            "current_price": (f"{latest_trade['price']:.3f}" if latest_trade else
                               (f"{latest_cand['selected_price']:.3f}" if latest_cand else "—")),
            "pressure_str": f"{pressure:.3f}" if pressure is not None else "—",
            "has_trade": trade is not None,
            "pnl_pct": (f"{latest_trade['pnl_pct'] * 100:+.1f}%" if latest_trade else None),
            "time_remaining": (f"{latest_trade['time_remaining']:.0f}s" if latest_trade else None),
        })

    return {"market_ok": market_ok, "has_activity": bool(items), "items": items,
            "refresh_interval": settings["refresh_interval"], "now_str": time.strftime("%H:%M:%S")}


def build_tab4_context() -> dict:
    """
    Full detail breakdown of every currently active trade/candidate — reads
    the same live state Tab 3 does (no throttled snapshot). Multiple can be
    active concurrently, so this returns one detail block per slot; only
    chart images are regenerated on a slower cadence (chart_refresh_interval),
    handled separately by background_worker.py.
    """
    with state.lock:
        slots = list(state.tab3_slots)

    if not slots:
        return {"has_activity": False}

    return {"has_activity": True, "as_of": time.strftime("%H:%M:%S"),
            "items": [_build_trade_detail_item(s["candidate"], s["trade"]) for s in slots]}


def _build_trade_detail_item(candidate, trade) -> dict:
    """One slot's full Tab 4 detail block — signal/side always come from
    *this* candidate's own original signal, never the live global Tab 1
    prediction (which may have already moved on to an unrelated candle)."""
    predicted_label = candidate.prediction
    latest_cand = candidate.snapshot_history[-1] if candidate.snapshot_history else None
    latest_trade = trade.snapshot_history[-1] if trade and trade.snapshot_history else None

    pf = obe.profit_factor(trade.entry_price) if trade else None

    # Limit Order Position explanation text
    if trade is not None:
        limit_text = (f"Selected side is {trade.selected_side} because Tab 1 predicted {trade.prediction}. "
                       f"Entry filled at {trade.entry_price:.3f} via {trade.entry_mode}. {trade.entry_reason}")
    elif candidate is not None and candidate.limit_price is not None:
        current_ask = f"{latest_cand['best_ask']:.3f}" if latest_cand else "—"
        intro = (f"Selected side is {candidate.selected_side} because Tab 1 predicted {candidate.prediction}. "
                 f"Current {candidate.selected_side} ask is {current_ask}.")
        if candidate.last_mode == "IMMEDIATE":
            limit_text = f"{intro} Pressure is strong, so the bot chooses immediate entry."
        else:
            touched = (f"has touched {candidate.limit_price:.3f}" if candidate.limit_touched
                       else f"has not touched {candidate.limit_price:.3f} yet")
            limit_text = (f"{intro} Pressure is not yet confirming immediate entry, so the bot placed a "
                           f"simulated limit order at {candidate.limit_price:.3f} (the lowest price seen "
                           f"since the signal). The order {touched}.")
    else:
        limit_text = "No limit order placed yet — waiting for the first order book snapshot."

    # Step-by-step narrative
    def describe_pressure(p):
        if p > 0.15:
            return "buyers are stronger than sellers"
        if p < -0.15:
            return "sellers are stronger than buyers"
        return "buyers and sellers are pretty evenly matched"

    side = candidate.selected_side if candidate else trade.selected_side
    direction = predicted_label if predicted_label in ("GREEN", "RED") else \
        (candidate.prediction if candidate else trade.prediction)
    steps = [f"Tab 1 spotted a {direction} signal candle, so the bot decided to only watch the "
             f"{side} side (never the other side)."]
    if candidate is not None:
        steps.append(f"The bot started watching {side}'s order book right after the signal.")
        if candidate.local_low is not None:
            steps.append(f"The lowest {side} price seen since the signal is {candidate.local_low:.3f}.")
        if latest_cand is not None:
            steps.append(f"Right now, {describe_pressure(latest_cand['pressure'])} "
                         f"({side} pressure = {latest_cand['pressure']:.2f}).")
        if candidate.limit_price is not None and trade is None:
            touched = ("has come back down to that level" if candidate.limit_touched
                       else "hasn't come back down to that level yet")
            steps.append(f"The bot placed a simulated resting order at {candidate.limit_price:.3f} "
                         f"(the lowest price seen) — price {touched}.")
    if trade is not None:
        steps.append(f"The bot bought in at {trade.entry_price:.3f} using {trade.entry_mode} entry. "
                     f"{trade.entry_reason}")
        if latest_trade is not None:
            pct = latest_trade["pnl_pct"] * 100
            word = "up" if pct >= 0 else "down"
            steps.append(f"Right now the trade is {word} {abs(pct):.1f}% — current price is "
                         f"{latest_trade['price']:.3f}.")
        if trade.status != "OPEN":
            steps.append(f"The trade is now {trade.status} — final result: {trade.final_result} "
                         f"({trade.return_pct * 100:+.1f}% return). {trade.exit_reason}")
    elif candidate is not None and candidate.last_mode:
        steps.append(f"Current plan: {candidate.last_mode} — {candidate.last_reason}")

    return {
        "id": candidate.db_id, "has_activity": True, "as_of": time.strftime("%H:%M:%S"),
        "signal_direction": predicted_label,
        "selected_side": candidate.selected_side if candidate else trade.selected_side,
        "signal_time_str": (time.strftime("%H:%M:%S", time.localtime(candidate.signal_time))
                             if candidate else "—"),
        "entry_status": _entry_status_label(candidate, trade),
        "limit_price": f"{candidate.limit_price:.3f}" if candidate and candidate.limit_price else "—",
        "best_bid": f"{latest_cand['best_bid']:.3f}" if latest_cand else "—",
        "best_ask": f"{latest_cand['best_ask']:.3f}" if latest_cand else "—",
        "current_price": (f"{latest_trade['price']:.3f}" if latest_trade else
                           (f"{latest_cand['selected_price']:.3f}" if latest_cand else "—")),
        "entry_price": f"{trade.entry_price:.3f}" if trade else "—",
        "stake": f"${trade.stake:.2f}" if trade else "—",
        "profit_factor": f"{pf:.2f}x" if pf is not None else "—",
        "unrealized_pnl": f"{latest_trade['pnl_pct'] * 100:+.1f}%" if latest_trade else "—",
        "pressure": (f"{latest_trade['pressure']:.3f}" if latest_trade else
                     (f"{latest_cand['pressure']:.3f}" if latest_cand else "—")),
        "pressure_trend": latest_trade["pressure_trend"] if latest_trade else "—",
        "reason": trade.entry_reason if trade else (candidate.last_reason if candidate else "—"),
        "limit_text": limit_text,
        "chart_path": (trade.candle_chart_path if trade and trade.candle_chart_path else
                       (candidate.chart_path if candidate else None)),
        "pressure_chart_path": (trade.pressure_chart_path if trade and trade.pressure_chart_path else
                                 (candidate.chart_path.replace("_candle.png", "_pressure.png")
                                  if candidate and candidate.chart_path else None)),
        "depth_chart_path": (trade.depth_chart_path if trade and trade.depth_chart_path else
                              (candidate.chart_path.replace("_candle.png", "_depth.png")
                               if candidate and candidate.chart_path else None)),
        "side_only": side,
        "orderbook_snap": latest_cand,
        "steps": steps,
    }


def _closed_trades_summary(trades: list[dict]) -> dict:
    wins = [t for t in trades if t["final_result"] == "WIN"]
    losses = [t for t in trades if t["final_result"] == "LOSS"]
    early_exits = [t for t in trades if t["status"] == "EARLY_EXIT"]
    profits = [t["pnl"] for t in trades if t["pnl"] is not None]
    entry_prices = [t["entry_price"] for t in trades if t["entry_price"] is not None]
    pfs = [obe.profit_factor(p) for p in entry_prices]
    return {
        "total": len(trades), "wins": len(wins), "losses": len(losses), "early_exits": len(early_exits),
        "win_rate": (len(wins) / len(trades) * 100) if trades else 0.0,
        "total_profit": sum(profits) if profits else 0.0,
        "avg_profit": (sum(profits) / len(profits)) if profits else 0.0,
        "best": max(profits) if profits else None, "worst": min(profits) if profits else None,
        "avg_entry": (sum(entry_prices) / len(entry_prices)) if entry_prices else None,
        "avg_pf": (sum(pfs) / len(pfs)) if pfs else None,
    }


def build_trade_report(row: dict) -> dict:
    report = trade_engine.build_report(row)
    candidate_row = report["candidate"]
    cand_snaps = report["candidate_snapshots"]
    trade_snaps = report["trade_snapshots"]

    danger_rows = []
    for s in trade_snaps:
        danger = "DANGER" if (s["pnl_pct"] <= -0.20 and s["pressure"] < 0) else "SAFE"
        danger_rows.append({
            "Time": time.strftime("%H:%M:%S", time.localtime(s["ts"])),
            "Price": round(s["price"], 3), "PnL %": f"{s['pnl_pct'] * 100:+.1f}%",
            "Pressure": round(s["pressure"], 3), "Danger Status": danger,
        })

    duration = (row["exit_time"] - row["entry_time"]) if row["exit_time"] and row["entry_time"] else None

    return {
        "row": row, "candidate": candidate_row,
        "signal_time_str": (time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(candidate_row["signal_time"]))
                             if candidate_row else "—"),
        "limit_price_at_entry": (f"{cand_snaps[-1]['limit_price']:.3f}"
                                  if cand_snaps and cand_snaps[-1]["limit_price"] is not None else "—"),
        "snapshots_before_entry": len(cand_snaps),
        "entry_snap": cand_snaps[-1] if cand_snaps else None,
        "danger_rows": danger_rows,
        "exit_type": "Early Exit" if row["status"] == "EARLY_EXIT" else ("Expiry" if row["exit_reason"] else None),
        "duration": round(duration, 1) if duration is not None else "—",
        "snapshot_count": len(cand_snaps) + len(trade_snaps),
    }


def build_tab5_context() -> dict:
    """
    Just the summary table — each row links to its own /tab5/trade/{id} page
    (build_trade_detail_context) rather than eagerly building every trade's
    full report here (which used to mean fetching every snapshot for every
    past trade on every single poll, most of which the user never opens).

    Skipped-late candidates (trade_engine.skip_late_candidate — the
    TAB3_ENTRY_DEADLINE_SEC backstop) are merged in for visibility, sorted
    chronologically alongside real trades, but stats are computed from
    all_trades alone — a skipped candidate never risked a stake, so it must
    never touch win rate/profit/PF numbers.
    """
    all_trades = trade_db.fetch_all_trades()
    skipped = trade_db.fetch_skipped_late_candidates()
    if not all_trades and not skipped:
        return {"has_trades": False}
    stats = _closed_trades_summary(all_trades)
    stats["skipped_late"] = len(skipped)

    rows = []
    for t in all_trades:
        icon = "🟢" if t["final_result"] == "WIN" else ("🔴" if t["final_result"] == "LOSS" else "⏳")
        rows.append({
            "kind": "trade", "id": t["id"], "icon": icon, "sort_ts": t["entry_time"] or 0,
            "signal_time_str": time.strftime("%H:%M:%S", time.localtime(t["entry_time"])) if t["entry_time"] else "—",
            "side": "YES" if t["direction"] == 1 else "NO", "entry_mode": t["entry_mode"],
            "entry_price": round(t["entry_price"], 3) if t["entry_price"] is not None else "—",
            "exit_price": round(t["exit_price"], 3) if t["exit_price"] is not None else "—",
            "pnl": round(t["pnl"], 4) if t["pnl"] is not None else "—",
            "result": t["final_result"] or "—", "status": t["status"], "prediction": t["prediction"],
            "return_pct": t["return_pct"],
        })
    for c in skipped:
        rows.append({
            "kind": "skipped", "id": c["id"], "icon": "⏭️", "sort_ts": c["signal_time"] or 0,
            "signal_time_str": time.strftime("%H:%M:%S", time.localtime(c["signal_time"])) if c["signal_time"] else "—",
            "side": "YES" if c["direction"] == 1 else "NO", "entry_mode": "—",
            "entry_price": "—", "exit_price": "—", "pnl": "—",
            "result": "SKIPPED", "status": "SKIPPED_LATE", "prediction": c["prediction"],
            "return_pct": None,
        })
    rows.sort(key=lambda r: r["sort_ts"], reverse=True)
    return {"has_trades": True, "stats": stats, "rows": rows}


def build_money_management_context() -> dict:
    """
    Tab 6 — replays trade_db's real settled trades (oldest -> newest) through
    money_management.run_simulation() using the currently-applied mm
    settings. Recomputed fresh on every call (like Tab 5) rather than cached
    — trade counts here are small enough that replaying the whole history
    every poll is cheap, and it guarantees the balance always reflects the
    latest settled trade plus whatever settings are currently applied.
    """
    settings = dict(state.mm_settings)
    db_rows = trade_db.fetch_all_trades()
    if not db_rows:
        return {"has_trades": False, "settings": settings}

    sim_trades = mm.trades_from_db_rows(db_rows)
    result = mm.run_simulation(sim_trades, settings)
    hourly = mm.hourly_balance_curve(result["trade_log"], settings["starting_balance"])

    summary = result["summary"]
    trade_log = result["trade_log"]
    for row in trade_log:
        row["time_str"] = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(row["time"]))
    return {
        "has_trades": True, "settings": settings, "summary": summary,
        "hourly": hourly, "curves": result["curves"],
        "trade_log": list(reversed(trade_log))[:100],   # newest first for display, capped like Tab 5's recent list
    }


def build_trade_detail_context(trade_id: int) -> dict:
    """The full per-trade report page — what the Tab 5 table's Details link opens."""
    row = trade_db.fetch_trade(trade_id)
    if row is None:
        return {"found": False}
    report = build_trade_report(row)
    icon = "🟢" if row["final_result"] == "WIN" else ("🔴" if row["final_result"] == "LOSS" else "⏳")
    return {
        "found": True, "row": row, "report": report, "icon": icon,
        "side": "YES" if row["direction"] == 1 else "NO",
        "entry_price": round(row["entry_price"], 3) if row["entry_price"] is not None else "—",
        "exit_price": round(row["exit_price"], 3) if row["exit_price"] is not None else "—",
        "pnl": round(row["pnl"], 4) if row["pnl"] is not None else "—",
        "return_pct": row["return_pct"],
    }
