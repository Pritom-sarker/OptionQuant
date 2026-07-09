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

# Early Entry (opt-in, OFF by default): normally a pattern is only ever
# evaluated once its candle has fully closed (see btc_price_api's
# _drop_forming_candle). When this is on, the last DEFAULT_TAB1_EARLY_ENTRY_
# LEAD_SEC seconds before close also get checked against the *forming*
# candle's still-moving OHLC — if the pattern already matches, that's used
# as an early signal so Tab 3 can place the next window's order without
# waiting for the close + the normal detection cycle. Trades entered off an
# early signal are never cancelled/exited if the true close later disagrees
# with the provisional one — they ride to expiry like any other trade; only
# a log line records the mismatch.
DEFAULT_TAB1_EARLY_ENTRY_ENABLED  = False
DEFAULT_TAB1_EARLY_ENTRY_LEAD_SEC = 10

# ─── Timing ────────────────────────────────────────────────────────────────
REFRESH_MS = 60_000   # dashboard + candle refresh, once a minute
LAST_N_CANDLES_TABLE = 10   # rows shown in the last-candles signal table
CHART_VISIBLE_CANDLES = 30  # chart is hard-sliced to exactly this many closed candles

# How often background_worker's tab1_loop re-fetches candles and re-checks
# for a freshly-closed candle's signal. This directly gates how late an
# Immediate Entry trade can land after the real candle open — a signal can
# only be noticed on the next tick after the candle actually closes, so this
# interval is the single biggest lever on entry-price slippage for Immediate
# Entry mode. Was 15s (worst case ~15s stale detection, stacked with
# tab3_loop's idle interval below into ~25s of total lag); keep this low.
TAB1_POLL_INTERVAL_SEC = 2

# How often background_worker's tab3_loop ticks while no candidate/trade is
# active (the common state — most ticks find nothing to do). Was 10s, which
# meant a brand-new signal from tab1_loop could sit unnoticed for up to 10
# more seconds even after being detected. Kept low for the same
# Immediate-Entry-latency reason as TAB1_POLL_INTERVAL_SEC above.
TAB3_IDLE_POLL_INTERVAL_SEC = 2

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
DEFAULT_TAB3_IMMEDIATE_MODE = True

TAB3_SNAPSHOT_HISTORY_MAX = 2000   # bounded in-memory rolling history per candidate/trade

# ─── Tab 6: Money Management Simulator ──────────────────────────────────────
# Ported from pine_strategy_simulator/money_management.py's "Money Management
# Simulator" tab — identical sizing/loss-basket/recovery formulas, same
# settings. Only one deliberate change: that version assumed every WIN pays
# out at a static profit factor of 1 (gross_win = trade_amount flat, i.e. as
# if every contract were bought at exactly $0.50). This version replays the
# app's own REAL settled trades (trade_db), so it uses each trade's own
# actual entry price via orderbook_engine.profit_factor() instead — a $0.30
# entry pays a different multiple than a $0.60 one, same as real money would.
DEFAULT_MM_STARTING_BALANCE        = 1000.0
DEFAULT_MM_BASE_TRADE_AMOUNT       = 1.0
DEFAULT_MM_MAX_TRADE_AMOUNT        = 10.0
DEFAULT_MM_RECOVERY_PERCENT        = 0.10   # 10% — used only when dynamic_mode is off
DEFAULT_MM_DYNAMIC_MODE            = False
DEFAULT_MM_PROFIT_SPLIT_RECOVERY   = 0.50   # 50% of each win's profit pays down the loss basket
DEFAULT_MM_RESET_MODE              = "never"   # "never" | "on_zero" | "after_n_wins"
DEFAULT_MM_RESET_AFTER_N_WINS      = 5
