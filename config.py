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
DEFAULT_ENABLED_PATTERNS = ["ATR Reversal", "Exhaustion"]   # enabled by default; the rest start off

DEFAULT_ATR_LENGTH     = 14
DEFAULT_ATR_MULTIPLIER = 0.2
DEFAULT_ATR_SMA_LENGTH = 50

DEFAULT_F1_TREND        = False  # EMA20 > EMA50 alignment
DEFAULT_F2_VOLATILITY   = False  # ATR above ATR SMA
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

# Early Entry (ON by default): normally a pattern is only ever evaluated
# once its candle has fully closed (see btc_price_api's _drop_forming_candle).
# With this on, the last DEFAULT_TAB1_EARLY_ENTRY_LEAD_SEC seconds before
# close also get checked against the *forming* candle's still-moving OHLC —
# if the pattern already matches, that's used as an early signal so Tab 3
# can place the next window's order right as it opens instead of waiting for
# the close + the normal detection cycle. This is the fix for signals
# occasionally being detected 2-3 minutes late (see TAB3_ENTRY_DEADLINE_SEC
# below for the backstop on the rare cases this still misses). Trades
# entered off an early signal are never cancelled/exited if the true close
# later disagrees with the provisional one — they ride to expiry like any
# other trade; only a log line records the mismatch.
DEFAULT_TAB1_EARLY_ENTRY_ENABLED  = True
DEFAULT_TAB1_EARLY_ENTRY_LEAD_SEC = 15

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

# Early Entry (see DEFAULT_TAB1_EARLY_ENTRY_* below) runs in its own
# dedicated loop at this much tighter cadence, decoupled from
# TAB1_POLL_INTERVAL_SEC — it only needs one cheap forming-candle fetch +
# a pattern check against the already-cached closed-candle dataframe, not
# the full _tick_tab1() pipeline, so polling it this often is cheap. It's
# only ever active during the last early_entry_lead_sec seconds before a
# candle closes, so this is the number of real chances to catch a pattern
# the moment it becomes confirmable — at 2s shared with the full tick, a
# pattern that only stabilizes in the final second or two of the candle
# (common for reversal patterns, which depend on the exact final body)
# could get essentially zero chances to be caught early; at 1s it gets up
# to early_entry_lead_sec chances instead.
TAB1_EARLY_POLL_INTERVAL_SEC = 1

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
# the sidebar has applied its own settings dict. No per-trade stake setting
# here on purpose — every real trade's stake is now sized live by Tab 6's
# Money Management settings (see money_management.next_trade_amount and
# background_worker._tick_tab3), not a flat dollar amount.
TAB3_DB_PATH = "tab3_trades.db"
TAB3_CHART_DIR = "tab3_charts"

DEFAULT_TAB3_REFRESH_INTERVAL_SEC      = 3      # engine tick — fast, drives Tab 3's live view + trade logic
DEFAULT_TAB3_CHART_REFRESH_SEC         = 30     # chart images only regenerate this often — values are always live
DEFAULT_TAB3_OBSERVATION_BURST_SEC     = 20     # Candidate Observation Time (initial burst, ~10 snapshots)

# tab3_loop() switches to this much tighter tick cadence whenever any
# candidate is still OBSERVING (no trade placed yet) and within
# fast_poll_lead_sec of its candle's actual open (before or after) — this is
# the "catch the exact right price" window, so it's worth polling the order
# book close to every second instead of the normal refresh_interval. Once a
# trade is placed (or the candidate falls outside that window entirely —
# e.g. dropped as SKIPPED_LATE), the loop falls back to refresh_interval for
# ordinary trade-outcome monitoring. Both configurable on Settings (Tab 3).
DEFAULT_TAB3_FAST_POLL_LEAD_SEC     = 15    # start fast-polling this many seconds before candle open
DEFAULT_TAB3_FAST_POLL_INTERVAL_SEC = 1.0   # tick cadence while fast-polling

DEFAULT_TAB3_MAX_ENTRY_PRICE        = 0.50    # soft cap used by the entry-mode logic
DEFAULT_TAB3_HARD_BLOCK_PRICE       = 0.55    # hard rule — never enter above this, no exceptions
DEFAULT_TAB3_MIN_PROFIT_FACTOR      = 0.90
DEFAULT_TAB3_EARLY_EXIT_LOSS_PCT    = 0.20
DEFAULT_TAB3_PRESSURE_CONFIRM_COUNT = 3       # consecutive snapshots required for slope/streak checks
DEFAULT_TAB3_MAX_SPREAD             = 0.08
DEFAULT_TAB3_MIN_LIQUIDITY_USD      = 25.0
DEFAULT_TAB3_PRESSURE_THRESHOLD     = 0.15    # Mode 1 "pressure >= threshold"
DEFAULT_TAB3_DEPTH_STABLE_TOLERANCE = 0.10    # Mode 1 "ask depth stable" — max fractional change allowed

# When ON: skip every order-book condition (pressure/spread/liquidity —
# Minimum Profit Factor and the Immediate Entry Window still apply, see
# _decide_entry in trade_engine.py) and enter as soon as both clear, instead
# of waiting on pressure/slope confirmation; skip early exit entirely (only
# settle_at_expiry ever closes the trade). ON by default — this is the
# live strategy currently in use.
DEFAULT_TAB3_IMMEDIATE_MODE = True

# Immediate Entry only fires within this many seconds of the candle's actual
# open (candidate.signal_time) — from signal_time - window to signal_time +
# window. Outside that window it holds off (WAIT) even if the profit factor
# floor is met; still bounded overall by entry_deadline_sec above, which
# eventually drops the candidate as SKIPPED_LATE if it never lines up.
# 25s: deliberately tight — if it can't get filled within the first ~25s of
# the candle actually opening, the price has moved too far from "the open"
# to be worth chasing; better to skip the trade than buy deep and late. This
# only works because Early Entry (Tab 1) + the fast-poll tick (see
# fast_poll_lead_sec below) already have the candidate created and actively
# checking price ~15s BEFORE the candle even opens — most of the old
# candidate-creation latency that justified a wider window is absorbed
# before signal_time is even reached. Configurable on Settings (Tab 3).
DEFAULT_TAB3_IMMEDIATE_ENTRY_WINDOW_SEC = 25

# Backstop for signals that still land late despite Early Entry (see Tab 1's
# early-entry settings above): once a candidate's predicted window has
# actually opened (time.time() >= candidate.signal_time), it gets this many
# seconds to enter — after that, entering would mean buying deep into a
# window at a price that's no longer representative of the open, so the
# candidate is marked SKIPPED_LATE instead of entered. Skipped-late
# candidates show up in Tab 5's history for visibility but never factor into
# win/loss/profit stats, since no money ever moved on them. Configurable on
# Settings (Tab 3) — a tight deadline (e.g. 10s) suits Immediate Entry, a
# longer one (e.g. 130s) gives the order-book-confirmed recovery-entry mode
# enough time to actually find its dip-and-recover setup.
DEFAULT_TAB3_ENTRY_DEADLINE_SEC = 130

TAB3_SNAPSHOT_HISTORY_MAX = 2000   # bounded in-memory rolling history per candidate/trade

# ─── Tab 6: Money Management ─────────────────────────────────────────────────
# Tiered cycle/win-pool sizing — ported from pine_strategy_simulator/
# money_management.py::run_tiered_simulation (validated there against the
# Order-1-10 sequence and an adversarial repeated-loss scenario). Every real
# trade Tab 3 places is sized live from this (see
# money_management.next_trade_amount_tiered and
# background_worker._tick_tab3) — this replays the app's own REAL settled
# trades (trade_db) each time, using each trade's own actual entry price via
# orderbook_engine.profit_factor() for WIN payout/recovery accounting (a
# $0.30 entry pays a different multiple than a $0.60 one, same as real money
# would); stake SIZING itself never divides by a profit factor, since an
# upcoming trade's own price is unknowable in advance — see
# money_management.py's module docstring.
DEFAULT_MM_STARTING_BALANCE        = 1000.0   # display only — does not affect sizing
DEFAULT_MM_BASE_STAKE              = 1.01     # order 1 of every new cycle
DEFAULT_MM_STATIC_LP_PCT           = 0.20     # % of the permanent pool added to order 1 only
DEFAULT_MM_MAX_FIRST_ORDER_STAKE   = 3.01     # caps order 1's base + LP add-on combined
DEFAULT_MM_MAXIMUM_CYCLE_ORDERS    = 10
DEFAULT_MM_FALLBACK_MODE           = "stop"   # "stop" | "continue" | "manual"
DEFAULT_MM_CYCLE_TIMEOUT_LP_PCT    = 0.20     # % of a maxed-out cycle's loss sent to the LP; rest written off
DEFAULT_MM_WIN_POOL_CONTRIBUTION_PCT = 0.20   # % of every win's profit set aside into the win pool
DEFAULT_MM_WIN_POOL_LP_COVERAGE_PCT  = 0.50   # % of the current LP paid from the win pool after every win
DEFAULT_MM_TIERS = [
    {"start": 1, "end": 3, "pct": 1.0},
    {"start": 4, "end": 6, "pct": 0.5},
    {"start": 7, "end": 10, "pct": 0.2},
]
