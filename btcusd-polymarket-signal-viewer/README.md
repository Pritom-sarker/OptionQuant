# btcusd-polymarket-signal-viewer

A single-purpose Streamlit app: fetch BTCUSD Polymarket candles, run the
exact signal logic from `btc_polymarket_signal_tester.pine`, and show a
TradingView-style chart plus a plain-English breakdown of the last 10
candles.

> **⚠️ Visualisation only.** No order book, no mock trading, no real orders,
> no wallet. Only BTCUSD is fetched — no other markets.

---

## How it works

1. **`polymarket_api.py`** — finds the currently active BTC 5-minute
   Up/Down market (`btc-updown-5m-{window_end_ts}` slug) and fetches its
   YES token's raw price history (timestamp/price points).
2. **`candle_builder.py`** — buckets those points into 5-minute OHLC
   candles, keeping the latest 100.
3. **`signal_engine.py`** — recreates the Pine Script indicator exactly:
   - ATR(14, Wilder) and ATR SMA(50)
   - EMA20 / EMA50 / EMA200
   - 4 candle patterns: ATR Reversal, Engulfing, Hammer/SS, Exhaustion
   - 5 filters: F1 Trend, F2 Volatility, F3 Close Location, F4 Continuation,
     F5 Anti-chop
   - A candle only produces a final ENTRY signal if its pattern fires AND
     every *enabled* filter passes.
4. **`chart_builder.py`** — TradingView-style Plotly candlestick chart with
   EMA overlays, UP/DOWN entry markers, and break-price lines.
5. **`app.py`** — sidebar settings (mirrors the Pine Script inputs exactly),
   summary cards, the chart, and the last-10-candle signal table with a
   plain-English reason for every ENTRY or NO ENTRY.

Everything is recomputed fresh on each refresh — there is no database.

---

## Install and run

```bash
cd btcusd-polymarket-signal-viewer
pip install -r requirements.txt
streamlit run app.py
```

The dashboard auto-refreshes every 1 minute, matching the 5-minute candle
close cadence.

---

## Project files

| File | Purpose |
|---|---|
| `app.py` | Streamlit UI — sidebar settings, chart, summary cards, signal table |
| `config.py` | Pattern/filter defaults, timing, candle settings |
| `polymarket_api.py` | Read-only BTCUSD market + price history fetch |
| `candle_builder.py` | Raw price ticks -> 5-minute OHLC candles |
| `signal_engine.py` | ATR/EMA indicators, 4 patterns, 5 filters, entry logic |
| `chart_builder.py` | TradingView-style Plotly chart |

---

## Known limitations

- EMA200 needs 200 candles to fully converge; with the default 100-candle
  window it is a reasonable approximation, not a mature long-run average —
  same limitation any chart has with limited history.
- ATR SMA(50) needs `ATR_LENGTH + ATR_SMA_LENGTH - 1` candles before it
  produces a value; until then F2/F5 (and any pattern relying on them)
  show as unavailable for the earliest candles in the window.
- Candle history depends on how long the current 5-minute market's own
  token has been trading; a very freshly listed window may briefly have
  fewer than 100 candles.
