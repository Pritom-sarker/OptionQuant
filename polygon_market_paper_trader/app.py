# app.py — main Streamlit entry point
# Run with: streamlit run app.py

import streamlit as st
from streamlit_autorefresh import st_autorefresh

from src.database import init_db
from src.scanner  import get_engine
from src.dashboard import (
    render_sidebar,
    render_overview,
    render_scanner,
    render_candidates,
    render_live_monitor,
    render_active_trades,
    render_trade_history,
    render_logs,
)

# ── Page config ────────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Polygon Market Paper Trader",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── One-time init ──────────────────────────────────────────────────────────────
init_db()

# Module-level singleton — one engine per process regardless of browser tabs
engine = get_engine()
if not engine.is_running:
    engine.start()

# Auto-refresh every 5 seconds so charts and metrics stay live
st_autorefresh(interval=5_000, key="global_refresh")

# ── Sidebar ────────────────────────────────────────────────────────────────────
settings = render_sidebar(engine)

# ── Header ─────────────────────────────────────────────────────────────────────
st.title("📈 Polygon Market Paper Trader")
st.caption(
    "Real-time paper trading on Polymarket crypto prediction markets — "
    "no real money, no real orders."
)

# ── Tabs ───────────────────────────────────────────────────────────────────────
tabs = st.tabs([
    "Overview",
    "Market Scanner",
    "Candidate Ranking",
    "Live Monitor",
    "Active Trades",
    "Trade History",
    "Logs",
])

with tabs[0]:
    render_overview(engine)

with tabs[1]:
    render_scanner()

with tabs[2]:
    render_candidates()

with tabs[3]:
    render_live_monitor(engine)

with tabs[4]:
    render_active_trades()

with tabs[5]:
    render_trade_history()

with tabs[6]:
    render_logs()
