---
name: journal
description: >-
  Week Trader journal: logs each trade the user actually takes (paper or real),
  checks open trades against live prices and says which to sell (stop hit, target
  hit, back above the 5-day average, or end of the week), records exits with profit
  in AUD and R, and reports the real win rate and profit by setup, market and paper
  vs real. Use when the user says "I bought", "log this trade", "I sold", "close
  trade", "what should I sell", "check my trades", "how am I doing", or "my stats".
argument-hint: "add|close|list|review|stats"
---

# Week Trader: journal

```bash
WT="${CLAUDE_SKILL_DIR}/../../engine/wt.py"
python3 "$WT" journal add BHP --market ASX --setup breakout --entry 45.20 --shares 50 --stop 44.10 --target 47.40
python3 "$WT" journal add NVDA --setup pullback --entry 180.5 --shares 3 --stop 172 --real   # --real for real money (paper is the default)
python3 "$WT" journal review          # live check: HOLD or SELL with the reason
python3 "$WT" journal close --id 3 --exit 47.40 --note "target"
python3 "$WT" journal list
python3 "$WT" journal stats
```

`add` also takes `--date YYYY-MM-DD` (default today) and `--sell-by YYYY-MM-DD`
(default: the 5th trading day). The journal lives in `weektrader/journal.json`.

## Steps

- **Logging a buy:** take the real fill price and share count from the user, not the
  plan. If they don't give a stop, use the plan's stop and say so. Paper unless the
  user says it was real money.
- **Review:** run `review` and lead with any SELL lines. A stop that was hit means
  the trade is over. Don't suggest moving the stop down or holding past the sell-by
  date to "give it room": that breaks the plan the numbers were tested on.
- **Closing:** record the actual exit price. Profit is after commission both ways,
  in AUD.
- **Stats:** report them straight. Fewer than 30 closed trades is too few to judge.
  Compare the live avg R with the backtest's: much worse usually means fills, timing
  or skipped rules.

Not financial advice.
