# src/monitor.py — per-market monitoring session (Stage 3)
from __future__ import annotations

import time
from typing import Optional

import config
from src.api_client import fetch_order_book
from src.analyzer import calc_features, calc_scores, direction_from
from src.database import insert_snapshot, insert_log
from src.utils import utcnow_str


class MarketMonitor:
    """
    Tracks one market during the pre-entry monitoring window.
    Call take_snapshot() every SNAPSHOT_INTERVAL seconds.
    When should_complete is True, call get_final_verdict() to decide on entry.
    """

    def __init__(self, market_id: str, token_id: str, expiry_type: str):
        self.market_id   = market_id
        self.token_id    = token_id
        self.expiry_type = expiry_type

        self._start      = time.time()
        self._duration   = config.MONITOR_DURATION.get(expiry_type, 120)
        self._snapshots: list[dict] = []
        self.is_complete = False

    # ── Timing ─────────────────────────────────────────────────────────────────

    @property
    def elapsed(self) -> float:
        return time.time() - self._start

    @property
    def should_complete(self) -> bool:
        return self.elapsed >= self._duration

    @property
    def progress_pct(self) -> float:
        return min(self.elapsed / self._duration * 100, 100.0)

    # ── Pressure tracking ──────────────────────────────────────────────────────

    @property
    def first_pressure(self) -> float:
        return self._snapshots[0]["weighted_pressure"] if self._snapshots else 0.0

    @property
    def latest_pressure(self) -> float:
        return self._snapshots[-1]["weighted_pressure"] if self._snapshots else 0.0

    @property
    def pressure_momentum(self) -> float:
        return self.latest_pressure - self.first_pressure

    # ── Snapshot ───────────────────────────────────────────────────────────────

    def take_snapshot(self) -> Optional[dict]:
        book = fetch_order_book(self.token_id)
        if not book:
            return None

        features = calc_features(book)
        momentum = self.pressure_momentum
        scores   = calc_scores(features, momentum)

        snap = {
            "market_id":         self.market_id,
            "token_id":          self.token_id,
            "snapshot_time":     utcnow_str(),
            "best_bid":          features["best_bid"],
            "best_ask":          features["best_ask"],
            "spread":            features["spread"],
            "near_bid_depth":    features["near_bid_depth"],
            "near_ask_depth":    features["near_ask_depth"],
            "pressure":          features["pressure"],
            "weighted_pressure": features["weighted_pressure"],
            "liquidity_score":   scores["liquidity_score"],
            "spread_score":      scores["spread_score"],
            "pressure_score":    scores["pressure_score"],
            "entry_score":       scores["entry_score"],
            "snapshot_type":     "monitoring",
        }

        self._snapshots.append(snap)
        insert_snapshot(snap)
        return snap

    # ── Final verdict ──────────────────────────────────────────────────────────

    def get_final_verdict(self) -> tuple[float, str, float]:
        """
        Returns (entry_score, direction, weighted_pressure).
        Called once monitoring period is complete.
        """
        if not self._snapshots:
            return 0.0, "NEUTRAL", 0.0

        latest = self._snapshots[-1]
        features = {
            "weighted_pressure": latest["weighted_pressure"],
            "spread":            latest["spread"],
            "near_bid_depth":    latest["near_bid_depth"],
            "near_ask_depth":    latest["near_ask_depth"],
        }
        scores    = calc_scores(features, self.pressure_momentum)
        direction = direction_from(features)

        insert_log(
            "INFO", "monitor",
            f"Monitoring complete [{self.market_id[:12]}…] "
            f"score={scores['entry_score']:.1f} dir={direction} "
            f"momentum={self.pressure_momentum:+.4f}"
        )
        return scores["entry_score"], direction, latest["weighted_pressure"]
