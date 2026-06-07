# src/analyzer.py — order book feature + score calculation (Stage 2)

import config


# ── Order-book features ────────────────────────────────────────────────────────

def calc_features(book: dict) -> dict:
    """Extract all numeric features from a raw CLOB order-book dict."""
    bids = sorted(book.get("bids") or [], key=lambda x: float(x["price"]), reverse=True)
    asks = sorted(book.get("asks") or [], key=lambda x: float(x["price"]))

    if not bids or not asks:
        return _empty_features()

    best_bid = float(bids[0]["price"])
    best_ask = float(asks[0]["price"])
    spread   = best_ask - best_bid

    top_bids = bids[:3]
    top_asks = asks[:3]

    near_bid_depth = sum(float(b["size"]) for b in top_bids)
    near_ask_depth = sum(float(a["size"]) for a in top_asks)

    total = near_bid_depth + near_ask_depth
    pressure = (near_bid_depth - near_ask_depth) / total if total > 0 else 0.0

    def weighted(levels):
        return sum(float(lv["size"]) / (i + 1) for i, lv in enumerate(levels[:3]))

    w_bid = weighted(top_bids)
    w_ask = weighted(top_asks)
    w_tot = w_bid + w_ask
    weighted_pressure = (w_bid - w_ask) / w_tot if w_tot > 0 else 0.0

    return {
        "best_bid":          best_bid,
        "best_ask":          best_ask,
        "spread":            spread,
        "near_bid_depth":    near_bid_depth,
        "near_ask_depth":    near_ask_depth,
        "pressure":          pressure,
        "weighted_pressure": weighted_pressure,
        "bid_levels":        len(bids),
        "ask_levels":        len(asks),
    }


def _empty_features() -> dict:
    return {
        "best_bid":          0.0,
        "best_ask":          1.0,
        "spread":            1.0,
        "near_bid_depth":    0.0,
        "near_ask_depth":    0.0,
        "pressure":          0.0,
        "weighted_pressure": 0.0,
        "bid_levels":        0,
        "ask_levels":        0,
    }


# ── Scoring ────────────────────────────────────────────────────────────────────

def calc_scores(features: dict, momentum: float = 0.0) -> dict:
    """
    Map features → component scores.
    pressure_score  : 0–40  (based on |weighted_pressure|)
    liquidity_score : 0–35  (total near depth, capped at 500 units)
    spread_score    : 0–25  (tighter is better; 0.02 → max, 0.10+ → 0)
    momentum_score  : 0–20  bonus for monitoring phase
    entry_score     : sum, capped at 100
    """
    wp    = abs(features.get("weighted_pressure", 0.0))
    depth = features.get("near_bid_depth", 0.0) + features.get("near_ask_depth", 0.0)
    sp    = features.get("spread", 1.0)

    pressure_score  = wp * config.SCORE_WEIGHT_PRESSURE
    liquidity_score = min(depth / 500.0, 1.0) * config.SCORE_WEIGHT_LIQUIDITY
    spread_score    = max(0.0, (0.10 - sp) / 0.08) * config.SCORE_WEIGHT_SPREAD
    momentum_score  = min(abs(momentum) * 20.0, 20.0)

    entry_score = min(pressure_score + liquidity_score + spread_score + momentum_score, 100.0)

    return {
        "pressure_score":  pressure_score,
        "liquidity_score": liquidity_score,
        "spread_score":    spread_score,
        "momentum_score":  momentum_score,
        "entry_score":     entry_score,
    }


def direction_from(features: dict) -> str:
    wp = features.get("weighted_pressure", 0.0)
    if wp > 0.02:
        return "UP"
    if wp < -0.02:
        return "DOWN"
    return "NEUTRAL"


def rank_candidates(candidates: list[dict]) -> list[dict]:
    """
    Each item must have 'features' already populated.
    Adds 'scores', 'candidate_score', 'direction'.
    Returns list sorted by candidate_score desc.
    """
    for c in candidates:
        scores = calc_scores(c.get("features", _empty_features()))
        c["scores"]          = scores
        c["candidate_score"] = scores["entry_score"]
        c["direction"]       = direction_from(c.get("features", _empty_features()))
    return sorted(candidates, key=lambda x: x["candidate_score"], reverse=True)
