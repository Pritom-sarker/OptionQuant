"""
pine_strategy_simulator — configuration.

Standalone from the main app on purpose: this simulator doesn't import
anything from the parent OptionQuant project, so it can never accidentally
touch the live trading app's state or config.
"""
from __future__ import annotations
import itertools
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
RESULTS_DIR = os.path.join(BASE_DIR, "results")

# ─── Universe ────────────────────────────────────────────────────────────────
PAIRS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
TIMEFRAMES = ["5m", "15m", "30m", "1h"]
STRATEGIES = ["ATR Reversal", "Engulfing", "Hammer/SS", "Exhaustion"]

ATR_MULTIPLIERS = [0.25, 0.50, 0.75, 1.00, 1.25, 1.50]

# ATR length / ATR SMA length are NOT swept — the Pine script exposes these as
# separate global inputs from the ATR multiplier, and the task only asks to
# sweep the multiplier. Kept at the Pine script's own defaults.
ATR_LENGTH = 14
ATR_SMA_LENGTH = 50

FILTER_KEYS = ["f1", "f2", "f3", "f4", "f5"]
FILTER_LABELS = {
    "f1": "F1 Trend", "f2": "F2 Volatility", "f3": "F3 Close Location",
    "f4": "F4 Continuation", "f5": "F5 Anti-Chop",
}

F3_CLOSE_LOCATION_PCT = 0.70
F5_ANTI_CHOP_ATR_MULT = 0.15
MINTICK = 1e-6   # floor to avoid division by zero on a perfectly flat candle — not asset-scaled

# All 32 filter combinations (the empty set = "no filters" = raw pattern only,
# the full set = "all filters" ON), each labeled for display.
def _combo_label(flags: dict[str, bool]) -> str:
    on = [k.upper() for k in FILTER_KEYS if flags[k]]
    if not on:
        return "None"
    if len(on) == len(FILTER_KEYS):
        return "All"
    return "+".join(on)


FILTER_COMBOS: list[dict] = []
for bits in itertools.product([False, True], repeat=len(FILTER_KEYS)):
    flags = dict(zip(FILTER_KEYS, bits))
    FILTER_COMBOS.append({"flags": flags, "label": _combo_label(flags)})

# ─── Data fetching ───────────────────────────────────────────────────────────
BINANCE_KLINES_URL = "https://api.binance.com/api/v3/klines"
REQUEST_TIMEOUT = 15
BINANCE_MAX_LIMIT = 1000     # Binance's per-request cap; pagination fetches beyond this
PAGINATION_SLEEP_SEC = 0.05  # small courtesy delay between paginated requests

CANDLE_COUNT_OPTIONS = [10_000, 20_000, 50_000, 100_000]
DEFAULT_CANDLE_COUNT = 10_000

# ─── Backtest / edge-detection ───────────────────────────────────────────────
MIN_SIGNALS_FOR_RELIABLE = 30   # "reliable"/"best overall" setups need at least this many resolved signals
