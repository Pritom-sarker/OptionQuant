"""
Process-wide application state — replaces Streamlit's st.session_state.

A background thread (background_worker.py) and FastAPI request handlers now
run concurrently, unlike Streamlit's single-threaded rerun model, so every
read/write goes through this one lock-guarded singleton.

Settings persistence: tab1_settings/tab3_settings are also mirrored to
SETTINGS_PATH on every change (see save_settings(), called from
routes/pages.py's POST handlers) and reloaded here at startup if present.
This survives a local restart/reload always; it only survives a Railway
redeploy if the filesystem itself persists across deploys (e.g. a mounted
Volume) — Railway's default ephemeral container filesystem does not, so the
committed config.py defaults (DEFAULT_TAB3_IMMEDIATE_MODE etc.) are still
the only thing guaranteed to survive every deploy. Change those defaults
directly for anything that must never silently reset.
"""
from __future__ import annotations
import json
import os
import threading
from typing import Optional

import config

SETTINGS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "app_settings.json")


def _load_saved_settings() -> dict:
    try:
        with open(SETTINGS_PATH) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


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
            "early_entry_enabled": config.DEFAULT_TAB1_EARLY_ENTRY_ENABLED,
            "early_entry_lead_sec": config.DEFAULT_TAB1_EARLY_ENTRY_LEAD_SEC,
        }
        self.tab1_prediction: Optional[dict] = None
        self.backfill_rows: list = []
        self.backfill_total: int = 0
        self.live_active_prediction: Optional[dict] = None
        # Provisional signal built from the still-forming candle (Early Entry
        # only) — see background_worker._tick_tab1_early. None whenever Early
        # Entry is off or no provisional signal is currently pending.
        self.tab1_early_prediction: Optional[dict] = None
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
            "immediate_mode": config.DEFAULT_TAB3_IMMEDIATE_MODE,
        }
        # Each slot is {"candidate": TradeCandidate, "trade": Optional[ActiveTrade]} —
        # multiple can be active at once (one per candle/contract; a new candle's
        # signal is never blocked just because a previous trade hasn't settled
        # yet), deduped by signal_time/market_slug in background_worker.py so
        # the same candle/contract can never get two orders.
        self.tab3_slots: list = []
        self.tab3_last_chart_refresh: float = 0.0   # gates chart image regeneration only — values are always live
        self.tab3_market_ok: bool = False

        # Tab 6 — Money Management Simulator (replays trade_db's real settled
        # trades through the same sizing/loss-basket formulas as
        # pine_strategy_simulator's Money Management tab — see money_management.py)
        self.mm_settings: dict = {
            "starting_balance": config.DEFAULT_MM_STARTING_BALANCE,
            "base_trade_amount": config.DEFAULT_MM_BASE_TRADE_AMOUNT,
            "max_trade_amount": config.DEFAULT_MM_MAX_TRADE_AMOUNT,
            "recovery_percent": config.DEFAULT_MM_RECOVERY_PERCENT,
            "dynamic_mode": config.DEFAULT_MM_DYNAMIC_MODE,
            "profit_split_recovery_pct": config.DEFAULT_MM_PROFIT_SPLIT_RECOVERY,
            "reset_mode": config.DEFAULT_MM_RESET_MODE,
            "reset_after_n_wins": config.DEFAULT_MM_RESET_AFTER_N_WINS,
        }

        saved = _load_saved_settings()
        if "tab1_settings" in saved:
            self.tab1_settings.update(saved["tab1_settings"])
        if "tab3_settings" in saved:
            self.tab3_settings.update(saved["tab3_settings"])
        if "mm_settings" in saved:
            self.mm_settings.update(saved["mm_settings"])


def save_settings() -> None:
    """Persists the current tab1/tab3/mm settings to disk — called after every
    POST /settings/tab1, /settings/tab3, or /settings/money_management so a
    local restart picks them back up (see the module docstring for the
    Railway-redeploy caveat)."""
    with state.lock:
        payload = {"tab1_settings": state.tab1_settings, "tab3_settings": state.tab3_settings,
                   "mm_settings": state.mm_settings}
    try:
        with open(SETTINGS_PATH, "w") as f:
            json.dump(payload, f, indent=2)
    except OSError:
        pass


state = AppState()
