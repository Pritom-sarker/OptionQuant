"""
btcusd-polymarket-signal-viewer — Streamlit entry point.

VISUALISATION ONLY.
  - No order book (Tab 1). No mock trading. No real orders. No wallet.
  - Tab 1: real BTC/USD 5-minute candles (Binance/Coinbase) + the Pine Script
    signal logic.
  - Tab 2: Polymarket order book paper-trade entry simulator. Completely
    independent from Tab 1 except for two shared fields: the prediction
    direction (GREEN/RED) and the signal candle close price. Tab 2 is a
    paper-trading simulator only — no wallet, no order placement, no API
    trading, ever.
"""
from __future__ import annotations
import time

import pandas as pd
import streamlit as st
from streamlit_autorefresh import st_autorefresh

import config
import btc_price_api as btcapi
import signal_engine as se
import chart_builder as chartb
import polymarket_api
import orderbook_api
import candidate_manager


def _sidebar() -> int:
    """
    Renders sidebar widgets. Signal/stats/chart calculation settings are only
    applied to st.session_state["applied_settings"] when the user clicks
    "Apply Settings" — nothing recalculates from a tentative widget change
    until then. The refresh interval is separate: it takes effect immediately
    since it only controls the refresh mechanism, not any calculation.
    Returns the refresh interval in seconds.
    """
    st.sidebar.header("Pattern")
    mode = st.sidebar.selectbox("Base Pattern", config.PATTERN_OPTIONS,
                                 index=config.PATTERN_OPTIONS.index(config.DEFAULT_PATTERN))

    st.sidebar.header("ATR")
    atr_length = st.sidebar.number_input("ATR Length", min_value=1, value=config.DEFAULT_ATR_LENGTH)
    atr_mult = st.sidebar.number_input("ATR Multiplier", min_value=0.1, step=0.1,
                                        value=config.DEFAULT_ATR_MULTIPLIER)
    atr_sma_length = st.sidebar.number_input("ATR SMA Length", min_value=5,
                                              value=config.DEFAULT_ATR_SMA_LENGTH)

    st.sidebar.header("Filters (active chart signal)")
    f1 = st.sidebar.checkbox("F1  Trend: EMA20 > EMA50 alignment", value=config.DEFAULT_F1_TREND)
    f2 = st.sidebar.checkbox("F2  Volatility: ATR above ATR SMA", value=config.DEFAULT_F2_VOLATILITY)
    f3 = st.sidebar.checkbox("F3  Close Location: close in top/bottom 30%", value=config.DEFAULT_F3_CLOSE_LOC)
    f4 = st.sidebar.checkbox("F4  Continuation: close breaks prior candle", value=config.DEFAULT_F4_CONTINUATION)
    f5 = st.sidebar.checkbox("F5  Anti-chop: EMA spread > ATR x 0.15", value=config.DEFAULT_F5_ANTI_CHOP)

    st.sidebar.header("Edge Detection")
    min_signals = st.sidebar.number_input("Min signals for edge detection", min_value=5, max_value=100,
                                           value=config.DEFAULT_MIN_SIGNALS)

    st.sidebar.header("Visual")
    show_ema = st.sidebar.checkbox("Show EMA 20 / 50 / 200", value=config.DEFAULT_SHOW_EMA)
    show_signals = st.sidebar.checkbox("Show signal markers + WIN/LOSS", value=config.DEFAULT_SHOW_SIGNALS)

    tentative = dict(
        mode=mode, atr_length=int(atr_length), atr_mult=float(atr_mult),
        atr_sma_length=int(atr_sma_length), min_signals=int(min_signals),
        enabled={"f1": f1, "f2": f2, "f3": f3, "f4": f4, "f5": f5},
        show_ema=show_ema, show_signals=show_signals,
    )

    st.sidebar.header("Apply")
    if st.sidebar.button("Apply Settings", use_container_width=True):
        st.session_state["applied_settings"] = tentative
    if "applied_settings" not in st.session_state:
        st.session_state["applied_settings"] = tentative   # seed defaults on first load only

    applied = st.session_state["applied_settings"]
    with st.sidebar.expander("📋 Applied Settings", expanded=False):
        st.write(f"Base Pattern = {applied['mode']}")
        st.write(f"ATR Length = {applied['atr_length']}")
        st.write(f"ATR Multiplier = {applied['atr_mult']}")
        st.write(f"ATR SMA Length = {applied['atr_sma_length']}")
        st.write(f"F1 Trend = {'ON' if applied['enabled']['f1'] else 'OFF'}")
        st.write(f"F2 Volatility = {'ON' if applied['enabled']['f2'] else 'OFF'}")
        st.write(f"F3 Close Location = {'ON' if applied['enabled']['f3'] else 'OFF'}")
        st.write(f"F4 Continuation = {'ON' if applied['enabled']['f4'] else 'OFF'}")
        st.write(f"F5 Anti-chop = {'ON' if applied['enabled']['f5'] else 'OFF'}")
        st.write(f"Min Signals = {applied['min_signals']}")
    if applied != tentative:
        st.sidebar.caption("⚠ Unapplied changes pending — click Apply Settings to use them.")

    st.sidebar.header("Refresh")
    refresh_seconds = st.sidebar.number_input("Refresh Interval Seconds", min_value=2, max_value=300,
                                               value=10)

    return int(refresh_seconds)


_PREDICTION_COLORS = {
    "GREEN": ("#00e676", "#0d2818"),
    "RED": ("#ff1744", "#2e0a10"),
    "UNKNOWN": ("#9e9e9e", "#232323"),
}


def _update_live_prediction(df, pat_dir, act_ok, filters, mode: str, enabled: dict) -> dict | None:
    """
    LIVE MODE — an explicit step machine, kept separate from Historical Mode
    (see _run_backfill_scan_once, which resolves immediately because its
    future candles already exist in the same historical batch — that's
    correct for history, but never appropriate here).

    This function advances by exactly one step only when a genuinely NEW
    candle has closed since the last refresh (detected by comparing the
    newest candle's own timestamp against what was last seen, stored in
    session_state). On a step:
      1. If there's a stored PENDING prediction and its own resolving
         candle now exists in the fetched data, resolve it to WIN/LOSS.
         This can only happen once that candle has actually, fully closed.
      2. Then check the newest candle for a (possibly new) signal.
    If nothing has changed since the last refresh (same newest candle as
    last time), this returns the exact same stored prediction untouched —
    it never re-derives or re-resolves anything from a bulk recompute, so a
    signal can never appear "pre-resolved": it is only ever evaluated one
    real candle-close at a time, exactly like a real-time indicator.
    """
    latest_time = int(df["time"].iloc[-1])
    last_seen_time = st.session_state.get("live_last_seen_time")

    if last_seen_time is not None and latest_time == last_seen_time:
        return st.session_state.get("live_active_prediction")

    n = len(df)
    ap = st.session_state.get("live_active_prediction")

    if ap is not None and ap.get("result") == "PENDING":
        matches = df.index[df["time"] == ap["time"]]
        if len(matches):
            pos = df.index.get_loc(matches[0])
            if pos + 1 < n:   # its resolving candle has now closed
                ap = se.build_signal_table(df, pat_dir, filters, act_ok, mode, enabled, last_n=n - pos)[0]

    if bool(act_ok.iloc[-1]) and (ap is None or ap["time"] != latest_time):
        ap = se.build_signal_table(df, pat_dir, filters, act_ok, mode, enabled, last_n=1)[0]

    st.session_state["live_active_prediction"] = ap
    st.session_state["live_last_seen_time"] = latest_time
    return ap


def _render_prediction_box(row: dict | None) -> None:
    prediction = row["predicted_next"] if row else "UNKNOWN"
    fg, bg = _PREDICTION_COLORS.get(prediction, _PREDICTION_COLORS["UNKNOWN"])

    if row is None:
        detail_html = (
            '<div style="font-size:15px; color:#dddddd; margin-top:10px; line-height:1.7;">'
            "Signal Candle Time: —<br>Signal Candle Close: —<br>"
            "Current Result: —<br>Reason: No valid signal detected in the current candle window."
            "</div>"
        )
    else:
        sig_time = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(row["time"]))
        detail_html = (
            f'<div style="font-size:15px; color:#dddddd; margin-top:10px; line-height:1.7;">'
            f"Signal Candle Time: {sig_time}<br>"
            f"Signal Candle Close: ${row['close']:,.2f}<br>"
            f"Current Result: {row['result']}<br>"
            f"Reason: {row['reason']}</div>"
        )

    st.markdown(
        f'<div style="background-color:{bg}; border:3px solid {fg}; border-radius:12px; '
        f'padding:22px 28px; margin-bottom:20px;">'
        f'<div style="font-size:32px; font-weight:800; color:{fg}; letter-spacing:0.5px;">'
        f'Current Active Prediction: {prediction}</div>{detail_html}</div>',
        unsafe_allow_html=True,
    )


def _run_backfill_scan_once(settings: dict) -> None:
    """
    HISTORICAL MODE. Fetches up to BACKFILL_CANDLES_TARGET historical candles
    and runs the exact same signal logic (signal_engine.py) across all of
    them, resolving every signal's WIN/LOSS immediately — which is correct
    here because every candle's own future candle already exists in this
    same fetched batch. (Live Mode, in _update_live_prediction, never does
    this — it only resolves a signal once its own resolving candle has
    genuinely closed in real time.) Cached in
    session_state so it runs exactly once per session, at startup — never
    re-run on the once-a-minute live refresh.
    """
    if "backfill_rows" in st.session_state:
        return

    backfill_candles = btcapi.fetch_btcusd_candles(config.BACKFILL_CANDLES_TARGET)
    if not backfill_candles:
        st.session_state["backfill_rows"] = []
        st.session_state["backfill_total"] = 0
        return

    bdf = se.candles_to_df(backfill_candles)
    bdf = se.compute_indicators(bdf, settings["atr_length"], settings["atr_sma_length"])
    bpat_dir = se.detect_pattern(bdf, settings["mode"], settings["atr_mult"])
    bfilters = se.compute_filters(bdf, bpat_dir)
    bact_ok = se.compute_active_signal(bpat_dir, bfilters, settings["enabled"])
    brows = se.build_signal_table(bdf, bpat_dir, bfilters, bact_ok, settings["mode"],
                                   settings["enabled"], last_n=len(bdf))

    st.session_state["backfill_rows"] = brows
    st.session_state["backfill_total"] = len(backfill_candles)


def _render_historical_scan() -> None:
    st.subheader("Historical Entry Scan")

    backfill_rows = st.session_state.get("backfill_rows", [])
    total_checked = st.session_state.get("backfill_total", 0)

    if total_checked == 0:
        st.warning("Backfill scan could not fetch any historical candles (Binance and Coinbase both failed).")
        return

    entries = [r for r in backfill_rows if r["predicted_next"] in ("GREEN", "RED")]
    green_entries = [r for r in entries if r["predicted_next"] == "GREEN"]
    red_entries = [r for r in entries if r["predicted_next"] == "RED"]
    entry_rate = f"{len(entries) / total_checked * 100:.1f}%" if total_checked else "—"
    latest_entry_time = (
        time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(entries[-1]["time"])) if entries else "—"
    )

    h1, h2, h3, h4, h5, h6 = st.columns(6)
    h1.metric("Candles Checked", total_checked)
    h2.metric("Entries Found", len(entries))
    h3.metric("GREEN Predictions", len(green_entries))
    h4.metric("RED Predictions", len(red_entries))
    h5.metric("Entry Rate", entry_rate)
    h6.metric("Latest Entry", latest_entry_time)

    show_all = st.checkbox("Show all checked candles")
    rows_to_show = backfill_rows if show_all else entries

    hist_display = [
        {
            "Time": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(r["time"])),
            "Open": round(r["open"], 2), "High": round(r["high"], 2),
            "Low": round(r["low"], 2), "Close": round(r["close"], 2),
            "ATR": round(r["atr"], 2) if pd.notna(r["atr"]) else "—",
            "Body": round(r["body"], 2),
            "Body/ATR": round(r["body_atr_ratio"], 2) if pd.notna(r["body_atr_ratio"]) else "—",
            "Pattern Matched": r["raw_pattern"],
            "Prediction": r["predicted_next"],
            "F1 Trend": r["f1_trend"], "F2 Volatility": r["f2_volatility"],
            "F3 Close Loc": r["f3_close_location"], "F4 Continuation": r["f4_continuation"],
            "F5 Anti-Chop": r["f5_anti_chop"],
            "Final Entry": "YES" if r["predicted_next"] in ("GREEN", "RED") else "NO",
            "Reason": r["reason"],
        }
        for r in reversed(rows_to_show)   # newest first
    ]
    st.caption(f"Showing {len(hist_display)} of {total_checked} scanned candles"
               f"{' (all candles)' if show_all else ' (entries only — check the box above to see every candle)'}.")
    st.dataframe(hist_display, width="stretch", hide_index=True, height=500)


def _render_tab1(refresh_seconds: int) -> None:
    """
    Tab 1 — BTC/USD candle prediction. Untouched: same computations, same
    UI, same order, as before tabs were added. The only addition is exporting
    the current prediction to session_state at the very end of the block
    that already computes it, so Tab 2 can read it — this does not change
    what Tab 1 itself computes or displays.
    """
    st.title("BTCUSD Polymarket Signal Viewer")
    st.caption("⚠️ Visualisation only — no order book, no mock trading, no real orders, no wallet.")

    settings = st.session_state["applied_settings"]
    _run_backfill_scan_once(settings)

    candles = btcapi.fetch_btcusd_candles(config.NUM_CANDLES_TARGET)
    min_needed = max(settings["atr_length"], settings["atr_sma_length"]) + settings["atr_length"]
    if not candles:
        _render_prediction_box(None)
        st.warning("Could not fetch real BTC/USD candle data right now. Retrying next refresh.")
        return

    df = se.candles_to_df(candles)
    df = se.compute_indicators(df, settings["atr_length"], settings["atr_sma_length"])
    pat_dir = se.detect_pattern(df, settings["mode"], settings["atr_mult"])
    filters = se.compute_filters(df, pat_dir)
    act_ok = se.compute_active_signal(pat_dir, filters, settings["enabled"])
    results = se.evaluate_signal_results(df, pat_dir, act_ok)

    # ─── Big bold prediction box (top of page) — LIVE MODE ─────────────────────
    # Never resolves WIN/LOSS before its resolving candle has genuinely
    # closed; advances exactly one step per real candle close. Separate from
    # Historical Mode (_run_backfill_scan_once above), which resolves
    # immediately since its future candles already exist in that batch.
    active_row = _update_live_prediction(df, pat_dir, act_ok, filters, settings["mode"], settings["enabled"])
    _render_prediction_box(active_row)

    # Shared with Tab 2 ONLY: prediction direction (GREEN/RED) + signal
    # candle close price. Tab 2 reads this; Tab 1's own logic/UI above is
    # unaffected by this line.
    st.session_state["tab1_prediction"] = active_row

    if len(candles) < min_needed:
        st.warning(f"Only {len(candles)} candles available so far — need at least {min_needed} "
                   f"for stable ATR/ATR-SMA. Showing what's available; values will stabilize as history builds.")

    # ─── Summary cards — matches btc_polymarket_signal_tester.pine exactly ────
    last = df.iloc[-1]
    stats = se.compute_full_stats(df, pat_dir, act_ok, results, settings["min_signals"])
    caveat = " ⚠" if stats["below_min_signals"] else ""

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Latest BTCUSD Close", f"${last['close']:,.2f}")
    c2.metric("Latest ATR", f"${last['atr']:,.2f}" if pd.notna(last["atr"]) else "—")
    c3.metric("Selected Pattern", settings["mode"])
    c4.metric("Last Refresh", time.strftime("%H:%M:%S"))

    c5, c6, c7, c8 = st.columns(4)
    c5.metric("Raw Pattern Signals", stats["raw_total"])
    c6.metric("Active Signals" + caveat, stats["active_total"])
    c7.metric("UP Signals", stats["up_signals"])
    c8.metric("DOWN Signals", stats["dn_signals"])

    c9, c10, c11, c12 = st.columns(4)
    c9.metric("Win Rate", f"{stats['win_rate']:.1f}% ({stats['wins']}W / {stats['losses']}L)")
    c10.metric("UP Win Rate", f"{stats['up_win_rate']:.1f}%" if stats["up_total"] else "—")
    c11.metric("DOWN Win Rate", f"{stats['dn_win_rate']:.1f}%" if stats["dn_total"] else "—")
    c12.metric("Max Consecutive Losses", stats["max_consecutive_losses"])

    c13, c14, c15, c16 = st.columns(4)
    c13.metric("Current Loss Streak", stats["current_loss_streak"])
    c14.metric("Last Signal", stats["last_signal"])
    c15.metric("Last Result", stats["last_result"])
    c16.metric("Pending Signal", stats["pending_signal"])

    if stats["below_min_signals"]:
        st.caption(f"⚠ Active Signals ({stats['active_total']}) is below the minimum of "
                   f"{settings['min_signals']} set in the sidebar — win rate not yet statistically reliable.")

    # ─── Chart (static matplotlib image, redrawn fresh on every refresh) ───────
    fig = chartb.build_chart(df, act_ok, pat_dir, results, settings["show_ema"],
                              settings["show_signals"], visible_candles=config.CHART_VISIBLE_CANDLES)
    st.pyplot(fig, clear_figure=True)

    # ─── Historical entry scan (computed once at startup, not every refresh) ──
    _render_historical_scan()

    # ─── Current candle breakdown ───────────────────────────────────────────────
    st.subheader("Current Candle — What's Matching, What's Missing")
    breakdown = se.build_condition_breakdown(df, pat_dir, settings["mode"], settings["atr_mult"],
                                              settings["enabled"], idx=-1)
    st.dataframe(
        [{"Condition": b["condition"], "Actual": b["actual"], "Required": b["required"], "Status": b["status"]}
         for b in breakdown],
        width="stretch", hide_index=True,
    )
    st.caption(f"Current candle closed at {time.strftime('%H:%M:%S', time.localtime(last['time']))}.")

    # ─── Last N candle signal check ─────────────────────────────────────────────
    st.subheader(f"Last {config.LAST_N_CANDLES_TABLE} Candle Signal Check")
    rows = se.build_signal_table(df, pat_dir, filters, act_ok, settings["mode"],
                                  settings["enabled"], config.LAST_N_CANDLES_TABLE)
    display_rows = [
        {
            "Time": time.strftime("%H:%M:%S", time.localtime(r["time"])),
            "Open": round(r["open"], 2), "High": round(r["high"], 2),
            "Low": round(r["low"], 2), "Close": round(r["close"], 2),
            "Pattern": r["raw_pattern"],
            "Predicted Next": r["predicted_next"],
            # Kept as a string (not a float) — mixing floats and "pending" in
            # one column breaks Streamlit's Arrow serialization.
            "Next Close (actual)": f"{r['next_close']:,.2f}" if r["next_close"] is not None else "pending",
            "Result": r["result"],
            "Reason": r["reason"],
        }
        for r in reversed(rows)   # newest first
    ]
    st.dataframe(display_rows, width="stretch", hide_index=True)


# ─────────────────────────────────────────────────────────────────────────────
# Tab 2 — Polymarket Order Book Simulator
# Always active, always refreshing, always showing YES/NO order book
# calculations — regardless of whether Tab 1 has a GREEN/RED signal yet.
# Tab 1's signal only changes WHAT Tab 2 focuses on (which side it tracks
# for local-low/recovery + confirmation), never whether Tab 2 shows anything
# at all. Tab 2 never places a real order and never creates a paper trade
# itself — it only ever reports OBSERVE / WAIT / READY FOR PAPER ENTRY.
# ─────────────────────────────────────────────────────────────────────────────

_DECISION_ICONS = {"OBSERVE": "⚪", "WAIT": "🟡", "READY": "🟢"}


def _side_pressure_change(observer: "candidate_manager.ObservationState", side: str) -> float | None:
    hist = observer.yes_pressure_history if side == "YES" else observer.no_pressure_history
    if len(hist) < 2:
        return None
    return hist[-1]["pressure"] - hist[-2]["pressure"]


def _render_status_cards(observer: "candidate_manager.ObservationState", prediction_label: str) -> None:
    yes_m = observer.last_yes_metrics
    no_m = observer.last_no_metrics
    final_decision = {"OBSERVE": "OBSERVE", "WAIT": "WAIT", "READY": "READY FOR PAPER ENTRY"}[observer.last_decision]

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Tab 1 Signal", prediction_label)
    c2.metric("Selected Side", observer.selected_side or "NONE")
    c3.metric("Current YES Price", f"{yes_m.price:.3f}" if yes_m else "—")
    c4.metric("Current NO Price", f"{no_m.price:.3f}" if no_m else "—")

    c5, c6, c7 = st.columns(3)
    c5.metric("YES Pressure", f"{yes_m.pressure:.3f}" if yes_m else "—", delta=observer.yes_trend)
    c6.metric("NO Pressure", f"{no_m.pressure:.3f}" if no_m else "—", delta=observer.no_trend)
    c7.metric("Final Decision", f"{_DECISION_ICONS.get(observer.last_decision, '')} {final_decision}")


def _render_explanation(observer: "candidate_manager.ObservationState", prediction_label: str) -> None:
    st.subheader("Explanation")
    if observer.selected_side is None:
        text = observer.last_reason
    elif observer.last_decision == "READY":
        text = f"Tab 1 predicts {prediction_label}. {observer.last_reason}"
    else:
        text = (f"Tab 1 predicts {prediction_label}, so Tab 2 is watching {observer.selected_side}. "
                f"{observer.selected_side} {observer.last_reason}.")
    st.info(text)


def _side_table_rows(observer: "candidate_manager.ObservationState", side: str) -> list[dict]:
    metrics = observer.last_yes_metrics if side == "YES" else observer.last_no_metrics
    if metrics is None:
        return [{"Field": "Status", "Value": "Waiting for the first order book snapshot..."}]

    trend = observer.yes_trend if side == "YES" else observer.no_trend
    change = _side_pressure_change(observer, side)
    is_selected = observer.selected_side == side
    local_low = observer.selected_side_local_low if is_selected else None
    recovering = observer.is_recovering() if is_selected else False
    decision = observer.last_decision if is_selected else "—"
    reason = observer.last_reason if is_selected else "Not the selected side."

    return [
        {"Field": "Best Bid", "Value": f"{metrics.best_bid:.4f}"},
        {"Field": "Best Ask", "Value": f"{metrics.best_ask:.4f}"},
        {"Field": "Mid Price", "Value": f"{metrics.mid:.4f}"},
        {"Field": "Spread", "Value": f"{metrics.spread:.4f}"},
        {"Field": "Top 5 Bid Depth", "Value": f"{metrics.top5_bid_depth:.2f}"},
        {"Field": "Top 5 Ask Depth", "Value": f"{metrics.top5_ask_depth:.2f}"},
        {"Field": "Weighted Bid Depth", "Value": f"{metrics.weighted_bid_depth:.2f}"},
        {"Field": "Weighted Ask Depth", "Value": f"{metrics.weighted_ask_depth:.2f}"},
        {"Field": "Pressure", "Value": f"{metrics.pressure:.3f}"},
        {"Field": "Pressure Change", "Value": f"{change:+.3f}" if change is not None else "—"},
        {"Field": "Pressure Trend", "Value": trend},
        {"Field": "Liquidity ($)", "Value": f"{metrics.liquidity_usd:.2f}"},
        {"Field": "Local Low After Signal", "Value": f"{local_low:.4f}" if local_low is not None else "—"},
        {"Field": "Recovery Status", "Value": ("Recovering" if recovering else "Not yet recovering")
                                               if is_selected else "—"},
        {"Field": "Decision", "Value": decision},
        {"Field": "Reason", "Value": reason},
    ]


def _render_tab2() -> None:
    st.title("Polymarket Order Book Simulator")
    st.caption("⚠️ Paper-trading simulation only — no wallet, no order placement, no API trading. "
               "Always active — runs independently of Tab 1.")

    # ─── Adaptive scan cadence ───────────────────────────────────────────────
    # 3s while the last known decision was READY (a paper trade would be
    # placed here), 30s otherwise (OBSERVE/WAIT) — read from the *previous*
    # render's observer, since this render hasn't recomputed a decision yet.
    prev_observer = st.session_state.get("tab2_observer")
    prev_decision = prev_observer.last_decision if prev_observer else "OBSERVE"
    refresh_ms = config.OB_REFRESH_MS_FAST if prev_decision == "READY" else config.OB_REFRESH_MS_SLOW
    st_autorefresh(interval=refresh_ms, key="tab2_refresh")

    market = polymarket_api.fetch_btcusd_market()
    if market is None:
        st.warning("No active BTCUSD 5-minute Polymarket market found right now. Retrying next refresh.")
        return

    # ─── Contract rotation — a new 5-minute BTC market slug means the prior
    # contract's window ended and fetch_btcusd_market() has already moved on
    # to the next one. Every chart/graph is reset to empty here so nothing
    # from the expired contract carries over into the new one.
    prev_slug = st.session_state.get("tab2_market_slug")
    rolled_over = prev_slug is not None and prev_slug != market["_slug"]
    if rolled_over:
        st.toast(f"Contract rolled over — now scanning {market['_slug']}. Charts reset.", icon="🔁")
    st.session_state["tab2_market_slug"] = market["_slug"]

    yes_id = market["_yes_token_id"]
    no_id = market["_no_token_id"]
    expiry_time = time.time() + market["_tte"]

    yes_book = orderbook_api.fetch_order_book(yes_id)
    no_book = orderbook_api.fetch_order_book(no_id)

    # ─── Always fetch + record both sides, regardless of Tab 1's state ───────
    observer = st.session_state.get("tab2_observer")
    if observer is None:
        observer = candidate_manager.ObservationState()
        st.session_state["tab2_observer"] = observer
    elif rolled_over:
        observer.reset()   # new contract — every chart/graph starts fresh, empty

    prediction = st.session_state.get("tab1_prediction")
    predicted_label = prediction.get("predicted_next", "UNKNOWN") if prediction else "UNKNOWN"
    observer.observe(yes_book, no_book, prediction if predicted_label in ("GREEN", "RED") else None)

    next_slug = f"{config.COIN}-updown-5m-{market['_window_end_ts'] + 300}"
    next_url = f"{config.POLYMARKET_EVENT_URL_BASE}/{next_slug}"

    st.subheader("Current Market")
    st.markdown(f"**Scanning now:** [{market.get('question', market['_slug'])}]({market['_market_url']})")
    st.markdown(f"**Next contract (in ~{int(market['_tte'])}s):** [{next_slug}]({next_url})")
    st.caption(f"Scan interval: every {refresh_ms // 1000}s "
               f"({'fast — READY' if refresh_ms == config.OB_REFRESH_MS_FAST else 'slow — no trade pending'}). "
               "When this contract's 5-minute window ends, the app automatically switches to the next "
               "BTC Up/Down contract.")

    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("Market", market.get("question", "—")[:28])
    m2.metric("Expiry Time", time.strftime("%H:%M:%S", time.localtime(expiry_time)))
    m3.metric("Current Threshold", "N/A (relative)")
    m4.metric("YES Price", f"{observer.last_yes_metrics.price:.3f}")
    m5.metric("NO Price", f"{observer.last_no_metrics.price:.3f}")
    st.caption("This market resolves on relative price movement (BTC price at window close vs. "
               "window open) — Polymarket does not publish an absolute strike/threshold value for it.")

    _render_status_cards(observer, predicted_label)
    _render_explanation(observer, predicted_label)

    st.subheader("Live Order Book")
    o1, o2 = st.columns(2)
    with o1:
        st.caption("YES")
        st.dataframe(_side_table_rows(observer, "YES"), width="stretch", hide_index=True)
    with o2:
        st.caption("NO")
        st.dataframe(_side_table_rows(observer, "NO"), width="stretch", hide_index=True)

    st.subheader("Chart 1 — Contract Price Movement")
    st.plotly_chart(chartb.build_tab2_price_chart(observer), width="stretch")

    st.subheader("Chart 2 — Order Book Pressure")
    st.plotly_chart(chartb.build_tab2_pressure_chart(observer), width="stretch")

    ch3, ch4 = st.columns(2)
    with ch3:
        st.subheader("Chart 3 — Top-5 Bid/Ask Depth")
        st.plotly_chart(chartb.build_tab2_depth_bar_chart(observer), width="stretch")
    with ch4:
        st.subheader("Chart 4 — Order Book Ladder")
        st.plotly_chart(chartb.build_tab2_ladder_chart(observer), width="stretch")

    st.subheader("Chart 5 — Decision Checklist")
    st.plotly_chart(chartb.build_tab2_checklist(observer), width="stretch")

    st.caption("Tab 2 never creates a real order or a paper trade by itself — it only reports "
               "OBSERVE / WAIT / READY FOR PAPER ENTRY. A future Tab 3 will combine Tab 1's signal "
               "with Tab 2's confirmation into an actual simulated trade.")


def main() -> None:
    st.set_page_config(page_title="BTCUSD Polymarket Signal Viewer", layout="wide")

    refresh_seconds = _sidebar()
    st_autorefresh(interval=refresh_seconds * 1000, key="refresh")

    tab1, tab2 = st.tabs(["Tab 1: BTC/USD Prediction", "Tab 2: Polymarket Order Book Simulator"])
    with tab1:
        _render_tab1(refresh_seconds)
    with tab2:
        _render_tab2()


if __name__ == "__main__":
    main()
