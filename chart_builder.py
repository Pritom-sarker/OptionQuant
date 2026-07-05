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
import plotly.graph_objects as go

import config

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
        label_offset = offset * 2.5

        # For every signal: signal_index = i (the candle that fired), always
        # evaluation_index = i + 1 (the very next candle — never any other
        # offset). The UP/DOWN marker is drawn only on signal_index; WIN/LOSS
        # is drawn only on evaluation_index — never on the signal candle
        # itself, never shifted.
        for signal_index in range(len(df)):
            if not bool(act_ok.iloc[signal_index]):
                continue
            evaluation_index = signal_index + 1
            if evaluation_index >= len(df):
                continue   # no evaluation candle yet (still pending) — nothing to draw

            up = pat_dir.iloc[signal_index] == 1
            signal_row = df.iloc[signal_index]
            signal_wpos = signal_index - start_pos
            signal_visible = 0 <= signal_wpos < n
            signal_y = signal_row["low"] - offset if up else signal_row["high"] + offset

            if signal_visible:
                ax.scatter(signal_wpos, signal_y, marker="^" if up else "v",
                           color=GREEN if up else RED, s=140, zorder=5, edgecolors="black", linewidths=0.5)

            # WIN/LOSS + connector only need evaluation_index to be visible —
            # they must not depend on whether the signal candle itself has
            # scrolled out of the visible window, otherwise a WIN/LOSS on an
            # on-screen candle could silently disappear.
            evaluation_wpos = evaluation_index - start_pos
            if not (0 <= evaluation_wpos < n):
                continue
            res = results.iloc[signal_index]
            if res not in ("WIN", "LOSS"):
                continue

            evaluation_row = df.iloc[evaluation_index]
            label_y = evaluation_row["high"] + label_offset if up else evaluation_row["low"] - label_offset
            txt, col = ("WIN", GREEN) if res == "WIN" else ("LOSS", RED)
            ax.annotate(txt, (evaluation_wpos, label_y), color=col, fontsize=10, fontweight="bold",
                        ha="center", va="center", zorder=6)

            # Dotted connector from the signal candle to its evaluation
            # candle, making the "this candle predicts that candle"
            # relationship visually explicit. If the signal candle itself
            # has scrolled off-screen, the line starts from the left edge.
            line_x0 = signal_wpos if signal_visible else -0.5
            ax.plot([line_x0, evaluation_wpos], [signal_y, label_y],
                    linestyle=":", color=col, linewidth=1.3, zorder=4)

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


def build_orderbook_chart(candidate) -> plt.Figure:
    """
    Tab 2 chart — Polymarket CONTRACT price (YES/NO, 0-1), not BTC price.
    Shows YES/NO price history since the signal, the lowest point reached
    on the selected side, and the entry marker (if a paper trade fired).
    """
    fig, ax = plt.subplots(figsize=(14, 6), dpi=100)
    fig.patch.set_facecolor("white")
    ax.set_facecolor("white")

    history = candidate.history
    if not history:
        ax.text(0.5, 0.5, "Waiting for the first order book snapshot...",
                 ha="center", va="center", fontsize=12, color="#888888", transform=ax.transAxes)
        ax.set_xticks([]); ax.set_yticks([])
        fig.tight_layout()
        return fig

    t0 = candidate.created_at
    xs = [h["t"] - t0 for h in history]
    yes_prices = [h["yes_price"] for h in history]
    no_prices = [h["no_price"] for h in history]
    selected_prices = [h["price"] for h in history]

    ax.plot(xs, yes_prices, color="#00aa44", linewidth=1.6, marker="o", markersize=3, label="YES price")
    ax.plot(xs, no_prices, color="#dd2222", linewidth=1.6, marker="o", markersize=3, label="NO price")

    # Lowest price reached on the selected (watched) side since the signal.
    low_idx = min(range(len(selected_prices)), key=lambda i: selected_prices[i])
    ax.scatter([xs[low_idx]], [selected_prices[low_idx]], color="#ff8c00", s=110, zorder=5,
               edgecolors="black", linewidths=0.6, label="Lowest since signal")

    # Entry marker, if a paper trade fired.
    if candidate.entry_price is not None and candidate.entry_time is not None:
        ex = candidate.entry_time - t0
        ax.scatter([ex], [candidate.entry_price], marker="*", color="#1e90ff", s=260, zorder=6,
                   edgecolors="black", linewidths=0.7, label="Entry")
        ax.annotate("BUY HERE", (ex, candidate.entry_price), textcoords="offset points",
                    xytext=(8, 8), fontsize=10, fontweight="bold", color="#1e90ff")

    ax.axhline(config.MAX_ENTRY_PRICE, color="#888888", linestyle="--", linewidth=1,
               label=f"Max entry {config.MAX_ENTRY_PRICE:.2f}")

    ax.set_xlim(-1, max(config.CANDIDATE_EXPIRY_SEC, xs[-1] + 1))
    ax.set_ylim(0, 1)
    ax.set_xlabel("Seconds since signal")
    ax.set_ylabel("Contract price")
    ax.set_title(f"{candidate.prediction} candidate — watching {candidate.selected_side()} "
                 f"(signal close {candidate.signal_close:.4f})", fontsize=13, fontweight="bold")
    ax.grid(True, color="#dddddd", linewidth=0.6, zorder=0)
    ax.legend(loc="upper right", fontsize=8, framealpha=0.9)
    fig.tight_layout()
    return fig


def build_pressure_chart(candidate) -> plt.Figure:
    """
    Live pressure + selected-contract-price graph, one point added every
    order book sample, so price / pressure / recovery are visible on the
    same timeline.
    """
    fig, ax1 = plt.subplots(figsize=(14, 4.5), dpi=100)
    fig.patch.set_facecolor("white")
    ax1.set_facecolor("white")

    history = candidate.history
    if not history:
        ax1.text(0.5, 0.5, "Waiting for the first order book snapshot...",
                  ha="center", va="center", fontsize=12, color="#888888", transform=ax1.transAxes)
        ax1.set_xticks([]); ax1.set_yticks([])
        fig.tight_layout()
        return fig

    t0 = candidate.created_at
    xs = [h["t"] - t0 for h in history]
    prices = [h["price"] for h in history]
    pressures = [h["pressure"] for h in history]

    color = "#00aa44" if candidate.direction == 1 else "#dd2222"
    ax1.plot(xs, prices, color=color, linewidth=1.8, marker="o", markersize=3, label="Selected contract price")
    ax1.set_ylim(0, 1)
    ax1.set_xlabel("Seconds since signal")
    ax1.set_ylabel("Contract price")

    ax2 = ax1.twinx()
    ax2.plot(xs, pressures, color="#8a2be2", linewidth=1.6, linestyle="--", marker="s", markersize=3,
              label="Order book pressure")
    ax2.axhline(0, color="#aaaaaa", linewidth=0.8)
    ax2.set_ylim(-1, 1)
    ax2.set_ylabel("Pressure")

    lines1, labels1 = ax1.get_legend_handles_labels()
    lines2, labels2 = ax2.get_legend_handles_labels()
    ax1.legend(lines1 + lines2, labels1 + labels2, loc="upper right", fontsize=8, framealpha=0.9)

    ax1.set_title("Price vs. Pressure since signal", fontsize=13, fontweight="bold")
    ax1.grid(True, color="#dddddd", linewidth=0.6, zorder=0)
    fig.tight_layout()
    return fig


def _empty_plotly(title: str, height: int = 380) -> go.Figure:
    fig = go.Figure()
    fig.add_annotation(text="Waiting for the first order book snapshot...", showarrow=False,
                        font=dict(size=13, color="#888888"))
    fig.update_layout(template="plotly_white", height=height, title=title,
                       xaxis={"visible": False}, yaxis={"visible": False})
    return fig


def _side_history_since_signal(observer, side_hist: list) -> list:
    return [h for h in side_hist if observer.tab1_signal_time is None
            or h["t"] >= observer.tab1_signal_time]


def build_tab2_price_chart(observer) -> go.Figure:
    """
    Chart 1 — Contract Price Movement. YES/NO price only, on a 0-100% axis,
    with a 50% neutral reference line. Highlights the selected side (thicker
    line) and marks the local low / recovery / entry-ready points on it.
    """
    yes_hist = observer.yes_price_history[-config.TAB2_CHART_WINDOW:]
    no_hist = observer.no_price_history[-config.TAB2_CHART_WINDOW:]
    if not yes_hist:
        return _empty_plotly("Chart 1 — Contract Price Movement", height=420)

    times = [pd.to_datetime(h["t"], unit="s") for h in yes_hist]
    yes_pct = [h["price"] * 100 for h in yes_hist]
    no_pct = [h["price"] * 100 for h in no_hist]
    yes_selected = observer.selected_side == "YES"
    no_selected = observer.selected_side == "NO"

    fig = go.Figure()
    fig.add_trace(go.Scatter(x=times, y=yes_pct, mode="lines", name="YES price",
                              line=dict(color="#00aa44", width=3.4 if yes_selected else 1.6)))
    fig.add_trace(go.Scatter(x=times, y=no_pct, mode="lines", name="NO price",
                              line=dict(color="#dd2222", width=3.4 if no_selected else 1.6)))
    fig.add_hline(y=50, line=dict(color="#888888", dash="dash", width=1.2),
                  annotation_text="50% neutral", annotation_position="top left")

    if observer.tab1_signal_time is not None:
        sig_dt = pd.to_datetime(observer.tab1_signal_time, unit="s")
        if times[0] <= sig_dt <= times[-1]:
            fig.add_vline(x=sig_dt, line=dict(color="#1e90ff", dash="dot", width=1.5))

    if observer.selected_side is not None and observer.selected_side_local_low is not None:
        side_hist = yes_hist if observer.selected_side == "YES" else no_hist
        since_signal = _side_history_since_signal(observer, side_hist)
        if since_signal:
            low_point = min(since_signal, key=lambda h: h["price"])
            fig.add_trace(go.Scatter(x=[pd.to_datetime(low_point["t"], unit="s")], y=[low_point["price"] * 100],
                                      mode="markers", name="Local low",
                                      marker=dict(color="#ff8c00", size=13, line=dict(color="black", width=1))))

            low_pos = since_signal.index(low_point)
            recovery_point = next((h for h in since_signal[low_pos + 1:] if h["price"] > low_point["price"]), None)
            if recovery_point:
                fig.add_trace(go.Scatter(x=[pd.to_datetime(recovery_point["t"], unit="s")],
                                          y=[recovery_point["price"] * 100], mode="markers", name="Recovery",
                                          marker=dict(symbol="triangle-up", color="#1e90ff", size=15,
                                                      line=dict(color="black", width=1))))

        if observer.last_decision == "READY":
            last_point = side_hist[-1]
            fig.add_trace(go.Scatter(x=[pd.to_datetime(last_point["t"], unit="s")], y=[last_point["price"] * 100],
                                      mode="markers", name="Entry ready",
                                      marker=dict(symbol="star", color="#ffd700", size=20,
                                                  line=dict(color="black", width=1.3))))

    title = "Chart 1 — Contract Price Movement"
    if observer.selected_side:
        title += f" (watching {observer.selected_side})"
    fig.update_yaxes(range=[0, 100], title="Contract price (%)")
    fig.update_xaxes(title="Time")
    fig.update_layout(template="plotly_white", height=420, title=title,
                       legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0))
    return fig


def build_tab2_pressure_chart(observer) -> go.Figure:
    """
    Chart 2 — Order Book Pressure, shown separately from price. -1..+1 axis,
    zero line, and a trend label (Increasing/Decreasing/Flat) per side.
    """
    yes_p_hist = observer.yes_pressure_history[-config.TAB2_CHART_WINDOW:]
    no_p_hist = observer.no_pressure_history[-config.TAB2_CHART_WINDOW:]
    if not yes_p_hist:
        return _empty_plotly("Chart 2 — Order Book Pressure", height=380)

    times = [pd.to_datetime(h["t"], unit="s") for h in yes_p_hist]
    yes_pressure = [h["pressure"] for h in yes_p_hist]
    no_pressure = [h["pressure"] for h in no_p_hist]

    fig = go.Figure()
    fig.add_trace(go.Scatter(x=times, y=yes_pressure, mode="lines", name="YES pressure",
                              line=dict(color="#2e7d32", width=2)))
    fig.add_trace(go.Scatter(x=times, y=no_pressure, mode="lines", name="NO pressure",
                              line=dict(color="#c62828", width=2)))
    fig.add_hline(y=0, line=dict(color="#888888", dash="dash", width=1.2))
    fig.add_annotation(x=times[-1], y=yes_pressure[-1], text=f"YES: {observer.yes_trend}",
                        showarrow=True, arrowhead=2, ax=45, ay=-25, font=dict(color="#2e7d32"))
    fig.add_annotation(x=times[-1], y=no_pressure[-1], text=f"NO: {observer.no_trend}",
                        showarrow=True, arrowhead=2, ax=45, ay=25, font=dict(color="#c62828"))

    fig.update_yaxes(range=[-1, 1], title="Pressure (buyers stronger above 0, sellers below)")
    fig.update_xaxes(title="Time")
    fig.update_layout(template="plotly_white", height=380, title="Chart 2 — Order Book Pressure",
                       legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0))
    return fig


def build_tab2_depth_bar_chart(observer) -> go.Figure:
    """
    Chart 3 — Top-5 weighted bid/ask depth for YES and NO, current snapshot,
    grouped bar chart so it's obvious which side buyers/sellers are stronger.
    """
    yes_m, no_m = observer.last_yes_metrics, observer.last_no_metrics
    if yes_m is None or no_m is None:
        return _empty_plotly("Chart 3 — Top-5 Bid/Ask Depth", height=380)

    fig = go.Figure()
    fig.add_trace(go.Bar(name="Weighted Bid Depth", x=["YES", "NO"],
                          y=[yes_m.weighted_bid_depth, no_m.weighted_bid_depth], marker_color="#2e7d32"))
    fig.add_trace(go.Bar(name="Weighted Ask Depth", x=["YES", "NO"],
                          y=[yes_m.weighted_ask_depth, no_m.weighted_ask_depth], marker_color="#c62828"))
    fig.update_layout(template="plotly_white", height=380, barmode="group",
                       title="Chart 3 — Top-5 Bid/Ask Depth (weighted)", yaxis_title="Weighted depth",
                       legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0))
    return fig


def build_tab2_ladder_chart(observer) -> go.Figure:
    """
    Chart 4 — Top-5 order book ladder for the selected side (YES if none
    selected yet): bids on one side, asks on the other, at their price level.
    """
    side = observer.selected_side or "YES"
    book = observer.last_yes_book if side == "YES" else observer.last_no_book
    if not book or not (book.get("bids") or book.get("asks")):
        return _empty_plotly(f"Chart 4 — Top-5 Order Book Ladder ({side})", height=420)

    bids = book.get("bids", [])[:config.OB_LEVELS]
    asks = book.get("asks", [])[:config.OB_LEVELS]
    category_order = [f"{p:.3f}" for p in sorted({lv["price"] for lv in bids} | {lv["price"] for lv in asks},
                                                  reverse=True)]

    fig = go.Figure()
    fig.add_trace(go.Bar(y=[f"{lv['price']:.3f}" for lv in bids], x=[-lv["size"] for lv in bids],
                          orientation="h", name="Bids", marker_color="#2e7d32",
                          text=[f"{lv['size']:.1f}" for lv in bids], textposition="outside"))
    fig.add_trace(go.Bar(y=[f"{lv['price']:.3f}" for lv in asks], x=[lv["size"] for lv in asks],
                          orientation="h", name="Asks", marker_color="#c62828",
                          text=[f"{lv['size']:.1f}" for lv in asks], textposition="outside"))
    fig.add_vline(x=0, line=dict(color="#888888", width=1))

    fig.update_yaxes(categoryorder="array", categoryarray=category_order, title="Price level")
    fig.update_xaxes(title="Size (bids left, asks right)")
    fig.update_layout(template="plotly_white", height=420, title=f"Chart 4 — Top-5 Order Book Ladder ({side})",
                       legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0))
    return fig


def build_tab2_checklist(observer) -> go.Figure:
    """
    Chart 5 — visual PASS/FAIL decision checklist for the selected side,
    plus the final OBSERVE / WAIT / READY decision (already computed by
    orderbook_engine.check_confirmation — this only displays it).
    """
    side = observer.selected_side
    metrics = observer.last_yes_metrics if side == "YES" else observer.last_no_metrics if side == "NO" else None
    trend = observer.yes_trend if side == "YES" else observer.no_trend if side == "NO" else None

    checks = [
        ("Tab 1 signal exists", side is not None),
        ("Selected price <= 50%", metrics.price <= config.MAX_ENTRY_PRICE if metrics else None),
        ("Local low found", observer.selected_side_local_low is not None if side else None),
        ("Recovery started", observer.is_recovering() if side else None),
        ("Pressure > 0", metrics.pressure > config.MIN_OB_PRESSURE if metrics else None),
        ("Pressure improving", (trend == "Increasing") if side else None),
        ("Spread acceptable", metrics.spread <= config.MAX_SPREAD if metrics else None),
        ("Liquidity acceptable", metrics.liquidity_usd >= config.MIN_LIQUIDITY_USD if metrics else None),
    ]

    def _status(cond) -> tuple[str, str]:
        if cond is None:
            return "—", "#9e9e9e"
        return ("PASS", "#1b5e20") if cond else ("FAIL", "#b71c1c")

    labels = [c[0] for c in checks]
    marks = [_status(c[1]) for c in checks]
    statuses = [m[0] for m in marks]
    colors = [m[1] for m in marks]

    final_label = {"OBSERVE": "OBSERVE", "WAIT": "WAIT", "READY": "READY"}[observer.last_decision]
    final_color = {"OBSERVE": "#616161", "WAIT": "#e65100", "READY": "#1b5e20"}[observer.last_decision]

    fig = go.Figure(data=[go.Table(
        columnwidth=[75, 25],
        header=dict(values=["Condition", "Status"], fill_color="#37474f",
                    font=dict(color="white", size=13), align="left", height=30),
        cells=dict(values=[labels, statuses],
                   fill_color=[["#f5f5f5"] * len(labels), colors],
                   font=dict(color=[["#212121"] * len(labels), ["white"] * len(labels)], size=13),
                   align="left", height=28),
    )])
    fig.update_layout(height=380, margin=dict(t=60, b=10, l=10, r=10),
                       title=dict(text=f"Chart 5 — Decision Checklist   |   Final Decision: {final_label}",
                                  font=dict(color=final_color, size=15)))
    return fig
