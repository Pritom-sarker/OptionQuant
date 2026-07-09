"""
Recreates btc_polymarket_signal_tester.pine exactly, in pandas:
  - ATR(14) Wilder smoothing, ATR SMA(50), EMA20/50/200
  - 4 candle patterns: ATR Reversal, Engulfing, Hammer/SS, Exhaustion
  - 5 filters: F1 Trend, F2 Volatility, F3 Close Location, F4 Continuation,
    F5 Anti-chop
  - Active signal = pattern direction AND every *enabled* filter passes.
"""
from __future__ import annotations
import time

import numpy as np
import pandas as pd

import config

FILTER_NAMES = {
    "f1": "trend",
    "f2": "volatility",
    "f3": "close location",
    "f4": "continuation",
    "f5": "anti-chop",
}


def candles_to_df(candles: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(candles)
    return df.reset_index(drop=True)


def _wilder_atr(df: pd.DataFrame, length: int) -> pd.Series:
    high, low, close = df["high"], df["low"], df["close"]
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    tr.iloc[0] = (high.iloc[0] - low.iloc[0])

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


def compute_indicators(df: pd.DataFrame, atr_length: int, atr_sma_length: int) -> pd.DataFrame:
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
    for key in ("f1", "f2", "f3", "f4", "f5"):
        if enabled.get(key, True):
            ok = ok & filters[key].fillna(False)
    return ok


PATTERN_PRIORITY = ["ATR Reversal", "Engulfing", "Hammer/SS", "Exhaustion"]


def evaluate_patterns(df: pd.DataFrame, patterns_settings: dict, atr_mult: float) -> dict:
    """
    Runs every *enabled* base pattern's own detect_pattern/compute_filters/
    compute_active_signal completely independently of the others — identical,
    unmodified per-pattern math to a single-pattern Pine strategy (Pine itself
    never runs more than one pattern at a time, so there is no "combined mode"
    to match there).

    Multiple enabled patterns are resolved to a single signal per candle by
    PATTERN_PRIORITY order: the highest-priority enabled pattern that actually
    fired a raw shape on that candle "wins" the candle, and only *that*
    pattern's own filter toggles decide whether the candle's signal is active
    — other enabled patterns' filters never apply to a shape they didn't
    themselves detect.
    """
    per_pattern: dict[str, dict] = {}
    for name in PATTERN_PRIORITY:
        cfg = patterns_settings.get(name)
        if not cfg or not cfg.get("enabled"):
            continue
        pat_dir = detect_pattern(df, name, atr_mult)
        filters = compute_filters(df, pat_dir)
        act_ok = compute_active_signal(pat_dir, filters, cfg["filters"])
        per_pattern[name] = {
            "pat_dir": pat_dir, "filters": filters, "act_ok": act_ok,
            "enabled_filters": cfg["filters"],
        }

    n = len(df)
    combined_dir = pd.Series(0, index=df.index)
    combined_mode = pd.Series([""] * n, index=df.index, dtype=object)
    combined_act_ok = pd.Series(False, index=df.index)

    for pos in range(n):
        i = df.index[pos]
        for name, p in per_pattern.items():
            d = int(p["pat_dir"].loc[i])
            if d != 0:
                combined_dir.loc[i] = d
                combined_mode.loc[i] = name
                combined_act_ok.loc[i] = bool(p["act_ok"].loc[i])
                break

    return {
        "per_pattern": per_pattern, "combined_dir": combined_dir,
        "combined_mode": combined_mode, "combined_act_ok": combined_act_ok,
    }


def build_condition_breakdown(df: pd.DataFrame, pat_dir: pd.Series, mode: str, atr_mult: float,
                               enabled: dict[str, bool], idx: int = -1) -> list[dict]:
    """
    Detailed pass/fail breakdown for one candle (default: the latest), with
    the *actual* computed value and the *required* value side by side, so
    it's clear exactly what's matching and what's missing.
    """
    row = df.iloc[idx]
    d = int(pat_dir.iloc[idx])
    rows = []

    body, atr = row["body"], row["atr"]
    needed_body = atr * atr_mult if pd.notna(atr) else float("nan")
    if mode == "ATR Reversal":
        pattern_ok = pd.notna(atr) and body >= needed_body
        rows.append({
            "condition": "Pattern — ATR Reversal",
            "actual": f"body {body:.2f} ({'red' if row['is_red'] else 'green' if row['is_green'] else 'flat'} candle)",
            "required": f"body >= ATR({atr:.2f}) x {atr_mult} = {needed_body:.2f}" if pd.notna(atr) else "ATR not ready yet",
            "status": "PASS" if pattern_ok else "FAIL",
        })
    else:
        rows.append({
            "condition": f"Pattern — {mode}",
            "actual": f"raw direction = {_dir_label(d)}",
            "required": f"{mode} shape must be detected on this candle",
            "status": "PASS" if d != 0 else "FAIL",
        })

    def add(key: str, label: str, actual: str, required: str, passed: bool):
        if not enabled.get(key, True):
            rows.append({"condition": label, "actual": "filter disabled in sidebar",
                         "required": "n/a — turned OFF", "status": "OFF"})
            return
        if d == 0:
            status = "N/A"
        else:
            status = "PASS" if passed else "FAIL"
        rows.append({"condition": label, "actual": actual, "required": required, "status": status})

    ema20, ema50, atr_sma = row["ema20"], row["ema50"], row["atr_sma"]
    if d == 1:
        f1_pass = ema20 > ema50
        f1_req = f"need EMA20 > EMA50 (UP)"
    elif d == -1:
        f1_pass = ema20 < ema50
        f1_req = f"need EMA20 < EMA50 (DOWN)"
    else:
        f1_pass, f1_req = False, "need a pattern direction first"
    add("f1", "F1 Trend", f"EMA20={ema20:.2f}, EMA50={ema50:.2f}", f1_req, f1_pass)

    f2_pass = pd.notna(atr_sma) and atr > atr_sma
    f2_actual = f"ATR={atr:.2f}, ATR_SMA={atr_sma:.2f}" if pd.notna(atr_sma) else f"ATR={atr:.2f}, ATR_SMA not ready yet"
    add("f2", "F2 Volatility", f2_actual, "need ATR > ATR_SMA", f2_pass)

    range_ = row["range_"]
    loc_ratio_up = (row["close"] - row["low"]) / range_
    loc_ratio_dn = (row["high"] - row["close"]) / range_
    if d == 1:
        f3_pass, f3_actual, f3_req = loc_ratio_up >= config.F3_CLOSE_LOCATION_PCT, \
            f"(close-low)/range = {loc_ratio_up:.2f}", f"need >= {config.F3_CLOSE_LOCATION_PCT:.2f} (top 30%)"
    elif d == -1:
        f3_pass, f3_actual, f3_req = loc_ratio_dn >= config.F3_CLOSE_LOCATION_PCT, \
            f"(high-close)/range = {loc_ratio_dn:.2f}", f"need >= {config.F3_CLOSE_LOCATION_PCT:.2f} (bottom 30%)"
    else:
        f3_pass, f3_actual, f3_req = False, "—", "need a pattern direction first"
    add("f3", "F3 Close Location", f3_actual, f3_req, f3_pass)

    pos = idx if idx >= 0 else len(df) + idx
    prev = df.iloc[pos - 1] if pos > 0 else None
    if prev is not None:
        if d == 1:
            f4_pass = bool(row["is_green"] and prev["is_green"] and row["close"] > prev["high"])
            f4_actual = f"close={row['close']:.2f}, prev_high={prev['high']:.2f}, current & prev both green={bool(row['is_green'] and prev['is_green'])}"
            f4_req = "need current & previous candle both green AND close > prev high"
        elif d == -1:
            f4_pass = bool(row["is_red"] and prev["is_red"] and row["close"] < prev["low"])
            f4_actual = f"close={row['close']:.2f}, prev_low={prev['low']:.2f}, current & prev both red={bool(row['is_red'] and prev['is_red'])}"
            f4_req = "need current & previous candle both red AND close < prev low"
        else:
            f4_pass, f4_actual, f4_req = False, "—", "need a pattern direction first"
    else:
        f4_pass, f4_actual, f4_req = False, "no previous candle available", "need a previous candle"
    add("f4", "F4 Continuation", f4_actual, f4_req, f4_pass)

    ema_spread = abs(ema20 - ema50)
    needed_spread = atr * config.F5_ANTI_CHOP_ATR_MULT if pd.notna(atr) else float("nan")
    f5_pass = pd.notna(atr) and ema_spread > needed_spread
    add("f5", "F5 Anti-Chop", f"|EMA20-EMA50| = {ema_spread:.2f}",
        f"need > ATR({atr:.2f}) x {config.F5_ANTI_CHOP_ATR_MULT} = {needed_spread:.2f}" if pd.notna(atr) else "ATR not ready yet",
        f5_pass)

    return rows


def _dir_label(d: int) -> str:
    return "UP" if d == 1 else "DOWN" if d == -1 else "NONE"


def color_label(d: int) -> str:
    """GREEN/RED/UNKNOWN — the next-candle-color prediction wording."""
    return "GREEN" if d == 1 else "RED" if d == -1 else "UNKNOWN"


def _pattern_description(mode: str, d: int, body: float, atr: float) -> str:
    mult = body / atr if atr else 0.0
    if mode == "ATR Reversal":
        color = "red" if d == 1 else "green"
        return f"ATR reversal found. Candle is a big {color} candle. Body is {mult:.1f}x ATR"
    if mode == "Hammer/SS":
        shape = "hammer" if d == 1 else "shooting star"
        return f"{shape.capitalize()} pattern found"
    if mode == "Engulfing":
        shape = "bullish engulfing" if d == 1 else "bearish engulfing"
        return f"{shape.capitalize()} pattern found"
    if mode == "Exhaustion":
        shape = "bullish exhaustion (3 red candles, shrinking bodies)" if d == 1 else \
                "bearish exhaustion (3 green candles, shrinking bodies)"
        return f"{shape.capitalize()} found"
    return "Pattern found"


def _pattern_phrase(mode: str, d: int, body: float, atr: float) -> str:
    """Lowercase-friendly phrase for embedding mid-sentence after 'because'."""
    mult = body / atr if atr else 0.0
    if mode == "ATR Reversal":
        color = "red" if d == 1 else "green"
        return f"candle was a big {color} candle (ATR reversal, body {mult:.1f}x ATR)"
    if mode == "Hammer/SS":
        shape = "hammer" if d == 1 else "shooting star"
        return f"a {shape} pattern was detected"
    if mode == "Engulfing":
        shape = "bullish engulfing" if d == 1 else "bearish engulfing"
        return f"a {shape} pattern was detected"
    if mode == "Exhaustion":
        shape = "bullish exhaustion (3 red candles, shrinking bodies)" if d == 1 else \
                "bearish exhaustion (3 green candles, shrinking bodies)"
        return f"a {shape} pattern was detected"
    return "a pattern was detected"


def evaluate_signal_results(df: pd.DataFrame, pat_dir: pd.Series, act_ok: pd.Series) -> pd.Series:
    """
    For every candle N with a confirmed signal, evaluate that signal against
    candle N+1's OWN body (open vs close) — never against candle N itself,
    and never against N+1's close relative to N's close:
      GREEN/UP   wins if close[N+1] > open[N+1]
      RED/DOWN   wins if close[N+1] < open[N+1]
      close[N+1] == open[N+1] (exact tie) -> LOSS for the side that needed
        a strict move — vanishingly rare, but Polymarket itself has to break
        the tie somehow, so this can never register as a win either way.
    Returns "WIN" / "LOSS" / "PENDING" per row, or None where there was no
    signal. This matches trade_engine.settle_at_expiry exactly — a real
    trade resolves on N+1's own open-vs-close, so Tab 1's displayed
    WIN/LOSS must use the identical rule or the two can silently disagree
    on the same candle (N+1 can look bearish on its own body yet still
    have closed above N's close, or vice versa).

    Candle N+1's CLOSE price is only used once N+1 has actually closed —
    its close timestamp must be in the past. btc_price_api.py already drops
    the currently-forming candle before it ever reaches this function, so
    this check is a second, explicit guard against ever scoring a signal off
    a still-moving live candle (defense in depth, not reliance on caller
    behavior).
    """
    n = len(df)
    open_ = df["open"].values
    close = df["close"].values
    candle_time = df["time"].values
    now = time.time()
    results: list[str | None] = [None] * n
    for i in range(n):
        if not bool(act_ok.iloc[i]):
            continue
        d = int(pat_dir.iloc[i])
        if i + 1 >= n:
            results[i] = "PENDING"
            continue
        if candle_time[i + 1] > now:
            results[i] = "PENDING"   # next candle's close timestamp hasn't arrived yet
            continue
        next_open, next_close = open_[i + 1], close[i + 1]
        if next_close > next_open:
            next_direction = 1
        elif next_close < next_open:
            next_direction = -1
        else:
            next_direction = 0   # tie -> never a win for either side
        results[i] = "WIN" if d == next_direction else "LOSS"
    return pd.Series(results, index=df.index)


def compute_full_stats(df: pd.DataFrame, pat_dir: pd.Series, act_ok: pd.Series,
                        results: pd.Series, min_signals: int) -> dict:
    """
    Matches btc_polymarket_signal_tester.pine's dashboard scan exactly: a
    signal only counts once its own resolution (the next candle) is a fully
    closed bar. The most recent candle's signal (if any) therefore can never
    be resolved yet, so it is excluded from every total below and reported
    separately as "pending" — mirroring Pine's `off >= 2` scan boundary.
    """
    n = len(df)
    eligible_end = max(0, n - 1)   # exclude the last row — its resolution isn't available yet

    pd_e  = pat_dir.iloc[:eligible_end]
    ok_e  = act_ok.iloc[:eligible_end]
    res_e = results.iloc[:eligible_end]

    raw_total    = int((pd_e != 0).sum())
    active_total = int(ok_e.sum())
    up_signals   = int(((pd_e == 1) & ok_e).sum())
    dn_signals   = int(((pd_e == -1) & ok_e).sum())

    resolved_mask = res_e.isin(["WIN", "LOSS"])
    wins   = int((res_e[resolved_mask] == "WIN").sum())
    losses = int((res_e[resolved_mask] == "LOSS").sum())
    win_rate = (wins / (wins + losses) * 100.0) if (wins + losses) else 0.0

    up_mask   = resolved_mask & (pd_e == 1) & ok_e
    up_total  = int(up_mask.sum())
    up_wins   = int((res_e[up_mask] == "WIN").sum())
    up_win_rate = (up_wins / up_total * 100.0) if up_total else 0.0

    dn_mask   = resolved_mask & (pd_e == -1) & ok_e
    dn_total  = int(dn_mask.sum())
    dn_wins   = int((res_e[dn_mask] == "WIN").sum())
    dn_win_rate = (dn_wins / dn_total * 100.0) if dn_total else 0.0

    # Streaks and last-signal/last-result: walked chronologically (oldest to
    # newest), exactly matching the Pine loop's iteration order.
    cur_streak = 0
    max_streak = 0
    last_signal_dir = 0
    last_result = None
    for i in range(eligible_end):
        if not bool(act_ok.iloc[i]):
            continue
        r = results.iloc[i]
        if r == "WIN":
            cur_streak = 0
            last_signal_dir = int(pat_dir.iloc[i])
            last_result = "WIN"
        elif r == "LOSS":
            cur_streak += 1
            max_streak = max(max_streak, cur_streak)
            last_signal_dir = int(pat_dir.iloc[i])
            last_result = "LOSS"

    pending_dir = int(pat_dir.iloc[-1]) if n > 0 and bool(act_ok.iloc[-1]) else 0

    return {
        "raw_total": raw_total,
        "active_total": active_total,
        "up_signals": up_signals,
        "dn_signals": dn_signals,
        "wins": wins,
        "losses": losses,
        "win_rate": win_rate,
        "up_total": up_total,
        "up_win_rate": up_win_rate,
        "dn_total": dn_total,
        "dn_win_rate": dn_win_rate,
        "max_consecutive_losses": max_streak,
        "current_loss_streak": cur_streak,
        "last_signal": color_label(last_signal_dir) if last_signal_dir != 0 else "NONE",
        "last_result": last_result or "—",
        "pending_signal": color_label(pending_dir) if pending_dir != 0 else "NONE",
        "below_min_signals": active_total < min_signals,
    }


def build_reason(mode: str, d: int, act_ok: bool, filt_status: dict[str, str], body: float, atr: float,
                  result: str | None = None, next_close: float | None = None, next_open: float | None = None) -> str:
    if d == 0:
        return "NO SIGNAL: No valid candle pattern detected."

    desc = _pattern_description(mode, d, body, atr)
    if not act_ok:
        failing = [FILTER_NAMES[k] for k, v in filt_status.items() if v == "FAIL"]
        if failing:
            return f"NO SIGNAL: {desc}, but {failing[0]} filter failed."
        return f"NO SIGNAL: {desc}, but conditions were not fully met."

    phrase = _pattern_phrase(mode, d, body, atr)
    lead = f"Signal {color_label(d)} because {phrase}."
    if result == "PENDING" or result is None:
        return f"{lead} Next candle hasn't closed yet — result pending."
    side_map = {(1, "WIN"): "above", (1, "LOSS"): "below",
                (-1, "WIN"): "below", (-1, "LOSS"): "above"}
    side = side_map.get((d, result), "at")
    return f"{lead} Next candle closed {side} its own open ({next_open:.2f}), so result = {result}."


def build_signal_table(df: pd.DataFrame, per_pattern: dict, combined_dir: pd.Series,
                        combined_mode: pd.Series, combined_act_ok: pd.Series, last_n: int) -> list[dict]:
    """
    Builds the last-N-candle next-candle-prediction table rows, newest last.
    `per_pattern`/`combined_*` come from evaluate_patterns() — each row is
    displayed using whichever enabled pattern actually won that candle
    (combined_mode), falling back to the highest-priority enabled pattern for
    candles where nothing fired (so filter columns still have something
    sensible to show).
    """
    results = evaluate_signal_results(df, combined_dir, combined_act_ok)
    fallback_pattern = next(iter(per_pattern), None)
    rows = []
    idx_positions = range(max(0, len(df) - last_n), len(df))
    for pos in idx_positions:
        i = df.index[pos]
        d = int(combined_dir.loc[i])
        mode = combined_mode.loc[i] or fallback_pattern
        atr_val = df["atr"].loc[i]
        body_val = df["body"].loc[i]
        ratio = body_val / atr_val if pd.notna(atr_val) and atr_val > 0 else float("nan")

        filt_status = {}
        if mode is not None:
            p = per_pattern[mode]
            for key in ("f1", "f2", "f3", "f4", "f5"):
                if not p["enabled_filters"].get(key, True):
                    filt_status[key] = "OFF"
                else:
                    val = p["filters"][key].loc[i]
                    passed = bool(val) if pd.notna(val) else False
                    filt_status[key] = "PASS" if passed else "FAIL"
        else:
            filt_status = {k: "OFF" for k in ("f1", "f2", "f3", "f4", "f5")}

        predicted = bool(combined_act_ok.loc[i]) if d != 0 else False
        predicted_next = color_label(d) if predicted else "UNKNOWN"
        result = results.loc[i]  # WIN / LOSS / PENDING / None

        next_close = df["close"].iloc[pos + 1] if pos + 1 < len(df) else None
        next_open = df["open"].iloc[pos + 1] if pos + 1 < len(df) else None

        reason = build_reason(mode or "no pattern enabled", d, predicted, filt_status, body_val,
                               atr_val if pd.notna(atr_val) else 0.0,
                               result=result, next_close=next_close, next_open=next_open)

        rows.append({
            "time": df["time"].loc[i],
            "open": df["open"].loc[i],
            "high": df["high"].loc[i],
            "low": df["low"].loc[i],
            "close": df["close"].loc[i],
            "atr": atr_val,
            "body": body_val,
            "body_atr_ratio": ratio,
            "raw_pattern": _dir_label(d),
            "pattern_name": mode or "—",
            "f1_trend": filt_status["f1"],
            "f2_volatility": filt_status["f2"],
            "f3_close_location": filt_status["f3"],
            "f4_continuation": filt_status["f4"],
            "f5_anti_chop": filt_status["f5"],
            "predicted_next": predicted_next,
            "next_close": next_close,
            "result": result if result is not None else ("—" if not predicted else "PENDING"),
            "reason": reason,
        })
    return rows
