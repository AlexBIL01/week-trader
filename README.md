# Week Trader

Find the best US and ASX stocks to buy and sell within one trading week (or a
month, for monthly momentum), test
every rule on five years of history before trusting it, size each trade in
Australian dollars, and keep an honest journal.

Eight Claude Code skills, one standard-library Python engine (`engine/wt.py`).
Nothing to pip install, no API keys, and no orders are ever placed.

| command | what it does |
|---|---|
| `/week-trader:week` | This week's best trades to buy, plus what to sell from open trades |
| `/week-trader:scan` | Today's signals on US and ASX stocks, ranked by backtested edge |
| `/week-trader:backtest` | Tests the setups on 5 years of data with costs, vs a buy-any-day baseline |
| `/week-trader:predict` | Climb predictor: rates every stock on how likely it is to beat the average stock over the next 5 days, graded on weeks it never saw |
| `/week-trader:forward` | Forward tests: rules locked on 2026-11-11 (US quiet buying, ASX oversold), judged only on new weeks |
| `/week-trader:insider` | US insider buying: SEC data on every officer/director purchase, tested against SPY over 5 and 21 days |
| `/week-trader:plan` | Entry, stop, target, sell-by date and share count in AUD for one stock |
| `/week-trader:journal` | Logs trades, says when to sell, tracks your real win rate and profit |

## Install (Mac)

Inside Claude Code (start it from your trading folder, e.g. `cd ~/day-trading && claude`):

```
/plugin marketplace add AlexBIL01/week-trader
/plugin install week-trader@week-trader
/reload-plugins
```

Then run `/week-trader:week`. The first run asks for your account size and risk
per trade, checks the data, and downloads 5 years of prices (a few minutes).

To update later: `/plugin marketplace update week-trader`.

## Settings

`weektrader/config.json` in the folder you run Claude from:

| setting | default | meaning |
|---|---|---|
| `account_aud` | 10000 | trading account size in AUD |
| `risk_per_trade_pct` | 1.0 | max loss at the stop, % of the account |
| `max_position_pct` | 25 | largest single position, % of the account |
| `max_open_trades` | 4 | the weekly report won't suggest more than this |
| `hold_days` | 5 | sell by the close of this trading day |
| `stop_atr` / `target_atr` | 1.5 / 3.0 | stop and target distance in ATR(14) |
| `momentum_top` / `momentum_lookback` / `momentum_stop_atr` | 10 / 126 / 3.0 | momentum leaders: how many, ranked on how many days' return, stop distance |
| `month_hold_days` / `momentum_month_stop_atr` | 21 / 5.0 | monthly momentum: holding period and stop distance |
| `costs` | US $0 + 5 bps, ASX $5 + 10 bps | commission per side and slippage |
| `aud_per_usd` | live | fixed AUD/USD rate if set |
| `sec_user_agent` | empty | your name and email, required by the SEC for insider data |
| `insider_quarters` / `insider_min_value` | 12 / 25000 | insider test: quarters of history, minimum purchase in US$ |
| `watchlist` | empty | extra symbols: `{"US": ["HIMS"], "ASX": ["CXO"]}` |

## Tests

```
python3 -m unittest discover -s tests -v
```

Offline, on simulated prices: indicators, stops, gaps, time exits, costs,
sizing, the weekly report and the journal.

## Fine print

Not financial advice. Backtests use daily bars, assume the stop is hit first on
a day that touches both levels, and use today's stock list (survivorship bias),
so real results are usually worse. Yahoo Finance's free data is unofficial and
can lag or rate-limit. In Australia, shares held under 12 months get no capital gains
discount; keep records. Paper trade first.
