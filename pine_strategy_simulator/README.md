# Pine Strategy Simulator

A standalone Streamlit backtesting dashboard for `btc_polymarket_signal_tester.pine`.
Selecting a pair, timeframe, and candle count automatically runs **every**
combination of base pattern x ATR multiplier x filter toggle (768 setups) —
there's no "pick one strategy" mode. This is a separate project from the
main OptionQuant app — it doesn't import or modify anything in the parent
directory.

## Install

```bash
cd pine_strategy_simulator
pip install -r requirements.txt
```

## Run

```bash
streamlit run app.py
```

## Scoring rule — read this first

A signal fires on candle N and predicts **candle N+1 only**, scored once
N+1 has fully closed (never against N itself, never against a still-forming
candle):

- **UP signal**: WIN if `close[N+1] > open[N+1]`, LOSS if
  `close[N+1] < open[N+1]`.
- **DOWN signal**: WIN if `close[N+1] < open[N+1]`, LOSS if
  `close[N+1] > open[N+1]`.
- `close[N+1] == open[N+1]` → **NEUTRAL** — excluded from Win Rate and Loss
  Rate (both are computed as a fraction of Wins+Losses only), though it still
  counts toward Total Signals and Signal Frequency %.

This is a deliberate correction from an earlier version of this simulator,
which scored a signal against the signal candle's own close instead of the
result candle's open — that matched the Pine script's own dashboard exactly,
but this simulator now intentionally scores differently, per instruction.

**Streak counting**: NEUTRAL results are skipped entirely when computing Max
Consecutive Losses / Current Loss Streak / Max Consecutive Wins — a
break-even candle neither extends nor breaks a streak, so `LOSS, NEUTRAL,
LOSS` counts as a 2-loss streak.

## How candle caching works

Candle data is cached to `data/{PAIR}_{TIMEFRAME}.csv` (e.g.
`data/BTCUSDT_5m.csv`), and the cache always holds the **largest** candle
count you've ever requested for that pair/timeframe:

1. If the cached file already has at least as many candles as you asked for,
   it's just sliced from disk — no API call at all.
2. If you ask for more than what's cached, only the *missing older history*
   is paginated from Binance (going further back in time) and merged in —
   already-cached candles are never re-fetched.
3. If there's no cache at all yet, the full requested count is paginated
   from scratch.

Binance's `/klines` endpoint caps each request at 1,000 candles, so reaching
10,000-100,000 candles means dozens to ~100 paginated requests — the
dashboard shows live progress while this happens ("Fetching candles...",
"Loaded 12,000 candles...", etc.).

100,000 candles may exceed what Binance actually has for some pair/timeframe
combinations (e.g. 100,000 hourly candles is ~11.4 years of history) — you
get however many genuinely exist, with a warning banner, never fabricated
data.

**Force Refresh**: the sidebar's "Force Refresh" button ignores the cache
entirely and re-paginates the full requested count from scratch, overwriting
the CSV.

## How the Pine Script was converted

`pine_logic.py` is a line-for-line Python port of
`btc_polymarket_signal_tester.pine`'s indicator and pattern/filter logic —
nothing here invents new *detection* behavior (only the win/loss scoring
rule above is an intentional departure, per instruction):

- **ATR**: Wilder/RMA smoothing (`ta.atr`), same recursive formula as Pine.
- **4 base patterns** (`detect_pattern`): ATR Reversal (body >= ATR x
  multiplier), Engulfing, Hammer/Shooting Star, Exhaustion — same conditions,
  same UP(+1)/DOWN(-1) direction convention as the Pine script's
  `atr_raw_dir` / `eng_raw_dir` / `hss_raw_dir` / `exh_raw_dir`.
- **5 filters** (`compute_filters`): F1 Trend (EMA20 vs EMA50), F2 Volatility
  (ATR above its own SMA), F3 Close Location (top/bottom 30% of the candle's
  range), F4 Continuation (close breaks the prior candle's extreme), F5
  Anti-chop (EMA20/50 spread > ATR x 0.15) — identical formulas to the Pine
  script's `f1_bull`/`f1_bear`, `f2_pass`, `f3_bull`/`f3_bear`,
  `f4_bull`/`f4_bear`, `f5_pass`.

One important behavior carried over from Pine: the **ATR multiplier only
affects the ATR Reversal pattern's threshold**. Engulfing, Hammer/SS, and
Exhaustion don't take an ATR multiplier as an input in the Pine script
either, so sweeping the multiplier produces identical results for those 3
strategies — this isn't a bug, it's the same behavior as the original
script. The "ATR Multiplier vs Win Rate" graph and the "Best Setup Per ATR
Multiplier" ranking mix all 4 strategies together (as literally requested),
but keep this in mind when reading them.

ATR length (14) and ATR SMA length (50) are **not** swept — only the ATR
multiplier is — and are fixed at the Pine script's own defaults
(`config.py`).

## How to read the dashboard

- **Sidebar**: Pair, Timeframe, Candle Count (10,000 / 20,000 / 50,000 /
  100,000). Changing any of these automatically fetches (or slices from
  cache) that dataset and re-runs the full 768-setup scan — no button
  needed. Results for a given (pair, timeframe, candle count) are cached for
  the rest of the browser session, so flipping back to a combination you've
  already run is instant.
- **Main Result Table**: all 768 setups for the current selection, with
  every column requested (Pair, Timeframe, Total Candles Tested, Strategy
  Name, ATR Multiplier, Filter Combination, Total Signals, Total Wins, Total
  Losses, Neutral Signals, Win Rate, Loss Rate, Signal Frequency %, Average
  Next Candle Move, Average Winning Move, Average Losing Move, Best Win
  Move, Worst Loss Move, Max Consecutive Losses, Current Loss Streak, Max
  Consecutive Wins, Last Signal Time/Direction/Result/Candle Close, Result
  Candle Open/Close). Download button gives the full CSV.
- **Ranking Tables** (all require >= 30 signals unless noted, so a lucky
  2-signal 100% win rate can't top the list):
  1. **Best Overall** — highest win rate among sufficiently-sampled setups.
  2. **Best High-Frequency** — highest signal frequency % among setups that
     still clear a 50% win rate floor (a setup that fires constantly but
     loses isn't "best").
  3. **Best Low-Risk** — lowest max consecutive losses, tie-broken by win
     rate.
  4. **Best Per Strategy** — one row per base pattern.
  5. **Best Per ATR Multiplier** — one row per multiplier (see ATR caveat
     above).
  6. **Best Per Filter Combination** — one row per filter combo (32 rows).
- **Graphs**:
  1. Best strategy by win rate (bar).
  2. ATR multiplier vs win rate (bar, all strategies mixed).
  3. Filter combination vs win rate (horizontal bar, all 32).
  4. Total signals by strategy (bar).
  5. Max consecutive losses by setup (histogram — distribution across all
     768 setups).
  6. Win rate vs signal frequency (scatter, colored by strategy).
- **Progress log**: while a dataset is being fetched/scanned, a live status
  panel shows exactly what's happening (fetching, candles loaded, which
  strategy/ATR/filter combination is being tested, how many of 768
  combinations are done, saving, ready) — every row in the result table
  comes from real candle-by-candle backtesting over the exact data shown in
  the success banner above the table, nothing is fabricated or simulated.

## Project layout

```
pine_strategy_simulator/
  app.py            entry point — streamlit run app.py
  config.py          pairs/timeframes/strategies/ATR multipliers/32 filter combos/candle-count options
  data_fetcher.py     Binance pagination + incremental local CSV cache
  pine_logic.py       exact Pine indicator/pattern/filter port
  backtester.py       signal extraction (corrected scoring rule) + single-dataset sweep runner
  metrics.py          per-setup stats + ranking-table aggregation
  dashboard.py        Streamlit UI
  requirements.txt
  data/               cached candle CSVs (created on first fetch)
  results/            sweep output CSVs (created on first sweep run)
```
