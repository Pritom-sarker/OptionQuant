# config.py — application constants

GAMMA_API_BASE = "https://gamma-api.polymarket.com"
CLOB_API_BASE  = "https://clob.polymarket.com"
DB_PATH        = "database/market.db"
LOG_DIR        = "logs"

# Engine timing (seconds)
SCAN_INTERVAL     = 300   # 5-minute full market scan
SNAPSHOT_INTERVAL = 3     # order book refresh
BOOK_DELAY        = 0.05  # throttle between individual API fetches

# Monitoring
DEFAULT_MAX_MONITORED = 20

# Order sizing
DEFAULT_ORDER_SIZE = 1.0
DEFAULT_ORDER_MODE = "fixed"
DEFAULT_MAX_ORDER  = 5.0

# Entry rules (dashboard defaults)
DEFAULT_MIN_REWARD_PF   = 1.0
DEFAULT_MIN_FLIP_SCORE  = 75
DEFAULT_MAX_SPREAD      = 0.04
DEFAULT_MIN_CHEAP_PRICE = 0.05
DEFAULT_MAX_ENTRY_PRICE = 0.50
MIN_SECONDS_FOR_ENTRY   = 120   # 2 minutes hard minimum

# Flip detection thresholds (must ALL pass for entry)
FLIP_PRESSURE_THRESHOLD     = 0.25
FLIP_VELOCITY_THRESHOLD     = 0.08
FLIP_ACCELERATION_THRESHOLD = 0.03
FLIP_DEPTH_SHIFT_THRESHOLD  = 0.15

# Exit
DEFAULT_EXIT_MODE   = "Hold Until Expiry"
DEFAULT_TAKE_PROFIT = 0.50   # 50 % profit ratio
DEFAULT_STOP_LOSS   = 0.50   # 50 % loss ratio

# Streamlit
DASHBOARD_REFRESH_MS = 3000

# Crypto detection
CRYPTO_KEYWORDS = [
    "bitcoin", "btc", "ethereum", "eth", "solana", "sol",
    "xrp", "ripple", "crypto", "bnb", "doge", "dogecoin",
    "ada", "cardano", "matic", "avax", "avalanche",
    "link", "chainlink", "dot", "polkadot", "ltc", "litecoin",
    "uni", "uniswap", "shib", "pepe", "wif", "bonk",
]
CRYPTO_TAG_SLUGS = frozenset({"crypto", "cryptocurrency", "bitcoin", "ethereum"})

# Expiry time windows  (min_remaining_min, max_remaining_min)
TIME_WINDOWS = {
    "5m":  (2.5,  8.0),
    "15m": (2.5, 18.0),
    "1h":  (2.5, 75.0),
}
