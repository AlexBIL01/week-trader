---
name: forward
description: >-
  Week Trader forward tests: rules locked in advance (US "quiet buying" volume
  signal, ASX short-term oversold), judged only on trading days after the date they
  were locked, against the average stock in the same market. Shows progress toward a
  verdict and the stocks signalling now (watch only). Use when the user asks "how's
  the forward test", "is quiet buying working", "check the locked rules", or "what's
  signalling now".
---

# Week Trader: forward tests

```bash
WT="${CLAUDE_SKILL_DIR}/../../engine/wt.py"
python3 "$WT" forward
python3 "$WT" forward --market US
```

Nothing needs logging: the signals come from prices, so every new week since the lock
date is checked automatically. The weekly report (`/week-trader:week`) includes this
section too.

## Rules (locked 2026-11-11)

| id | market | signal | why it was chosen |
|---|---|---|---|
| us_quiet_buying | US | top fifth for volume last week vs the last 2 months | t 3.1 on the climb predictor's unseen weeks |
| asx_oversold | ASX | bottom fifth for 2-day RSI | t 2.2 on the climb predictor's unseen weeks |

Each week: the signalling stocks' next-5-day return minus the average stock's.

## Verdicts

- **collecting: N of 26 weeks** means it's too early. Don't read anything into the
  average yet, even if it looks good or bad.
- **confirmed on new data** means 26+ weeks, t ≥ 2, positive. Only then treat the signal
  as real, and still paper trade it first.
- **failed on new data** means drop it.

Never edit a locked rule or its date. To test a new idea, add a new rule with a new id
and today's date in `FORWARD_RULES` (engine/wt.py), and tell the user it starts from
zero. Not financial advice.
