---
name: insider
description: >-
  Week Trader insider-buying test (US): downloads the SEC's official quarterly data on
  every insider trade, finds the times company officers and directors bought their own
  shares on the open market (small companies included), and measures whether those
  stocks beat the S&P 500 (SPY) over the next week and month, graded separately on
  older and recent data. Use when the user asks about "insider buying", "directors
  buying", "Form 4", "does insider buying work", or "what are insiders buying".
---

# Week Trader: insider buying (US)

```bash
WT="${CLAUDE_SKILL_DIR}/../../engine/wt.py"
python3 "$WT" insider                 # last 12 quarters
python3 "$WT" insider --quarters 20   # longer history
python3 "$WT" insider --refresh       # re-read the SEC files
```

## First run

1. The SEC requires a name and email with every download. If
   `weektrader/config.json` has no `sec_user_agent`, ask the user for their name and
   the email they want to use, then add `"sec_user_agent": "Name email@example.com"`
   to the config. Never invent one.
2. The first run downloads about 10 MB per quarter from the SEC, then daily prices
   for up to 800 companies from Yahoo. Expect 10–20 minutes. Later runs use the cache.

## The test

- **Event:** one or more open-market purchases (Form 4, code P) by the company's
  officers or directors on the same filing date, totalling US$25,000+, at US$3+ a share.
  10%-owner funds and option exercises don't count.
- **Trade:** buy at the open after the filing date (when the public could first act),
  sell at the close 5 and 21 trading days later, minus what SPY did over the same days.
- **Grading:** events are averaged per week and the t-stat is across weeks. The older
  2/3 and newest 1/3 of weeks are shown separately. "Works" needs t ≥ 2 overall, with
  both parts positive.
- **Subgroups** (2+ insiders together, smaller companies, big purchases) were chosen in
  advance from published research. They're for interest only: with 10 of them, one
  can pass t 2 by chance.

## Presenting results

Lead with the verdict for each horizon, then the older vs recent split, then the
subgroups. The "most recent buys" list comes from quarterly data, so it is weeks to
months old: never present it as a live buy signal. If the test works, say that the
next step is a live feed of new filings, which isn't built yet.

Not financial advice.
