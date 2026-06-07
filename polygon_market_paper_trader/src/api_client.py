# src/api_client.py — Polymarket API wrapper
from __future__ import annotations

import requests
import time
import config

_session: requests.Session | None = None


def _get_session() -> requests.Session:
    global _session
    if _session is None:
        _session = requests.Session()
        _session.headers.update({"User-Agent": "polygon-market-paper-trader/1.0"})
    return _session


def check_api_status() -> tuple[bool, str]:
    try:
        r = _get_session().get(
            f"{config.GAMMA_BASE}/events/keyset",
            params={"limit": 1},
            timeout=5,
        )
        return r.status_code == 200, str(r.status_code)
    except Exception as e:
        return False, str(e)


def _fetch_events_page(after_cursor: str | None) -> dict:
    params = {
        "limit":     config.PAGE_SIZE,
        "order":     "createdAt",
        "ascending": "false",
        "closed":    "false",
        "active":    "true",
    }
    if after_cursor:
        params["after_cursor"] = after_cursor
    r = _get_session().get(
        f"{config.GAMMA_BASE}/events/keyset",
        params=params,
        timeout=config.REQUEST_TIMEOUT,
    )
    r.raise_for_status()
    return r.json()


def fetch_all_active_events() -> list[dict]:
    """Paginate through all active events and return them."""
    from src.database import insert_log   # late import avoids circular at module load

    events: list[dict] = []
    cursor = None
    pages  = 0

    while True:
        try:
            resp = _fetch_events_page(cursor)
        except Exception as e:
            insert_log("ERROR", "scan", f"API page {pages} failed: {e}")
            break

        page_events = resp.get("events") or []
        events.extend(page_events)
        pages += 1

        next_cursor = resp.get("next_cursor")
        if not next_cursor or not page_events:
            break
        cursor = next_cursor

    insert_log("INFO", "scan", f"API: fetched {len(events)} events over {pages} pages")
    return events


def fetch_order_book(token_id: str) -> dict | None:
    """Fetch CLOB order book for one token. Returns None on 404 or error."""
    from src.database import insert_log

    try:
        r = _get_session().get(
            f"{config.CLOB_BASE}/book",
            params={"token_id": token_id},
            timeout=config.REQUEST_TIMEOUT,
        )
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.json()
    except Exception as e:
        insert_log("WARNING", "analyze", f"Book fetch failed [{token_id[:16]}…]: {e}")
        return None
