"""
Runs the Pine strategy over historical candles and extracts a trade log.

Scoring rule (per explicit correction — this is a deliberate refinement of
the Pine script's own dashboard scoring, not a bug):
  - A signal fires on candle N and predicts candle N+1 only. It is scored
    once N+1 is a fully closed historical bar — never against N itself,
    never against a still-forming candle.
  - UP signal:   WIN if close[N+1] > open[N+1], LOSS if close[N+1] < open[N+1]
  - DOWN signal: WIN if close[N+1] < open[N+1], LOSS if close[N+1] > open[N+1]
  - close[N+1] == open[N+1] -> NEUTRAL (excluded from win/loss counts and
    from win-rate/loss-rate, per the correction).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

import pine_logic


def compute_pattern_context(df: pd.DataFrame, mode: str, atr_mult: float):
    """Raw pattern direction + filter pass/fail series — independent of which
    filters are actually enabled, so this is computed once and reused across
    all 32 filter combinations for a given (mode, atr_mult)."""
    pat_dir = pine_logic.detect_pattern(df, mode, atr_mult)
    filters = pine_logic.compute_filters(df, pat_dir)
    return pat_dir, filters


def extract_trades(df: pd.DataFrame, pat_dir: pd.Series, filters: dict, enabled_flags: dict) -> pd.DataFrame:
    """
    Builds the resolved trade log for one filter combination: every candle
    where the (pattern + enabled filters) signal fired, scored against the
    very next candle's own open/close (see module docstring).
    """
    act_ok = pine_logic.compute_active_signal(pat_dir, filters, enabled_flags)
    n = len(df)
    if n < 2:
        return _empty_trades()

    act_ok_scoreable = act_ok.iloc[:-1]   # last row has no next candle to resolve against
    fired = act_ok_scoreable[act_ok_scoreable].index
    if len(fired) == 0:
        return _empty_trades()

    d = pat_dir.loc[fired].astype(int).to_numpy()
    signal_time = df["time"].loc[fired].to_numpy()
    signal_close = df["close"].loc[fired].to_numpy()
    result_open = df["open"].shift(-1).loc[fired].to_numpy()
    result_close = df["close"].shift(-1).loc[fired].to_numpy()

    move = d * (result_close - result_open)
    result = np.where(move > 0, "WIN", np.where(move < 0, "LOSS", "NEUTRAL"))
    direction_label = np.where(d == 1, "UP", "DOWN")

    return pd.DataFrame({
        "signal_time": signal_time, "direction": direction_label,
        "signal_close": signal_close, "result_candle_open": result_open,
        "result_candle_close": result_close, "result": result, "move": move,
    })


def _empty_trades() -> pd.DataFrame:
    return pd.DataFrame(columns=["signal_time", "direction", "signal_close",
                                  "result_candle_open", "result_candle_close", "result", "move"])


def run_sweep(df: pd.DataFrame, pair: str, timeframe: str, strategies: list, atr_mults: list,
              filter_combos: list, log=lambda msg: None, tick=lambda msg: None,
              progress_cb=lambda done, total: None) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Runs every strategy x ATR-multiplier x filter-combination over a single
    (pair, timeframe) dataset — this is the automatic full scan triggered by
    selecting a pair/timeframe/candle count. Pattern/filter series are
    computed once per (strategy, ATR mult) and reused across all 32 filter
    combinations, since the filter toggles never change raw pattern
    detection itself.
    """
    import metrics

    total_candles = len(df)
    total_combos = len(strategies) * len(atr_mults) * len(filter_combos)
    summary_rows = []
    trade_frames = []
    done = 0

    for mode in strategies:
        log(f"Running strategy {mode}...")
        for atr_mult in atr_mults:
            log(f"Testing ATR {atr_mult}...")
            pat_dir, filters = compute_pattern_context(df, mode, atr_mult)
            for combo in filter_combos:
                tick(f"Testing filter combination {combo['label']}...")
                trades = extract_trades(df, pat_dir, filters, combo["flags"])
                stats = metrics.summarize_trades(trades, total_candles)
                summary_rows.append({
                    "pair": pair, "timeframe": timeframe, "total_candles": total_candles,
                    "strategy": mode, "atr_mult": atr_mult, "filters_label": combo["label"], **stats,
                })
                if len(trades):
                    t = trades.copy()
                    t["pair"] = pair
                    t["timeframe"] = timeframe
                    t["strategy"] = mode
                    t["atr_mult"] = atr_mult
                    t["filters"] = combo["label"]
                    trade_frames.append(t)
                done += 1
                if done % 16 == 0 or done == total_combos:
                    log(f"Completed {done} / {total_combos} combinations...")
                progress_cb(done, total_combos)

    summary_df = pd.DataFrame(summary_rows)
    trades_df = pd.concat(trade_frames, ignore_index=True) if trade_frames else pd.DataFrame(
        columns=["signal_time", "direction", "signal_close", "result_candle_open", "result_candle_close",
                 "result", "move", "pair", "timeframe", "strategy", "atr_mult", "filters"])
    return summary_df, trades_df
