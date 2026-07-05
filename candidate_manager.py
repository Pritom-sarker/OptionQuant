"""
Tab 2 state tracking.

ObservationState is what Tab 2 actually uses today: an always-on, dual-sided
(YES + NO) order book observer that keeps running regardless of whether
Tab 1 has produced a GREEN/RED signal yet. It never places a real order —
it only ever reports READY/WAIT/OBSERVE as a status.

Candidate (below) is a single-sided, time-boxed tracker built for an earlier
design where Tab 2 itself created and settled paper trades. That
responsibility now belongs to a future "Tab 3" (which will combine Tab 1's
signal with Tab 2's order-book confirmation into an actual paper trade), so
Candidate and paper_trade.py are kept but currently unused by app.py.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional
import time

import config
import orderbook_api as obapi
import orderbook_engine as obe


@dataclass
class Candidate:
    signal_time: int          # Tab 1's signal candle close time (shared field)
    direction: int            # 1 = GREEN/UP (watch YES), -1 = RED/DOWN (watch NO)
    signal_close: float       # Tab 1's signal candle close price (shared field)
    prediction: str           # "GREEN" or "RED" — shared display label

    # Market/tokens are snapshotted at candidate-creation time and reused for
    # this candidate's whole lifetime — even if the *current* active market
    # rolls over to a new 5-minute window mid-candidate, this candidate keeps
    # watching the window it was actually created against.
    market_id: str = ""
    yes_token_id: str = ""
    no_token_id: str = ""
    market_expiry: float = 0.0

    created_at: float = field(default_factory=time.time)
    status: str = "ACTIVE"    # ACTIVE | ENTERED | EXPIRED | SKIPPED

    local_low: Optional[float] = None
    prev_selected_price: Optional[float] = None
    prev_pressure: Optional[float] = None
    last_decision: str = "WAIT"
    last_reason: str = "Just created — waiting for the first order book snapshot."
    last_metrics: Optional[obe.OBMetrics] = None
    pressure_trend: str = "Flat"

    history: list = field(default_factory=list)   # [{t, price, pressure, decision}]
    entry_price: Optional[float] = None
    entry_time: Optional[float] = None

    def selected_side(self) -> str:
        return "YES" if self.direction == 1 else "NO"

    def is_expired(self) -> bool:
        return time.time() > self.created_at + config.CANDIDATE_EXPIRY_SEC

    def is_valid(self) -> bool:
        return self.status == "ACTIVE" and not self.is_expired()

    def time_remaining(self) -> float:
        return max(0.0, self.created_at + config.CANDIDATE_EXPIRY_SEC - time.time())

    def is_recovering(self) -> bool:
        if self.local_low is None or self.prev_selected_price is None:
            return False
        return self.prev_selected_price > self.local_low

    def sample(self):
        """
        Pull a fresh order book snapshot for both sides (using the tokens
        this candidate was created against — never a later market's tokens),
        update local-low / recovery tracking, run the entry decision tree,
        and record one point of history for the chart.
        Returns (decision, reason, metrics).
        """
        selected_token = self.yes_token_id if self.direction == 1 else self.no_token_id
        other_token = self.no_token_id if self.direction == 1 else self.yes_token_id
        tte_seconds = self.market_expiry - time.time()

        selected_book = obapi.fetch_order_book(selected_token)
        other_book = obapi.fetch_order_book(other_token)
        metrics = obe.compute_metrics(self.selected_side(), selected_book, other_book)

        if self.local_low is None or metrics.selected_price < self.local_low:
            self.local_low = metrics.selected_price

        decision, reason = obe.check_entry(
            metrics=metrics,
            candidate_valid=self.is_valid(),
            candidate_expired=self.is_expired(),
            prev_selected_price=self.prev_selected_price,
            local_low=self.local_low,
            prev_pressure=self.prev_pressure,
            tte_seconds=tte_seconds,
        )

        self.pressure_trend = obe.pressure_trend(metrics.pressure, self.prev_pressure)
        self.history.append({
            "t": time.time(), "price": metrics.selected_price,
            "yes_price": metrics.yes_price, "no_price": metrics.no_price,
            "pressure": metrics.pressure, "decision": decision,
        })

        self.prev_selected_price = metrics.selected_price
        self.prev_pressure = metrics.pressure
        self.last_decision = decision
        self.last_reason = reason
        self.last_metrics = metrics

        return decision, reason, metrics

    def mark_entered(self, price: float) -> None:
        self.status = "ENTERED"
        self.entry_price = price
        self.entry_time = time.time()

    def mark_expired_or_skipped(self) -> None:
        self.status = "EXPIRED" if self.is_expired() else "SKIPPED"


@dataclass
class ObservationState:
    """
    Tab 2's always-on dual-sided order book observer. Fetches and records
    YES and NO metrics every refresh regardless of Tab 1's signal state.

    When Tab 1 has no GREEN/RED signal, this stays in OBSERVE mode: history
    keeps accumulating, but no side is "selected" and no local-low/recovery
    tracking runs. The moment Tab 1's active signal changes (a new
    signal_time, or a flip from GREEN<->RED), the selected side and its
    local-low/recovery tracking reset and start fresh — and keep running
    until Tab 1's signal changes again (there is no separate expiry timer
    here; that concept belongs to the future Tab 3 trade-entry flow).
    """
    yes_price_history: list = field(default_factory=list)     # [{t, price}]
    no_price_history: list = field(default_factory=list)
    yes_pressure_history: list = field(default_factory=list)  # [{t, pressure}]
    no_pressure_history: list = field(default_factory=list)

    prev_yes_pressure: Optional[float] = None
    prev_no_pressure: Optional[float] = None

    tab1_signal_time: Optional[int] = None
    selected_side: Optional[str] = None             # "YES" | "NO" | None
    selected_side_local_low: Optional[float] = None
    selected_side_prev_price: Optional[float] = None
    selected_side_prev_pressure: Optional[float] = None

    last_yes_metrics: Optional[obe.SideMetrics] = None
    last_no_metrics: Optional[obe.SideMetrics] = None
    last_yes_book: dict = field(default_factory=dict)   # raw {"bids": [...], "asks": [...]} snapshot
    last_no_book: dict = field(default_factory=dict)
    yes_trend: str = "Flat"
    no_trend: str = "Flat"

    last_decision: str = "OBSERVE"   # OBSERVE | WAIT | READY
    last_reason: str = ("No trade decision yet because Tab 1 has not produced GREEN or RED. "
                         "Order book is being monitored only.")

    def is_recovering(self) -> bool:
        if self.selected_side_local_low is None or self.selected_side_prev_price is None:
            return False
        return self.selected_side_prev_price > self.selected_side_local_low

    def reset(self) -> None:
        """
        Clears all rolling history, selected-side tracking, and last-known
        metrics/books — used when the underlying 5-minute Polymarket contract
        rolls over, so Tab 2's charts start empty again for the new contract
        instead of carrying over the expired one's price/pressure history.
        """
        self.yes_price_history = []
        self.no_price_history = []
        self.yes_pressure_history = []
        self.no_pressure_history = []

        self.prev_yes_pressure = None
        self.prev_no_pressure = None

        self.tab1_signal_time = None
        self.selected_side = None
        self.selected_side_local_low = None
        self.selected_side_prev_price = None
        self.selected_side_prev_pressure = None

        self.last_yes_metrics = None
        self.last_no_metrics = None
        self.last_yes_book = {}
        self.last_no_book = {}
        self.yes_trend = "Flat"
        self.no_trend = "Flat"

        self.last_decision = "OBSERVE"
        self.last_reason = ("No trade decision yet because Tab 1 has not produced GREEN or RED. "
                             "Order book is being monitored only.")

    def observe(self, yes_book: dict, no_book: dict, tab1_prediction: Optional[dict]) -> None:
        """
        Always fetches/records both sides' metrics, then — only if Tab 1
        currently has a GREEN/RED signal — updates the selected side's
        local-low/recovery tracking and the READY/WAIT confirmation check.
        """
        now = time.time()
        yes_metrics = obe.compute_side_metrics(yes_book)
        no_metrics = obe.compute_side_metrics(no_book)

        self.yes_price_history.append({"t": now, "price": yes_metrics.price})
        self.no_price_history.append({"t": now, "price": no_metrics.price})
        self.yes_pressure_history.append({"t": now, "pressure": yes_metrics.pressure})
        self.no_pressure_history.append({"t": now, "pressure": no_metrics.pressure})
        for hist_name in ("yes_price_history", "no_price_history",
                          "yes_pressure_history", "no_pressure_history"):
            hist = getattr(self, hist_name)
            if len(hist) > config.TAB2_HISTORY_MAX:
                setattr(self, hist_name, hist[-config.TAB2_HISTORY_MAX:])

        self.yes_trend = obe.pressure_trend(yes_metrics.pressure, self.prev_yes_pressure)
        self.no_trend = obe.pressure_trend(no_metrics.pressure, self.prev_no_pressure)
        self.last_yes_metrics = yes_metrics
        self.last_no_metrics = no_metrics
        self.last_yes_book = yes_book
        self.last_no_book = no_book

        predicted = tab1_prediction.get("predicted_next") if tab1_prediction else None
        if predicted not in ("GREEN", "RED"):
            self.selected_side = None
            self.tab1_signal_time = None
            self.selected_side_local_low = None
            self.selected_side_prev_price = None
            self.selected_side_prev_pressure = None
            self.last_decision = "OBSERVE"
            self.last_reason = ("No trade decision yet because Tab 1 has not produced GREEN or RED. "
                                 "Order book is being monitored only.")
        else:
            new_side = "YES" if predicted == "GREEN" else "NO"
            signal_time = int(tab1_prediction["time"])

            if self.tab1_signal_time != signal_time or self.selected_side != new_side:
                # A genuinely new (or direction-flipped) Tab 1 signal — reset
                # local-low/recovery tracking and start fresh for this signal.
                self.tab1_signal_time = signal_time
                self.selected_side = new_side
                self.selected_side_local_low = None
                self.selected_side_prev_price = None
                self.selected_side_prev_pressure = None

            side_metrics = yes_metrics if new_side == "YES" else no_metrics
            if self.selected_side_local_low is None or side_metrics.price < self.selected_side_local_low:
                self.selected_side_local_low = side_metrics.price

            decision, reason = obe.check_confirmation(
                side_metrics, self.selected_side_local_low,
                self.selected_side_prev_price, self.selected_side_prev_pressure,
            )
            self.last_decision = decision
            self.last_reason = reason
            self.selected_side_prev_price = side_metrics.price
            self.selected_side_prev_pressure = side_metrics.pressure

        self.prev_yes_pressure = yes_metrics.pressure
        self.prev_no_pressure = no_metrics.pressure
