---
name: scan
description: >-
  Week Trader scanner: checks about 110 liquid US stocks and 85 ASX stocks (plus the
  user's watchlist) for four week-long setups (breakout, pullback, gap-and-go, momentum leaders) and one month-long one (monthly momentum) on
  the last daily close, and ranks each signal by how that setup has performed in a
  five-year backtest after costs. Shows whether each market is in an uptrend. Use
  when the user says "scan", "any setups", "what's breaking out", "what's pulling
  back", or wants signals without a full weekly plan.
argument-hint: "[US|ASX|both]"
---

# Week Trader: scanner

```bash
WT="${CLAUDE_SKILL_DIR}/../../engine/wt.py"
python3 "$WT" scan                  # both markets
python3 "$WT" scan --market US
python3 "$WT" scan --json           # machine-readable
```

## The setups (signal on the close, buy at the next open, out within 5 trading days)

| setup | fires when | exits |
|---|---|---|
| breakout | close above the prior 20-day high, above the 50-day average, volume 1.5x+ normal, close in the top 40% of the day's range, market index above its 50-day average | stop 1.5 ATR below entry, target 3 ATR above, or close on day 5 |
| pullback | above the 200-day average, 2-day RSI under 10 | first close above the 5-day average, stop 1.5 ATR, or close on day 5 |
| gapgo | opens 3%+ above yesterday's close and closes above the open, volume 2x+ normal, above the 200-day average, market index up | same as breakout |
| momentum | on the week's last trading day (Friday): one of the top 10 stocks in its market by 6-month return, above its 50- and 200-day averages, market index up | close on day 5 (Friday), or a wide stop 3 ATR below entry; no target |
| momentum_month | the same ranking, on the month's last trading day | close after 21 trading days (about a month), or a stop 5 ATR below entry; no target |

Liquidity filters: price at least US$5 / A$0.50 and average daily turnover at least
US$20M / A$2M.

Momentum only fires when the last bar is a Friday, and monthly momentum only on the month's last trading day: run it after Friday's close
(Friday afternoon for the ASX, Saturday morning Adelaide time for the US).

## Reading the output

- `score` is the setup's average R in the backtest (cut in half if it only worked in
  older data), nudged by that stock's own history when it has 5+ past trades.
- "not ranked" means the setup has no edge, or too little history, on that market.
  Show these as watch-only, never as buys.
- Market DOWN switches off breakout and gap-and-go (they do worse when the index is
  falling); pullbacks still run.

## Watchlist

Extra symbols go in `weektrader/config.json` under `"watchlist": {"US": [...], "ASX": [...]}`
(ASX codes without `.AX`). Edit the file with the user's say-so, then rerun.

Not financial advice. To turn a signal into a trade, use `/week-trader:plan`.
