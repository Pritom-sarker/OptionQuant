"""Per-combo trade summary + ranking tables for a single (pair, timeframe, candle_count) sweep."""
from __future__ import annotations

import numpy as np
import pandas as pd

import config


def _streaks(results: list[str]) -> tuple[int, int, int]:
    """(max_consecutive_losses, current_loss_streak, max_consecutive_wins),
    walked chronologically. NEUTRAL results are skipped — they break neither
    a win streak nor a loss streak (same "excluded from win/loss" treatment
    as the win-rate/loss-rate formulas)."""
    max_loss, cur_loss, max_win, cur_win = 0, 0, 0, 0
    for r in results:
        if r == "WIN":
            cur_win += 1
            max_win = max(max_win, cur_win)
            cur_loss = 0
        elif r == "LOSS":
            cur_loss += 1
            max_loss = max(max_loss, cur_loss)
            cur_win = 0
        # NEUTRAL: skip entirely, neither streak resets
    return max_loss, cur_loss, max_win


def _empty_stats(total_candles: int) -> dict:
    return {
        "total_signals": 0, "wins": 0, "losses": 0, "neutral": 0,
        "win_rate": 0.0, "loss_rate": 0.0, "signal_frequency": 0.0,
        "avg_next_candle_move": None, "avg_winning_move": None, "avg_losing_move": None,
        "best_win_move": None, "worst_loss_move": None,
        "max_consecutive_losses": 0, "current_loss_streak": 0, "max_consecutive_wins": 0,
        "last_signal_time": None, "last_signal_direction": "NONE", "last_signal_result": "—",
        "last_signal_candle_close": None, "result_candle_open": None, "result_candle_close": None,
    }


def summarize_trades(trades: pd.DataFrame, total_candles: int) -> dict:
    total = len(trades)
    if total == 0:
        return _empty_stats(total_candles)

    wins = int((trades["result"] == "WIN").sum())
    losses = int((trades["result"] == "LOSS").sum())
    neutral = int((trades["result"] == "NEUTRAL").sum())
    resolved = wins + losses
    win_rate = (wins / resolved * 100.0) if resolved else 0.0
    loss_rate = (losses / resolved * 100.0) if resolved else 0.0
    signal_frequency = (total / total_candles * 100.0) if total_candles else 0.0

    moves = trades["move"].to_numpy(dtype=float)
    win_moves = moves[trades["result"].to_numpy() == "WIN"]
    loss_moves = moves[trades["result"].to_numpy() == "LOSS"]

    max_loss, cur_loss, max_win = _streaks(trades["result"].tolist())

    last = trades.iloc[-1]
    return {
        "total_signals": total, "wins": wins, "losses": losses, "neutral": neutral,
        "win_rate": win_rate, "loss_rate": loss_rate, "signal_frequency": signal_frequency,
        "avg_next_candle_move": float(moves.mean()),
        "avg_winning_move": float(win_moves.mean()) if len(win_moves) else None,
        "avg_losing_move": float(loss_moves.mean()) if len(loss_moves) else None,
        "best_win_move": float(win_moves.max()) if len(win_moves) else None,
        "worst_loss_move": float(loss_moves.min()) if len(loss_moves) else None,
        "max_consecutive_losses": max_loss, "current_loss_streak": cur_loss, "max_consecutive_wins": max_win,
        "last_signal_time": int(last["signal_time"]), "last_signal_direction": last["direction"],
        "last_signal_result": last["result"], "last_signal_candle_close": float(last["signal_close"]),
        "result_candle_open": float(last["result_candle_open"]), "result_candle_close": float(last["result_candle_close"]),
    }


# ─── Ranking tables (operate on the full 768-row single-dataset sweep) ───────

def best_overall(summary: pd.DataFrame, top_n: int = 15) -> pd.DataFrame:
    reliable = summary[summary["total_signals"] >= config.MIN_SIGNALS_FOR_RELIABLE]
    return reliable.sort_values("win_rate", ascending=False).head(top_n)


def best_high_frequency(summary: pd.DataFrame, top_n: int = 15, min_win_rate: float = 50.0) -> pd.DataFrame:
    candidates = summary[(summary["total_signals"] >= config.MIN_SIGNALS_FOR_RELIABLE) &
                          (summary["win_rate"] >= min_win_rate)]
    return candidates.sort_values("signal_frequency", ascending=False).head(top_n)


def best_low_risk(summary: pd.DataFrame, top_n: int = 15) -> pd.DataFrame:
    reliable = summary[summary["total_signals"] >= config.MIN_SIGNALS_FOR_RELIABLE]
    return reliable.sort_values(["max_consecutive_losses", "win_rate"], ascending=[True, False]).head(top_n)


def _best_per_group(summary: pd.DataFrame, group_col: str, group_values: list) -> pd.DataFrame:
    rows = []
    for val in group_values:
        subset = summary[summary[group_col] == val]
        reliable = subset[subset["total_signals"] >= config.MIN_SIGNALS_FOR_RELIABLE]
        pool = reliable if not reliable.empty else subset
        if pool.empty:
            continue
        rows.append(pool.sort_values("win_rate", ascending=False).iloc[0])
    return pd.DataFrame(rows) if rows else pd.DataFrame(columns=summary.columns)


def best_per_strategy(summary: pd.DataFrame) -> pd.DataFrame:
    return _best_per_group(summary, "strategy", config.STRATEGIES)


def best_per_atr(summary: pd.DataFrame) -> pd.DataFrame:
    return _best_per_group(summary, "atr_mult", config.ATR_MULTIPLIERS)


def best_per_filter(summary: pd.DataFrame) -> pd.DataFrame:
    labels = [c["label"] for c in config.FILTER_COMBOS]
    return _best_per_group(summary, "filters_label", labels)


def filter_setups(summary: pd.DataFrame, min_win_rate: float, min_signal_frequency: float,
                   min_total_signals: int = 0) -> pd.DataFrame:
    """Filters the already-computed sweep — never re-runs the backtest itself."""
    mask = (summary["win_rate"] >= min_win_rate) & (summary["signal_frequency"] >= min_signal_frequency)
    if min_total_signals > 0:
        mask = mask & (summary["total_signals"] >= min_total_signals)
    return summary[mask].sort_values("win_rate", ascending=False).reset_index(drop=True)


# ─── Chart aggregations ───────────────────────────────────────────────────────

def _weighted_win_rate(g: pd.DataFrame) -> float:
    resolved = g["wins"].sum() + g["losses"].sum()
    return (g["wins"].sum() / resolved * 100.0) if resolved else 0.0


def win_rate_by_group(summary: pd.DataFrame, group_col: str) -> pd.DataFrame:
    rows = []
    for val, g in summary.groupby(group_col):
        rows.append({group_col: val, "win_rate": _weighted_win_rate(g), "total_signals": int(g["total_signals"].sum())})
    return pd.DataFrame(rows)
