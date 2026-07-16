"""
Streamlit UI. Selecting pair + timeframe + candle count automatically runs
the full scan (4 strategies x 6 ATR multipliers x 32 filter combinations =
768 setups) over that single dataset — there is no separate "pick one
strategy" mode; the full scan is always what drives the tables/graphs below.
"""
from __future__ import annotations
import os
import time

import numpy as np
import pandas as pd
import streamlit as st
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

import config
import data_fetcher
import pine_logic
import backtester
import metrics
import money_management as mm

GREEN = "#00aa44"
RED = "#dd2222"


# ─── Sweep execution (session-cached per pair/timeframe/candle_count) ───────

def _run_sweep_with_progress(pair: str, timeframe: str, candle_count: int, force_refresh: bool):
    with st.status("Running backtest sweep...", expanded=True) as status:
        log_lines: list[str] = []
        log_box = st.empty()

        def log(msg: str):
            log_lines.append(msg)
            log_box.code("\n".join(log_lines[-25:]))

        ticker = st.empty()

        def tick(msg: str):
            ticker.caption(msg)

        df, from_cache, cached_rows_before = data_fetcher.load_candles(
            pair, timeframe, candle_count, force_refresh=force_refresh, log=log)

        if df.empty:
            status.update(label="Failed — no data available.", state="error")
            return None, None, None, from_cache

        log(f"Loaded {len(df):,} candles total for {pair} {timeframe}.")
        df = pine_logic.compute_indicators(df)

        progress_bar = st.progress(0.0)

        def progress_cb(done, total):
            progress_bar.progress(done / total)

        summary_df, trades_df = backtester.run_sweep(
            df, pair, timeframe, config.STRATEGIES, config.ATR_MULTIPLIERS, config.FILTER_COMBOS,
            log=log, tick=tick, progress_cb=progress_cb)

        log("Saving results...")
        os.makedirs(config.RESULTS_DIR, exist_ok=True)
        summary_df.to_csv(os.path.join(config.RESULTS_DIR, "all_summary_results.csv"), index=False)
        trades_df.to_csv(os.path.join(config.RESULTS_DIR, "all_trade_logs.csv"), index=False)

        log("Dashboard ready.")
        status.update(label="Dashboard ready.", state="complete")

    return df, summary_df, trades_df, from_cache


def _get_or_run(pair: str, timeframe: str, candle_count: int, force_refresh: bool):
    st.session_state.setdefault("sweep_cache", {})
    key = (pair, timeframe, candle_count)
    if force_refresh or key not in st.session_state["sweep_cache"]:
        result = _run_sweep_with_progress(pair, timeframe, candle_count, force_refresh)
        if result[0] is None:
            return None
        st.session_state["sweep_cache"][key] = result
    return st.session_state["sweep_cache"][key]


# ─── Chart builders ───────────────────────────────────────────────────────────

def _bar_chart(labels, values, title, xlabel, horizontal=False, figsize=(10, 5)):
    fig, ax = plt.subplots(figsize=figsize, dpi=100)
    colors = [GREEN if v >= 50 else RED for v in values] if "Win Rate" in xlabel or "%" in xlabel else "#3778c2"
    if horizontal:
        ax.barh(labels, values, color=colors if isinstance(colors, list) else "#3778c2")
        ax.set_xlabel(xlabel)
    else:
        ax.bar(labels, values, color=colors if isinstance(colors, list) else "#3778c2")
        ax.set_ylabel(xlabel)
        plt.xticks(rotation=30, ha="right")
    ax.set_title(title, fontsize=12, fontweight="bold")
    ax.grid(True, axis="x" if horizontal else "y", color="#dddddd", linewidth=0.6)
    fig.tight_layout()
    return fig


def _scatter_chart(x, y, colors_by, title):
    fig, ax = plt.subplots(figsize=(9, 6), dpi=100)
    palette = {"ATR Reversal": "#3778c2", "Engulfing": "#c9a227", "Hammer/SS": "#00aa44", "Exhaustion": "#dd2222"}
    for strat, color in palette.items():
        mask = colors_by == strat
        ax.scatter(np.asarray(x)[mask], np.asarray(y)[mask], label=strat, color=color, alpha=0.6, s=25)
    ax.set_xlabel("Signal Frequency %")
    ax.set_ylabel("Win Rate %")
    ax.set_title(title, fontsize=12, fontweight="bold")
    ax.axhline(50, color="#888888", linestyle="--", linewidth=1)
    ax.legend(fontsize=8)
    ax.grid(True, color="#dddddd", linewidth=0.6)
    fig.tight_layout()
    return fig


def _histogram(values, title, xlabel):
    fig, ax = plt.subplots(figsize=(9, 5), dpi=100)
    ax.hist(values, bins=range(0, int(max(values, default=0)) + 2), color="#3778c2", edgecolor="white")
    ax.set_xlabel(xlabel)
    ax.set_ylabel("Number of Setups")
    ax.set_title(title, fontsize=12, fontweight="bold")
    ax.grid(True, axis="y", color="#dddddd", linewidth=0.6)
    fig.tight_layout()
    return fig


# ─── Formatting ───────────────────────────────────────────────────────────────

_COLUMN_RENAME = {
    "pair": "Pair", "timeframe": "Timeframe", "total_candles": "Total Candles Tested",
    "strategy": "Strategy Name", "atr_mult": "ATR Multiplier", "filters_label": "Filter Combination",
    "total_signals": "Total Signals", "wins": "Total Wins", "losses": "Total Losses",
    "neutral": "Neutral Signals", "win_rate": "Win Rate", "loss_rate": "Loss Rate",
    "signal_frequency": "Signal Frequency %", "avg_next_candle_move": "Average Next Candle Move",
    "avg_winning_move": "Average Winning Move", "avg_losing_move": "Average Losing Move",
    "best_win_move": "Best Win Move", "worst_loss_move": "Worst Loss Move",
    "max_consecutive_losses": "Max Consecutive Losses", "current_loss_streak": "Current Loss Streak",
    "max_consecutive_wins": "Max Consecutive Wins", "last_signal_time": "Last Signal Time",
    "last_signal_direction": "Last Signal Direction", "last_signal_result": "Last Signal Result",
    "last_signal_candle_close": "Last Signal Candle Close", "result_candle_open": "Result Candle Open",
    "result_candle_close": "Result Candle Close",
}

_ROUND_COLS = ["win_rate", "loss_rate", "signal_frequency", "avg_next_candle_move", "avg_winning_move",
               "avg_losing_move", "best_win_move", "worst_loss_move", "last_signal_candle_close",
               "result_candle_open", "result_candle_close"]


def _format_for_display(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    out = df.copy()
    for c in _ROUND_COLS:
        if c in out.columns:
            out[c] = out[c].apply(lambda v: round(v, 4) if isinstance(v, (int, float)) and pd.notna(v) else v)
    if "last_signal_time" in out.columns:
        out["last_signal_time"] = out["last_signal_time"].apply(
            lambda t: time.strftime("%Y-%m-%d %H:%M", time.localtime(t)) if pd.notna(t) else "—")
    return out.rename(columns=_COLUMN_RENAME)


# ─── Main render ──────────────────────────────────────────────────────────────

def render():
    st.set_page_config(page_title="Pine Strategy Simulator", layout="wide")
    st.title("Pine Strategy Simulator")
    st.caption("Selecting a pair/timeframe/candle count automatically runs all 4 strategies x 6 ATR "
               "multipliers x 32 filter combinations (768 setups) over that dataset. "
               "Visualisation/backtesting only — no orders.")

    with st.sidebar:
        st.header("Dataset")
        pair = st.selectbox("Pair", config.PAIRS)
        timeframe = st.selectbox("Timeframe", config.TIMEFRAMES)
        candle_count = st.selectbox("Candle Count", config.CANDLE_COUNT_OPTIONS,
                                     index=config.CANDLE_COUNT_OPTIONS.index(config.DEFAULT_CANDLE_COUNT))
        st.caption("100,000 candles on 1h may exceed available exchange history for some pairs — "
                   "you'll get as many as actually exist, with a warning.")
        force_refresh = st.button("Force Refresh (refetch from Binance)")

    result = _get_or_run(pair, timeframe, candle_count, force_refresh)
    if result is None:
        st.error(f"No local cache for {pair} {timeframe} and the Binance API call failed. "
                 f"Check your internet connection and try again.")
        return

    df, summary_df, trades_df, from_cache = result
    total_candles = len(df)
    if total_candles < candle_count:
        st.warning(f"Requested {candle_count:,} candles but only {total_candles:,} are available from Binance "
                   f"for {pair} {timeframe} — every table/graph below reflects the {total_candles:,} actually tested.")

    source = "local cache" if from_cache else "freshly fetched from Binance (now cached)"
    st.success(f"{pair} {timeframe}: {total_candles:,} candles tested ({source}). "
               f"Range: {time.strftime('%Y-%m-%d %H:%M', time.localtime(df['time'].iloc[0]))} -> "
               f"{time.strftime('%Y-%m-%d %H:%M', time.localtime(df['time'].iloc[-1]))}. "
               f"{len(summary_df)} setups tested, {len(trades_df):,} total resolved+neutral signals.")

    tab1, tab2, tab3 = st.tabs(["All Results", "Filtered Results", "Money Management Simulator"])

    with tab1:
        st.download_button("Download full summary CSV", summary_df.to_csv(index=False),
                            file_name="all_summary_results.csv", mime="text/csv")
        st.download_button("Download full trade log CSV", trades_df.to_csv(index=False),
                            file_name="all_trade_logs.csv", mime="text/csv")

        st.subheader("Main Result Table")
        st.caption(f"All {len(summary_df)} setups ({len(config.STRATEGIES)} strategies x "
                   f"{len(config.ATR_MULTIPLIERS)} ATR multipliers x {len(config.FILTER_COMBOS)} filter combos) "
                   f"for {pair} {timeframe} @ {total_candles:,} candles.")
        st.dataframe(_format_for_display(summary_df), width="stretch", height=420)

        st.divider()
        st.header("Ranking Tables")
        st.caption(f"'Reliable' rankings require >= {config.MIN_SIGNALS_FOR_RELIABLE} signals so a lucky "
                   f"small sample can't look like the best setup.")

        rt1, rt2, rt3 = st.columns(3)
        with rt1:
            st.markdown("**1. Best Overall Setups**")
            st.dataframe(_format_for_display(metrics.best_overall(summary_df)), width="stretch", height=300)
        with rt2:
            st.markdown("**2. Best High-Frequency Setups**")
            st.dataframe(_format_for_display(metrics.best_high_frequency(summary_df)), width="stretch", height=300)
        with rt3:
            st.markdown("**3. Best Low-Risk Setups**")
            st.dataframe(_format_for_display(metrics.best_low_risk(summary_df)), width="stretch", height=300)

        rt4, rt5, rt6 = st.columns(3)
        with rt4:
            st.markdown("**4. Best Setup Per Strategy**")
            st.dataframe(_format_for_display(metrics.best_per_strategy(summary_df)), width="stretch", height=250)
        with rt5:
            st.markdown("**5. Best Setup Per ATR Multiplier**")
            st.dataframe(_format_for_display(metrics.best_per_atr(summary_df)), width="stretch", height=250)
        with rt6:
            st.markdown("**6. Best Setup Per Filter Combination**")
            st.dataframe(_format_for_display(metrics.best_per_filter(summary_df)), width="stretch", height=250)

        st.divider()
        st.header("Graphs")

        strat_wr = metrics.win_rate_by_group(summary_df, "strategy").sort_values("win_rate", ascending=False)
        atr_wr = metrics.win_rate_by_group(summary_df, "atr_mult").sort_values("atr_mult")
        filt_wr = metrics.win_rate_by_group(summary_df, "filters_label").sort_values("win_rate", ascending=False)
        strat_signals = summary_df.groupby("strategy")["total_signals"].sum().sort_values(ascending=False)

        g1, g2 = st.columns(2)
        with g1:
            st.pyplot(_bar_chart(strat_wr["strategy"], strat_wr["win_rate"],
                                  "1. Best Strategy by Win Rate", "Win Rate %"))
            plt.close("all")
        with g2:
            st.pyplot(_bar_chart(atr_wr["atr_mult"].astype(str), atr_wr["win_rate"],
                                  "2. ATR Multiplier vs Win Rate (all strategies mixed — only ATR "
                                  "Reversal actually varies by multiplier)", "Win Rate %"))
            plt.close("all")

        st.pyplot(_bar_chart(filt_wr["filters_label"], filt_wr["win_rate"],
                              "3. Filter Combination vs Win Rate (all 32)", "Win Rate %",
                              horizontal=True, figsize=(10, 11)))
        plt.close("all")

        g3, g4 = st.columns(2)
        with g3:
            st.pyplot(_bar_chart(strat_signals.index, strat_signals.values,
                                  "4. Total Signals by Strategy", "Total Signals"))
            plt.close("all")
        with g4:
            st.pyplot(_histogram(summary_df["max_consecutive_losses"].tolist(),
                                  "5. Max Consecutive Losses by Setup (distribution)", "Max Consecutive Losses"))
            plt.close("all")

        st.pyplot(_scatter_chart(summary_df["signal_frequency"], summary_df["win_rate"], summary_df["strategy"],
                                  "6. Win Rate vs Signal Frequency (every setup)"))
        plt.close("all")

    with tab2:
        _render_filtered_tab(summary_df, total_candles)

    with tab3:
        _render_money_management_tab()


_FILTERED_COLUMN_ORDER = [
    "pair", "timeframe", "total_candles", "strategy", "atr_mult", "filters_label",
    "total_signals", "signal_frequency", "wins", "losses", "neutral", "win_rate", "loss_rate",
    "max_consecutive_losses", "current_loss_streak", "avg_next_candle_move", "best_win_move",
    "worst_loss_move", "last_signal_time", "last_signal_direction", "last_signal_result",
]


def _setup_label(row) -> str:
    return f"{row['strategy']}|ATR{row['atr_mult']}|{row['filters_label']}"


def _render_filtered_tab(summary_df: pd.DataFrame, total_candles: int):
    st.caption("Filters the sweep that already ran above — changing these controls never re-runs the backtest.")

    c1, c2, c3 = st.columns(3)
    with c1:
        min_win_rate = st.slider("Minimum Win Rate %", min_value=0, max_value=100, value=50)
    with c2:
        min_signal_frequency = st.slider("Minimum Signal Frequency %", min_value=0.0, max_value=100.0,
                                          value=2.0, step=0.5)
    with c3:
        min_total_signals = st.number_input("Minimum Total Signals (optional, 0 = no minimum)",
                                             min_value=0, value=0, step=10)

    st.caption(f"Signal Frequency % = Total Signals / Total Candles Tested x 100. With {total_candles:,} candles "
               f"tested, a {min_signal_frequency:.1f}% floor requires at least "
               f"{int(np.ceil(total_candles * min_signal_frequency / 100)):,} signals.")

    filtered = metrics.filter_setups(summary_df, min_win_rate, min_signal_frequency, int(min_total_signals))

    st.subheader("Filtered Summary")
    if filtered.empty:
        st.warning("No setups meet these thresholds — loosen the filters above.")
        return

    best = filtered.iloc[0]
    highest_freq = filtered.sort_values("signal_frequency", ascending=False).iloc[0]
    lowest_risk = filtered.sort_values("max_consecutive_losses", ascending=True).iloc[0]

    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("Setups Found", len(filtered))
    m2.metric("Best Filtered Setup", _setup_label(best), f"{best['win_rate']:.1f}% WR")
    m3.metric("Highest Win Rate", f"{best['win_rate']:.1f}%")
    m4.metric("Highest Signal Frequency", f"{highest_freq['signal_frequency']:.2f}%")
    m5.metric("Lowest Max Consecutive Losses", int(lowest_risk["max_consecutive_losses"]))

    st.subheader("Filtered Result Table")
    display_cols = [c for c in _FILTERED_COLUMN_ORDER if c in filtered.columns]
    st.dataframe(_format_for_display(filtered[display_cols]), width="stretch", height=420)
    st.download_button("Download filtered_results.csv", filtered.to_csv(index=False),
                        file_name="filtered_results.csv", mime="text/csv")

    st.subheader("Filtered Graphs")
    labels = filtered.apply(_setup_label, axis=1)
    top = filtered.head(30)
    top_labels = top.apply(_setup_label, axis=1)
    if len(filtered) > 30:
        st.caption(f"Showing the top 30 of {len(filtered)} filtered setups (by win rate) in the bar charts below "
                   f"for readability — the table and scatter plot above/below include all of them.")

    fg1, fg2 = st.columns(2)
    with fg1:
        st.pyplot(_bar_chart(top_labels, top["win_rate"], "1. Filtered Setups by Win Rate", "Win Rate %",
                              horizontal=True, figsize=(9, max(4, len(top) * 0.3))))
        plt.close("all")
    with fg2:
        by_freq = top.sort_values("signal_frequency", ascending=False)
        st.pyplot(_bar_chart(by_freq.apply(_setup_label, axis=1), by_freq["signal_frequency"],
                              "2. Filtered Setups by Signal Frequency", "Signal Frequency %",
                              horizontal=True, figsize=(9, max(4, len(top) * 0.3))))
        plt.close("all")

    st.pyplot(_scatter_chart(filtered["signal_frequency"], filtered["win_rate"], filtered["strategy"],
                              "3. Filtered Win Rate vs Signal Frequency"))
    plt.close("all")

    st.pyplot(_histogram(filtered["max_consecutive_losses"].tolist(),
                          "4. Filtered Max Consecutive Losses (distribution)", "Max Consecutive Losses"))
    plt.close("all")


# ─── Money Management Simulator tab ──────────────────────────────────────────

def _line_chart(values, title, ylabel, color="#3778c2", zero_line=False):
    fig, ax = plt.subplots(figsize=(11, 4), dpi=100)
    ax.plot(range(1, len(values) + 1), values, color=color, linewidth=1.3)
    if zero_line:
        ax.axhline(0, color="#888888", linestyle="--", linewidth=1)
    ax.set_xlabel("Trade #")
    ax.set_ylabel(ylabel)
    ax.set_title(title, fontsize=12, fontweight="bold")
    ax.grid(True, color="#dddddd", linewidth=0.6)
    fig.tight_layout()
    return fig


def _period_line_chart(labels, values, title, ylabel, color="#3778c2", starting_balance=None):
    fig, ax = plt.subplots(figsize=(12, 4.5), dpi=100)
    ax.plot(range(len(values)), values, color=color, linewidth=1.5, marker="o", markersize=3)
    if starting_balance is not None:
        ax.axhline(starting_balance, color="#888888", linestyle="--", linewidth=1, label="Starting balance")
        ax.legend(fontsize=8)
    ax.set_xticks(range(len(labels)))
    ax.set_xticklabels(labels, rotation=45, ha="right", fontsize=8)
    ax.set_ylabel(ylabel)
    ax.set_title(title, fontsize=12, fontweight="bold")
    ax.grid(True, color="#dddddd", linewidth=0.6)
    fig.tight_layout()
    return fig


def _render_money_management_tab():
    st.caption("Replays every historical signal from oldest to newest candle with a dynamic loss-basket "
               "recovery model. This is not martingale — sizing only adds a small percentage of the "
               "*outstanding* loss basket to the base trade amount, always capped at the max trade amount.")

    c1, c2, c3 = st.columns(3)
    with c1:
        mm_pair = st.selectbox("Pair", config.PAIRS, key="mm_pair")
    with c2:
        mm_timeframe = st.selectbox("Timeframe", config.TIMEFRAMES, key="mm_timeframe")
    with c3:
        mm_candle_count = st.selectbox("Candle Count", config.CANDLE_COUNT_OPTIONS,
                                        index=config.CANDLE_COUNT_OPTIONS.index(config.DEFAULT_CANDLE_COUNT),
                                        key="mm_candle_count")

    selected_strategies = st.multiselect("Strategies (multi-select)", config.STRATEGIES,
                                          default=[config.STRATEGIES[0]], key="mm_strategies")
    if not selected_strategies:
        st.warning("Select at least one strategy to run a simulation.")
        return

    priority_order = st.multiselect(
        "Priority Order — first = highest priority. If more than one selected strategy fires on the same "
        "candle, the first one here wins (only one trade per candle). Re-click strategies below in your "
        "preferred order to change it.",
        selected_strategies, default=selected_strategies, key="mm_priority")
    if set(priority_order) != set(selected_strategies):
        st.warning("Priority order must include every selected strategy exactly once — add any missing ones below.")
        return

    st.markdown("**Filters per strategy** (chosen independently for each strategy)")
    filters_per_strategy = {}
    filter_cols = st.columns(len(priority_order))
    for col, name in zip(filter_cols, priority_order):
        with col:
            st.markdown(f"*{name}*")
            filters_per_strategy[name] = {
                "f1": st.checkbox("F1 Trend", value=True, key=f"mm_f1_{name}"),
                "f2": st.checkbox("F2 Volatility", value=True, key=f"mm_f2_{name}"),
                "f3": st.checkbox("F3 Close Location", value=False, key=f"mm_f3_{name}"),
                "f4": st.checkbox("F4 Continuation", value=False, key=f"mm_f4_{name}"),
                "f5": st.checkbox("F5 Anti-Chop", value=True, key=f"mm_f5_{name}"),
            }

    atr_mult = st.selectbox("ATR Multiplier", config.ATR_MULTIPLIERS,
                             index=config.ATR_MULTIPLIERS.index(1.5), key="mm_atr")

    mm_mode = st.radio(
        "Money Management Mode",
        ["Fixed / Dynamic Loss-Basket (existing)", "Tiered Recovery (cycle-based)"],
        key="mm_mode", horizontal=True,
    )

    if mm_mode == "Tiered Recovery (cycle-based)":
        _render_tiered_money_management_section(mm_pair, mm_timeframe, mm_candle_count, priority_order,
                                                  filters_per_strategy, atr_mult)
        return

    st.markdown("**Money Management Settings**")
    mc1, mc2, mc3, mc4 = st.columns(4)
    with mc1:
        starting_balance = st.number_input("Starting Balance ($)", min_value=1.0, value=1000.0,
                                            step=10.0, key="mm_start_bal")
        base_trade_amount = st.number_input("Base Trade Amount ($)", min_value=0.01, value=1.0,
                                             step=0.1, key="mm_base_trade")
    with mc2:
        dynamic_mode = st.checkbox("Dynamic recovery % (overrides fixed % below)", value=False, key="mm_dynamic")
        recovery_percent_pct = st.selectbox("Fixed Recovery % (used when dynamic is off)", [5, 10, 15, 25],
                                             index=1, key="mm_recovery_pct")
    with mc3:
        profit_split_pct = st.slider("Win Split -> Loss Basket Recovery (%)", 0, 100, 50, key="mm_split")
        max_trade_amount = st.number_input("Max Trade Amount Cap ($)", min_value=base_trade_amount,
                                            value=max(10.0, base_trade_amount * 10), step=1.0, key="mm_max_trade")
    with mc4:
        reset_label = st.selectbox("Loss Basket Reset", ["Never reset", "Reset when loss basket becomes 0",
                                                           "Reset after X winning trades"], key="mm_reset_mode")
        reset_after_n_wins = 0
        if reset_label == "Reset after X winning trades":
            reset_after_n_wins = st.number_input("Reset after N wins", min_value=1, value=5, step=1, key="mm_reset_n")

    reset_mode = {"Never reset": "never", "Reset when loss basket becomes 0": "on_zero",
                  "Reset after X winning trades": "after_n_wins"}[reset_label]

    if dynamic_mode:
        st.caption("Dynamic mode: recovery % = 25% while loss basket <= 5x base trade, 15% <= 10x, "
                   "10% <= 20x, 5% beyond that — the fixed % above is ignored.")

    if max_trade_amount > 10 * base_trade_amount:
        st.warning(f"Max trade amount cap (${max_trade_amount:.2f}) is more than 10x your base trade amount "
                   f"(${base_trade_amount:.2f}) — this allows large position sizing during a losing streak. "
                   f"Make sure this is intentional before running.")

    run_clicked = st.button("Run Simulation", type="primary", key="mm_run")

    cache_key = (mm_pair, mm_timeframe, mm_candle_count, tuple(priority_order), atr_mult,
                 tuple(sorted((k, tuple(sorted(v.items()))) for k, v in filters_per_strategy.items())),
                 starting_balance, base_trade_amount, dynamic_mode, recovery_percent_pct,
                 profit_split_pct, max_trade_amount, reset_mode, reset_after_n_wins)

    st.session_state.setdefault("mm_cache", {})
    if run_clicked:
        with st.spinner(f"Loading {mm_pair} {mm_timeframe} and replaying up to {mm_candle_count:,} candles "
                         f"oldest -> newest..."):
            df, from_cache, _ = data_fetcher.load_candles(mm_pair, mm_timeframe, mm_candle_count)
            if df.empty:
                st.error("No local cache for this pair/timeframe and the Binance API call failed — "
                         "check your internet connection and try again.")
                return
            df = pine_logic.compute_indicators(df)
            money = {
                "starting_balance": starting_balance, "base_trade_amount": base_trade_amount,
                "max_trade_amount": max_trade_amount, "recovery_percent": recovery_percent_pct / 100.0,
                "dynamic_mode": dynamic_mode, "profit_split_recovery_pct": profit_split_pct / 100.0,
                "reset_mode": reset_mode, "reset_after_n_wins": reset_after_n_wins,
            }
            result = mm.run_simulation(df, priority_order, filters_per_strategy, atr_mult, money)
            result["meta"] = {"pair": mm_pair, "timeframe": mm_timeframe, "candle_count": len(df),
                               "priority_order": list(priority_order), "atr_mult": atr_mult,
                               "base_trade_amount": base_trade_amount,
                               "filters_per_strategy": dict(filters_per_strategy)}
            st.session_state["mm_cache"][cache_key] = result

    result = st.session_state["mm_cache"].get(cache_key)
    if result is None:
        st.info("Configure your settings above and click Run Simulation.")
        return

    _render_money_management_results(result)


def _render_money_management_results(result: dict):
    summary = result["summary"]
    trade_log = result["trade_log"]
    strategy_breakdown = result["strategy_breakdown"]
    curves = result["curves"]
    meta = result["meta"]

    st.divider()

    if summary["bankrupt"]:
        st.error(
            f"**ACCOUNT WIPED OUT — 100%+ drawdown.** Balance reached "
            f"${summary['ending_balance']:.2f} (<= $0) on trade #{summary['bankrupt_trade_num']} "
            f"({time.strftime('%Y-%m-%d %H:%M', time.localtime(summary['bankrupt_time']))}). There is no "
            f"money left to fund another trade, so the simulation stopped here — everything below reflects "
            f"only the trades that happened before this point. This setup is not viable at this starting "
            f"balance/sizing; reduce the base trade amount, lower the max trade cap, or raise starting "
            f"balance and re-run."
        )

    st.subheader("Risk Warnings")
    warned = False
    if summary["max_consecutive_losses"] >= 5:
        st.warning(f"Max consecutive losses reached {summary['max_consecutive_losses']} — a long losing "
                   f"streak occurred in this run.")
        warned = True
    if summary["final_loss_basket"] > 5 * meta.get("base_trade_amount", summary["average_trade_amount"] or 1):
        st.warning(f"Final loss basket (${summary['final_loss_basket']:.2f}) is still large relative to the "
                   f"base trade amount — recovery hasn't kept up with losses by the end of this run.")
        warned = True
    if summary["max_trade_amount_used"] >= 5 * (meta.get("base_trade_amount") or summary["average_trade_amount"] or 1):
        st.warning(f"Max trade amount actually used (${summary['max_trade_amount_used']:.2f}) grew to 5x+ "
                   f"the base trade amount during this run.")
        warned = True
    if not warned:
        st.success("No risk thresholds triggered for this run.")

    st.subheader("Summary")
    filters_label = "; ".join(
        f"{name}: {'+'.join(k.upper() for k, v in flags.items() if v) or 'None'}"
        for name, flags in meta.get("filters_per_strategy", {}).items()
    )
    summary_row = {
        "Pair": meta["pair"], "Timeframe": meta["timeframe"], "Candle Count": meta["candle_count"],
        "Selected Strategies": " > ".join(meta["priority_order"]), "Selected Filters": filters_label,
        "ATR Multiplier": meta["atr_mult"],
        "Starting Balance": round(summary["starting_balance"], 2), "Ending Balance": round(summary["ending_balance"], 2),
        "Net PnL": round(summary["net_pnl"], 2), "ROI %": round(summary["roi_pct"], 2),
        "Total Trades": summary["total_trades"], "Wins": summary["wins"], "Losses": summary["losses"],
        "Neutrals": summary["neutrals"], "Win Rate": round(summary["win_rate"], 2),
        "Max Consecutive Losses": summary["max_consecutive_losses"],
        "Biggest Loss Basket": round(summary["biggest_loss_basket"], 2),
        "Final Loss Basket": round(summary["final_loss_basket"], 2),
        "Max Trade Amount Used": round(summary["max_trade_amount_used"], 2),
        "Average Trade Amount": round(summary["average_trade_amount"], 2),
    }
    summary_df_row = pd.DataFrame([summary_row])
    st.dataframe(summary_df_row, width="stretch")

    m1, m2, m3, m4, m5, m6 = st.columns(6)
    m1.metric("Ending Balance", f"${summary['ending_balance']:.2f}", f"{summary['net_pnl']:+.2f}")
    m2.metric("ROI %", f"{summary['roi_pct']:.2f}%")
    m3.metric("Win Rate", f"{summary['win_rate']:.1f}%")
    m4.metric("Max Consec. Losses", summary["max_consecutive_losses"])
    m5.metric("Final Loss Basket", f"${summary['final_loss_basket']:.2f}")
    m6.metric("Max Drawdown", f"{summary['max_drawdown_pct']:.2f}%")

    os.makedirs(config.RESULTS_DIR, exist_ok=True)
    summary_df_row.to_csv(os.path.join(config.RESULTS_DIR, "money_management_summary.csv"), index=False)
    trade_log.to_csv(os.path.join(config.RESULTS_DIR, "money_management_trade_log.csv"), index=False)

    st.subheader("Trade Log")
    tl = trade_log.copy()
    if not tl.empty:
        tl["signal_time"] = tl["signal_time"].apply(lambda t: time.strftime("%Y-%m-%d %H:%M", time.localtime(t)))
        tl["result_time"] = tl["result_time"].apply(lambda t: time.strftime("%Y-%m-%d %H:%M", time.localtime(t)))
        tl = tl.rename(columns={
            "trade_num": "Trade #", "signal_time": "Signal Time", "result_time": "Result Candle Time",
            "strategy": "Strategy Triggered", "direction": "Signal Direction", "signal_close": "Signal Candle Close",
            "result_open": "Result Candle Open", "result_close": "Result Candle Close", "result": "Result",
            "base_trade_amount": "Base Trade Amount", "recovery_addon": "Recovery Addon",
            "trade_amount": "Final Trade Amount", "balance_before": "Balance Before", "balance_after": "Balance After",
            "pnl": "PnL", "loss_basket_before": "Loss Basket Before", "loss_basket_after": "Loss Basket After",
            "recovery_pct_used": "Recovery Percent Used", "recovered_amount": "Recovered Amount",
            "realized_profit_added": "Realized Profit Added",
        })
    st.dataframe(tl, width="stretch", height=420)
    st.download_button("Download money_management_trade_log.csv", trade_log.to_csv(index=False),
                        file_name="money_management_trade_log.csv", mime="text/csv")
    st.download_button("Download money_management_summary.csv", summary_df_row.to_csv(index=False),
                        file_name="money_management_summary.csv", mime="text/csv")

    st.subheader("Strategy Breakdown")
    if strategy_breakdown.empty:
        st.info("No trades to break down by strategy.")
    else:
        st.dataframe(strategy_breakdown.rename(columns={
            "strategy": "Strategy", "trades": "Trades", "wins": "Wins", "losses": "Losses",
            "neutrals": "Neutrals", "win_rate": "Win Rate", "net_pnl": "Net PnL",
            "average_trade_amount": "Average Trade Amount", "max_consecutive_losses": "Max Consecutive Losses",
        }).round(2), width="stretch")
        st.caption(f"Best strategy in this run: **{summary['best_strategy']}** — "
                   f"Worst: **{summary['worst_strategy']}**")

    st.subheader("Charts")
    if not curves["balance"]:
        st.info("No trades were taken, so there are no curves to plot.")
        return

    ch1, ch2 = st.columns(2)
    with ch1:
        st.pyplot(_line_chart(curves["balance"], "1. Balance Curve", "Balance ($)"))
        plt.close("all")
    with ch2:
        st.pyplot(_line_chart(curves["loss_basket"], "2. Loss Basket Curve", "Loss Basket ($)", color="#dd2222"))
        plt.close("all")

    ch3, ch4 = st.columns(2)
    with ch3:
        st.pyplot(_line_chart(curves["trade_amount"], "3. Trade Amount Curve", "Trade Amount ($)", color="#c9a227"))
        plt.close("all")
    with ch4:
        st.pyplot(_line_chart(curves["drawdown"], "4. Drawdown Curve", "Drawdown %", color="#8844cc"))
        plt.close("all")

    ch5, ch6 = st.columns(2)
    with ch5:
        st.pyplot(_bar_chart(["Wins", "Losses", "Neutrals"],
                              [summary["wins"], summary["losses"], summary["neutrals"]],
                              "5. Win/Loss Distribution", "Count"))
        plt.close("all")
    with ch6:
        if not strategy_breakdown.empty:
            st.pyplot(_bar_chart(strategy_breakdown["strategy"], strategy_breakdown["net_pnl"],
                                  "6. Strategy PnL Comparison", "Net PnL ($)"))
            plt.close("all")

    st.divider()
    st.subheader("Time-Based Analysis")
    weekly = mm.time_bucketed_breakdown(trade_log, summary["starting_balance"], "W")
    monthly = mm.time_bucketed_breakdown(trade_log, summary["starting_balance"], "M")

    if weekly.empty:
        st.info("No trades to break down by week/month.")
    else:
        st.caption(f"Whole test period spans {len(weekly)} week(s) across {len(monthly)} month(s), from "
                   f"{weekly['label'].iloc[0]} to {weekly['label'].iloc[-1]}.")

        wc1, wc2 = st.columns(2)
        with wc1:
            st.pyplot(_bar_chart(weekly["label"], weekly["trade_count"],
                                  "Trades Per Week", "Trade Count"))
            plt.close("all")
        with wc2:
            st.pyplot(_period_line_chart(weekly["label"], weekly["ending_balance"],
                                          "Balance Per Week", "Balance ($)",
                                          starting_balance=summary["starting_balance"]))
            plt.close("all")

        st.markdown("**Monthly Balance**")
        st.pyplot(_period_line_chart(monthly["label"], monthly["ending_balance"],
                                      "Balance At End Of Each Month", "Balance ($)",
                                      color="#00aa44", starting_balance=summary["starting_balance"]))
        plt.close("all")

        monthly_table = monthly.rename(columns={
            "label": "Month", "trade_count": "Trades", "ending_balance": "Balance At Month End",
        })[["Month", "Trades", "Balance At Month End"]].round(2)
        st.dataframe(monthly_table, width="stretch")
        st.download_button("Download monthly_balance.csv", monthly_table.to_csv(index=False),
                            file_name="money_management_monthly_balance.csv", mime="text/csv")


# ─── Tiered Money Management (cycle-based recovery) ─────────────────────────

_DEFAULT_TIERS_DF = pd.DataFrame([
    {"Tier": 1, "Start": 1, "End": 3, "Recovery %": 100},
    {"Tier": 2, "Start": 4, "End": 6, "Recovery %": 50},
    {"Tier": 3, "Start": 7, "End": 10, "Recovery %": 20},
])


def _render_tiered_money_management_section(mm_pair, mm_timeframe, mm_candle_count, priority_order,
                                              filters_per_strategy, atr_mult):
    st.caption("Replays every historical signal oldest -> newest using a tiered recovery cycle: consecutive "
               "losses build up a *temporary* cycle loss, sized order-by-order via your configured recovery "
               "tiers; only the portion left unrecovered when the cycle finally wins gets transferred into "
               "the *permanent* loss pool, which is then paid down gradually on top of every future order.")

    st.markdown("**Tiered Money Management**")
    st.caption("Define recovery tiers by loss-order-number range. Ranges must start at 1, be contiguous "
               "(no gaps/overlaps), and end exactly at Maximum Cycle Orders below.")
    tiers_df = st.data_editor(
        _DEFAULT_TIERS_DF, num_rows="dynamic", key="mm_tiers_editor", width="stretch",
        column_config={
            "Tier": st.column_config.NumberColumn("Tier", min_value=1, step=1),
            "Start": st.column_config.NumberColumn("Start", min_value=1, step=1),
            "End": st.column_config.NumberColumn("End", min_value=1, step=1),
            "Recovery %": st.column_config.NumberColumn("Recovery %", min_value=0, max_value=100, step=1),
        },
    )

    tc1, tc2, tc3 = st.columns(3)
    with tc1:
        base_stake = st.number_input("Base Stake ($) — used for order 1 of every new cycle", min_value=0.01,
                                      value=1.0, step=0.1, key="mm_tier_base_stake")
        starting_balance = st.number_input("Starting Balance ($)", min_value=1.0, value=1000.0, step=10.0,
                                            key="mm_tier_start_bal")
    with tc2:
        net_profit_ratio = st.number_input(
            "Net Profit Ratio (payout multiple per $1 staked on a win — 1.0 = 1:1)",
            min_value=0.01, value=1.0, step=0.05, key="mm_tier_payout")
        maximum_cycle_orders = st.number_input("Maximum Cycle Orders", min_value=1, value=10, step=1,
                                                key="mm_tier_max_orders")
    with tc3:
        fallback_label = st.selectbox(
            "When Maximum Cycle Orders Is Reached",
            ["Stop new orders", "Continue using the final tier percentage", "Reset only after manual confirmation"],
            key="mm_tier_fallback")

    fallback_mode = {"Stop new orders": "stop", "Continue using the final tier percentage": "continue",
                      "Reset only after manual confirmation": "manual"}[fallback_label]

    cycle_timeout_lp_pct = 20
    if fallback_mode == "stop":
        cycle_timeout_lp_pct = st.slider(
            "Max-Cycle Timeout — % Of Unresolved Loss Sent To Permanent Pool (rest is written off)",
            0, 100, 20, key="mm_tier_timeout_pct")
        st.caption("No order 11 is placed. The maxed-out cycle is force-closed right there: this % of its "
                   "still-unresolved temporary loss becomes permanent-pool debt to chase later, the rest is "
                   "written off for good, and a fresh cycle starts at order 1 on the next signal — the "
                   "backtest keeps running. No other part of the sizing/recovery math changes.")
    elif fallback_mode == "manual":
        st.caption("Note: this is a historical backtest replay with no human in the loop, so 'manual "
                   "confirmation' halts the run right there and reports exactly where it stopped. This option "
                   "is provided for a future live-trading integration where someone could actually click to "
                   "resume.")

    st.markdown("**Order-1 Loss-Pool Tax & Cap**")
    st.caption("Unlike the previous version, the loss-pool add-on now applies ONLY to order 1 of a new cycle "
               "(not to every order) — that's what was causing runaway stake growth. Order 1's stake is "
               "base_stake + (permanent_loss_pool × static %), capped at Max First Order Size. Because "
               "temporary_cycle_loss tracks whatever order 1 actually lost, a capped order 1 (e.g. $3 instead "
               "of an uncapped $6) automatically makes order 2 start recovering from $3, not from base_stake.")
    lp1, lp2 = st.columns(2)
    with lp1:
        static_lp_pct = st.slider("Static LP % Added To Base Order (of the permanent pool)",
                                   0, 100, 20, key="mm_tier_static_lp_pct")
    with lp2:
        max_first_order_stake = st.number_input(
            "Max First Order Size ($) — caps base + LP add-on combined",
            min_value=base_stake, value=max(3.0, base_stake * 3), step=0.5, key="mm_tier_max_first_order")

    st.markdown("**Win Pool**")
    st.caption("A configurable share of every win's profit is set aside into a separate win pool, which then "
               "opportunistically pays down the permanent loss pool after every winning trade — this money "
               "doesn't change the account balance, it's a bookkeeping reserve layered on top of the same P&L.")
    wp1, wp2 = st.columns(2)
    with wp1:
        win_pool_contribution_pct = st.slider("Win Pool Contribution % (of each win's profit)",
                                               0, 100, 20, key="mm_tier_win_pool_pct")
    with wp2:
        win_pool_lp_coverage_pct = st.slider(
            "Win Pool LP Coverage % (fraction of the current loss pool paid from the win pool, if funds allow)",
            0, 100, 50, key="mm_tier_win_pool_coverage")

    tiers = [{"start": r["Start"], "end": r["End"], "pct": r["Recovery %"] / 100.0}
             for r in tiers_df.to_dict("records")
             if r.get("Start") is not None and r.get("End") is not None and r.get("Recovery %") is not None]

    errors = mm.validate_tiers(tiers, maximum_cycle_orders)
    for err in errors:
        st.error(err)

    run_clicked = st.button("Run Simulation", type="primary", key="mm_tier_run", disabled=bool(errors))
    if errors:
        st.info("Fix the tier configuration errors above before running.")
        return

    sorted_tiers = sorted(tiers, key=lambda t: t["start"])
    cache_key = (mm_pair, mm_timeframe, mm_candle_count, tuple(priority_order), atr_mult,
                 tuple(sorted((k, tuple(sorted(v.items()))) for k, v in filters_per_strategy.items())),
                 starting_balance, base_stake, net_profit_ratio, static_lp_pct, max_first_order_stake,
                 maximum_cycle_orders, fallback_mode, cycle_timeout_lp_pct,
                 win_pool_contribution_pct, win_pool_lp_coverage_pct,
                 tuple((t["start"], t["end"], t["pct"]) for t in sorted_tiers))

    st.session_state.setdefault("mm_tiered_cache", {})
    if run_clicked:
        with st.spinner(f"Loading {mm_pair} {mm_timeframe} and replaying up to {mm_candle_count:,} candles "
                         f"oldest -> newest..."):
            df, from_cache, _ = data_fetcher.load_candles(mm_pair, mm_timeframe, mm_candle_count)
            if df.empty:
                st.error("No local cache for this pair/timeframe and the Binance API call failed — "
                         "check your internet connection and try again.")
                return
            df = pine_logic.compute_indicators(df)
            money = {
                "starting_balance": starting_balance, "base_stake": base_stake,
                "net_profit_ratio": net_profit_ratio, "static_lp_pct": static_lp_pct / 100.0,
                "max_first_order_stake": max_first_order_stake,
                "maximum_cycle_orders": maximum_cycle_orders, "fallback_mode": fallback_mode,
                "cycle_timeout_lp_pct": cycle_timeout_lp_pct / 100.0,
                "win_pool_contribution_pct": win_pool_contribution_pct / 100.0,
                "win_pool_lp_coverage_pct": win_pool_lp_coverage_pct / 100.0,
            }
            result = mm.run_tiered_simulation(df, priority_order, filters_per_strategy, atr_mult, money, sorted_tiers)
            result["meta"] = {"pair": mm_pair, "timeframe": mm_timeframe, "candle_count": len(df),
                               "priority_order": list(priority_order), "atr_mult": atr_mult,
                               "tiers": sorted_tiers}
            st.session_state["mm_tiered_cache"][cache_key] = result

    result = st.session_state["mm_tiered_cache"].get(cache_key)
    if result is None:
        st.info("Configure your settings above and click Run Simulation.")
        return

    _render_tiered_money_management_results(result)


def _render_tiered_money_management_results(result: dict):
    summary = result["summary"]
    trade_log = result["trade_log"]
    curves = result["curves"]
    live = result["live_status"]
    loss_streaks = result["loss_streaks"]

    st.divider()

    if summary["bankrupt"]:
        st.error(f"**ACCOUNT WIPED OUT — 100%+ drawdown.** Balance reached ${summary['ending_balance']:.2f} "
                  f"(<= $0) on trade #{summary['bankrupt_trade_num']}. Simulation stopped here.")
    elif summary["halted"]:
        st.warning(f"**Simulation halted before completing all signals.** {summary['halt_reason']}")

    st.subheader("Live Money-Management Status")
    st.caption("State as of the NEXT order this configuration would place, given everything that happened in "
               "this run.")
    lc1, lc2, lc3, lc4 = st.columns(4)
    lc1.metric("Cycle Order Number", live["cycle_order_number"])
    lc1.metric("Consecutive Losses", live["consecutive_losses"])
    lc2.metric("Temporary Cycle Loss", f"${live['temporary_cycle_loss']:.2f}")
    lc2.metric("Active Recovery Tier", live["active_recovery_tier"])
    lc3.metric("Active Recovery %",
               f"{live['active_recovery_percentage'] * 100:.0f}%" if live["active_recovery_percentage"] is not None else "—")
    lc3.metric("Base/Cycle Stake Component",
               f"${live['base_or_cycle_stake']:.2f}" if live["base_or_cycle_stake"] is not None else "—")
    lc4.metric("Permanent Loss Pool", f"${live['permanent_loss_pool']:.2f}")
    lc4.metric("Static LP % (order 1 only)", f"{live['static_lp_pct'] * 100:.0f}%")

    lc5, lc6, lc7, lc8 = st.columns(4)
    lc5.metric("Loss-Pool Extra Stake Component",
               f"${live['loss_pool_extra_stake']:.2f}" if live["loss_pool_extra_stake"] is not None else "—")
    lc5.metric("Max First Order Size", f"${live['max_first_order_stake']:.2f}" if live["max_first_order_stake"] else "—")
    lc6.metric("Final Calculated Stake",
               f"${live['final_stake']:.2f}" if live["final_stake"] is not None else "—")
    lc6.metric("Maximum Permitted Cycle Orders", live["maximum_cycle_orders"])
    lc7.metric("Fallback Mode", {"stop": "Stop new orders", "continue": "Continue final tier %",
                                  "manual": "Manual confirmation"}[live["fallback_mode"]])
    lc7.metric("Win Pool Balance", f"${live['win_pool']:.2f}")
    lc8.metric("Win Pool Contribution %", f"{live['win_pool_contribution_pct'] * 100:.0f}%")
    lc8.metric("Win Pool LP Coverage %", f"{live['win_pool_lp_coverage_pct'] * 100:.0f}%")

    st.subheader("Summary")
    summary_row = {
        "Starting Balance": round(summary["starting_balance"], 2), "Ending Balance": round(summary["ending_balance"], 2),
        "Net PnL": round(summary["net_pnl"], 2), "ROI %": round(summary["roi_pct"], 2),
        "Total Trades": summary["total_trades"], "Wins": summary["wins"], "Losses": summary["losses"],
        "Neutrals": summary["neutrals"], "Cycle Timeouts": summary["cycle_timeouts"],
        "Win Rate": round(summary["win_rate"], 2),
        "Max Consecutive Losses": summary["max_consecutive_losses"],
        "Final Temporary Cycle Loss": round(summary["final_temporary_cycle_loss"], 2),
        "Final Permanent Loss Pool": round(summary["final_permanent_loss_pool"], 2),
        "Final Win Pool": round(summary["final_win_pool"], 2),
        "Max Drawdown %": round(summary["max_drawdown_pct"], 2),
    }
    st.dataframe(pd.DataFrame([summary_row]), width="stretch")

    st.subheader("Cycle History")
    tl = trade_log.copy()
    if not tl.empty:
        tl["timestamp"] = tl["timestamp"].apply(lambda t: time.strftime("%Y-%m-%d %H:%M", time.localtime(t)))
        tl["recovery_percentage"] = tl["recovery_percentage"].apply(
            lambda p: f"{p * 100:.0f}%" if pd.notna(p) else "—")
        tl = tl.rename(columns={
            "trade_id": "Trade ID", "timestamp": "Timestamp", "cycle_id": "Cycle ID",
            "order_number_in_cycle": "Order Number In Cycle", "result": "Result",
            "recovery_tier": "Recovery Tier", "recovery_percentage": "Recovery Percentage",
            "temporary_loss_before": "Temporary Loss Before Trade", "base_or_cycle_stake": "Base/Cycle Stake",
            "permanent_pool_before": "Permanent Pool Before Trade", "pool_recovery_stake": "Pool-Recovery Stake",
            "final_stake": "Final Stake", "actual_payout": "Actual Payout",
            "net_profit_or_loss": "Actual Net Profit/Loss", "temporary_loss_after": "Temporary Loss After Trade",
            "recovered_from_cycle": "Amount Recovered From Temporary Cycle",
            "transferred_to_pool": "Amount Transferred To Permanent Loss Pool",
            "recovered_from_pool": "Amount Recovered From Permanent Loss Pool",
            "permanent_pool_after": "Permanent Loss Pool After Trade",
            "win_pool_before": "Win Pool Before Trade", "win_pool_contribution": "Win Pool Contribution",
            "win_pool_lp_payment": "Win Pool -> LP Payment", "win_pool_after": "Win Pool After Trade",
            "strategy": "Strategy", "direction": "Direction",
            "balance_before": "Balance Before", "balance_after": "Balance After",
        })
    st.dataframe(tl, width="stretch", height=420)
    st.download_button("Download tiered_money_management_trade_log.csv", trade_log.to_csv(index=False),
                        file_name="tiered_money_management_trade_log.csv", mime="text/csv")

    st.subheader("Charts")
    if not curves["balance"]:
        st.info("No trades were taken, so there are no curves to plot.")
        return

    ch1, ch2 = st.columns(2)
    with ch1:
        st.pyplot(_line_chart(curves["balance"], "1. Balance Curve", "Balance ($)"))
        plt.close("all")
    with ch2:
        st.pyplot(_line_chart(curves["temporary_cycle_loss"], "2. Temporary Cycle Loss Curve",
                               "Temporary Cycle Loss ($)", color="#dd8822"))
        plt.close("all")

    ch3, ch4 = st.columns(2)
    with ch3:
        st.pyplot(_line_chart(curves["permanent_loss_pool"], "3. Permanent Loss Pool Curve",
                               "Permanent Loss Pool ($)", color="#dd2222"))
        plt.close("all")
    with ch4:
        st.pyplot(_line_chart(curves["final_stake"], "4. Final Stake Curve", "Final Stake ($)", color="#c9a227"))
        plt.close("all")

    ch5, ch6 = st.columns(2)
    with ch5:
        st.pyplot(_line_chart(curves["win_pool"], "5. Win Pool Curve", "Win Pool ($)", color="#00aa44"))
        plt.close("all")
    with ch6:
        if loss_streaks:
            streak_counts = pd.Series(loss_streaks).value_counts().sort_index()
            st.pyplot(_bar_chart([str(k) for k in streak_counts.index], streak_counts.values,
                                  "6. Back-To-Back Loss Streak Distribution", "Number Of Cycles"))
            plt.close("all")
        else:
            st.info("No losing cycles recorded — nothing to plot for the loss-streak distribution.")

    st.subheader("Back-To-Back Loss Streak Counts")
    if loss_streaks:
        streak_counts = pd.Series(loss_streaks).value_counts().sort_index()
        streak_table = pd.DataFrame({
            "Back-To-Back Losses": [f"{k} in a row" for k in streak_counts.index],
            "Number Of Times": streak_counts.values,
        })
        st.dataframe(streak_table, width="stretch", hide_index=True)
    else:
        st.info("No losing cycles recorded yet.")
