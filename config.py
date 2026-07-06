"""
btcusd-polymarket-signal-viewer — Configuration

VISUALISATION ONLY. No orders, no order book, no mock trading, no wallet.
Recreates the exact logic of btc_polymarket_signal_tester.pine.
"""

# ─── BTC/USD real price data (btc_price_api.py) ─────────────────────────────
# Real spot BTC/USD OHLC candles — NOT Polymarket's prediction-contract price
# (which trades 0-1 and reflects market odds, not the underlying asset).
BINANCE_KLINES_URL   = "https://api.binance.com/api/v3/klines"
BINANCE_SYMBOL       = "BTCUSDT"
COINBASE_CANDLES_URL = "https://api.exchange.coinbase.com/products/BTC-USD/candles"
CANDLE_TIMEFRAME_MIN = 5    # 5-minute candles
NUM_CANDLES_TARGET   = 100
REQUEST_TIMEOUT      = 10

# ─── Polymarket API (polymarket_api.py) ─────────────────────────────────────
# Reserved for a later feature — market odds / order book. NOT used for
# BTCUSD candles.
GAMMA_API = "https://gamma-api.polymarket.com"
CLOB_API  = "https://clob.polymarket.com"
POLYMARKET_EVENT_URL_BASE = "https://polymarket.com/event"
# Polymarket's 5-minute BTC Up/Down markets follow a fixed slug pattern:
#   "btc-updown-5m-{unix_ts}"  (unix_ts = window end, a multiple of 300)
COIN              = "btc"
WINDOWS_TO_CHECK   = 3     # how many current/upcoming 5-min windows to check
MARKET_MAX_TTE_SEC = 900   # ignore markets more than this far out
MARKET_MIN_TTE_SEC = 0     # market must not have expired yet
CANDLE_FIDELITY_MIN = 1    # (unused for candles now — kept for polymarket_api.py)
LOOKBACK_HOURS      = 10

# ─── Pine Script defaults (sidebar) ─────────────────────────────────────────
# Multiple base patterns can now be enabled at once (checkboxes, not a single
# dropdown). Each enabled pattern runs its own detect_pattern/compute_filters/
# compute_active_signal completely independently — identical, unmodified
# per-pattern math to the Pine strategy. When more than one enabled pattern
# fires on the same candle, PATTERN_PRIORITY order (see signal_engine.py)
# picks a single winner for that candle: the first enabled pattern (in this
# order) whose raw shape actually fired.
PATTERN_OPTIONS = ["ATR Reversal", "Engulfing", "Hammer/SS", "Exhaustion"]
PATTERN_SLUGS = {"ATR Reversal": "atr_reversal", "Engulfing": "engulfing",
                  "Hammer/SS": "hammer_ss", "Exhaustion": "exhaustion"}
DEFAULT_PATTERN = "ATR Reversal"   # only this one is enabled by default

DEFAULT_ATR_LENGTH     = 14
DEFAULT_ATR_MULTIPLIER = 1.5
DEFAULT_ATR_SMA_LENGTH = 50

DEFAULT_F1_TREND        = True   # EMA20 > EMA50 alignment
DEFAULT_F2_VOLATILITY   = True   # ATR above ATR SMA
DEFAULT_F3_CLOSE_LOC    = False  # close in top/bottom 30%
DEFAULT_F4_CONTINUATION = False  # close breaks prior candle
DEFAULT_F5_ANTI_CHOP    = True   # EMA spread > ATR x 0.15

DEFAULT_PATTERN_FILTERS = {
    "f1": DEFAULT_F1_TREND, "f2": DEFAULT_F2_VOLATILITY, "f3": DEFAULT_F3_CLOSE_LOC,
    "f4": DEFAULT_F4_CONTINUATION, "f5": DEFAULT_F5_ANTI_CHOP,
}

DEFAULT_SHOW_EMA     = True
DEFAULT_SHOW_SIGNALS = True   # signal UP/DOWN markers + WIN/LOSS labels

F3_CLOSE_LOCATION_PCT = 0.70   # top/bottom 30% => close location ratio >= 0.70
F5_ANTI_CHOP_ATR_MULT = 0.15

DEFAULT_MIN_SIGNALS = 10   # matches Pine's i_min_sigs — "Min signals for edge detection"
MINTICK = 0.01             # matches Pine's syminfo.mintick floor for BTC/USDT-scale prices

# ─── Timing ────────────────────────────────────────────────────────────────
REFRESH_MS = 60_000   # dashboard + candle refresh, once a minute
LAST_N_CANDLES_TABLE = 10   # rows shown in the last-candles signal table
CHART_VISIBLE_CANDLES = 30  # chart is hard-sliced to exactly this many closed candles

# ─── Historical backfill scan (runs once on app startup, not every refresh) ─
BACKFILL_CANDLES_TARGET = 1000

# ─── Tab 2: Polymarket Order Book Simulator ─────────────────────────────────
# Paper-trading simulation only — no wallet, no real order, no API trading.
OB_REFRESH_MS          = 60_000   # flat scan cadence — independent of decision state
CANDIDATE_EXPIRY_SEC   = 60       # a candidate has 60s to find a valid entry

MAX_ENTRY_PRICE        = 0.50    # selected contract must be <= this
MIN_PROFIT_FACTOR      = 1.0     # (1 - price) / price must be >= this
MAX_SPREAD             = 0.08    # max acceptable bid-ask spread on selected side
MIN_LIQUIDITY_USD      = 25.0    # min combined top-5-level depth ($) on selected side
MIN_OB_PRESSURE        = 0.0     # pressure on selected side must be > this
PRESSURE_TREND_EPSILON = 0.02    # |change| below this counts as "Flat", not noise

OB_LEVELS  = 5
OB_WEIGHTS = [5, 4, 3, 2, 1]   # level 1 (best) weighted highest

# Tab 2's always-on dual-sided observer (independent of Tab 1's signal state)
TAB2_HISTORY_MAX   = 1200   # bounded rolling history length (1200 x 3s = 1 hour)
TAB2_CHART_WINDOW  = 200    # most recent samples plotted on the pressure graph

DEFAULT_STAKE = 1.0   # $1 per simulated paper trade — never a real order

# ─── Tab 3: Trading Engine (paper trading only — no wallet, no real order) ──
# Defaults only — the Tab 3 sidebar ("Apply Settings") is the single source
# of truth at runtime; nothing here is read directly by trade_engine.py once
# the sidebar has applied its own settings dict.
TAB3_DB_PATH = "tab3_trades.db"
TAB3_CHART_DIR = "tab3_charts"

DEFAULT_TAB3_REFRESH_INTERVAL_SEC      = 3      # engine tick — fast, drives Tab 3's live view + trade logic
DEFAULT_TAB3_CHART_REFRESH_SEC         = 30     # chart images only regenerate this often — values are always live
DEFAULT_TAB3_OBSERVATION_BURST_SEC     = 20     # Candidate Observation Time (initial burst, ~10 snapshots)

DEFAULT_TAB3_STAKE                  = 1.0
DEFAULT_TAB3_MAX_ENTRY_PRICE        = 0.52    # soft cap used by the entry-mode logic
DEFAULT_TAB3_HARD_BLOCK_PRICE       = 0.55    # hard rule — never enter above this, no exceptions
DEFAULT_TAB3_MIN_PROFIT_FACTOR      = 0.90
DEFAULT_TAB3_EARLY_EXIT_LOSS_PCT    = 0.20
DEFAULT_TAB3_PRESSURE_CONFIRM_COUNT = 3       # consecutive snapshots required for slope/streak checks
DEFAULT_TAB3_MAX_SPREAD             = 0.08
DEFAULT_TAB3_MIN_LIQUIDITY_USD      = 25.0
DEFAULT_TAB3_PRESSURE_THRESHOLD     = 0.15    # Mode 1 "pressure >= threshold"
DEFAULT_TAB3_DEPTH_STABLE_TOLERANCE = 0.10    # Mode 1 "ask depth stable" — max fractional change allowed

# When ON: skip every order-book condition (pressure/profit-factor/spread/
# liquidity) and enter immediately at whatever price is available the moment
# a candidate is created; skip early exit entirely (only settle_at_expiry
# ever closes the trade). Simpler alternative to the order-book-based entry
# modes above — everything else (stake, settlement, PnL, charts) is unchanged.
DEFAULT_TAB3_IMMEDIATE_MODE = False

TAB3_SNAPSHOT_HISTORY_MAX = 2000   # bounded in-memory rolling history per candidate/trade
