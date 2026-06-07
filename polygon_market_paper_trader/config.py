# config.py — all application constants

# ── API ────────────────────────────────────────────────────────────────────────
GAMMA_BASE      = "https://gamma-api.polymarket.com"
CLOB_BASE       = "https://clob.polymarket.com"
REQUEST_TIMEOUT = 10
PAGE_SIZE       = 500
BOOK_DELAY      = 0.15   # seconds between CLOB calls

# ── Background engine timing ───────────────────────────────────────────────────
SCAN_INTERVAL     = 300   # full market scan every 5 minutes
SNAPSHOT_INTERVAL = 3     # monitoring snapshot every 3 seconds

# ── Monitoring duration per market type (seconds) ─────────────────────────────
MONITOR_DURATION = {
    "5m":  120,    # 2 minutes
    "15m": 300,    # 5 minutes
    "1h":  600,    # 10 minutes
}

# ── Acceptance window: (min_remaining_min, max_remaining_min) ─────────────────
# Only accept markets that have this much time left at scan time.
EXPIRY_WINDOWS = {
    "5m":  (3.0,  8.0),
    "15m": (7.0,  18.0),
    "1h":  (15.0, 75.0),
}

# ── Crypto detection ───────────────────────────────────────────────────────────
CRYPTO_TAG_SLUGS = {
    "crypto", "cryptocurrency", "bitcoin", "ethereum", "defi",
    "nft", "blockchain", "web3", "crypto-prices",
}

CRYPTO_KEYWORDS = {
    "bitcoin", "btc", "ethereum", "eth", "crypto", "cryptocurrency",
    "blockchain", "defi", "nft", "solana", "sol", "bnb", "binance",
    "ripple", "xrp", "dogecoin", "doge", "cardano", "ada",
    "polkadot", "dot", "avalanche", "avax", "chainlink", "link",
    "uniswap", "uni", "litecoin", "ltc", "stellar", "xlm",
    "tron", "trx", "shiba", "pepe", "memecoin",
}

# ── Paths ──────────────────────────────────────────────────────────────────────
DB_PATH = "database/market.db"
LOG_DIR = "logs"

# ── Dashboard defaults ─────────────────────────────────────────────────────────
DEFAULT_MAX_MONITOR    = 3
DEFAULT_ORDER_SIZE     = 2.0
DEFAULT_ENTRY_THRESHOLD = 70
DEFAULT_EXIT_MODE      = "Hold Until Expiry"
DEFAULT_TAKE_PROFIT    = 0.20
DEFAULT_STOP_LOSS      = 0.10

# ── Scoring weights (must sum to 100) ─────────────────────────────────────────
SCORE_WEIGHT_PRESSURE  = 40
SCORE_WEIGHT_LIQUIDITY = 35
SCORE_WEIGHT_SPREAD    = 25
