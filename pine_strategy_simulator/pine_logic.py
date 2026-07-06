"""
Exact Python port of btc_polymarket_signal_tester.pine's indicators, 4 base
patterns, and 5 filters. Every formula here is copied line-for-line from the
Pine script (see the file's ATR/pattern/filter sections) — nothing here
invents new strategy behavior.

Kept standalone from the main app's signal_engine.py (this project imports
nothing from the parent OptionQuant app) even though the math is identical.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

import config


def _wilder_atr(df: pd.DataFrame, length: int) -> pd.Series:
    """Matches Pine's ta.atr(length) — RMA (Wilder) smoothing of True Range."""
    high, low, close = df["high"], df["low"], df["close"]
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    tr.iloc[0] = high.iloc[0] - low.iloc[0]

    atr = pd.Series(np.nan, index=df.index)
    n = len(df)
    if n < length:
        return atr
    seed = tr.iloc[:length].mean()
    atr.iloc[length - 1] = seed
    prev_atr = seed
    for i in range(length, n):
        prev_atr = (prev_atr * (length - 1) + tr.iloc[i]) / length
        atr.iloc[i] = prev_atr
    return atr


def compute_indicators(df: pd.DataFrame, atr_length: int = config.ATR_LENGTH,
                        atr_sma_length: int = config.ATR_SMA_LENGTH) -> pd.DataFrame:
    df = df.copy()
    df["atr"] = _wilder_atr(df, atr_length)
    df["atr_sma"] = df["atr"].rolling(window=atr_sma_length, min_periods=atr_sma_length).mean()
    df["ema20"] = df["close"].ewm(span=20, adjust=False).mean()
    df["ema50"] = df["close"].ewm(span=50, adjust=False).mean()
    df["ema200"] = df["close"].ewm(span=200, adjust=False).mean()

    df["body"] = (df["close"] - df["open"]).abs()
    df["body_safe"] = df["body"].clip(lower=config.MINTICK)
    df["up_wick"] = df["high"] - df[["open", "close"]].max(axis=1)
    df["dn_wick"] = df[["open", "close"]].min(axis=1) - df["low"]
    df["range_"] = (df["high"] - df["low"]).clip(lower=config.MINTICK)
    df["is_green"] = df["close"] > df["open"]
    df["is_red"] = df["open"] > df["close"]
    return df


def detect_pattern(df: pd.DataFrame, mode: str, atr_mult: float) -> pd.Series:
    """
    Raw directional signal (1 = UP predicted, -1 = DOWN predicted, 0 = none).
    Only "ATR Reversal" actually uses atr_mult — Engulfing/Hammer-SS/Exhaustion
    don't take an ATR multiplier in the Pine script either.
    """
    body, body_safe = df["body"], df["body_safe"]
    is_green, is_red = df["is_green"], df["is_red"]
    up_wick, dn_wick = df["up_wick"], df["dn_wick"]
    prev_is_red, prev_is_green = is_red.shift(1), is_green.shift(1)
    prev2_is_red, prev2_is_green = is_red.shift(2), is_green.shift(2)
    body1, body2 = body.shift(1), body.shift(2)
    open_, close_ = df["open"], df["close"]
    prev_open, prev_close = open_.shift(1), close_.shift(1)

    if mode == "Engulfing":
        bull = is_green & prev_is_red & (open_ <= prev_close) & (close_ >= prev_open)
        bear = is_red & prev_is_green & (open_ >= prev_close) & (close_ <= prev_open)
        return pd.Series(np.where(bull, 1, np.where(bear, -1, 0)), index=df.index)

    if mode == "Hammer/SS":
        hammer = (dn_wick >= 2.0 * body_safe) & (up_wick <= body_safe) & prev_is_red
        star = (up_wick >= 2.0 * body_safe) & (dn_wick <= body_safe) & prev_is_green
        return pd.Series(np.where(hammer, 1, np.where(star, -1, 0)), index=df.index)

    if mode == "Exhaustion":
        bull = is_red & prev_is_red & prev2_is_red & (body < body1) & (body < body2)
        bear = is_green & prev_is_green & prev2_is_green & (body < body1) & (body < body2)
        return pd.Series(np.where(bull, 1, np.where(bear, -1, 0)), index=df.index)

    # default: ATR Reversal
    big = body >= df["atr"] * atr_mult
    return pd.Series(np.where(big & is_red, 1, np.where(big & is_green, -1, 0)), index=df.index)


def compute_filters(df: pd.DataFrame, pat_dir: pd.Series) -> dict[str, pd.Series]:
    ema20, ema50, atr = df["ema20"], df["ema50"], df["atr"]
    close, low, high = df["close"], df["low"], df["high"]
    range_ = df["range_"]
    is_green, is_red = df["is_green"], df["is_red"]
    prev_is_green, prev_is_red = is_green.shift(1), is_red.shift(1)
    prev_high, prev_low = high.shift(1), low.shift(1)

    f1_bull, f1_bear = ema20 > ema50, ema20 < ema50
    f2_pass = atr > df["atr_sma"]
    f3_bull = (close - low) / range_ >= config.F3_CLOSE_LOCATION_PCT
    f3_bear = (high - close) / range_ >= config.F3_CLOSE_LOCATION_PCT
    f4_bull = is_green & prev_is_green & (close > prev_high)
    f4_bear = is_red & prev_is_red & (close < prev_low)
    f5_pass = (ema20 - ema50).abs() > atr * config.F5_ANTI_CHOP_ATR_MULT

    d = pat_dir
    f1_ok = pd.Series(np.where(d == 1, f1_bull, np.where(d == -1, f1_bear, False)), index=df.index)
    f3_ok = pd.Series(np.where(d == 1, f3_bull, np.where(d == -1, f3_bear, False)), index=df.index)
    f4_ok = pd.Series(np.where(d == 1, f4_bull, np.where(d == -1, f4_bear, False)), index=df.index)

    return {"f1": f1_ok, "f2": f2_pass, "f3": f3_ok, "f4": f4_ok, "f5": f5_pass}


def compute_active_signal(pat_dir: pd.Series, filters: dict[str, pd.Series], enabled: dict[str, bool]) -> pd.Series:
    ok = pat_dir != 0
    for key in config.FILTER_KEYS:
        if enabled.get(key, False):
            ok = ok & filters[key].fillna(False)
    return ok
