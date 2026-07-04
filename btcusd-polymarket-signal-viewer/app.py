"""
btcusd-polymarket-signal-viewer — Streamlit entry point.

VISUALISATION ONLY.
  - No order book. No mock trading. No real orders. No wallet.
  - Real BTC/USD 5-minute candles (Binance/Coinbase) + the Pine Script signal
    logic. Polymarket is not used here — it's reserved for a later
    market-odds/order-book feature (see polymarket_api.py).
"""
from __future__ import annotations
import sys
import time
import traceback

import pandas as pd
import streamlit as st
from streamlit_autorefresh import st_autorefresh

import config
import btc_price_api as btcapi
import signal_engine as se
import chart_builder as chartb


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

    try:
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
    except Exception as e:
        err_detail = traceback.format_exc()
        print(f"[app] Backfill scan failed: {type(e).__name__}: {e}\n{err_detail}", file=sys.stderr)
        st.session_state["backfill_rows"] = []
        st.session_state["backfill_total"] = 0
        st.session_state["backfill_error"] = f"{type(e).__name__}: {e}"


def _render_historical_scan() -> None:
    st.subheader("Historical Entry Scan")

    backfill_rows = st.session_state.get("backfill_rows", [])
    total_checked = st.session_state.get("backfill_total", 0)
    backfill_error = st.session_state.get("backfill_error")

    if total_checked == 0:
        if backfill_error:
            st.error(f"Backfill scan failed with an unexpected error: {backfill_error}\n\n"
                     "Check the service logs for the full traceback.")
        else:
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


def main() -> None:
    st.set_page_config(page_title="BTCUSD Polymarket Signal Viewer", layout="wide")

    st.title("BTCUSD Polymarket Signal Viewer")
    st.caption("⚠️ Visualisation only — no order book, no mock trading, no real orders, no wallet.")

    refresh_seconds = _sidebar()
    st_autorefresh(interval=refresh_seconds * 1000, key="refresh")

    settings = st.session_state["applied_settings"]
    _run_backfill_scan_once(settings)

    try:
        candles = btcapi.fetch_btcusd_candles(config.NUM_CANDLES_TARGET)
    except Exception as e:
        err_detail = traceback.format_exc()
        print(f"[app] Live candle fetch raised an unexpected exception: {type(e).__name__}: {e}\n{err_detail}",
              file=sys.stderr)
        candles = []

    min_needed = max(settings["atr_length"], settings["atr_sma_length"]) + settings["atr_length"]
    if not candles:
        _render_prediction_box(None)
        st.warning("Could not fetch real BTC/USD candle data right now. Retrying next refresh.")
        return

    try:
        df = se.candles_to_df(candles)
        df = se.compute_indicators(df, settings["atr_length"], settings["atr_sma_length"])
        pat_dir = se.detect_pattern(df, settings["mode"], settings["atr_mult"])
        filters = se.compute_filters(df, pat_dir)
        act_ok = se.compute_active_signal(pat_dir, filters, settings["enabled"])
        results = se.evaluate_signal_results(df, pat_dir, act_ok)
    except Exception as e:
        err_detail = traceback.format_exc()
        print(f"[app] Signal computation failed: {type(e).__name__}: {e}\n{err_detail}", file=sys.stderr)
        _render_prediction_box(None)
        st.error(f"Signal computation failed: {type(e).__name__}: {e}\n\n"
                 "Check the service logs for the full traceback.")
        return

    # ─── Big bold prediction box (top of page) — LIVE MODE ─────────────────────
    # Never resolves WIN/LOSS before its resolving candle has genuinely
    # closed; advances exactly one step per real candle close. Separate from
    # Historical Mode (_run_backfill_scan_once above), which resolves
    # immediately since its future candles already exist in that batch.
    active_row = _update_live_prediction(df, pat_dir, act_ok, filters, settings["mode"], settings["enabled"])
    _render_prediction_box(active_row)

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


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        err_detail = traceback.format_exc()
        print(f"[app] Fatal error in main(): {type(e).__name__}: {e}\n{err_detail}", file=sys.stderr)
        # Surface the error in the UI rather than silently crashing.
        # st.set_page_config may or may not have been called yet, so we
        # attempt a best-effort render — Streamlit will ignore a second
        # set_page_config call if it was already issued.
        try:
            st.set_page_config(page_title="BTCUSD Signal Viewer — Startup Error")
        except Exception:
            pass
        st.error(
            f"**The app encountered a fatal startup error and could not initialise.**\n\n"
            f"`{type(e).__name__}: {e}`\n\n"
            "Check the service logs for the full traceback."
        )
