---
name: plan
description: >-
  Week Trader trade planner: for one US or ASX stock, works out entry, stop, target
  or exit rule, sell-by date within the week, and how many shares to buy so the loss
  at the stop stays within the user's risk limit in Australian dollars (converting
  US prices at the live AUD/USD rate), capped at a share of the account. Flags the
  ASX $500 minimum. Use when the user says "plan a trade", "how many shares",
  "where's my stop", "size this", or names a stock to buy.
argument-hint: "SYMBOL [--setup breakout|pullback|gapgo] [--entry PRICE]"
---

# Week Trader: trade planner

```bash
WT="${CLAUDE_SKILL_DIR}/../../engine/wt.py"
python3 "$WT" plan NVDA                         # US; uses whichever setup fired on the last close
python3 "$WT" plan BHP --market ASX
python3 "$WT" plan CBA --setup pullback
python3 "$WT" plan TSLA --entry 412.50          # recompute from the actual fill or the open price
python3 "$WT" plan AAPL --json
```

ASX codes can be given as `BHP` or `BHP.AX`. A code in the built-in ASX list is
treated as ASX automatically; anything else defaults to US unless `--market ASX`.

## The numbers

- Stop = entry − 1.5 × ATR(14). Target = entry + 3 × ATR (breakout, gap-and-go).
  Pullbacks have no fixed target: they sell on the first close above the 5-day
  average.
- Shares = (account × risk %) ÷ (entry − stop in AUD), rounded down, then capped
  so the position is at most `max_position_pct` of the account (default 25%).
- Sell-by = the 5th trading day counting the entry day. Public holidays are not
  skipped: move it if one falls in the week.

## Steps

1. Run `plan`. If no setup fired, the engine says so and plans the stock as a
   breakout for the numbers only: tell the user this is not a signal.
2. Show the plan in plain words, including the max loss in AUD and the setup's
   backtest line.
3. If the stock opens far from the planned entry, rerun with `--entry` set to the
   real price: the stop and share count change.
4. If the user takes it, log it with `/week-trader:journal add`.

Nothing here places orders. Not financial advice.
