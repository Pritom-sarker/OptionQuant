# src/scanner.py — background engine (Stages 1-3 in one daemon thread)
from __future__ import annotations

import threading
import time

import config
from src import database as db
from src.api_client import fetch_all_active_events, fetch_order_book, check_api_status
from src.filter import filter_events
from src.analyzer import calc_features, calc_scores, direction_from, rank_candidates
from src.monitor import MarketMonitor
from src.trader import enter_trade, exit_trade, close_at_expiry, check_exit_conditions
from src.utils import utcnow_str


# ── Singleton ──────────────────────────────────────────────────────────────────

_engine: "BackgroundEngine | None" = None
_engine_lock = threading.Lock()


def get_engine() -> "BackgroundEngine":
    global _engine
    with _engine_lock:
        if _engine is None:
            _engine = BackgroundEngine()
        return _engine


# ── Engine ─────────────────────────────────────────────────────────────────────

class BackgroundEngine:

    def __init__(self):
        self._thread: threading.Thread | None = None
        self._running  = False
        self._monitors: dict[str, MarketMonitor] = {}
        self._mon_lock = threading.Lock()

        self._settings = {
            "max_monitor":     config.DEFAULT_MAX_MONITOR,
            "order_size":      config.DEFAULT_ORDER_SIZE,
            "entry_threshold": config.DEFAULT_ENTRY_THRESHOLD,
            "exit_mode":       config.DEFAULT_EXIT_MODE,
            "take_profit":     config.DEFAULT_TAKE_PROFIT,
            "stop_loss":       config.DEFAULT_STOP_LOSS,
        }

        self._last_scan  = 0.0
        self._scan_count = 0
        self._status     = "idle"
        self._api_ok     = False
        self._api_msg    = "not checked"

    # ── Public interface ───────────────────────────────────────────────────────

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def status(self) -> str:
        return self._status

    @property
    def api_status(self) -> tuple[bool, str]:
        return self._api_ok, self._api_msg

    @property
    def monitor_count(self) -> int:
        return len(self._monitors)

    def update_settings(self, settings: dict):
        self._settings.update(settings)

    def force_scan(self):
        self._last_scan = 0.0

    def start(self):
        if self._running:
            return
        self._running = True
        self._thread  = threading.Thread(
            target=self._loop, daemon=True, name="BackgroundEngine"
        )
        self._thread.start()
        db.insert_log("INFO", "scan", "Background engine started")

    def stop(self):
        self._running = False
        self._status  = "stopped"
        db.insert_log("INFO", "scan", "Background engine stopped")

    # ── Main loop ──────────────────────────────────────────────────────────────

    def _loop(self):
        while self._running:
            now = time.time()

            if now - self._last_scan >= config.SCAN_INTERVAL:
                self._status = "scanning"
                try:
                    self._scan_cycle()
                except Exception as e:
                    db.insert_log("ERROR", "scan", f"Scan cycle error: {e}")
                self._last_scan = time.time()
                self._status = "monitoring"

            try:
                self._monitoring_cycle()
            except Exception as e:
                db.insert_log("ERROR", "monitor", f"Monitor cycle error: {e}")

            try:
                self._exit_check_cycle()
            except Exception as e:
                db.insert_log("ERROR", "trade_exit", f"Exit check error: {e}")

            time.sleep(config.SNAPSHOT_INTERVAL)

        self._status = "stopped"

    # ── Stage 1: Scan ──────────────────────────────────────────────────────────

    def _scan_cycle(self):
        self._scan_count += 1
        db.insert_log("INFO", "scan", f"=== Scan #{self._scan_count} started ===")

        ok, msg = check_api_status()
        self._api_ok, self._api_msg = ok, msg
        if not ok:
            db.insert_log("ERROR", "scan", f"API unreachable ({msg}) — skipping scan")
            return

        events = fetch_all_active_events()
        if not events:
            db.insert_log("WARNING", "scan", "No events returned from API")
            return

        accepted, rejected = filter_events(events)
        total_mkts = sum(len(e.get("markets") or []) for e in events)

        db.insert_log(
            "INFO", "scan",
            f"Scan #{self._scan_count}: {len(events)} events / {total_mkts} markets → "
            f"{len(accepted)} accepted, {len(rejected)} rejected"
        )

        scan_time = utcnow_str()
        self._save_rejected(rejected, scan_time)
        self._save_accepted(accepted, scan_time)
        self._select_for_monitoring(accepted)

    def _save_rejected(self, rejected: list[dict], scan_time: str):
        for item in rejected:
            event  = item["event"]
            mkt    = item.get("market") or {}
            mkt_id = str(mkt.get("id") or event.get("id") or "")
            if not mkt_id:
                continue
            # Use composite key to avoid collision between event-level rejections
            market_id = f"rej_{mkt_id}"
            db.upsert_market({
                "market_id":         market_id,
                "event_id":          str(event.get("id", "")),
                "title":             event.get("title") or mkt.get("question") or "",
                "question":          mkt.get("question") or "",
                "category":          event.get("category") or "",
                "expiry_type":       None,
                "end_time":          event.get("endDate") or mkt.get("endDate") or "",
                "status":            "rejected",
                "rejection_reason":  item["reason"],
                "token_id":          None,
                "outcome":           None,
                "accepting_orders":  int(bool(mkt.get("acceptingOrders", False))),
                "enable_order_book": int(bool(mkt.get("enableOrderBook", False))),
                "scan_time":         scan_time,
            })

    def _save_accepted(self, accepted: list[dict], scan_time: str):
        for item in accepted:
            event    = item["event"]
            mkt      = item["market"]
            mkt_id   = str(mkt.get("id") or event.get("id") or "")
            if not mkt_id:
                continue
            db.upsert_market({
                "market_id":         mkt_id,
                "event_id":          str(event.get("id", "")),
                "title":             event.get("title") or mkt.get("question") or "",
                "question":          mkt.get("question") or "",
                "category":          event.get("category") or "",
                "expiry_type":       item["expiry_type"],
                "end_time":          item.get("end_time") or "",
                "status":            "accepted",
                "rejection_reason":  None,
                "token_id":          item["token_id"],
                "outcome":           item["outcome"],
                "accepting_orders":  int(bool(mkt.get("acceptingOrders", False))),
                "enable_order_book": int(bool(mkt.get("enableOrderBook", False))),
                "scan_time":         scan_time,
            })
            db.insert_log(
                "INFO", "filter",
                f"ACCEPTED [{item['expiry_type']}] {(event.get('title') or '')[:55]} "
                f"| remaining={item['remaining_minutes']:.1f}m"
            )

    # ── Stage 2: Candidate selection ──────────────────────────────────────────

    def _select_for_monitoring(self, accepted: list[dict]):
        max_monitor = self._settings.get("max_monitor", config.DEFAULT_MAX_MONITOR)
        if not accepted:
            return

        with self._mon_lock:
            already_monitoring = set(self._monitors.keys())

        candidates = []
        for item in accepted:
            mkt_id = str(item["market"].get("id") or item["event"].get("id") or "")
            if mkt_id in already_monitoring:
                continue

            book = fetch_order_book(item["token_id"])
            time.sleep(config.BOOK_DELAY)
            if not book:
                continue

            features = calc_features(book)
            scores   = calc_scores(features)

            # Save candidate snapshot
            db.insert_snapshot({
                "market_id":         mkt_id,
                "token_id":          item["token_id"],
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
                "snapshot_type":     "candidate",
            })

            candidates.append({
                **item,
                "market_id": mkt_id,
                "features":  features,
            })

        ranked   = rank_candidates(candidates)
        top_n    = ranked[:max_monitor]

        for c in top_n:
            mid = c["market_id"]
            with self._mon_lock:
                if mid not in self._monitors:
                    self._monitors[mid] = MarketMonitor(
                        mid, c["token_id"], c["expiry_type"]
                    )
            db.insert_log(
                "INFO", "monitor",
                f"START monitoring [{c['expiry_type']}] "
                f"{(c['event'].get('title') or '')[:50]} "
                f"| candidate_score={c['candidate_score']:.1f} "
                f"| direction={c['direction']}"
            )

    # ── Stage 3: Monitoring ────────────────────────────────────────────────────

    def _monitoring_cycle(self):
        with self._mon_lock:
            monitors = dict(self._monitors)

        completed = []
        for mid, mon in monitors.items():
            mon.take_snapshot()

            if mon.should_complete and not mon.is_complete:
                mon.is_complete = True
                self._evaluate_entry(mid, mon)
                completed.append(mid)

        with self._mon_lock:
            for mid in completed:
                self._monitors.pop(mid, None)

    def _evaluate_entry(self, market_id: str, mon: MarketMonitor):
        threshold  = self._settings.get("entry_threshold", config.DEFAULT_ENTRY_THRESHOLD)
        order_size = self._settings.get("order_size", config.DEFAULT_ORDER_SIZE)

        score, direction, _ = mon.get_final_verdict()

        if direction == "NEUTRAL":
            db.insert_log("INFO", "monitor",
                f"NO TRADE [{market_id[:12]}…]: direction neutral")
            return

        if score < threshold:
            db.insert_log("INFO", "monitor",
                f"NO TRADE [{market_id[:12]}…]: score {score:.1f} < threshold {threshold}")
            return

        mkt  = db.get_market(market_id)
        snap = db.get_latest_snapshot(market_id)
        if not mkt or not snap:
            return

        enter_trade(
            market_id   = market_id,
            token_id    = mkt.get("token_id") or mon.token_id,
            title       = mkt.get("title") or market_id,
            side        = direction,
            entry_price = snap["best_ask"],
            order_size  = order_size,
            entry_score = score,
            expiry_type = mkt.get("expiry_type") or mon.expiry_type,
            end_time    = mkt.get("end_time") or "",
        )

    # ── Exit checks ────────────────────────────────────────────────────────────

    def _exit_check_cycle(self):
        open_trades = db.get_open_trades()
        settings    = self._settings

        for trade in open_trades:
            snap = db.get_latest_snapshot(trade["market_id"])
            if not snap:
                # Try to take a fresh snapshot
                if trade.get("token_id"):
                    book = fetch_order_book(trade["token_id"])
                    if book:
                        features = calc_features(book)
                        scores   = calc_scores(features)
                        snap = {
                            "market_id":         trade["market_id"],
                            "token_id":          trade["token_id"],
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
                            "snapshot_type":     "trade_monitor",
                        }
                        db.insert_snapshot(snap)
                if not snap:
                    continue

            should_exit, reason, exit_price = check_exit_conditions(
                trade, snap, settings
            )
            if should_exit:
                if reason == "Market expired":
                    close_at_expiry(trade["trade_id"])
                else:
                    exit_trade(trade["trade_id"], exit_price, reason)
