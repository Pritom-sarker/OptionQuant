"""
Tab 3 — SQLite persistence. SIMULATION ONLY, no real orders.

Every candidate/trade snapshot is INSERTed and never overwritten — the
`candidates` and `trades` tables each hold one evolving summary row per
entity (its current status), which is a distinct thing from a snapshot log
and is allowed to be updated in place (matching how PaperTrade.status
already works in paper_trade.py).

A fresh sqlite3 connection is opened per call rather than cached/shared,
since Streamlit can run a session across multiple threads and sqlite3
connections are not safe to share across threads by default.
"""
from __future__ import annotations
import json
import sqlite3
import time

import config

_SCHEMA = """
CREATE TABLE IF NOT EXISTS candidates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_time INTEGER,
    direction INTEGER,
    prediction TEXT,
    signal_open REAL,
    signal_high REAL,
    signal_low REAL,
    signal_close REAL,
    atr REAL,
    body REAL,
    body_atr_ratio REAL,
    reason TEXT,
    selected_side TEXT,
    market_slug TEXT,
    created_at REAL,
    status TEXT
);

CREATE TABLE IF NOT EXISTS candidate_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    candidate_id INTEGER,
    ts REAL,
    best_bid REAL,
    best_ask REAL,
    mid REAL,
    spread REAL,
    top5_bids_json TEXT,
    top5_asks_json TEXT,
    weighted_bid_depth REAL,
    weighted_ask_depth REAL,
    pressure REAL,
    pressure_change REAL,
    pressure_slope REAL,
    bid_depth_change REAL,
    ask_depth_change REAL,
    selected_price REAL,
    local_low REAL,
    recovering INTEGER,
    decision TEXT,
    mode TEXT,
    reason TEXT
);

CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    candidate_id INTEGER,
    market_slug TEXT,
    direction INTEGER,
    prediction TEXT,
    entry_time REAL,
    entry_price REAL,
    stake REAL,
    entry_mode TEXT,
    entry_reason TEXT,
    expiry_time REAL,
    status TEXT,
    exit_time REAL,
    exit_price REAL,
    exit_reason TEXT,
    final_result TEXT,
    pnl REAL,
    return_pct REAL,
    settled_at REAL
);

CREATE TABLE IF NOT EXISTS trade_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_id INTEGER,
    ts REAL,
    price REAL,
    pnl REAL,
    pnl_pct REAL,
    pressure REAL,
    pressure_trend TEXT,
    spread REAL,
    liquidity REAL,
    bid_depth REAL,
    ask_depth REAL,
    time_remaining REAL
);
"""

# Columns added after the initial release above — kept as guarded ALTERs
# (rather than baked into _SCHEMA) so an existing tab3_trades.db from before
# this change upgrades in place instead of erroring.
_MIGRATIONS = [
    ("candidates", "f1_trend", "TEXT"),
    ("candidates", "f2_volatility", "TEXT"),
    ("candidates", "f3_close_location", "TEXT"),
    ("candidates", "f4_continuation", "TEXT"),
    ("candidates", "f5_anti_chop", "TEXT"),
    ("candidates", "chart_path", "TEXT"),
    ("candidate_snapshots", "limit_price", "REAL"),
    ("candidate_snapshots", "limit_touched", "INTEGER"),
    ("trades", "candle_chart_path", "TEXT"),
    ("trades", "pressure_chart_path", "TEXT"),
    ("trades", "depth_chart_path", "TEXT"),
    ("trades", "pnl_chart_path", "TEXT"),
    ("trades", "report_text", "TEXT"),
]


def _run_migrations(conn: sqlite3.Connection) -> None:
    for table, column, coltype in _MIGRATIONS:
        existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
        if column not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {coltype}")


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(config.TAB3_DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    _run_migrations(conn)
    return conn


def insert_candidate(candidate: dict) -> int:
    with get_connection() as conn:
        cur = conn.execute(
            """INSERT INTO candidates
               (signal_time, direction, prediction, signal_open, signal_high, signal_low,
                signal_close, atr, body, body_atr_ratio, reason, selected_side, market_slug,
                created_at, status, f1_trend, f2_volatility, f3_close_location, f4_continuation,
                f5_anti_chop)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (candidate["signal_time"], candidate["direction"], candidate["prediction"],
             candidate["signal_open"], candidate["signal_high"], candidate["signal_low"],
             candidate["signal_close"], candidate["atr"], candidate["body"],
             candidate["body_atr_ratio"], candidate["reason"], candidate["selected_side"],
             candidate["market_slug"], time.time(), "OBSERVING",
             candidate.get("f1_trend"), candidate.get("f2_volatility"),
             candidate.get("f3_close_location"), candidate.get("f4_continuation"),
             candidate.get("f5_anti_chop")),
        )
        return cur.lastrowid


def update_candidate_status(candidate_id: int, status: str) -> None:
    with get_connection() as conn:
        conn.execute("UPDATE candidates SET status = ? WHERE id = ?", (status, candidate_id))


def update_candidate_chart_path(candidate_id: int, chart_path: str) -> None:
    with get_connection() as conn:
        conn.execute("UPDATE candidates SET chart_path = ? WHERE id = ?", (chart_path, candidate_id))


def insert_candidate_snapshot(candidate_id: int, snap: dict) -> int:
    with get_connection() as conn:
        cur = conn.execute(
            """INSERT INTO candidate_snapshots
               (candidate_id, ts, best_bid, best_ask, mid, spread, top5_bids_json, top5_asks_json,
                weighted_bid_depth, weighted_ask_depth, pressure, pressure_change, pressure_slope,
                bid_depth_change, ask_depth_change, selected_price, local_low, recovering,
                decision, mode, reason, limit_price, limit_touched)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (candidate_id, snap["ts"], snap["best_bid"], snap["best_ask"], snap["mid"],
             snap["spread"], json.dumps(snap["top5_bids"]), json.dumps(snap["top5_asks"]),
             snap["weighted_bid_depth"], snap["weighted_ask_depth"], snap["pressure"],
             snap["pressure_change"], snap["pressure_slope"], snap["bid_depth_change"],
             snap["ask_depth_change"], snap["selected_price"], snap["local_low"],
             int(bool(snap["recovering"])), snap["decision"], snap["mode"], snap["reason"],
             snap.get("limit_price"), int(bool(snap.get("limit_touched")))),
        )
        return cur.lastrowid


def insert_trade(trade: dict) -> int:
    with get_connection() as conn:
        cur = conn.execute(
            """INSERT INTO trades
               (candidate_id, market_slug, direction, prediction, entry_time, entry_price, stake,
                entry_mode, entry_reason, expiry_time, status, exit_time, exit_price, exit_reason,
                final_result, pnl, return_pct, settled_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, NULL, NULL, NULL, NULL, NULL)""",
            (trade["candidate_id"], trade["market_slug"], trade["direction"], trade["prediction"],
             trade["entry_time"], trade["entry_price"], trade["stake"], trade["entry_mode"],
             trade["entry_reason"], trade["expiry_time"], "OPEN"),
        )
        return cur.lastrowid


def update_trade_chart_paths(trade_id: int, candle_chart_path: str = None, pressure_chart_path: str = None,
                              depth_chart_path: str = None, pnl_chart_path: str = None) -> None:
    """Only overwrites the paths actually passed in (None = leave as-is)."""
    fields, values = [], []
    for col, val in (("candle_chart_path", candle_chart_path), ("pressure_chart_path", pressure_chart_path),
                      ("depth_chart_path", depth_chart_path), ("pnl_chart_path", pnl_chart_path)):
        if val is not None:
            fields.append(f"{col} = ?")
            values.append(val)
    if not fields:
        return
    values.append(trade_id)
    with get_connection() as conn:
        conn.execute(f"UPDATE trades SET {', '.join(fields)} WHERE id = ?", values)


def insert_trade_snapshot(trade_id: int, snap: dict) -> int:
    with get_connection() as conn:
        cur = conn.execute(
            """INSERT INTO trade_snapshots
               (trade_id, ts, price, pnl, pnl_pct, pressure, pressure_trend, spread, liquidity,
                bid_depth, ask_depth, time_remaining)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (trade_id, snap["ts"], snap["price"], snap["pnl"], snap["pnl_pct"], snap["pressure"],
             snap["pressure_trend"], snap["spread"], snap["liquidity"], snap["bid_depth"],
             snap["ask_depth"], snap["time_remaining"]),
        )
        return cur.lastrowid


def update_trade_settlement(trade_id: int, status: str, exit_time: float, exit_price: float,
                             exit_reason: str, final_result: str, pnl: float, return_pct: float,
                             report_text: str = None) -> None:
    with get_connection() as conn:
        conn.execute(
            """UPDATE trades SET status = ?, exit_time = ?, exit_price = ?, exit_reason = ?,
               final_result = ?, pnl = ?, return_pct = ?, settled_at = ?, report_text = ? WHERE id = ?""",
            (status, exit_time, exit_price, exit_reason, final_result, pnl, return_pct,
             time.time(), report_text, trade_id),
        )


def fetch_recent_trades(limit: int = 20) -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM trades ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]


def fetch_all_trades() -> list[dict]:
    """Uncapped — used for Closed Trades Block summary stats, not just the recent-N list."""
    with get_connection() as conn:
        rows = conn.execute("SELECT * FROM trades WHERE status != 'OPEN' ORDER BY id DESC").fetchall()
        return [dict(r) for r in rows]


def fetch_candidate(candidate_id: int) -> dict | None:
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM candidates WHERE id = ?", (candidate_id,)).fetchone()
        return dict(row) if row else None


def fetch_candidate_snapshots(candidate_id: int) -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM candidate_snapshots WHERE candidate_id = ? ORDER BY ts", (candidate_id,)
        ).fetchall()
        return [dict(r) for r in rows]


def fetch_trade_snapshots(trade_id: int) -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM trade_snapshots WHERE trade_id = ? ORDER BY ts", (trade_id,)
        ).fetchall()
        return [dict(r) for r in rows]
