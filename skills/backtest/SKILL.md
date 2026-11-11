---
name: backtest
description: >-
  Week Trader backtester: tests the week-long setups on five years of daily US and
  ASX prices with commission and slippage, compares them with simply buying any
  day and selling five days later, and checks whether the edge held up in the most
  recent third of the data. Can test one setup, one stock, or different rules (hold
  days, stop and target sizes, market filter on/off). Use when the user asks "does
  this work", "how has X done", "backtest", "what if I held longer", "is this
  setup profitable", or wants to compare rules.
argument-hint: "[setup] [symbol]"
---

# Week Trader: backtester

```bash
WT="${CLAUDE_SKILL_DIR}/../../engine/wt.py"
python3 "$WT" backtest                                   # all setups, both markets (saves the stats the scanner uses)
python3 "$WT" backtest --market ASX --setup pullback
python3 "$WT" backtest --symbol BHP --market ASX
python3 "$WT" backtest --hold-days 3 --stop-atr 1 --target-atr 2   # what-if (not saved)
python3 "$WT" backtest --no-regime                       # without the market-uptrend filter
python3 "$WT" backtest --json
```

## How a test trade works

Signal on the close, buy at the next open plus slippage. Each day after that: a gap
below the stop exits at the open; a touch of the stop exits at the stop; for breakout
and gap-and-go, a touch of the target exits at the target; for pullback, a close above
the 5-day average exits at the close; momentum has no target and a wider 3 ATR stop; momentum_month holds 21 trading days with a 5 ATR stop. Otherwise it exits at the close on day 5. If a
day touches both the stop and the target, the stop counts (daily bars can't show
which came first). Commission and slippage come from `weektrader/config.json`.
Prices are adjusted for splits and dividends.

## Reading the results

- **avg R**: average profit per trade in units of the planned risk. +0.2 R at A$100
  risk is about A$20 per trade on average.
- **profit factor**: total won divided by total lost. Below 1 loses money.
- **recent third avg R**: the same rule on the most recent third of the history only.
  A setup is called an "edge" only if it is profitable overall, in that third, and
  reliably beats the baseline.
- **Baseline**: buying any day and selling five days later (21 days for
  momentum_month). **vs baseline** is how much more each trade earned than that,
  with a t-stat: a setup only counts as an edge if t is at least 2, meaning the
  extra is unlikely to be luck. Below 2 it is "not reliably better".

## Honesty rules

- Report the numbers as they are, including "no edge". Never tune the rules over and
  over until something looks good and then present it as an edge: that is
  curve-fitting, and it fails on new data. If the user tries several what-ifs, say
  that the best of many tries will look better than it really is.
- Under 30 trades is too few to judge.
- With 5 setups on 2 markets, roughly one in twenty no-edge setups will still clear
  t = 2 by luck. Treat a single pass as a reason to paper trade, not proof.
- The stock list is today's stocks, so the test misses companies that collapsed and
  dropped out (survivorship bias): real results are usually somewhat worse.
- Past results do not predict future ones. Not financial advice.
