# BTCUSD Polymarket Signal Viewer & Trading Engine

A FastAPI app that watches real BTC/USD price action for a Pine-Script-defined
candle pattern, cross-references it against Polymarket's live BTC 5-minute
Up/Down order book, and — entirely in simulation — decides when, at what
price, and how to enter, monitor, and exit a paper trade.

> **⚠️ Simulation only, always.** No wallet, no real order placement, no
> live Polymarket trading, ever. Every "entry," "limit order," and "exit" in
> this app is a paper-trade record in a local SQLite file — nothing is ever
> sent to Polymarket or any exchange.

The app is five pages, each answering one question:

| Tab | Question it answers |
|---|---|
| **Tab 1 — BTC/USD Prediction** | Does the Pine Script pattern say there's a trade to make right now? |
| **Tab 2 — Order Book Simulator** | Is Polymarket's order book actually confirming that trade? |
| **Tab 3 — Live Trade** | What is the trade doing *right now*, second by second? |
| **Tab 4 — Trade Details** | Why did the bot do what it did — full breakdown, plain English. |
| **Tab 5 — Trade Stats & Documentation** | How has the strategy performed across every trade so far? |

---

## 1. The strategy — how Tab 1's signal actually works

Tab 1 is a line-for-line Python port of the Pine Script indicator in
[`btc_polymarket_signal_tester.pine`](btc_polymarket_signal_tester.pine)
(open it directly in TradingView if you want to compare candle-by-candle
behavior). The logic lives in [`signal_engine.py`](signal_engine.py) and
runs on real 5-minute BTC/USD candles fetched from Binance (falling back to
Coinbase) — [`btc_price_api.py`](btc_price_api.py).

### Indicators computed every candle
- **ATR** (Wilder, configurable length, default 14) and its own SMA (default
  50) — used for volatility filtering and pattern sizing.
- **EMA 20 / EMA 50 / EMA 200** — trend regime.

### Candle patterns (pick one — "Base Pattern")
Only one pattern is active at a time; it produces the *raw* directional
signal (`pat_dir`: +1 up, −1 down, 0 none) before any filter is applied.

- **ATR Reversal** (default) — the candle's body is at least
  `ATR × multiplier` (default 1.5×). A big red candle signals **UP** next
  (mean-reversion), a big green candle signals **DOWN** next.
- **Engulfing** — a green candle whose body fully engulfs the prior red
  candle's body signals **UP**; the mirrored bearish engulfing signals
  **DOWN**.
- **Hammer/Shooting Star** — a hammer (long lower wick ≥ 2× body, tiny upper
  wick, prior candle red) signals **UP**; a shooting star (mirrored) signals
  **DOWN**.
- **Exhaustion** — three candles in the same direction with a shrinking body
  each time (the move is running out of steam) signals a reversal.

### Filters (F1–F5 — each toggleable, layered on top of the pattern)
A candle only becomes an **active signal** if the pattern fired **and every
currently-enabled filter passes**. Turning a filter off skips it entirely —
it is never evaluated, not evaluated-and-ignored.

| Filter | Passes when |
|---|---|
| **F1 — Trend** | EMA20/EMA50 alignment agrees with the signal direction |
| **F2 — Volatility** | Current ATR is above its own SMA (the market is "active enough") |
| **F3 — Close Location** | The candle closed in the top (for UP) or bottom (for DOWN) 30% of its own range |
| **F4 — Continuation** | Close breaks the prior candle's high/low in the signal direction |
| **F5 — Anti-chop** | `\|EMA20 − EMA50\|` exceeds `ATR × 0.15` (rules out flat, choppy markets) |

### Live vs. historical evaluation
- **Live Mode** (the big prediction banner) advances exactly one step per
  *genuinely new, fully-closed* candle — it never scores a signal against a
  candle that hasn't closed yet, and never re-evaluates history in bulk.
- **Historical Entry Scan** runs once at startup over the last 1000 candles,
  scoring every past signal immediately (its future candle already exists in
  that batch) — this is what produces the win-rate stats, not the live
  banner.

Tab 1 exports only two things for the rest of the app to consume: the
direction (`GREEN`/`RED`/`UNKNOWN`) and the signal candle's data. Tab 1 never
reads anything back from Tabs 2–5.

---

## 2. Tab 2 — does the order book agree?

[`candidate_manager.py`](candidate_manager.py) / [`orderbook_engine.py`](orderbook_engine.py)
watch Polymarket's live CLOB order book for the currently active BTC 5-minute
Up/Down market, independent of whether Tab 1 currently has a signal:

- **Pressure** = weighted top-5 bid/ask depth imbalance, `(bid − ask) / (bid + ask)`,
  weights `[5, 4, 3, 2, 1]` (best level weighted highest) → ranges −1 (sellers
  in control) to +1 (buyers in control).
- The moment Tab 1 has a `GREEN`/`RED` signal, Tab 2 starts tracking **only**
  the corresponding side (`GREEN → YES`, `RED → NO`) — the local low since
  the signal, whether price is recovering off that low, and whether pressure
  is positive *and improving*. When all of that lines up, Tab 2 reports
  **READY** — but Tab 2 never places anything itself; it's a confirmation
  signal Tab 3 uses.

---

## 3. Tabs 3–5 — the trading engine

[`trade_engine.py`](trade_engine.py) is the actual decision engine (pure
logic, no network calls — [`background_worker.py`](background_worker.py)
does the fetching and calls into it). [`trade_db.py`](trade_db.py) persists
every candidate, snapshot, and trade to a local SQLite file
(`tab3_trades.db`, gitignored) — nothing is ever lost across a tick.

### Candidate → limit order → entry
The instant Tab 1 produces a `GREEN`/`RED` signal, a **candidate** is
created, locked to that specific Polymarket market (its own token IDs, so a
later market rollover never contaminates it) and to one side only
(`selected_side`) — GREEN never touches the NO book, RED never touches YES.

Every tick (`record_candidate_snapshot`), the engine computes pressure,
pressure *slope* (average change over the last few snapshots — the trend
matters far more than the instantaneous value), bid/ask depth change, and
tracks the **local low** since the signal. A simulated **limit order** rests
at that local low — the philosophy is "capture good trades," not "always buy
the cheapest price":

- **Mode 1 — Immediate Entry**: pressure is strong *and rising*, bid depth is
  increasing, ask depth is stable, spread/liquidity/profit-factor are all
  acceptable → buy now, at market, rather than risk missing the trade.
- **Mode 2 — Wait**: pressure is roughly balanced → keep watching, no edge
  yet.
- **Mode 3 — Deep Wait**: pressure is negative (price still falling) → wait
  for it to turn positive and start recovering, then it's a **recovery
  entry** at the resting limit price.
- A **hard price ceiling** (default 0.55) blocks entry no matter what any
  mode says.

### Active trade → early exit or expiry
Once filled, every tick records price/PnL/pressure (`record_trade_snapshot`).
The trade is held to expiry by default — **early exit** only fires to cut a
genuinely collapsing position, and only when *every one* of these agree:
real loss beyond the threshold, pressure negative for N consecutive
snapshots *and still falling*, bid depth falling, ask depth rising, spread
widening. Profit alone shrinking is never a reason to exit early.

### Settlement — the one bug worth documenting
These Polymarket markets resolve on "BTC price at window close vs. window
open." Polymarket's own order book is **not** used to determine win/loss —
once a market's 5-minute window closes, its book empties out completely, and
the only-briefly-tempting shortcut (reading the post-expiry mid-price) gets
stuck at a permanently ambiguous 0.5 forever. Instead, `settle_at_expiry`
resolves directly against the **real BTC candle spanning that exact window**
(the same candle data Tab 1 already fetches): if it closed above its open,
BTC went UP; compared against the trade's own direction, that's WIN or LOSS.
No dependency on Polymarket's post-resolution API at all.

### Where it shows up
- **Tab 3 (Live Trade)** — the lean, fast view: signal/side, status, current
  price, pressure, unrealized PnL, one live price chart.
- **Tab 4 (Trade Details)** — the full breakdown of the *same* live
  candidate/trade: every metric, the Limit Order Position explanation, the
  order book table, and a numbered "Step 1 / Step 2 / …" plain-English
  narrative of what happened and why.
- **Tab 5 (Trade Stats & Documentation)** — once a trade settles it
  disappears from Tab 3/4 and appears here automatically: aggregate stats
  (win rate, total/average profit, best/worst trade, …) plus a table of every
  past trade. Click **Details** on any row to open that trade's full report
  on its own page — signal info, entry/exit reasoning, every monitoring
  snapshot with a danger flag, and the four saved chart images
  (candle+limit-order, pressure, depth, PnL).

All of Tab 3/4/5's thresholds (max entry price, hard block, profit factor,
early-exit loss %, pressure confirmation count, spread/liquidity/pressure
thresholds, chart refresh interval, …) are adjustable on the **Settings**
page — nothing is hardcoded.

---

## 4. Architecture

```
main.py                  FastAPI app; starts the 3 background threads on boot
engine_state.py           Thread-safe in-memory state (replaces per-request state)
background_worker.py       3 independent loops:
                             tab1_loop  — every 15s: fetch candles, run signal_engine
                             tab2_loop  — every 60s: fetch order book, update pressure
                             tab3_loop  — fast while a candidate/trade is active,
                                          ~10s idle: the full trade lifecycle above
routes/
  pages.py                Full HTML pages (Jinja2)
  api.py                  HTML-partial endpoints polled by each page's JS
  charts.py               Server-rendered PNG chart images
templates/ + static/       Plain HTML/CSS/JS — no build step, edit and reload
```

**Why a background thread instead of computing on each page request:** the
trading engine has to keep ticking (watch the order book, decide entry,
monitor, settle) whether or not anyone has a browser open — it's not tied to
a request/response cycle.

**Why polling instead of websockets:** each page's JavaScript
(`static/js/poll.js`) fetches its own live-partial endpoint on its own timer
and swaps it into the DOM — no full page reload. Because each page is a
separate browser navigation, one page's refresh timer can never affect
another page's — unlike a single-page app where one shared timer would touch
everything at once. Values update every **2 seconds** on every page; chart
images specifically are regenerated only every **30 seconds** (chart
rendering — candle fetch + matplotlib/Plotly + disk write — is comparatively
expensive, and a picture doesn't need to be *that* fresh).

---

## 5. Running it locally

```bash
pip install -r requirements.txt
uvicorn main:app --reload
```

Open **http://localhost:8000** (redirects to Tab 1). `--reload` restarts the
server whenever you edit a `.py` file or a template.

The background engine starts automatically with the server — there's nothing
else to launch. `tab3_trades.db` (SQLite) and `tab3_charts/` (saved chart
images) are created in this folder on first run; both are gitignored.

> **Note:** live trade state lives only in memory, not the database. If you
> restart the server while a trade is genuinely open, that trade is orphaned
> (stuck "OPEN" in the DB). Restarting while Tab 3 shows no active trade is
> always safe.

---

## 6. Deploying to Railway

Already wired up — `Procfile` runs:

```
web: uvicorn main:app --host 0.0.0.0 --port $PORT
```

Push to your connected GitHub repo (or `railway up`) and Railway installs
`requirements.txt` and starts the app; no extra configuration needed. If the
repo root isn't this folder, set Railway's **Root Directory** to wherever
this `main.py` lives.

---

## 7. Project layout

```
btc_polymarket_signal_tester.pine   The original Pine Script indicator (source of truth for Tab 1's logic)
config.py                            Every default/threshold — nothing else hardcodes a number
signal_engine.py                     Pattern + filter logic (Section 1 above)
btc_price_api.py / candle_builder.py Real BTC/USD candles (Binance → Coinbase fallback)
polymarket_api.py / orderbook_api.py Polymarket market discovery + order book fetch (read-only)
orderbook_engine.py / candidate_manager.py   Tab 2's pressure math + always-on observer
trade_engine.py / trade_db.py         Tab 3-5's decision engine + SQLite persistence
chart_builder.py                     Every chart (Tab 1 candle chart, Tab 2's 5 charts, Tab 3-5's saved images)
engine_state.py / background_worker.py   The FastAPI process's live state + its 3 background loops
view_context.py                      Builds each page's template data (one place per tab, shared by pages.py/api.py)
routes/, templates/, static/         FastAPI routers, Jinja2 HTML, CSS/JS
paper_trade.py                       An earlier, simpler paper-trade design — kept but unused, superseded by trade_engine.py
```
