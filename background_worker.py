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
import money_management as mm
import trade_engine
import trade_db
from engine_state import state

log = logging.getLogger("background_worker")

_last_tick_start: dict = {}   # loop name -> start ts of its previous tick, module-local (no lock needed, one writer per key)


def _record_tick(name: str, tick_start: float, tick_duration: float, interval: float) -> None:
    """
    Engine Health bookkeeping — see engine_state.AppState.engine_health's
    docstring. gap_sec is measured from the previous tick's START to this
    tick's START (not end-to-end), so it directly reflects real scheduling
    cadence regardless of how long any individual tick took.
    """
    prev_start = _last_tick_start.get(name)
    gap_sec = (tick_start - prev_start) if prev_start is not None else None
    _last_tick_start[name] = tick_start
    with state.lock:
        state.engine_health[name] = {
            "last_tick_start": tick_start, "tick_duration": tick_duration,
            "gap_sec": gap_sec, "interval": interval,
        }
    if gap_sec is not None and gap_sec > interval * 3:
        log.warning("[EngineHealth] %s fell behind schedule: gap=%.1fs (interval=%.1fs, last tick took %.2fs)",
                    name, gap_sec, interval, tick_duration)


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
        # The real, fully-closed result for this window has just landed —
        # if Early Entry had already staged a provisional guess for the same
        # window (matched by close time), compare them. A trade may already
        # be open off that provisional signal; per the configured behavior it
        # is never cancelled/exited here, this is purely a log line so
        # mismatches are visible.
        early = state.tab1_early_prediction
        early_resolved = (early is not None and active_row is not None
                          and int(early["time"]) == int(active_row["time"]))
        if early_resolved and early.get("predicted_next") != active_row.get("predicted_next"):
            log.warning("[Tab1] EARLY ENTRY MISMATCH window=%s provisional=%s actual=%s — "
                        "any trade already entered off the provisional signal stays open.",
                        active_row["time"], early.get("predicted_next"), active_row.get("predicted_next"))
        if early_resolved:
            state.tab1_early_prediction = None   # this window is resolved for real now

        # A provisional signal still staged for a window that hasn't closed
        # yet (early is not None and NOT early_resolved) must never be
        # stomped here — this tick's active_row only ever reflects closed
        # candles, so it can't possibly know about that still-forming
        # window yet. Without this guard, tab1_loop's own 2s cadence
        # overwrote tab1_early_loop's provisional write within ~2s of it
        # being set, every time, making Early Entry effectively invisible.
        if early is None or early_resolved:
            state.tab1_prediction = active_row
        state.tab1_df = df
        state.tab1_computed = {
            "pat_dir": combined_dir, "act_ok": combined_act_ok, "results": results,
            "stats": stats, "breakdown": breakdown, "last_n_rows": rows,
            "enabled_pattern_names": list(per_pattern.keys()),
            "last_refreshed": time.time(),
        }


def _tick_tab1_early() -> None:
    """
    Opt-in Early Entry (default OFF, see config.DEFAULT_TAB1_EARLY_ENTRY_
    ENABLED): checks the still-forming candle's live OHLC against the same
    pattern pipeline _tick_tab1 uses. Two separate things happen here, on
    different gates:

    1. state.tab1_forming_breakdown is recomputed on EVERY tick this candle
       is still open, regardless of how much time is left — this is pure
       visibility (Tab 1's live "how is the running candle trending" table),
       never used to decide anything.
    2. Actually staging a signal (state.tab1_early_prediction /
       state.tab1_prediction, tagged "provisional") only happens in the last
       `early_entry_lead_sec` seconds before close — the confirmation gate.
       A pattern that already matches at minute 3 is visible via (1) the
       whole time, but is never acted on until (2)'s window opens.

    Runs on its own dedicated loop (tab1_early_loop, much tighter cadence
    than tab1_loop — see TAB1_EARLY_POLL_INTERVAL_SEC) since it only needs
    the already-cached state.tab1_df, not a full _tick_tab1() re-run. A
    genuine real closed-candle result (written by _tick_tab1, on its own
    separate loop) is never stomped by a stale provisional one for a
    *different*, already-passed window; see the "not within lead window"
    branch below.
    """
    settings = state.tab1_settings
    if not settings.get("early_entry_enabled"):
        with state.lock:
            state.tab1_forming_breakdown = None
        return

    with state.lock:
        closed_candles_df = state.tab1_df
    if closed_candles_df is None:
        return

    forming = btcapi.fetch_forming_btcusd_candle()
    if forming is None:
        return

    lead = settings.get("early_entry_lead_sec", config.DEFAULT_TAB1_EARLY_ENTRY_LEAD_SEC)
    seconds_to_close = forming["time"] - time.time()
    seconds_from_open = config.CANDLE_TIMEFRAME_MIN * 60 - seconds_to_close

    # "Resolved" must be checked against the actual closed-candle data, never
    # against state.tab1_prediction's own time — Early Entry itself writes
    # tab1_prediction with time == forming["time"] the moment it stages a
    # signal, so comparing against that field would make this look
    # "resolved" one tick after staging, even though the candle is still
    # very much still forming. Comparing against the latest CLOSED candle's
    # own time is the only check that can't be self-fulfilled this way.
    latest_closed_time = int(closed_candles_df["time"].iloc[-1]) if len(closed_candles_df) else None
    resolved = latest_closed_time is not None and latest_closed_time >= int(forming["time"])
    if resolved:
        with state.lock:
            state.tab1_forming_breakdown = None   # real data has landed for this window — nothing left to preview
        return   # the real closed-candle signal for this exact window already landed

    closed_candles = closed_candles_df[["time", "open", "high", "low", "close", "volume"]].to_dict("records")
    bdf = se.candles_to_df(closed_candles + [forming])
    bdf = se.compute_indicators(bdf, settings["atr_length"], settings["atr_sma_length"])
    ev = se.evaluate_patterns(bdf, settings["patterns"], settings["atr_mult"])
    row = se.build_signal_table(bdf, ev["per_pattern"], ev["combined_dir"], ev["combined_mode"],
                                 ev["combined_act_ok"], last_n=1)[0]

    with state.lock:
        state.tab1_forming_breakdown = {
            "window_time": forming["time"],
            "seconds_from_open": seconds_from_open, "seconds_to_close": seconds_to_close,
            "predicted_next": row["predicted_next"], "reason": row["reason"],
            "within_action_window": 0 < seconds_to_close <= lead,
            "breakdown": [
                {"pattern": name, "rows": se.build_condition_breakdown(
                    bdf, p["pat_dir"], name, settings["atr_mult"], p["enabled_filters"], idx=-1)}
                for name, p in ev["per_pattern"].items()
            ],
        }

    if not (0 < seconds_to_close <= lead):
        with state.lock:
            if state.tab1_early_prediction is not None and int(state.tab1_early_prediction["time"]) != int(forming["time"]):
                state.tab1_early_prediction = None   # stale provisional from a window that already passed
        return

    with state.lock:
        already_staged = (state.tab1_early_prediction is not None
                          and int(state.tab1_early_prediction["time"]) == int(forming["time"]))
        if row["predicted_next"] in ("GREEN", "RED"):
            row["provisional"] = True
            # confirmed_at is the FIRST tick this window matched, not "now" —
            # otherwise it would keep sliding forward every tick for as long
            # as the match holds, making the "confirmed at" time on the UI
            # banner meaningless.
            row["confirmed_at"] = (state.tab1_early_prediction["confirmed_at"]
                                    if already_staged else time.time())
            if not already_staged:
                log.info("[Tab1] EARLY ENTRY %s matched %.0fs before candle close (window=%s) — staged for Tab 3",
                          row["predicted_next"], seconds_to_close,
                          time.strftime("%H:%M:%S", time.localtime(forming["time"])))
            state.tab1_early_prediction = row
            state.tab1_prediction = row
        else:
            state.tab1_early_prediction = None


def tab1_loop() -> None:
    while True:
        t0 = time.time()
        try:
            _tick_tab1()
        except Exception:
            log.exception("tab1_loop tick failed")
        _record_tick("tab1", t0, time.time() - t0, config.TAB1_POLL_INTERVAL_SEC)
        time.sleep(config.TAB1_POLL_INTERVAL_SEC)


def tab1_early_loop() -> None:
    """
    Early Entry gets its own dedicated, much tighter polling loop instead of
    riding along on tab1_loop's heavier cadence — see config.
    TAB1_EARLY_POLL_INTERVAL_SEC's docstring for why. Safe to run
    concurrently with tab1_loop: _tick_tab1_early() only ever reads
    state.tab1_df (written by _tick_tab1()) and writes state.tab1_prediction
    / state.tab1_early_prediction, all under state.lock.
    """
    while True:
        t0 = time.time()
        try:
            _tick_tab1_early()
        except Exception:
            log.exception("tab1_early_loop tick failed")
        _record_tick("tab1_early", t0, time.time() - t0, config.TAB1_EARLY_POLL_INTERVAL_SEC)
        time.sleep(config.TAB1_EARLY_POLL_INTERVAL_SEC)


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
        t0 = time.time()
        try:
            _tick_tab2()
        except Exception:
            log.exception("tab2_loop tick failed")
        _record_tick("tab2", t0, time.time() - t0, 60)
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
    # never a stale carry-over from a previous tick. The candidate's market
    # is ALWAYS fetched by fetch_market_for_window(signal_time) — the exact
    # contract that candle's signal is about — never the generic "market"
    # above (which only answers "is anything active at all right now" and
    # can point at a later window than the signal actually predicts if
    # there's any delay before the candidate gets created).
    if predicted_label in ("GREEN", "RED") and tab1_prediction is not None and market is not None:
        signal_time = int(tab1_prediction["time"])
        # Checked against the DB, not just the currently-active slots list —
        # see trade_db.candidate_exists_for_signal's docstring for why a
        # memory-only check lets a dropped (settled/expired/skipped-late)
        # candidate's signal get re-created every tick until the real candle
        # closes, which is what caused dozens of duplicate SKIPPED_LATE rows
        # for one window before this fix.
        duplicate = (any(s["candidate"].signal_time == signal_time for s in slots)
                     or trade_db.candidate_exists_for_signal(signal_time))
        if duplicate:
            log.debug("[Tab3] Signal candle=%s already has a candidate — skipping duplicate.", signal_time)
        else:
            target_market = polymarket_api.fetch_market_for_window(signal_time)
            if target_market is None:
                log.debug("[Tab3] Signal candle=%s (%s) has no matching Polymarket market available yet — "
                           "will retry next tick.", signal_time, time.strftime("%H:%M:%S", time.localtime(signal_time)))
            elif any(s["candidate"].market_slug == target_market["_slug"] for s in slots):
                log.debug("[Tab3] Contract %s already has an order this cycle — skipping duplicate.",
                           target_market["_slug"])
            else:
                log.info("[Tab3] NEW SIGNAL candle=%s (%s) direction=%s contract=%s reason=%r -> creating candidate",
                          signal_time, time.strftime("%H:%M:%S", time.localtime(signal_time)),
                          predicted_label, target_market["_slug"], tab1_prediction.get("reason", ""))
                candidate = trade_engine.create_candidate(tab1_prediction, target_market)
                slots.append({"candidate": candidate, "trade": None})
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

            if candidate.entry_deadline_passed(settings["entry_deadline_sec"]):
                log.info("[Tab3] Candidate candle=%s (%s) SKIPPED — window opened %.0fs ago, past the "
                          "%ds entry deadline. No stake risked.",
                          candidate.signal_time, time.strftime("%H:%M:%S", time.localtime(candidate.signal_time)),
                          time.time() - candidate.signal_time, settings["entry_deadline_sec"])
                trade_engine.skip_late_candidate(candidate, settings["entry_deadline_sec"])
                continue   # drop this slot — too late to enter fairly, nothing more to track

            yes_book = orderbook_api.fetch_order_book(candidate.yes_token_id)
            no_book = orderbook_api.fetch_order_book(candidate.no_token_id)
            snap = trade_engine.record_candidate_snapshot(candidate, yes_book, no_book, settings)
            if snap["decision"] == "BUY":
                # Every real trade's stake is sized by Tab 6's tiered Money
                # Management settings, never a flat number — same sizing
                # formula Tab 6 replays historically, evaluated live against
                # the cycle/pool state left behind by every trade settled so
                # far (see money_management.next_trade_amount_tiered's
                # docstring for why this needs no separately-tracked state).
                with state.lock:
                    mm_settings = dict(state.mm_settings)
                    mm_tiers = list(state.mm_tiers)
                mm_result = mm.next_trade_amount_tiered(trade_db.fetch_all_trades(), mm_settings, mm_tiers)
                live_stake = mm_result["live_status"]["final_stake"]
                if live_stake is None:
                    # fallback_mode == "manual" and the cycle is halted awaiting a config
                    # change — do not risk a stake until a human resolves it.
                    log.warning("[Tab3] Candidate candle=%s BUY signal held back — money management is halted: %s",
                                candidate.signal_time, mm_result["live_status"]["halt_reason"])
                    still_active.append(slot)
                    continue
                entry_settings = {**settings, "stake": live_stake}

                trade = trade_engine.enter_trade(candidate, snap, entry_settings)
                slot["trade"] = trade
                log.info("[Tab3] TRADE OPENED candle=%s (%s) side=%s mode=%s entry_price=%.3f stake=$%.2f (money-management) reason=%r",
                          candidate.signal_time, time.strftime("%H:%M:%S", time.localtime(candidate.signal_time)),
                          trade.selected_side, trade.entry_mode, trade.entry_price, live_stake, trade.entry_reason)
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


def _wants_fast_poll(slots: list, settings: dict) -> bool:
    """
    True while any candidate is still OBSERVING (no trade placed yet) and
    within fast_poll_lead_sec of its candle's actual open, on either side —
    the "catch the exact right price" window worth polling close to every
    second for instead of the normal refresh_interval. The trailing edge is
    bounded by whatever actually determines the entry cutoff for the current
    global mode (immediate_entry_window_sec in Immediate Entry, else the
    general entry_deadline_sec) — no point polling fast once entry is
    structurally impossible for the rest of the candidate's life.
    """
    now = time.time()
    lead = settings["fast_poll_lead_sec"]
    trail = settings["immediate_entry_window_sec"] if settings.get("immediate_mode") else settings["entry_deadline_sec"]
    for slot in slots:
        if slot["trade"] is not None:
            continue   # already entered — outcome monitoring only, no need to rush
        seconds_from_open = now - slot["candidate"].signal_time
        if -lead <= seconds_from_open <= trail:
            return True
    return False


def tab3_loop() -> None:
    while True:
        t0 = time.time()
        try:
            _tick_tab3()
        except Exception:
            log.exception("tab3_loop tick failed")
        with state.lock:
            slots = state.tab3_slots
            settings = state.tab3_settings
            active = bool(slots)
            fast = _wants_fast_poll(slots, settings) if active else False
            interval = settings["fast_poll_interval_sec"] if fast else settings["refresh_interval"]
        interval = interval if active else config.TAB3_IDLE_POLL_INTERVAL_SEC
        _record_tick("tab3", t0, time.time() - t0, interval)
        time.sleep(interval)


def start_background_threads() -> None:
    threading.Thread(target=tab1_loop, daemon=True, name="tab1_loop").start()
    threading.Thread(target=tab1_early_loop, daemon=True, name="tab1_early_loop").start()
    threading.Thread(target=tab2_loop, daemon=True, name="tab2_loop").start()
    threading.Thread(target=tab3_loop, daemon=True, name="tab3_loop").start()
