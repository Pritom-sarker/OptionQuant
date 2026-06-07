# src/utils.py — shared helpers
from __future__ import annotations

from datetime import datetime, timezone


def utcnow_str() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def parse_dt(s: str) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None


def minutes_remaining(end_str: str) -> float | None:
    dt = parse_dt(end_str)
    if dt is None:
        return None
    now = datetime.now(timezone.utc)
    return (dt - now).total_seconds() / 60.0


def fmt_pnl(pnl: float | None) -> str:
    if pnl is None:
        return "—"
    sign = "+" if pnl >= 0 else ""
    return f"{sign}${pnl:.4f}"


def fmt_score(score: float | None) -> str:
    if score is None:
        return "—"
    return f"{score:.1f}"


def fmt_pressure(p: float | None) -> str:
    if p is None:
        return "—"
    return f"{p:+.4f}"


def age_str(ts: str) -> str:
    dt = parse_dt(ts)
    if dt is None:
        return "—"
    diff = (datetime.now(timezone.utc) - dt).total_seconds()
    if diff < 60:
        return f"{int(diff)}s"
    if diff < 3600:
        return f"{int(diff // 60)}m {int(diff % 60)}s"
    return f"{int(diff // 3600)}h {int((diff % 3600) // 60)}m"
