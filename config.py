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
PATTERN_OPTIONS = ["ATR Reversal", "Engulfing", "Hammer/SS", "Exhaustion"]
DEFAULT_PATTERN = "ATR Reversal"

DEFAULT_ATR_LENGTH     = 14
DEFAULT_ATR_MULTIPLIER = 1.5
DEFAULT_ATR_SMA_LENGTH = 50

DEFAULT_F1_TREND        = True   # EMA20 > EMA50 alignment
DEFAULT_F2_VOLATILITY   = True   # ATR above ATR SMA
DEFAULT_F3_CLOSE_LOC    = False  # close in top/bottom 30%
DEFAULT_F4_CONTINUATION = False  # close breaks prior candle
DEFAULT_F5_ANTI_CHOP    = True   # EMA spread > ATR x 0.15

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
OB_REFRESH_MS_FAST     = 3_000    # scan cadence while decision is READY (trade would be placed)
OB_REFRESH_MS_SLOW     = 30_000   # scan cadence otherwise (OBSERVE / WAIT) — fewer API calls
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
