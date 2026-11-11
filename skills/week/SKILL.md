---
name: week
description: >-
  Week Trader's main command: finds the best US and ASX stocks to buy and sell
  within the same trading week, and says what to sell from open trades. Runs
  the scanner, checks every signal against years of backtested history, plans
  entry, stop, target, sell-by date and share count in Australian dollars, and
  saves a report. Use when the user says "best trades this week", "what should
  I buy", "what should I sell", "weekly picks", "run my week", or starts the
  day/week of trading. Also handles first-time setup (init, doctor).
argument-hint: "[US|ASX|both]"
---

# Week Trader: this week's trades

The engine is `${CLAUDE_SKILL_DIR}/../../engine/wt.py` (standard-library Python 3.9+,
nothing to install). Its data lives in `./weektrader/` in the folder Claude Code was
started from. Run every command from that folder.

```bash
WT="${CLAUDE_SKILL_DIR}/../../engine/wt.py"
python3 "$WT" init --account 10000 --risk 1     # first time only
python3 "$WT" doctor                            # checks US + ASX data and the AUD/USD rate
python3 "$WT" week                              # both markets
python3 "$WT" week --market ASX                 # one market
python3 "$WT" week --refresh                    # ignore cached prices
```

## First run

1. If `weektrader/config.json` does not exist, ask the user for their account size in
   AUD and how much of it they will risk per trade (suggest 1%, never above 2%). Run
   `init --account N --risk R`.
2. Run `doctor`. If a fetch fails, it is usually Yahoo rate-limiting; wait a minute and
   retry once. Do not try other data sources.
3. The first `week` downloads five years of daily prices for about 190 stocks and
   backtests every setup (a few minutes). Later runs use the cache.

## Steps

1. Run `week` (add `--market` if the user named one).
2. Present the report in this order, briefly:
   - **Sell first:** every open trade marked SELL, with the reason. These come before
     anything new.
   - **Buy:** each pick with shares, entry, stop, target or exit rule, sell-by date,
     max loss in AUD, and the one-line history ("this setup: N trades, X% winners,
     avg R"). Say plainly when the verdict is "weak".
   - Momentum leaders only appear when the data ends on a Friday, so the weekly run
     is best done after Friday's close.
   - If there are no picks, say so as a good result: no setup with a proven edge
     fired. Never invent a pick or loosen the rules to produce one.
3. Remind the user once: paper trade first, prices are from the last close, and the
   real fill will differ.
4. When the user takes a trade, log it with `/week-trader:journal`.

## When things happen (Adelaide time)

- **ASX** trades 10:00 am to 4:00 pm Sydney time (9:30 am to 3:30 pm in Adelaide).
  Run `week --market ASX` after the close, then place orders for the next open.
- **US** trades 9:30 am to 4:00 pm New York time, which is the middle of the night in
  Adelaide (1:00 am to 7:30 am in the Australian summer). Run `week --market US` in
  the morning after the US close, then place orders for that night's open.
- If run while a market is open, today's unfinished bar is dropped automatically.

## Rules

- Nothing in Week Trader places orders. The user places them at their broker.
- Not financial advice. A pick means "matches a rule that made money in the past,
  after costs"; it is not a prediction.
- Never change `account_aud` or `risk_per_trade_pct` without the user asking.
- Never present a setup whose verdict is "no edge" as a trade.
