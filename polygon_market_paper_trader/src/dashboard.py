# src/dashboard.py — Streamlit UI components (all 7 tabs)

import streamlit as st
import pandas as pd
import plotly.graph_objects as go

import config
from src import database as db
from src.utils import fmt_pnl, fmt_score, fmt_pressure, age_str


# ── Sidebar ────────────────────────────────────────────────────────────────────

def render_sidebar(engine) -> dict:
    st.sidebar.title("⚙ Trading Controls")

    st.sidebar.subheader("Market Selection")
    max_monitor = st.sidebar.selectbox(
        "Max markets to monitor", [1, 2, 3, 5],
        index=[1, 2, 3, 5].index(config.DEFAULT_MAX_MONITOR),
        key="sb_max_monitor",
    )

    st.sidebar.subheader("Order Settings")
    order_size = st.sidebar.selectbox(
        "Order size ($)", [1.0, 2.0, 5.0],
        index=[1.0, 2.0, 5.0].index(config.DEFAULT_ORDER_SIZE),
        key="sb_order_size",
    )
    entry_threshold = st.sidebar.selectbox(
        "Entry score threshold", [60, 70, 80],
        index=[60, 70, 80].index(config.DEFAULT_ENTRY_THRESHOLD),
        key="sb_threshold",
    )

    st.sidebar.subheader("Exit Mode")
    exit_modes = [
        "Hold Until Expiry",
        "Take Profit / Stop Loss",
        "Signal Flip Exit",
        "Manual Exit",
    ]
    exit_mode = st.sidebar.selectbox("Exit mode", exit_modes, key="sb_exit_mode")

    take_profit = config.DEFAULT_TAKE_PROFIT
    stop_loss   = config.DEFAULT_STOP_LOSS
    if exit_mode == "Take Profit / Stop Loss":
        take_profit = st.sidebar.slider("Take profit %", 5, 50, 20, key="sb_tp") / 100
        stop_loss   = st.sidebar.slider("Stop loss %",   5, 30, 10, key="sb_sl") / 100

    st.sidebar.divider()

    col1, col2 = st.sidebar.columns(2)
    with col1:
        if st.button("Force Scan", use_container_width=True, key="btn_scan"):
            engine.force_scan()
            st.sidebar.success("Scan queued")
    with col2:
        if engine.is_running:
            if st.button("Stop", use_container_width=True, key="btn_stop"):
                engine.stop()
                st.sidebar.warning("Engine stopped")
        else:
            if st.button("Start", use_container_width=True, key="btn_start"):
                engine.start()
                st.sidebar.success("Engine started")

    settings = {
        "max_monitor":     max_monitor,
        "order_size":      order_size,
        "entry_threshold": entry_threshold,
        "exit_mode":       exit_mode,
        "take_profit":     take_profit,
        "stop_loss":       stop_loss,
    }
    engine.update_settings(settings)
    return settings


# ── Tab 1: Overview ────────────────────────────────────────────────────────────

def render_overview(engine):
    st.header("System Overview")

    api_ok, api_msg = engine.api_status
    col_a, col_b = st.columns(2)
    col_a.markdown(
        f"**API Status:** {'🟢 Connected' if api_ok else f'🔴 Disconnected ({api_msg})'}"
    )
    col_b.markdown(
        f"**Engine:** `{engine.status}` &nbsp;|&nbsp; "
        f"**Monitoring:** `{engine.monitor_count}` markets"
    )

    st.divider()
    stats = db.get_stats()

    r1 = st.columns(4)
    r1[0].metric("Total Markets Scanned",  stats["total_markets"])
    r1[1].metric("Accepted",               stats["accepted_markets"])
    r1[2].metric("Rejected",               stats["rejected_markets"])
    r1[3].metric("Active Trades",          stats["open_trades"])

    r2 = st.columns(4)
    r2[0].metric("Closed Trades",  stats["closed_trades"])
    pnl = stats["total_pnl"]
    r2[1].metric("Total PnL",  fmt_pnl(pnl), delta=f"{pnl:+.4f}")
    r2[2].metric("Win Rate",   f"{stats['win_rate']:.1f}%")
    r2[3].metric("Monitoring", engine.monitor_count)

    st.divider()
    st.subheader("Recent Activity")
    logs = db.get_logs(limit=25)
    if logs:
        df = pd.DataFrame(logs)[["log_time", "level", "event_type", "message"]]
        df.columns = ["Time", "Level", "Type", "Message"]
        st.dataframe(_style_logs(df), use_container_width=True, hide_index=True)
    else:
        st.info("No logs yet. Start the engine to begin scanning.")


# ── Tab 2: Market Scanner ──────────────────────────────────────────────────────

def render_scanner():
    st.header("Market Scanner")

    t_acc, t_rej = st.tabs(["Accepted Markets", "Rejected Markets"])

    with t_acc:
        rows = db.get_accepted_markets()
        if rows:
            df = pd.DataFrame(rows)
            cols = ["market_id", "title", "expiry_type", "outcome",
                    "accepting_orders", "enable_order_book", "scan_time"]
            df = df[[c for c in cols if c in df.columns]]
            df.columns = [c.replace("_", " ").title() for c in df.columns]
            st.dataframe(df, use_container_width=True, hide_index=True)
            st.caption(f"{len(rows)} accepted market(s)")
        else:
            st.info("No accepted markets yet.")

    with t_rej:
        rows = db.get_rejected_markets(limit=500)
        if rows:
            df = pd.DataFrame(rows)
            cols = ["market_id", "title", "category", "rejection_reason", "scan_time"]
            df = df[[c for c in cols if c in df.columns]]
            df.columns = [c.replace("_", " ").title() for c in df.columns]

            reasons = ["All"] + sorted(
                df["Rejection Reason"].dropna().unique().tolist()
            )
            sel = st.selectbox("Filter by reason", reasons, key="rej_filter")
            if sel != "All":
                df = df[df["Rejection Reason"] == sel]

            st.dataframe(df, use_container_width=True, hide_index=True)
            st.caption(f"Showing {len(df)} rejected market(s)")
        else:
            st.info("No rejected markets yet.")


# ── Tab 3: Candidate Ranking ───────────────────────────────────────────────────

def render_candidates():
    st.header("Candidate Ranking")
    st.caption("Latest snapshot score for each accepted market.")

    accepted = db.get_accepted_markets()
    if not accepted:
        st.info("No accepted markets yet. Start the engine and wait for a scan.")
        return

    rows = []
    for mkt in accepted:
        mid  = mkt["market_id"]
        snap = db.get_latest_snapshot(mid)
        if not snap:
            continue
        wp = snap.get("weighted_pressure", 0.0) or 0.0
        rows.append({
            "Market":    (mkt.get("title") or mid)[:55],
            "Expiry":    mkt.get("expiry_type", "—"),
            "Spread":    f"{snap.get('spread', 0):.4f}",
            "Pressure":  fmt_pressure(wp),
            "Score":     snap.get("entry_score", 0.0) or 0.0,
            "Direction": "UP" if wp > 0 else ("DOWN" if wp < 0 else "NEUTRAL"),
            "Snapshot":  snap.get("snapshot_time", "—"),
        })

    if not rows:
        st.info("Waiting for order-book snapshots…")
        return

    rows.sort(key=lambda x: x["Score"], reverse=True)
    for i, r in enumerate(rows, 1):
        r["Rank"]  = i
        r["Score"] = fmt_score(r["Score"])

    df = pd.DataFrame(rows)[["Rank", "Market", "Expiry", "Spread",
                               "Pressure", "Score", "Direction", "Snapshot"]]

    def _dir_color(val):
        if val == "UP":    return "color:green"
        if val == "DOWN":  return "color:red"
        return "color:gray"

    st.dataframe(
        df.style.map(_dir_color, subset=["Direction"]),
        use_container_width=True, hide_index=True,
    )


# ── Tab 4: Live Monitor ────────────────────────────────────────────────────────

def render_live_monitor(engine):
    st.header("Live Monitor")

    accepted = db.get_accepted_markets()
    if not accepted:
        st.info("No markets available yet.")
        return

    options = {m["market_id"]: (m.get("title") or m["market_id"])[:60]
               for m in accepted}
    sel_id = st.selectbox(
        "Select market",
        list(options.keys()),
        format_func=lambda x: options[x],
        key="mon_select",
    )
    if not sel_id:
        return

    snaps = db.get_snapshots(sel_id, limit=80)
    if not snaps:
        st.info("No snapshots for this market yet.")
        return

    latest = snaps[0]   # newest first

    # KPI row
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Best Bid",     f"{latest.get('best_bid', 0):.4f}")
    c2.metric("Best Ask",     f"{latest.get('best_ask', 0):.4f}")
    c3.metric("Spread",       f"{latest.get('spread', 0):.4f}")
    c4.metric("Pressure",     fmt_pressure(latest.get("weighted_pressure")))
    c5.metric("Score",        fmt_score(latest.get("entry_score")))

    df = pd.DataFrame(reversed(snaps))
    df["snapshot_time"] = pd.to_datetime(df["snapshot_time"], utc=True, errors="coerce")

    # Chart: Weighted pressure
    fig_p = go.Figure()
    fig_p.add_trace(go.Scatter(
        x=df["snapshot_time"], y=df["weighted_pressure"],
        mode="lines+markers", name="Weighted Pressure",
        line=dict(color="#4285F4", width=2),
    ))
    fig_p.add_hline(y=0, line_dash="dash", line_color="gray", opacity=0.5)
    fig_p.update_layout(
        title="Weighted Pressure", height=240,
        margin=dict(l=0, r=0, t=30, b=0),
        yaxis=dict(range=[-1, 1]),
    )
    st.plotly_chart(fig_p, use_container_width=True)

    # Chart: Entry score + threshold line
    threshold = st.session_state.get("sb_threshold", config.DEFAULT_ENTRY_THRESHOLD)
    fig_s = go.Figure()
    fig_s.add_trace(go.Scatter(
        x=df["snapshot_time"], y=df["entry_score"],
        mode="lines+markers", name="Entry Score",
        line=dict(color="#FBBC04", width=2),
        fill="tozeroy", fillcolor="rgba(251,188,4,0.1)",
    ))
    fig_s.add_hline(
        y=threshold, line_dash="dash", line_color="green",
        annotation_text=f"Threshold ({threshold})",
        annotation_position="top left",
    )
    fig_s.update_layout(
        title="Entry Score", height=240,
        margin=dict(l=0, r=0, t=30, b=0),
        yaxis=dict(range=[0, 100]),
    )
    st.plotly_chart(fig_s, use_container_width=True)

    # Momentum
    if len(snaps) >= 2:
        first_p = snaps[-1].get("weighted_pressure", 0.0) or 0.0
        last_p  = snaps[0].get("weighted_pressure",  0.0) or 0.0
        momentum = last_p - first_p
        st.metric(
            "Pressure Momentum (latest − first)",
            fmt_pressure(momentum),
            delta=f"{momentum:+.4f}",
        )


# ── Tab 5: Active Trades ───────────────────────────────────────────────────────

def render_active_trades():
    st.header("Active Trades")

    trades = db.get_open_trades()
    if not trades:
        st.info("No active trades. The engine will enter a trade when score ≥ threshold.")
        return

    for trade in trades:
        tid   = trade["trade_id"]
        side  = trade.get("side", "?")
        title = (trade.get("title") or tid)[:60]

        with st.expander(f"[{side}] {title}", expanded=True):
            snap = db.get_latest_snapshot(trade["market_id"])

            r1 = st.columns(4)
            r1[0].metric("Side",        side)
            r1[1].metric("Entry Price", f"{trade.get('entry_price', 0):.4f}")
            r1[2].metric("Contracts",   f"{trade.get('contracts', 0):.2f}")
            r1[3].metric("Order Size",  f"${trade.get('order_size', 0):.2f}")

            if snap:
                bid    = snap.get("best_bid", 0.0) or 0.0
                pnl    = trade.get("contracts", 0) * bid - trade.get("order_size", 0)
                r2 = st.columns(4)
                r2[0].metric("Current Bid",  f"{bid:.4f}")
                r2[1].metric("Unrealized PnL", fmt_pnl(pnl), delta=f"{pnl:+.4f}")
                r2[2].metric("Score",  fmt_score(snap.get("entry_score")))
                r2[3].metric("Pressure", fmt_pressure(snap.get("weighted_pressure")))
            else:
                st.warning("No recent snapshot for this market.")

            st.caption(
                f"Entry score: {trade.get('entry_score', 0):.1f}  |  "
                f"Entered: {trade.get('entry_time', '—')}  |  "
                f"Age: {age_str(trade.get('entry_time', ''))}  |  "
                f"ID: {tid[:8]}…"
            )


# ── Tab 6: Trade History ───────────────────────────────────────────────────────

def render_trade_history():
    st.header("Trade History")

    trades = db.get_closed_trades(limit=100)
    if not trades:
        st.info("No closed trades yet.")
        return

    wins      = sum(1 for t in trades if (t.get("pnl") or 0) > 0)
    losses    = len(trades) - wins
    total_pnl = sum(t.get("pnl") or 0.0 for t in trades)
    win_rate  = wins / len(trades) * 100 if trades else 0

    r = st.columns(4)
    r[0].metric("Total Closed",  len(trades))
    r[1].metric("Win / Loss",    f"{wins} / {losses}")
    r[2].metric("Win Rate",      f"{win_rate:.1f}%")
    r[3].metric("Total PnL",     fmt_pnl(total_pnl))

    rows = []
    for t in trades:
        pnl = t.get("pnl") or 0.0
        rows.append({
            "Title":       (t.get("title") or "—")[:40],
            "Side":        t.get("side", "—"),
            "Expiry":      t.get("expiry_type", "—"),
            "Entry":       f"{t.get('entry_price', 0):.4f}",
            "Exit":        f"{t.get('exit_price', 0):.4f}" if t.get("exit_price") is not None else "—",
            "PnL":         fmt_pnl(pnl),
            "Result":      "WIN" if pnl > 0 else "LOSS",
            "Exit Reason": t.get("exit_reason", "—"),
            "Entry Score": fmt_score(t.get("entry_score")),
            "Entry Time":  t.get("entry_time", "—"),
        })

    df = pd.DataFrame(rows)

    def _res_color(val):
        return "color:green;font-weight:bold" if val == "WIN" else "color:red;font-weight:bold"

    st.dataframe(
        df.style.map(_res_color, subset=["Result"]),
        use_container_width=True, hide_index=True,
    )

    # Cumulative PnL chart
    if len(trades) > 1:
        pnls     = [t.get("pnl") or 0.0 for t in reversed(trades)]
        cum_pnl  = []
        running  = 0.0
        for p in pnls:
            running += p
            cum_pnl.append(running)

        colour = "green" if cum_pnl[-1] >= 0 else "red"
        fig    = go.Figure()
        fig.add_trace(go.Scatter(
            y=cum_pnl, mode="lines+markers", name="Cumulative PnL",
            line=dict(color=colour, width=2),
            fill="tozeroy", fillcolor=f"rgba({'0,200,0' if colour=='green' else '200,0,0'},0.1)",
        ))
        fig.add_hline(y=0, line_dash="dash", line_color="gray", opacity=0.5)
        fig.update_layout(
            title="Cumulative PnL", height=280,
            margin=dict(l=0, r=0, t=30, b=0),
        )
        st.plotly_chart(fig, use_container_width=True)


# ── Tab 7: Logs ────────────────────────────────────────────────────────────────

def render_logs():
    st.header("Logs")

    type_options = ["All", "scan", "filter", "analyze",
                    "monitor", "trade_entry", "trade_exit", "error"]
    sel_type = st.selectbox("Filter by type", type_options, key="log_type_sel")
    event_type = None if sel_type == "All" else sel_type

    logs = db.get_logs(event_type=event_type, limit=400)
    if not logs:
        st.info("No logs yet.")
        return

    df = pd.DataFrame(logs)[["log_time", "level", "event_type", "message"]]
    df.columns = ["Time", "Level", "Type", "Message"]
    st.dataframe(_style_logs(df), use_container_width=True, hide_index=True)
    st.caption(f"{len(df)} log entries")


# ── Helpers ────────────────────────────────────────────────────────────────────

def _style_logs(df: pd.DataFrame):
    def _lvl(val):
        if val == "ERROR":   return "color:red"
        if val == "WARNING": return "color:orange"
        if val == "INFO":    return "color:#1e90ff"
        return ""
    return df.style.map(_lvl, subset=["Level"])
