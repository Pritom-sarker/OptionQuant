"""
Polymarket order book — READ-ONLY, no auth, no orders.

Fetches the live CLOB order book (top bids/asks) for a given token. This is
the only file in Tab 2 that talks to Polymarket's order-book endpoint —
kept separate from polymarket_api.py (market discovery) so each file has one
clear job.
"""
from __future__ import annotations
from typing import Optional

import requests
import config

_SESSION = requests.Session()
_SESSION.headers.update({
    "Accept": "application/json",
    "User-Agent": "btcusd-polymarket-signal-viewer/1.0",
})


def fetch_order_book(token_id: str) -> dict:
    """
    Returns {"bids": [{price, size}, ...] (desc by price),
             "asks": [{price, size}, ...] (asc by price)}.
    Returns empty lists on any failure — never fabricates data.
    """
    if not token_id:
        return {"bids": [], "asks": []}
    try:
        r = _SESSION.get(f"{config.CLOB_API}/book", params={"token_id": token_id},
                          timeout=config.REQUEST_TIMEOUT)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        print(f"[orderbook_api] GET book failed: {e}")
        return {"bids": [], "asks": []}

    def parse(levels):
        out = []
        for lv in levels or []:
            try:
                out.append({"price": float(lv["price"]), "size": float(lv["size"])})
            except Exception:
                pass
        return out

    bids = sorted(parse(data.get("bids", [])), key=lambda x: x["price"], reverse=True)
    asks = sorted(parse(data.get("asks", [])), key=lambda x: x["price"])
    return {"bids": bids, "asks": asks}


def get_best_bid(order_book: dict) -> Optional[float]:
    bids = order_book.get("bids", [])
    return bids[0]["price"] if bids else None


def get_best_ask(order_book: dict) -> Optional[float]:
    asks = order_book.get("asks", [])
    return asks[0]["price"] if asks else None
