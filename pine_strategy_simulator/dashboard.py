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

    tab1, tab2 = st.tabs(["All Results", "Filtered Results"])

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
