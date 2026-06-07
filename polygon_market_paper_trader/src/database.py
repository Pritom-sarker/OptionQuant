# src/database.py — SQLite operations (thread-safe via per-thread connections + WAL)
from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

import config
from src.utils import utcnow_str

_local  = threading.local()
_write_lock = threading.Lock()


# ── Connection ─────────────────────────────────────────────────────────────────

def _conn() -> sqlite3.Connection:
    if not getattr(_local, "conn", None):
        Path(config.DB_PATH).parent.mkdir(parents=True, exist_ok=True)
        _local.conn = sqlite3.connect(config.DB_PATH, timeout=30, check_same_thread=False)
        _local.conn.row_factory = sqlite3.Row
        _local.conn.execute("PRAGMA journal_mode=WAL")
        _local.conn.execute("PRAGMA synchronous=NORMAL")
        _local.conn.execute("PRAGMA foreign_keys=ON")
    return _local.conn


# ── Schema ─────────────────────────────────────────────────────────────────────

def init_db():
    Path(config.DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    with _write_lock:
        c = _conn()
        c.executescript("""
            CREATE TABLE IF NOT EXISTS markets (
                market_id         TEXT PRIMARY KEY,
                event_id          TEXT,
                title             TEXT,
                question          TEXT,
                category          TEXT,
                expiry_type       TEXT,
                end_time          TEXT,
                status            TEXT,
                rejection_reason  TEXT,
                token_id          TEXT,
                outcome           TEXT,
                accepting_orders  INTEGER DEFAULT 0,
                enable_order_book INTEGER DEFAULT 0,
                scan_time         TEXT
            );

            CREATE TABLE IF NOT EXISTS orderbook_snapshots (
                snapshot_id        INTEGER PRIMARY KEY AUTOINCREMENT,
                market_id          TEXT,
                token_id           TEXT,
                snapshot_time      TEXT,
                best_bid           REAL,
                best_ask           REAL,
                spread             REAL,
                near_bid_depth     REAL,
                near_ask_depth     REAL,
                pressure           REAL,
                weighted_pressure  REAL,
                liquidity_score    REAL,
                spread_score       REAL,
                pressure_score     REAL,
                entry_score        REAL,
                snapshot_type      TEXT DEFAULT 'monitoring'
            );

            CREATE TABLE IF NOT EXISTS paper_trades (
                trade_id      TEXT PRIMARY KEY,
                market_id     TEXT,
                token_id      TEXT,
                title         TEXT,
                side          TEXT,
                entry_price   REAL,
                exit_price    REAL,
                contracts     REAL,
                order_size    REAL,
                entry_time    TEXT,
                exit_time     TEXT,
                pnl           REAL,
                status        TEXT DEFAULT 'open',
                exit_reason   TEXT,
                entry_score   REAL,
                expiry_type   TEXT,
                end_time      TEXT
            );

            CREATE TABLE IF NOT EXISTS logs (
                log_id     INTEGER PRIMARY KEY AUTOINCREMENT,
                log_time   TEXT,
                level      TEXT,
                event_type TEXT,
                message    TEXT
            );
        """)
        c.commit()


# ── Markets ────────────────────────────────────────────────────────────────────

def upsert_market(m: dict):
    with _write_lock:
        _conn().execute("""
            INSERT OR REPLACE INTO markets
              (market_id, event_id, title, question, category, expiry_type,
               end_time, status, rejection_reason, token_id, outcome,
               accepting_orders, enable_order_book, scan_time)
            VALUES
              (:market_id, :event_id, :title, :question, :category, :expiry_type,
               :end_time, :status, :rejection_reason, :token_id, :outcome,
               :accepting_orders, :enable_order_book, :scan_time)
        """, m)
        _conn().commit()


def get_accepted_markets() -> list[dict]:
    rows = _conn().execute(
        "SELECT * FROM markets WHERE status='accepted' ORDER BY scan_time DESC"
    ).fetchall()
    return [dict(r) for r in rows]


def get_rejected_markets(limit: int = 300) -> list[dict]:
    rows = _conn().execute(
        "SELECT * FROM markets WHERE status='rejected' ORDER BY scan_time DESC LIMIT ?",
        (limit,)
    ).fetchall()
    return [dict(r) for r in rows]


def get_market(market_id: str) -> dict | None:
    row = _conn().execute(
        "SELECT * FROM markets WHERE market_id=?", (market_id,)
    ).fetchone()
    return dict(row) if row else None


# ── Snapshots ──────────────────────────────────────────────────────────────────

def insert_snapshot(s: dict):
    with _write_lock:
        _conn().execute("""
            INSERT INTO orderbook_snapshots
              (market_id, token_id, snapshot_time, best_bid, best_ask, spread,
               near_bid_depth, near_ask_depth, pressure, weighted_pressure,
               liquidity_score, spread_score, pressure_score, entry_score, snapshot_type)
            VALUES
              (:market_id, :token_id, :snapshot_time, :best_bid, :best_ask, :spread,
               :near_bid_depth, :near_ask_depth, :pressure, :weighted_pressure,
               :liquidity_score, :spread_score, :pressure_score, :entry_score, :snapshot_type)
        """, s)
        _conn().commit()


def get_snapshots(market_id: str, limit: int = 100) -> list[dict]:
    rows = _conn().execute(
        """SELECT * FROM orderbook_snapshots
           WHERE market_id=? ORDER BY snapshot_time DESC LIMIT ?""",
        (market_id, limit)
    ).fetchall()
    return [dict(r) for r in rows]


def get_latest_snapshot(market_id: str) -> dict | None:
    row = _conn().execute(
        """SELECT * FROM orderbook_snapshots
           WHERE market_id=? ORDER BY snapshot_time DESC LIMIT 1""",
        (market_id,)
    ).fetchone()
    return dict(row) if row else None


# ── Trades ─────────────────────────────────────────────────────────────────────

def insert_trade(t: dict):
    with _write_lock:
        _conn().execute("""
            INSERT INTO paper_trades
              (trade_id, market_id, token_id, title, side, entry_price, exit_price,
               contracts, order_size, entry_time, exit_time, pnl, status,
               exit_reason, entry_score, expiry_type, end_time)
            VALUES
              (:trade_id, :market_id, :token_id, :title, :side, :entry_price, :exit_price,
               :contracts, :order_size, :entry_time, :exit_time, :pnl, :status,
               :exit_reason, :entry_score, :expiry_type, :end_time)
        """, t)
        _conn().commit()


def update_trade(trade_id: str, updates: dict):
    with _write_lock:
        fields = ", ".join(f"{k}=?" for k in updates)
        vals   = list(updates.values()) + [trade_id]
        _conn().execute(f"UPDATE paper_trades SET {fields} WHERE trade_id=?", vals)
        _conn().commit()


def get_open_trades() -> list[dict]:
    rows = _conn().execute(
        "SELECT * FROM paper_trades WHERE status='open' ORDER BY entry_time DESC"
    ).fetchall()
    return [dict(r) for r in rows]


def get_closed_trades(limit: int = 100) -> list[dict]:
    rows = _conn().execute(
        "SELECT * FROM paper_trades WHERE status='closed' ORDER BY exit_time DESC LIMIT ?",
        (limit,)
    ).fetchall()
    return [dict(r) for r in rows]


def get_trade(trade_id: str) -> dict | None:
    row = _conn().execute(
        "SELECT * FROM paper_trades WHERE trade_id=?", (trade_id,)
    ).fetchone()
    return dict(row) if row else None


# ── Logs ───────────────────────────────────────────────────────────────────────

def insert_log(level: str, event_type: str, message: str):
    with _write_lock:
        _conn().execute(
            "INSERT INTO logs (log_time, level, event_type, message) VALUES (?,?,?,?)",
            (utcnow_str(), level, event_type, message)
        )
        _conn().commit()


def get_logs(event_type: str | None = None, limit: int = 300) -> list[dict]:
    if event_type:
        rows = _conn().execute(
            "SELECT * FROM logs WHERE event_type=? ORDER BY log_time DESC LIMIT ?",
            (event_type, limit)
        ).fetchall()
    else:
        rows = _conn().execute(
            "SELECT * FROM logs ORDER BY log_time DESC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(r) for r in rows]


# ── Stats ──────────────────────────────────────────────────────────────────────

def get_stats() -> dict:
    c = _conn()
    total    = c.execute("SELECT COUNT(*) FROM markets").fetchone()[0]
    accepted = c.execute("SELECT COUNT(*) FROM markets WHERE status='accepted'").fetchone()[0]
    rejected = c.execute("SELECT COUNT(*) FROM markets WHERE status='rejected'").fetchone()[0]
    open_t   = c.execute("SELECT COUNT(*) FROM paper_trades WHERE status='open'").fetchone()[0]
    closed_t = c.execute("SELECT COUNT(*) FROM paper_trades WHERE status='closed'").fetchone()[0]
    pnl_row  = c.execute("SELECT SUM(pnl) FROM paper_trades WHERE status='closed'").fetchone()
    total_pnl = pnl_row[0] or 0.0
    wins     = c.execute(
        "SELECT COUNT(*) FROM paper_trades WHERE status='closed' AND pnl > 0"
    ).fetchone()[0]
    win_rate = (wins / closed_t * 100) if closed_t > 0 else 0.0
    return {
        "total_markets":    total,
        "accepted_markets": accepted,
        "rejected_markets": rejected,
        "open_trades":      open_t,
        "closed_trades":    closed_t,
        "total_pnl":        total_pnl,
        "win_rate":         win_rate,
    }
