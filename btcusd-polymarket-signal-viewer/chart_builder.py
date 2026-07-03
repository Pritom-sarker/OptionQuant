"""
Builds a static matplotlib candlestick chart for next-candle prediction:
candles, EMA20/50/200, GREEN/RED signal markers on the signal candle, and
WIN/LOSS labels on the candle that resolves it (N+1).

White background, plain matplotlib (no external charting service) — a new
static image is drawn from scratch on every refresh.
"""
from __future__ import annotations
import time

import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle

GREEN = "#00aa44"
RED = "#dd2222"


def build_chart(df: pd.DataFrame, act_ok: pd.Series, pat_dir: pd.Series, results: pd.Series,
                 show_ema: bool, show_signals: bool, visible_candles: int = 30) -> plt.Figure:
    n = min(visible_candles, len(df))
    window_df = df.tail(n).reset_index(drop=True)
    start_pos = len(df) - n   # maps a full-df position -> window position (window_pos = pos - start_pos)

    fig, ax = plt.subplots(figsize=(14, 7.5), dpi=100)
    fig.patch.set_facecolor("white")
    ax.set_facecolor("white")

    y_min = window_df["low"].min()
    y_max = window_df["high"].max()
    price_span = max(y_max - y_min, 1e-6)
    padding = max(price_span * 0.15, 0.005)
    min_body_height = price_span * 0.004   # keeps doji/flat candles visible as a thin bar, not invisible

    body_width = 0.6
    for pos, row in window_df.iterrows():
        color = GREEN if row["close"] >= row["open"] else RED
        ax.plot([pos, pos], [row["low"], row["high"]], color=color, linewidth=1.3, zorder=2)
        body_low = min(row["open"], row["close"])
        height = max(abs(row["close"] - row["open"]), min_body_height)
        ax.add_patch(Rectangle((pos - body_width / 2, body_low), body_width, height,
                                facecolor=color, edgecolor=color, zorder=3))

    if show_ema:
        ax.plot(window_df.index, window_df["ema20"], color="#c9a227", linewidth=1.1, label="EMA 20")
        ax.plot(window_df.index, window_df["ema50"], color="#ff8c00", linewidth=1.1, label="EMA 50")
        ax.plot(window_df.index, window_df["ema200"], color="#555555", linewidth=1.4, label="EMA 200")

    if show_signals:
        offset = price_span * 0.05
        for pos in range(len(df)):
            if not bool(act_ok.iloc[pos]):
                continue
            wpos = pos - start_pos
            if not (0 <= wpos < n):
                continue
            row = df.iloc[pos]
            up = pat_dir.iloc[pos] == 1
            marker_y = row["low"] - offset if up else row["high"] + offset
            ax.scatter(wpos, marker_y, marker="^" if up else "v",
                       color=GREEN if up else RED, s=140, zorder=5, edgecolors="black", linewidths=0.5)

            next_pos = pos + 1
            next_wpos = next_pos - start_pos
            if next_pos < len(df) and 0 <= next_wpos < n:
                res = results.iloc[pos]
                if res in ("WIN", "LOSS"):
                    next_row = df.iloc[next_pos]
                    # Wider offset than the signal marker so the label doesn't
                    # collide with a marker the resolving candle draws for its
                    # own (separate) signal, if it happens to have one too.
                    label_offset = offset * 2.5
                    label_y = next_row["high"] + label_offset if up else next_row["low"] - label_offset
                    txt, col = ("WIN", GREEN) if res == "WIN" else ("LOSS", RED)
                    ax.annotate(txt, (next_wpos, label_y), color=col, fontsize=10, fontweight="bold",
                                ha="center", va="center", zorder=6)

    ax.set_ylim(y_min - padding, y_max + padding)
    ax.set_xlim(-1, n)

    step = max(1, n // 10)
    tick_positions = list(range(0, n, step))
    tick_labels = [time.strftime("%H:%M", time.localtime(window_df["time"].iloc[p])) for p in tick_positions]
    ax.set_xticks(tick_positions)
    ax.set_xticklabels(tick_labels)

    ax.set_ylabel("Price")
    ax.set_title(f"BTCUSD — Last {n} Closed 5-Minute Candles", fontsize=14, fontweight="bold")
    ax.grid(True, color="#dddddd", linewidth=0.6, zorder=0)
    if show_ema:
        ax.legend(loc="upper left", fontsize=9, framealpha=0.9)
    fig.tight_layout()
    return fig
