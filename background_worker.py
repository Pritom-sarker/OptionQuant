"""
Background engine — three independent daemon threads replacing Streamlit's
autorefresh-triggered reruns. Each loop owns its own cadence and writes into
engine_state.state; FastAPI request handlers only ever *read* that state to
render pages — they never do the fetching/deciding themselves. This is what
makes Tab 3's fast trading-engine tick fully independent of Tab 1/Tab 2: it's
not tied to any browser page being open at all.
"""
from __future__ import annotations
import logging
import os
import threading
import time

import config
import btc_price_api as btcapi
import signal_engine as se
import chart_builder as chartb
import polymarket_api
import orderbook_api
import candidate_manager
import trade_engine
import trade_db
from engine_state import state

log = logging.getLogger("background_worker")


# ─────────────────────────────────────────────────────────────────────────────
# Tab 1 — BTC/USD candle signal. Ported from app.py's _render_tab1 /
# _update_live_prediction / _run_backfill_scan_once — identical logic, just
# writing into `state` instead of st.session_state.
# ─────────────────────────────────────────────────────────────────────────────

def _run_backfill_scan_once(settings: dict) -> None:
    if state.backfill_rows or state.backfill_total:
        return
    backfill_candles = btcapi.fetch_btcusd_candles(config.BACKFILL_CANDLES_TARGET)
    if not backfill_candles:
        return
    bdf = se.candles_to_df(backfill_candles)
    bdf = se.compute_indicators(bdf, settings["atr_length"], settings["atr_sma_length"])
    ev = se.evaluate_patterns(bdf, settings["patterns"], settings["atr_mult"])
    brows = se.build_signal_table(bdf, ev["per_pattern"], ev["combined_dir"], ev["combined_mode"],
                                   ev["combined_act_ok"], last_n=len(bdf))
    with state.lock:
        state.backfill_rows = brows
        state.backfill_total = len(backfill_candles)


def _update_live_prediction(df, per_pattern, combined_dir, combined_mode, combined_act_ok) -> dict | None:
    """
    Deliberately re-evaluates the latest candle fresh on every single tick —
    no "already looked at this candle" cache. Tab 1 refetches its rolling
    candle window from scratch every tick, and small shifts in that window's
    boundaries can make indicators (ATR/EMA, which depend on where the
    window starts) come out very slightly different for the *same* calendar
    candle between two ticks. A cache here would let one unlucky tick that
    lands on the wrong side of a borderline filter permanently lock in a
    missed signal for that candle's whole lifetime, even though a later tick
    with freshly recalculated indicators would have caught it correctly —
    this must self-heal every tick instead.
    """
    latest_time = int(df["time"].iloc[-1])
    with state.lock:
        ap = state.live_active_prediction

    n = len(df)
    just_resolved = False
    if ap is not None and ap.get("result") == "PENDING":
        matches = df.index[df["time"] == ap["time"]]
        if len(matches):
            pos = df.index.get_loc(matches[0])
            if pos + 1 < n:
                ap = se.build_signal_table(df, per_pattern, combined_dir, combined_mode,
                                            combined_act_ok, last_n=n - pos)[0]
                just_resolved = True

    if bool(combined_act_ok.iloc[-1]) and (ap is None or ap["time"] != latest_time):
        ap = se.build_signal_table(df, per_pattern, combined_dir, combined_mode,
                                    combined_act_ok, last_n=1)[0]
    elif not just_resolved and ap is not None and ap["time"] != latest_time and ap.get("result") != "PENDING":
        # The old signal already resolved to WIN/LOSS on a *previous* tick
        # (just_resolved is only true the one tick it happens) and the
        # current candle has no active signal of its own — there is no
        # "current" prediction anymore. Without this, ap would keep holding
        # that old GREEN/RED forever, and Tab 3's engine would keep
        # re-creating a brand new candidate from it every time a trade
        # finished (since candidate/trade reset to None but this stale
        # signal never did).
        ap = None

    with state.lock:
        state.live_active_prediction = ap
    return ap


def _tick_tab1() -> None:
    settings = state.tab1_settings
    _run_backfill_scan_once(settings)

    candles = btcapi.fetch_btcusd_candles(config.NUM_CANDLES_TARGET)
    if not candles:
        with state.lock:
            state.tab1_prediction = None
            state.tab1_df = None
            state.tab1_computed = None
        return

    df = se.candles_to_df(candles)
    df = se.compute_indicators(df, settings["atr_length"], settings["atr_sma_length"])
    ev = se.evaluate_patterns(df, settings["patterns"], settings["atr_mult"])
    per_pattern, combined_dir, combined_mode, combined_act_ok = (
        ev["per_pattern"], ev["combined_dir"], ev["combined_mode"], ev["combined_act_ok"])
    results = se.evaluate_signal_results(df, combined_dir, combined_act_ok)

    active_row = _update_live_prediction(df, per_pattern, combined_dir, combined_mode, combined_act_ok)
    stats = se.compute_full_stats(df, combined_dir, combined_act_ok, results, settings["min_signals"])

    # One breakdown table per *enabled* pattern (not merged into one) — each
    # pattern is evaluated fully independently, so each gets its own visible
    # condition/actual/required/status table for direct comparison.
    breakdown = [
        {"pattern": name, "rows": se.build_condition_breakdown(
            df, p["pat_dir"], name, settings["atr_mult"], p["enabled_filters"], idx=-1)}
        for name, p in per_pattern.items()
    ]

    rows = se.build_signal_table(df, per_pattern, combined_dir, combined_mode, combined_act_ok,
                                  config.LAST_N_CANDLES_TABLE)

    with state.lock:
        state.tab1_prediction = active_row
        state.tab1_df = df
        state.tab1_computed = {
            "pat_dir": combined_dir, "act_ok": combined_act_ok, "results": results,
            "stats": stats, "breakdown": breakdown, "last_n_rows": rows,
            "enabled_pattern_names": list(per_pattern.keys()),
            "last_refreshed": time.time(),
        }


def tab1_loop() -> None:
    while True:
        try:
            _tick_tab1()
        except Exception:
            log.exception("tab1_loop tick failed")
        time.sleep(15)


# ─────────────────────────────────────────────────────────────────────────────
# Tab 2 — Polymarket order book observer. Ported from app.py's _render_tab2.
# ─────────────────────────────────────────────────────────────────────────────

def _tick_tab2() -> None:
    market = polymarket_api.fetch_btcusd_market()
    if market is None:
        with state.lock:
            state.tab2_market = None
        return

    with state.lock:
        prev_slug = state.tab2_market_slug
        observer = state.tab2_observer
    rolled_over = prev_slug is not None and prev_slug != market["_slug"]

    yes_book = orderbook_api.fetch_order_book(market["_yes_token_id"])
    no_book = orderbook_api.fetch_order_book(market["_no_token_id"])

    if observer is None:
        observer = candidate_manager.ObservationState()
    elif rolled_over:
        observer.reset()

    prediction = state.tab1_prediction
    predicted_label = prediction.get("predicted_next", "UNKNOWN") if prediction else "UNKNOWN"
    observer.observe(yes_book, no_book, prediction if predicted_label in ("GREEN", "RED") else None)

    with state.lock:
        state.tab2_market = market
        state.tab2_market_slug = market["_slug"]
        state.tab2_observer = observer
        state.tab2_last_refresh = time.time()


def tab2_loop() -> None:
    while True:
        try:
            _tick_tab2()
        except Exception:
            log.exception("tab2_loop tick failed")
        time.sleep(60)


# ─────────────────────────────────────────────────────────────────────────────
# Tab 3 — Trading engine. Ported from app.py's _run_tab3_engine_tick /
# _save_tab3_charts. No conditional "only when active" gating on the loop
# itself is needed here (unlike the Streamlit version, which had to avoid
# forcing a fast rerun of the *whole app*) — this loop is already isolated in
# its own thread, so it simply sleeps fast while something's running and
# slower while idle, purely to avoid hammering the Polymarket API for no
# reason.
# ─────────────────────────────────────────────────────────────────────────────

def _save_tab3_charts(candidate, trade) -> None:
    os.makedirs(config.TAB3_CHART_DIR, exist_ok=True)
    cand_snaps = candidate.snapshot_history if candidate is not None else []
    trade_snaps = trade.snapshot_history if trade is not None else []

    # Reuse Tab 1's own candle series (state.tab1_df) instead of an
    # independent fresh fetch here — two separate Binance calls, even
    # seconds apart, can drift slightly, which used to let the signal-candle
    # marker land on a different candle than the one that actually generated
    # the signal, making a genuine trade look "disconnected" from any signal
    # on the chart. Reusing the exact df the signal was computed from
    # guarantees the marker always matches the real decision.
    with state.lock:
        candle_df = state.tab1_df

    if candidate is not None:
        candle_path = os.path.join(config.TAB3_CHART_DIR, f"candidate_{candidate.db_id}_candle.png")
        fig = chartb.build_tab3_candle_chart(
            candle_df, candidate.signal_time, direction=candidate.prediction,
            limit_price=candidate.limit_price, entry_price=trade.entry_price if trade else None,
            current_price=(trade_snaps[-1]["price"] if trade_snaps else
                            (cand_snaps[-1]["selected_price"] if cand_snaps else None)),
            exit_price=trade.exit_price if trade else None,
            result=trade.final_result if trade else None,
        )
        chartb.save_figure(fig, candle_path)
        candidate.chart_path = candle_path
        trade_db.update_candidate_chart_path(candidate.db_id, candle_path)

        pressure_path = os.path.join(config.TAB3_CHART_DIR, f"candidate_{candidate.db_id}_pressure.png")
        chartb.save_figure(chartb.build_tab3_pressure_chart(cand_snaps, trade_snaps), pressure_path)

        depth_path = os.path.join(config.TAB3_CHART_DIR, f"candidate_{candidate.db_id}_depth.png")
        chartb.save_figure(chartb.build_tab3_depth_chart(cand_snaps, trade_snaps), depth_path)

        if trade is not None:
            trade.candle_chart_path = candle_path
            trade.pressure_chart_path = pressure_path
            trade.depth_chart_path = depth_path
            trade_db.update_trade_chart_paths(trade.db_id, candle_chart_path=candle_path,
                                               pressure_chart_path=pressure_path, depth_chart_path=depth_path)

    if trade is not None and trade_snaps:
        pnl_path = os.path.join(config.TAB3_CHART_DIR, f"trade_{trade.db_id}_pnl.png")
        chartb.save_figure(chartb.build_tab3_pnl_chart(trade_snaps), pnl_path)
        trade.pnl_chart_path = pnl_path
        trade_db.update_trade_chart_paths(trade.db_id, pnl_chart_path=pnl_path)


_CANDLE_MATCH_TOLERANCE_SEC = 5   # Binance's closeTime truncates to :59, 1s off Polymarket's exact
                                   # on-the-minute window boundary — match within a few seconds, not exactly.


def _find_window_candle(market_slug: str) -> dict | None:
    """
    The real BTC candle spanning this market's exact 5-minute window — used
    to settle trades against actual price movement rather than Polymarket's
    order book (which is empty and unusable once the market has closed).
    Checks Tab 1's already-fetched rolling candles first (no extra network
    call, since Tab 1 ticks every 15s anyway); falls back to a fresh fetch
    only if that window has already scrolled out of Tab 1's window.

    market_slug encodes the window's START (verified against live Polymarket
    data — see polymarket_api.fetch_btcusd_market's docstring), so the
    candle that actually resolves this market is the one whose CLOSE time is
    start + 300, not the raw slug timestamp itself. Matching against the raw
    timestamp instead (an earlier version of this function did) settles
    against the candle immediately *before* the real window — i.e. the
    signal candle itself — which is a completely different, wrong result.
    """
    window_start_ts = trade_engine.parse_window_start_ts(market_slug)
    window_close_ts = window_start_ts + 300

    with state.lock:
        df = state.tab1_df

    if df is not None:
        diffs = (df["time"] - window_close_ts).abs()
        if len(diffs) and diffs.min() <= _CANDLE_MATCH_TOLERANCE_SEC:
            row = df.loc[diffs.idxmin()]
            return {"open": float(row["open"]), "close": float(row["close"])}

    for c in btcapi.fetch_btcusd_candles(config.BACKFILL_CANDLES_TARGET):
        if abs(c["time"] - window_close_ts) <= _CANDLE_MATCH_TOLERANCE_SEC:
            return c
    return None


def _tick_tab3() -> None:
    """
    Multiple candidates/trades can be active concurrently — a fresh signal is
    never blocked just because a previous trade hasn't settled yet. Deduped
    by signal_time AND market_slug: each candle gets at most one order, and
    each Polymarket contract gets at most one order, ever, regardless of how
    many are already open. Every slot ({"candidate", "trade"}) is processed
    independently each tick; a slot is dropped once its candidate expires
    unentered or its trade settles/early-exits.
    """
    settings = state.tab3_settings
    with state.lock:
        slots = list(state.tab3_slots)

    market = polymarket_api.fetch_btcusd_market()
    if market is None and not slots:
        with state.lock:
            state.tab3_market_ok = False
        return

    tab1_prediction = state.tab1_prediction
    predicted_label = tab1_prediction.get("predicted_next", "UNKNOWN") if tab1_prediction else "UNKNOWN"

    # signal_time always comes straight from Tab 1's own candle row (never a
    # cached/derived value here) — this is what guarantees a candidate can
    # only ever be created for the exact candle a signal actually fired on,
    # never a stale carry-over from a previous tick.
    if predicted_label in ("GREEN", "RED") and tab1_prediction is not None and market is not None:
        signal_time = int(tab1_prediction["time"])
        market_slug = market["_slug"]
        duplicate = any(
            s["candidate"].signal_time == signal_time or s["candidate"].market_slug == market_slug
            for s in slots
        )
        if not duplicate:
            log.info("[Tab3] NEW SIGNAL candle=%s (%s) direction=%s contract=%s reason=%r -> creating candidate",
                      signal_time, time.strftime("%H:%M:%S", time.localtime(signal_time)),
                      predicted_label, market_slug, tab1_prediction.get("reason", ""))
            candidate = trade_engine.create_candidate(tab1_prediction, market)
            slots.append({"candidate": candidate, "trade": None})
        else:
            log.debug("[Tab3] Signal candle=%s / contract=%s already has an order this cycle — skipping duplicate.",
                       signal_time, market_slug)
    elif not slots:
        log.debug("[Tab3] No valid signal this tick (predicted_label=%s) — trade skipped.", predicted_label)

    still_active = []
    for slot in slots:
        candidate, trade = slot["candidate"], slot["trade"]

        if trade is None:
            if candidate.is_expired():
                log.info("[Tab3] Candidate candle=%s (%s) EXPIRED with no entry — "
                          "No valid signal converted to a trade — trade skipped.",
                          candidate.signal_time, time.strftime("%H:%M:%S", time.localtime(candidate.signal_time)))
                trade_engine.expire_candidate(candidate)
                continue   # drop this slot — never entered, nothing more to track

            yes_book = orderbook_api.fetch_order_book(candidate.yes_token_id)
            no_book = orderbook_api.fetch_order_book(candidate.no_token_id)
            snap = trade_engine.record_candidate_snapshot(candidate, yes_book, no_book, settings)
            if snap["decision"] == "BUY":
                trade = trade_engine.enter_trade(candidate, snap, settings)
                slot["trade"] = trade
                log.info("[Tab3] TRADE OPENED candle=%s (%s) side=%s mode=%s entry_price=%.3f reason=%r",
                          candidate.signal_time, time.strftime("%H:%M:%S", time.localtime(candidate.signal_time)),
                          trade.selected_side, trade.entry_mode, trade.entry_price, trade.entry_reason)
            else:
                log.debug("[Tab3] Candidate candle=%s still OBSERVING — decision=%s mode=%s price=%.3f reason=%r",
                           candidate.signal_time, snap["decision"], snap["mode"], snap["selected_price"], snap["reason"])
            still_active.append(slot)
            continue

        if trade.status != "OPEN":
            continue   # already settled/early-exited elsewhere — drop

        yes_book = orderbook_api.fetch_order_book(trade.yes_token_id)
        no_book = orderbook_api.fetch_order_book(trade.no_token_id)
        trade_engine.record_trade_snapshot(trade, yes_book, no_book)
        should_exit, exit_reason = trade_engine.check_early_exit(trade, settings)
        if should_exit:
            log.info("[Tab3] TRADE EARLY EXIT candle=%s entry_price=%.3f reason=%r",
                      trade.entry_time, trade.entry_price, exit_reason)
            trade_engine.settle_early_exit(trade, exit_reason)
            continue   # settled — drop
        elif time.time() >= trade.expiry_time:
            window_candle = _find_window_candle(trade.market_slug)
            settled = trade_engine.settle_at_expiry(trade, window_candle)
            if settled is not None:
                log.info("[Tab3] TRADE SETTLED signal_candle=%s entry_price=%.3f exit_price=%.3f result=%s",
                          trade.entry_time, trade.entry_price, trade.exit_price, trade.final_result)
                continue   # settled — drop
        still_active.append(slot)

    # Chart images are comparatively expensive (candle fetch + matplotlib +
    # disk I/O) — regenerate them on their own slower cadence, once per
    # active slot. Values (state.tab3_slots below) are updated every tick,
    # always — Tab 3 and Tab 4 are both fully live now; only the pictures
    # are throttled.
    now = time.time()
    should_refresh_charts = (now - state.tab3_last_chart_refresh) >= settings["chart_refresh_interval"]
    if should_refresh_charts:
        for slot in still_active:
            _save_tab3_charts(slot["candidate"], slot["trade"])

    with state.lock:
        state.tab3_slots = still_active
        state.tab3_market_ok = True
        if should_refresh_charts:
            state.tab3_last_chart_refresh = now


def tab3_loop() -> None:
    while True:
        try:
            _tick_tab3()
        except Exception:
            log.exception("tab3_loop tick failed")
        with state.lock:
            active = bool(state.tab3_slots)
            interval = state.tab3_settings["refresh_interval"]
        time.sleep(interval if active else 10)


def start_background_threads() -> None:
    threading.Thread(target=tab1_loop, daemon=True, name="tab1_loop").start()
    threading.Thread(target=tab2_loop, daemon=True, name="tab2_loop").start()
    threading.Thread(target=tab3_loop, daemon=True, name="tab3_loop").start()
