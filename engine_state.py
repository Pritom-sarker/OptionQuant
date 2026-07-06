"""
Process-wide application state — replaces Streamlit's st.session_state.

A background thread (background_worker.py) and FastAPI request handlers now
run concurrently, unlike Streamlit's single-threaded rerun model, so every
read/write goes through this one lock-guarded singleton.
"""
from __future__ import annotations
import threading
from typing import Optional

import config


class AppState:
    def __init__(self):
        self.lock = threading.RLock()

        # Tab 1 — BTC/USD signal. `patterns` holds one entry per base pattern in
        # config.PATTERN_OPTIONS — each independently enable-able, each with its
        # own F1-F5 filter toggles. ATR length/mult/SMA length stay global (they
        # feed the F2/F5 filters and the ATR Reversal threshold regardless of
        # which patterns are enabled) — see signal_engine.evaluate_patterns.
        self.tab1_settings: dict = {
            "patterns": {
                name: {
                    "enabled": (name == config.DEFAULT_PATTERN),
                    "filters": dict(config.DEFAULT_PATTERN_FILTERS),
                }
                for name in config.PATTERN_OPTIONS
            },
            "atr_length": config.DEFAULT_ATR_LENGTH,
            "atr_mult": config.DEFAULT_ATR_MULTIPLIER,
            "atr_sma_length": config.DEFAULT_ATR_SMA_LENGTH,
            "min_signals": config.DEFAULT_MIN_SIGNALS,
            "show_ema": config.DEFAULT_SHOW_EMA, "show_signals": config.DEFAULT_SHOW_SIGNALS,
        }
        self.tab1_prediction: Optional[dict] = None
        self.backfill_rows: list = []
        self.backfill_total: int = 0
        self.live_active_prediction: Optional[dict] = None
        self.live_last_seen_time: Optional[int] = None
        self.tab1_last_refresh: float = 0.0
        self.tab1_df = None            # last computed candle DataFrame (for the candle chart route)
        self.tab1_computed: Optional[dict] = None   # pat_dir/filters/act_ok/results/stats bundle

        # Tab 2 — Polymarket order book observer
        self.tab2_observer = None      # candidate_manager.ObservationState
        self.tab2_market: Optional[dict] = None
        self.tab2_market_slug: Optional[str] = None
        self.tab2_last_refresh: float = 0.0

        # Tab 3 — Trading engine
        self.tab3_settings: dict = {
            "refresh_interval": config.DEFAULT_TAB3_REFRESH_INTERVAL_SEC,
            "chart_refresh_interval": config.DEFAULT_TAB3_CHART_REFRESH_SEC,
            "observation_burst": config.DEFAULT_TAB3_OBSERVATION_BURST_SEC,
            "stake": config.DEFAULT_TAB3_STAKE,
            "max_entry_price": config.DEFAULT_TAB3_MAX_ENTRY_PRICE,
            "hard_block_price": config.DEFAULT_TAB3_HARD_BLOCK_PRICE,
            "min_profit_factor": config.DEFAULT_TAB3_MIN_PROFIT_FACTOR,
            "early_exit_loss_pct": config.DEFAULT_TAB3_EARLY_EXIT_LOSS_PCT,
            "pressure_confirm_count": config.DEFAULT_TAB3_PRESSURE_CONFIRM_COUNT,
            "max_spread": config.DEFAULT_TAB3_MAX_SPREAD,
            "min_liquidity": config.DEFAULT_TAB3_MIN_LIQUIDITY_USD,
            "pressure_threshold": config.DEFAULT_TAB3_PRESSURE_THRESHOLD,
            "depth_stable_tolerance": config.DEFAULT_TAB3_DEPTH_STABLE_TOLERANCE,
        }
        self.tab3_candidate = None
        self.tab3_trade = None
        self.tab3_last_chart_refresh: float = 0.0   # gates chart image regeneration only — values are always live
        self.tab3_market_ok: bool = False


state = AppState()
