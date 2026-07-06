# Pine Strategy Simulator

A standalone Streamlit backtesting dashboard for `btc_polymarket_signal_tester.pine`.
Three tabs:

1. **All Results** — selecting a pair, timeframe, and candle count
   automatically runs **every** combination of base pattern x ATR multiplier
   x filter toggle (768 setups) — there's no "pick one strategy" mode.
2. **Filtered Results** — narrows that same 768-setup sweep down to the ones
   that clear your minimum win rate / signal frequency / trade count, without
   re-running anything.
3. **Money Management Simulator** — replays historical signals from a
   strategy combination *you* pick, candle by candle, oldest to newest,
   through a real account-balance + loss-basket recovery model.

This is a separate project from the main OptionQuant app — it doesn't
import or modify anything in the parent directory.

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
- **Filtered Results tab**: three controls (Min Win Rate %, Min Signal
  Frequency %, optional Min Total Signals) filter the *same* 768-row sweep
  already computed in "All Results" — moving these sliders never re-runs the
  backtest. Shows a narrower results table, 5 summary cards (setups found,
  best filtered setup, highest win rate, highest signal frequency, lowest
  max consecutive losses), and 4 charts scoped to just the filtered setups.

## Money Management Simulator

This tab is independent of the sidebar's pair/timeframe/candle count — it
has its own Pair/Timeframe/Candle Count pickers plus its own strategy,
filter, ATR, and money-management inputs, all gathered above a single
**Run Simulation** button. Nothing runs until you click it, and re-clicking
with the exact same settings reuses the cached result instantly.

**Strategy selection & priority**: pick any combination of the 4 base
patterns (multi-select), then a second "Priority Order" box where re-clicking
strategies sets the order. Each selected strategy gets its own independent
F1-F5 filter checkboxes. If more than one selected strategy fires a signal on
the same candle, the **first one in priority order wins** — only one trade
ever starts per candle, regardless of how many strategies technically agree.

**Scoring**: identical rule to the main sweep — a signal on candle N is
scored strictly against candle N+1's own open vs close (never candle N,
never an unfinished candle); `close[N+1] == open[N+1]` is NEUTRAL and never
touches the balance or loss basket.

### How the loss basket works

The loss basket is a **theoretical recovery tracker**, not real money by
itself — it exists only to size the *next* trade slightly larger after a
loss, so that a win has a chance to claw back part of what was lost. It is
never used to decide whether a signal fires; it only affects how much is
risked on the next trade that does fire.

- **On a loss**: the full trade amount is added to the loss basket, and the
  same amount is subtracted from the real account balance. Nothing is
  "recovered" yet — the loss basket just remembers how much is owed.
- **On a win**: the full trade amount is credited back to the real balance
  (a genuine win is a genuine win), but the win is then *split* on paper —
  your chosen "Win Split %" of it reduces the loss basket, and the rest
  becomes realized profit. The loss basket can never go below zero.
- **Sizing the next trade**: `trade_amount = min(base_trade_amount +
  loss_basket * recovery_percent, max_trade_amount)`. This is deliberately
  **not martingale** — a loss never doubles the next trade. The recovery
  add-on is only a small percentage of the *outstanding* loss basket, so it
  grows gently as losses accumulate and shrinks as wins pay it down, always
  clamped by the max trade amount cap.
- **Dynamic recovery mode** (optional): instead of one fixed recovery
  percentage, the percentage itself shrinks as the loss basket grows — 25%
  while the basket is small (<= 5x base trade), stepping down to 15%, 10%,
  and finally 5% once the basket exceeds 20x the base trade. This keeps
  position sizing from creeping up indefinitely during a long losing streak.
- **Reset modes**: "Never reset" and "reset when loss basket becomes 0" are
  mathematically identical here, since the basket is already floored at zero
  by the win formula above — neither forces anything extra. "Reset after X
  winning trades" is the one genuinely different mode: it forcibly zeroes the
  loss basket after N cumulative wins even if it hasn't organically paid
  itself down yet, as a deliberate "fresh start" mechanism.

### Account balance vs. the loss basket — these are not the same number

- **Account balance** is the real, true result of every trade — it only
  ever changes by the *full* trade amount won or lost. This is the number
  that answers "did this strategy actually make money."
- **Loss basket** is a side ledger used only for sizing decisions. It is not
  money that's missing from the balance, and it's not profit sitting
  somewhere — it's purely a bookkeeping signal that says "trades have been
  losing lately, size the next one up a little to help catch up."
- **Realized profit** and **recovered amount** are a further split of every
  *win's* gross amount on paper (not the balance) — realized profit is the
  portion that "counts" as pure profit; recovered amount is the portion
  credited toward paying down the loss basket. Neither of these two numbers
  alone equals the account balance change; only summing all trades' actual
  win/loss amounts (i.e. the balance curve) gives the true account result.

### Why the max trade amount cap matters

Without a hard cap, a sufficiently long losing streak would keep growing the
loss basket, which would keep growing the recovery add-on, which would keep
growing the next trade size — exactly the runaway sizing spiral that makes
martingale-style systems blow up an account on a bad streak. The max trade
amount cap puts a hard ceiling on this: no matter how large the loss basket
gets, the size of any single trade can never exceed the cap. The dashboard
warns you if the cap is set to more than 10x the base trade amount (before
running), and again after running if the actual max trade amount *used*
grew to 5x+ the base trade amount, if max consecutive losses hit 5+, or if
the loss basket is still large relative to the base trade at the end of the
run.

### Outputs

- `results/money_management_summary.csv` — the one-row run summary (Pair,
  Timeframe, Candle Count, Selected Strategies, Selected Filters, ATR
  Multiplier, Starting/Ending Balance, Net PnL, ROI %, Total Trades, Wins,
  Losses, Neutrals, Win Rate, Max Consecutive Losses, Biggest/Final Loss
  Basket, Max Trade Amount Used, Average Trade Amount).
- `results/money_management_trade_log.csv` — every individual trade (Trade #,
  Signal Time, Result Candle Time, Strategy Triggered, Signal
  Direction/Close, Result Candle Open/Close, Result, Base Trade Amount,
  Recovery Addon, Final Trade Amount, Balance Before/After, PnL, Loss Basket
  Before/After, Recovery Percent Used, Recovered Amount, Realized Profit
  Added).
- A Strategy Breakdown table (trades/wins/losses/neutrals/win rate/net
  PnL/average trade amount/max consecutive losses per strategy, when more
  than one is selected), plus 6 charts: balance curve, loss basket curve,
  trade amount curve, drawdown curve, win/loss distribution, and strategy
  PnL comparison.

## Project layout

```
pine_strategy_simulator/
  app.py            entry point — streamlit run app.py
  config.py          pairs/timeframes/strategies/ATR multipliers/32 filter combos/candle-count options
  data_fetcher.py     Binance pagination + incremental local CSV cache
  pine_logic.py       exact Pine indicator/pattern/filter port
  backtester.py       signal extraction (corrected scoring rule) + single-dataset sweep runner
  metrics.py          per-setup stats + ranking-table aggregation
  money_management.py priority-based multi-strategy combination + sequential balance/loss-basket simulation
  dashboard.py        Streamlit UI (All Results / Filtered Results / Money Management Simulator tabs)
  requirements.txt
  data/               cached candle CSVs (created on first fetch)
  results/            sweep output CSVs (created on first sweep run)
```
