---
name: predict
description: >-
  Week Trader climb predictor: rates every US and ASX stock on how likely it is to
  rise more than the average stock over the next 5 trading days, using 13 warning
  signs (volume building, quiet before a move, strength vs the market, near the
  52-week high, momentum, oversold, gaps, close in the day's range). The model is
  trained on older data and graded only on recent weeks it never saw, and it shows
  which warning signs actually came before climbs. Use when the user asks "what's
  about to climb", "predict", "which stocks will go up", "what comes before a
  run", "find stocks before they move", or "is the predictor working".
argument-hint: "[US|ASX|both]"
---

# Week Trader: climb predictor

```bash
WT="${CLAUDE_SKILL_DIR}/../../engine/wt.py"
python3 "$WT" predict                    # both markets, top 10 each
python3 "$WT" predict --market ASX --top 5
python3 "$WT" predict --retrain          # retrain now (otherwise weekly)
python3 "$WT" predict --json
```

## What "about to climb" means here

Buy at the next open, sell at the close 5 trading days later, and compare with the
**other stocks in the same market over the same days**. A stock "climbed" if it beat
the median stock. Comparing with the same days strips out whatever the whole market
did, so the model must find something specific to the stock.

(An earlier version measured "+2 ATR before −1 ATR". Noise in ATR made random data
look predictable, so it was replaced. Don't reintroduce a fixed-ATR target as the
thing being predicted.)

## How it is graded

1. Features are measured at every close for every stock (5 years).
2. A logistic model is trained on the older two-thirds of dates.
3. On the newest third, one date per week (so weeks don't overlap), the model ranks
   the stocks. Its top 10% that week is compared with that week's average stock.
4. **Works** = top picks beat the average stock with t ≥ 2 across those weeks, by more
   than 0.2% per week, and AUC ≥ 0.52.

Checked on simulated data: on pure random prices it falsely passes about 1 time in
40; when a real warning sign is planted, it finds it 10 times out of 10 and names the
right sign. So a pass on one market is good evidence but not proof.

## Presenting results

Lead with the verdict in plain words, then:
- **The decile line**: best-rated tenth first. A working model shows a clear slope
  from positive to negative. A flat or jumbled line means no real ranking ability.
- **The warning-sign table**: which signs came before climbs on unseen data, and
  whether high or low values helped. t ≥ 2 is meaningful. Under 2 is a hint at most.
- **Picks**: listed as predicted climbers only if the model works. Otherwise they are
  "watch only". Each pick comes with a plan: stop 1 ATR below, target 2 ATR above,
  sell by day 5, sized to the user's max loss.

## Rules

- Never call a stock "about to climb" if the verdict isn't "works".
- Don't keep adding or tweaking features and retraining until it passes: with enough
  tries, one will pass by luck. One change per idea, decided before seeing results.
- Picks go into the journal as paper trades (`journal add SYMBOL --setup climb ...`)
  until there are 30+ closed trades showing it works live.
- Not financial advice.
