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
import orderbook_engine as obe

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


# ─────────────────────────────────────────────────────────────────────────────
# Tab 3 — Trading Engine report charts. SIMULATION ONLY, purely additive —
# nothing above this line (Tab 1/Tab 2 charts) is modified.
# ─────────────────────────────────────────────────────────────────────────────

def save_figure(fig: plt.Figure, path: str) -> str:
    """Saves a matplotlib figure to disk and closes it. Returns the path (for chaining)."""
    fig.savefig(path, dpi=110, bbox_inches="tight")
    plt.close(fig)
    return path


def _empty_tab3_figure(title: str, figsize: tuple = (12, 4)) -> plt.Figure:
    fig, ax = plt.subplots(figsize=figsize, dpi=100)
    fig.patch.set_facecolor("white")
    ax.set_facecolor("white")
    ax.text(0.5, 0.5, "Waiting for the first order book snapshot...", ha="center", va="center",
            fontsize=12, color="#888888", transform=ax.transAxes)
    ax.set_xticks([]); ax.set_yticks([])
    ax.set_title(title, fontsize=13, fontweight="bold")
    fig.tight_layout()
    return fig


def build_tab3_live_price_chart(candidate_snapshots: list[dict], trade_snapshots: list[dict] = None) -> plt.Figure:
    """
    Live-only "selected price over time" chart for the Order Book
    Visualization section — regenerated every tick, never saved to disk
    (not part of the View Log's required saved-image set).
    """
    if not candidate_snapshots and not trade_snapshots:
        return _empty_tab3_figure("Selected Price Over Time")

    fig, ax = plt.subplots(figsize=(12, 4), dpi=100)
    fig.patch.set_facecolor("white")
    ax.set_facecolor("white")

    if candidate_snapshots:
        times = [pd.to_datetime(s["ts"], unit="s") for s in candidate_snapshots]
        ax.plot(times, [s["selected_price"] for s in candidate_snapshots], color="#607d8b",
                 linewidth=1.8, label="Price (observing)")
    if trade_snapshots:
        times = [pd.to_datetime(s["ts"], unit="s") for s in trade_snapshots]
        ax.plot(times, [s["price"] for s in trade_snapshots], color="#1e88e5",
                 linewidth=2.2, label="Price (trade open)")

    ax.set_ylim(0, 1)
    ax.set_ylabel("Contract price")
    ax.set_title("Selected Price Over Time", fontsize=13, fontweight="bold")
    ax.grid(True, color="#dddddd", linewidth=0.6)
    ax.legend(loc="upper left", fontsize=8, framealpha=0.9)
    fig.autofmt_xdate()
    fig.tight_layout()
    return fig


def build_candidate_scan_chart(snapshots: list[dict], signal_time: float) -> plt.Figure:
    """
    Debug chart for a single candidate's full pre-entry scan history (every
    order-book snapshot taken while it was OBSERVING, saved to trade_db
    regardless of whether it ever got entered — see trade_db.
    insert_candidate_snapshot, called on every tick by
    trade_engine.record_candidate_snapshot). X-axis is seconds relative to
    signal_time (0 = the candle's actual open) rather than wall-clock time,
    so it's immediately obvious how early/late each scan was and whether the
    entry window was actually wide enough to catch a good price. Price on
    the left axis, profit factor on the right; BUY-decision points marked.
    """
    if not snapshots:
        return _empty_tab3_figure("Scan History — Price & Profit Factor", figsize=(12, 4.5))

    seconds_from_open = [s["ts"] - signal_time for s in snapshots]
    prices = [s["selected_price"] for s in snapshots]
    pfs = [obe.profit_factor(p) for p in prices]

    fig, ax1 = plt.subplots(figsize=(12, 4.5), dpi=100)
    fig.patch.set_facecolor("white")
    ax1.set_facecolor("white")

    ax1.plot(seconds_from_open, prices, color="#607d8b", linewidth=1.8, marker="o", markersize=3, label="Price")
    buy_x = [x for x, s in zip(seconds_from_open, snapshots) if s["decision"] == "BUY"]
    buy_y = [p for p, s in zip(prices, snapshots) if s["decision"] == "BUY"]
    if buy_x:
        ax1.scatter(buy_x, buy_y, color=GREEN, s=70, zorder=5, marker="*", label="BUY")
    ax1.axvline(0, color="#888888", linestyle="--", linewidth=1.2, label="Candle open")
    ax1.set_ylim(0, 1)
    ax1.set_ylabel("Contract price", color="#607d8b")
    ax1.set_xlabel("Seconds relative to candle open")
    ax1.grid(True, color="#dddddd", linewidth=0.6)

    ax2 = ax1.twinx()
    ax2.plot(seconds_from_open, pfs, color="#8a2be2", linewidth=1.4, linestyle=":", label="Profit Factor")
    ax2.set_ylabel("Profit factor", color="#8a2be2")

    lines1, labels1 = ax1.get_legend_handles_labels()
    lines2, labels2 = ax2.get_legend_handles_labels()
    ax1.legend(lines1 + lines2, labels1 + labels2, loc="upper left", fontsize=8, framealpha=0.9)
    ax1.set_title("Scan History — Price & Profit Factor vs. Candle Open", fontsize=13, fontweight="bold")
    fig.tight_layout()
    return fig


def build_tab3_pressure_chart(candidate_snapshots: list[dict], trade_snapshots: list[dict] = None) -> plt.Figure:
    """Saved report image — pressure (+ slope during observation) across both phases."""
    if not candidate_snapshots and not trade_snapshots:
        return _empty_tab3_figure("Order Book Pressure & Slope")

    fig, ax = plt.subplots(figsize=(12, 4), dpi=100)
    fig.patch.set_facecolor("white")
    ax.set_facecolor("white")

    if candidate_snapshots:
        times = [pd.to_datetime(s["ts"], unit="s") for s in candidate_snapshots]
        ax.plot(times, [s["pressure"] for s in candidate_snapshots], color="#2e7d32",
                 linewidth=1.8, label="Pressure (observing)")
        ax.plot(times, [s["pressure_slope"] for s in candidate_snapshots], color="#8a2be2",
                 linewidth=1.4, linestyle=":", label="Pressure slope")
    if trade_snapshots:
        times = [pd.to_datetime(s["ts"], unit="s") for s in trade_snapshots]
        ax.plot(times, [s["pressure"] for s in trade_snapshots], color="#1565c0",
                 linewidth=2.2, label="Pressure (trade open)")

    ax.axhline(0, color="#888888", linewidth=1.0, linestyle="--")
    ax.set_ylim(-1, 1)
    ax.set_ylabel("Pressure")
    ax.set_title("Order Book Pressure & Slope", fontsize=13, fontweight="bold")
    ax.grid(True, color="#dddddd", linewidth=0.6)
    ax.legend(loc="upper left", fontsize=8, framealpha=0.9)
    fig.autofmt_xdate()
    fig.tight_layout()
    return fig


def build_tab3_depth_chart(candidate_snapshots: list[dict], trade_snapshots: list[dict] = None) -> plt.Figure:
    """Saved report image — weighted bid/ask depth across both phases."""
    if not candidate_snapshots and not trade_snapshots:
        return _empty_tab3_figure("Bid / Ask Depth")

    fig, ax = plt.subplots(figsize=(12, 4), dpi=100)
    fig.patch.set_facecolor("white")
    ax.set_facecolor("white")

    if candidate_snapshots:
        times = [pd.to_datetime(s["ts"], unit="s") for s in candidate_snapshots]
        ax.plot(times, [s["weighted_bid_depth"] for s in candidate_snapshots], color="#2e7d32",
                 linewidth=1.8, label="Bid depth (observing)")
        ax.plot(times, [s["weighted_ask_depth"] for s in candidate_snapshots], color="#c62828",
                 linewidth=1.8, label="Ask depth (observing)")
    if trade_snapshots:
        times = [pd.to_datetime(s["ts"], unit="s") for s in trade_snapshots]
        ax.plot(times, [s["bid_depth"] for s in trade_snapshots], color="#1b5e20",
                 linewidth=2.2, linestyle=":", label="Bid depth (trade open)")
        ax.plot(times, [s["ask_depth"] for s in trade_snapshots], color="#8e0000",
                 linewidth=2.2, linestyle=":", label="Ask depth (trade open)")

    ax.set_ylabel("Weighted depth")
    ax.set_title("Bid / Ask Depth", fontsize=13, fontweight="bold")
    ax.grid(True, color="#dddddd", linewidth=0.6)
    ax.legend(loc="upper left", fontsize=8, framealpha=0.9)
    fig.autofmt_xdate()
    fig.tight_layout()
    return fig


def build_tab3_pnl_chart(trade_snapshots: list[dict]) -> plt.Figure:
    """Saved report image — PnL % over the life of the (open or settled) trade."""
    if not trade_snapshots:
        return _empty_tab3_figure("PnL — Active Trade", figsize=(12, 3.5))

    fig, ax = plt.subplots(figsize=(12, 3.5), dpi=100)
    fig.patch.set_facecolor("white")
    ax.set_facecolor("white")

    times = [pd.to_datetime(s["ts"], unit="s") for s in trade_snapshots]
    pnl_pct = [s["pnl_pct"] * 100 for s in trade_snapshots]
    color = "#2e7d32" if pnl_pct[-1] >= 0 else "#c62828"

    ax.plot(times, pnl_pct, color=color, linewidth=2.2)
    ax.fill_between(times, pnl_pct, 0, color=color, alpha=0.15)
    ax.axhline(0, color="#888888", linewidth=1.0, linestyle="--")
    ax.set_ylabel("PnL (%)")
    ax.set_title("PnL — Active Trade", fontsize=13, fontweight="bold")
    ax.grid(True, color="#dddddd", linewidth=0.6)
    fig.autofmt_xdate()
    fig.tight_layout()
    return fig


def build_mm_balance_chart(hourly: list[dict], starting_balance: float) -> plt.Figure:
    """Tab 6 — account balance bucketed to the hour, with a starting-balance reference line."""
    if not hourly:
        return _empty_tab3_figure("Account Balance", figsize=(12, 4))

    fig, ax = plt.subplots(figsize=(12, 4), dpi=100)
    fig.patch.set_facecolor("white")
    ax.set_facecolor("white")

    times = [pd.to_datetime(h["hour"], unit="s") for h in hourly]
    balances = [h["balance"] for h in hourly]
    color = "#2e7d32" if balances[-1] >= starting_balance else "#c62828"

    ax.plot(times, balances, color=color, linewidth=2.0, marker="o", markersize=2.5)
    ax.axhline(starting_balance, color="#888888", linestyle="--", linewidth=1.0, label="Starting balance")
    ax.set_ylabel("Balance ($)")
    ax.set_title("Account Balance — Hourly", fontsize=13, fontweight="bold")
    ax.grid(True, color="#dddddd", linewidth=0.6)
    ax.legend(loc="upper left", fontsize=8, framealpha=0.9)
    fig.autofmt_xdate()
    fig.tight_layout()
    return fig


def build_mm_loss_basket_chart(curves: dict) -> plt.Figure:
    """Tab 6 — outstanding loss basket ("loss pool") after every replayed trade."""
    if not curves.get("time"):
        return _empty_tab3_figure("Loss Basket", figsize=(12, 3.5))

    fig, ax = plt.subplots(figsize=(12, 3.5), dpi=100)
    fig.patch.set_facecolor("white")
    ax.set_facecolor("white")

    times = [pd.to_datetime(t, unit="s") for t in curves["time"]]
    basket = curves["loss_basket"]

    ax.plot(times, basket, color="#c62828", linewidth=1.8)
    ax.fill_between(times, basket, 0, color="#c62828", alpha=0.15)
    ax.set_ylabel("Loss Basket ($)")
    ax.set_title("Loss Basket — After Each Trade", fontsize=13, fontweight="bold")
    ax.grid(True, color="#dddddd", linewidth=0.6)
    fig.autofmt_xdate()
    fig.tight_layout()
    return fig


def build_tab3_candle_chart(df: pd.DataFrame, signal_time: int, direction: int = None,
                             limit_price: float = None, entry_price: float = None,
                             current_price: float = None, exit_price: float = None,
                             result: str = None) -> plt.Figure:
    """
    The "Active Chart / Screenshot" — last ~30 BTC candles with the signal
    candle highlighted, plus the simulated limit/entry/exit/current contract
    prices as a secondary 0-1 axis (they're Polymarket contract prices, an
    entirely different scale from the BTCUSD candles, so they get their own
    twin y-axis rather than being drawn directly on the price axis).
    New, standalone function — does not touch build_chart (Tab 1's own chart).
    """
    fig, ax = plt.subplots(figsize=(14, 6), dpi=100)
    fig.patch.set_facecolor("white")
    ax.set_facecolor("white")

    if df is None or df.empty:
        ax.text(0.5, 0.5, "No candle data available for this window.", ha="center", va="center",
                fontsize=12, color="#888888", transform=ax.transAxes)
        ax.set_xticks([]); ax.set_yticks([])
        fig.tight_layout()
        return fig

    window_df = df.reset_index(drop=True)
    y_min, y_max = window_df["low"].min(), window_df["high"].max()
    padding = max((y_max - y_min) * 0.15, 0.005)
    body_width = 0.6

    for pos, row in window_df.iterrows():
        color = GREEN if row["close"] >= row["open"] else RED
        ax.plot([pos, pos], [row["low"], row["high"]], color=color, linewidth=1.3, zorder=2)
        body_low = min(row["open"], row["close"])
        height = max(abs(row["close"] - row["open"]), (y_max - y_min) * 0.004)
        ax.add_patch(Rectangle((pos - body_width / 2, body_low), body_width, height,
                                facecolor=color, edgecolor=color, zorder=3))

    def _nearest_pos(ts: float) -> int | None:
        if ts is None or not len(window_df):
            return None
        diffs = (window_df["time"] - ts).abs()
        pos = int(diffs.idxmin())
        # Guard against misleading markers on old reports whose candles have
        # since scrolled out of the currently-fetched candle window.
        if diffs.iloc[pos] > config.CANDLE_TIMEFRAME_MIN * 60 * 3:
            return None
        return pos

    sig_pos = _nearest_pos(signal_time)
    if sig_pos is not None:
        ax.scatter(sig_pos, window_df["low"].iloc[sig_pos] - padding * 0.5, marker="^", color="#1e90ff",
                   s=140, zorder=5, edgecolors="black", linewidths=0.6, label="Signal candle")

    ax.set_ylim(y_min - padding, y_max + padding)
    ax.set_xlim(-1, len(window_df))
    step = max(1, len(window_df) // 10)
    tick_positions = list(range(0, len(window_df), step))
    tick_labels = [time.strftime("%H:%M", time.localtime(window_df["time"].iloc[p])) for p in tick_positions]
    ax.set_xticks(tick_positions)
    ax.set_xticklabels(tick_labels)
    ax.set_ylabel("BTCUSD Price")
    ax.grid(True, color="#dddddd", linewidth=0.6, zorder=0)

    # Contract-price reference lines (limit/entry/exit/current) live on a
    # secondary 0-1 axis, since they're not on the same scale as BTC price.
    ax2 = ax.twinx()
    ax2.set_ylim(0, 1)
    ax2.set_ylabel("Contract price")
    if limit_price is not None:
        ax2.axhline(limit_price, color="#ff8c00", linewidth=1.4, linestyle="--",
                     label=f"Limit order {limit_price:.3f}")
    if entry_price is not None:
        ax2.axhline(entry_price, color="#2e7d32", linewidth=1.8, label=f"Entry {entry_price:.3f}")
    if exit_price is not None:
        ax2.axhline(exit_price, color="#c62828", linewidth=1.8, label=f"Exit {exit_price:.3f}")
    if current_price is not None:
        ax2.scatter(len(window_df) - 1, current_price, marker="o", color="#1e90ff", s=90, zorder=7,
                     edgecolors="black", linewidths=0.8, label=f"Current {current_price:.3f}")

    title = "Trade Candle Chart"
    if direction in ("GREEN", "RED"):
        title += f" — {direction} signal"
    if result:
        title += f" — {result}"
    ax.set_title(title, fontsize=13, fontweight="bold")

    lines1, labels1 = ax.get_legend_handles_labels()
    lines2, labels2 = ax2.get_legend_handles_labels()
    ax.legend(lines1 + lines2, labels1 + labels2, loc="upper left", fontsize=8, framealpha=0.9)
    fig.tight_layout()
    return fig
