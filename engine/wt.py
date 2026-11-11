#!/usr/bin/env python3
"""Week Trader engine. Standard library only (Python 3.9+).

Finds short swing trades on US and ASX stocks that are bought and sold
within one trading week, tests every rule on years of history before it is
trusted, plans the size of each trade in Australian dollars, and keeps a
journal of what actually happened.

    python3 wt.py init                         # create weektrader/config.json
    python3 wt.py doctor                       # check data access (one US, one ASX fetch)
    python3 wt.py week [--market US|ASX|both]  # best trades this week + what to sell
    python3 wt.py scan [--market ...]          # today's signals, ranked
    python3 wt.py backtest [--market ...] [--setup NAME] [--symbol SYM]
    python3 wt.py plan SYMBOL [--market ...] [--setup NAME] [--entry PRICE]
    python3 wt.py journal add|close|list|review|stats ...

Everything is written to ./weektrader/ (override with WT_HOME). Nothing here
places orders. Not financial advice.
"""

import argparse
import datetime as dt
import json
import math
import os
import statistics
import sys
import time
import urllib.error
import urllib.request

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None

VERSION = "1.0.0"
HOME = os.environ.get("WT_HOME") or os.path.join(os.getcwd(), "weektrader")
OFFLINE_DIR = os.environ.get("WT_OFFLINE_DIR")  # tests and demos: read <dir>/<SYMBOL>.json
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) week-trader/" + VERSION

MARKETS = {
    "US": {"index": "SPY", "suffix": "", "currency": "USD", "tz": "America/New_York",
           "close_hour": 16, "label": "US (NYSE/Nasdaq)"},
    "ASX": {"index": "^AXJO", "suffix": ".AX", "currency": "AUD", "tz": "Australia/Sydney",
            "close_hour": 16, "label": "ASX"},
}

SETUPS = {
    "breakout": "Closes at a new 20-day high on at least 1.5x normal volume, in an uptrend (above its 50-day average), near the top of the day's range.",
    "pullback": "In a long uptrend (above its 200-day average) but sold off hard for a day or two (2-day RSI under 10). Sells when it closes back above its 5-day average.",
    "gapgo": "Opens at least 3% above yesterday's close and holds it (closes above the open) on at least 2x normal volume.",
}

UNIVERSE = {
    "US": """AAPL MSFT NVDA AMZN GOOGL META TSLA AVGO AMD NFLX ADBE CRM ORCL INTC QCOM MU AMAT LRCX
KLAC TXN CSCO IBM NOW SHOP UBER ABNB PYPL PLTR SNOW CRWD PANW ZS DDOG NET MDB COIN HOOD SOFI
MARA RIVN JPM BAC WFC GS MS C SCHW V MA AXP BRK-B UNH LLY JNJ PFE MRK ABBV AMGN GILD MRNA
ISRG TMO DHR XOM CVX COP OXY SLB HAL WMT COST TGT HD LOW NKE SBUX MCD DIS CMCSA T VZ BA CAT
DE GE HON LMT RTX UPS FDX F GM KO PEP PG SMCI ARM DELL ANET MRVL ON ENPH FSLR CVNA DKNG RBLX ROKU""".split(),
    "ASX": """BHP CBA CSL NAB WBC ANZ WES MQG WDS RIO FMG TLS WOW GMG TCL ALL STO COL REA XRO QBE SUN
IAG ORG S32 MIN PLS LYC NST EVN NEM RMD CPU ASX SHL COH SCG APA BXB TWE QAN SEK CAR WTC PME NXT
TNE MPL AZJ ORI JBH HVN SGP MGR VCX GPT DXS CHC LLC IGO LTR SFR PDN BOQ BEN NHF HUB NWL CWY WOR
DRO ZIP BRG BPT VEA ALD CTD FLT WEB IEL EDV RHC SOL A2M""".split(),
}

DEFAULT_CONFIG = {
    "account_aud": 10000,
    "risk_per_trade_pct": 1.0,
    "max_position_pct": 25,
    "max_open_trades": 4,
    "aud_per_usd": None,
    "hold_days": 5,
    "stop_atr": 1.5,
    "target_atr": 3.0,
    "history": "5y",
    "markets": ["US", "ASX"],
    "costs": {
        "US": {"commission": 0.0, "slippage_bps": 5},
        "ASX": {"commission": 5.0, "slippage_bps": 10},
    },
    "backtest_position_aud": 2500,
    "min_price": {"US": 5.0, "ASX": 0.5},
    "min_dollar_volume": {"US": 20000000, "ASX": 2000000},
    "regime_filter": True,
    "watchlist": {"US": [], "ASX": []},
    "top_n": 5,
}


class WTError(Exception):
    pass


# ---------------------------------------------------------------- files

def path(*parts):
    return os.path.join(HOME, *parts)


def load_json(p, default=None):
    try:
        with open(p) as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def save_json(p, obj):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    tmp = p + ".tmp"
    with open(tmp, "w") as f:
        json.dump(obj, f, indent=2, default=str)
    os.replace(tmp, p)


def load_config():
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    user = load_json(path("config.json"), {}) or {}
    for k, v in user.items():
        if isinstance(v, dict) and isinstance(cfg.get(k), dict):
            for k2, v2 in v.items():
                if isinstance(v2, dict) and isinstance(cfg[k].get(k2), dict):
                    cfg[k][k2].update(v2)
                else:
                    cfg[k][k2] = v2
        else:
            cfg[k] = v
    return cfg


# ---------------------------------------------------------------- data

def yahoo_symbol(sym, market):
    sym = sym.upper().strip()
    if sym.startswith("^") or "=" in sym:
        return sym
    suffix = MARKETS[market]["suffix"]
    if suffix and not sym.endswith(suffix):
        sym += suffix
    return sym.replace(".", "-") if market == "US" else sym


def _now_in(tz):
    if ZoneInfo:
        try:
            return dt.datetime.now(ZoneInfo(tz))
        except Exception:
            pass
    return dt.datetime.now(dt.timezone.utc)


def parse_chart(payload, drop_partial=True, market=None):
    try:
        res = payload["chart"]["result"][0]
    except (KeyError, IndexError, TypeError):
        err = (payload.get("chart") or {}).get("error") if isinstance(payload, dict) else None
        raise WTError("no data (%s)" % ((err or {}).get("description") or "empty result"))
    ts = res.get("timestamp") or []
    q = (res.get("indicators") or {}).get("quote", [{}])[0]
    adj = ((res.get("indicators") or {}).get("adjclose") or [{}])[0].get("adjclose")
    meta = res.get("meta") or {}
    off = meta.get("gmtoffset") or 0
    bars = {"d": [], "o": [], "h": [], "l": [], "c": [], "v": []}
    for i, t in enumerate(ts):
        o, h, l, c, v = (q.get(k, [None] * len(ts))[i] for k in ("open", "high", "low", "close", "volume"))
        if None in (o, h, l, c) or c <= 0:
            continue
        f = 1.0
        if adj and i < len(adj) and adj[i]:
            f = adj[i] / c
        bars["d"].append(dt.datetime.fromtimestamp(t + off, dt.timezone.utc).date().isoformat())
        bars["o"].append(o * f)
        bars["h"].append(max(h, o, c) * f)
        bars["l"].append(min(l, o, c) * f)
        bars["c"].append(c * f)
        bars["v"].append(v or 0)
    if drop_partial and bars["d"] and market:
        now = _now_in(meta.get("exchangeTimezoneName") or MARKETS[market]["tz"])
        if bars["d"][-1] == now.date().isoformat() and now.hour < MARKETS[market]["close_hour"] + 1:
            for k in bars:
                bars[k].pop()
            bars["partial_dropped"] = True
    bars["currency"] = meta.get("currency")
    return bars


def fetch(sym, market, rng="5y", refresh=False, max_age_h=6):
    ysym = yahoo_symbol(sym, market)
    if OFFLINE_DIR:
        p = os.path.join(OFFLINE_DIR, ysym + ".json")
        data = load_json(p)
        if data is None:
            raise WTError("no offline data for %s" % ysym)
        return data
    cp = path("cache", "%s_%s.json" % (ysym.replace("^", "_"), rng))
    if not refresh and os.path.exists(cp) and time.time() - os.path.getmtime(cp) < max_age_h * 3600:
        data = load_json(cp)
        if data and data.get("c"):
            return data
    last = None
    for host in ("query1", "query2"):
        url = "https://%s.finance.yahoo.com/v8/finance/chart/%s?range=%s&interval=1d&includeAdjustedClose=true&events=div%%2Csplit" % (
            host, urllib.request.quote(ysym), rng)
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=30) as r:
                payload = json.loads(r.read().decode("utf-8"))
            data = parse_chart(payload, market=market)
            if not data["c"]:
                raise WTError("no bars")
            save_json(cp, data)
            time.sleep(0.25)
            return data
        except urllib.error.HTTPError as e:
            last = "HTTP %s" % e.code
            if e.code == 404:
                break
            time.sleep(1.0)
        except (urllib.error.URLError, OSError, ValueError, WTError) as e:
            last = str(e)
            time.sleep(0.5)
    if os.path.exists(cp):  # stale cache beats nothing
        data = load_json(cp)
        if data and data.get("c"):
            data["stale"] = True
            return data
    raise WTError("%s: %s" % (ysym, last))


def aud_per_usd(cfg):
    if cfg.get("aud_per_usd"):
        return float(cfg["aud_per_usd"]), "config"
    try:
        b = fetch("AUDUSD=X", "US", rng="1mo", max_age_h=12)
        return 1.0 / b["c"][-1], "Yahoo AUDUSD=X %s" % b["d"][-1]
    except WTError:
        return 1.55, "fallback 1.55 (set aud_per_usd in config)"


def fx_to_aud(market, cfg, cache={}):
    if market == "ASX":
        return 1.0
    if "fx" not in cache:
        cache["fx"] = aud_per_usd(cfg)[0]
    return cache["fx"]


# ---------------------------------------------------------------- indicators

def sma(xs, n):
    out, s = [None] * len(xs), 0.0
    for i, x in enumerate(xs):
        s += x
        if i >= n:
            s -= xs[i - n]
        if i >= n - 1:
            out[i] = s / n
    return out


def atr(h, l, c, n=14):
    out = [None] * len(c)
    trs = []
    for i in range(len(c)):
        tr = h[i] - l[i] if i == 0 else max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1]))
        trs.append(tr)
        if i == n - 1:
            out[i] = sum(trs) / n
        elif i >= n:
            out[i] = (out[i - 1] * (n - 1) + tr) / n
    return out


def rsi(c, n=14):
    out = [None] * len(c)
    if len(c) <= n:
        return out
    gains = losses = 0.0
    for i in range(1, n + 1):
        d = c[i] - c[i - 1]
        gains += max(d, 0)
        losses += max(-d, 0)
    ag, al = gains / n, losses / n
    out[n] = 100.0 if al == 0 else 100 - 100 / (1 + ag / al)
    for i in range(n + 1, len(c)):
        d = c[i] - c[i - 1]
        ag = (ag * (n - 1) + max(d, 0)) / n
        al = (al * (n - 1) + max(-d, 0)) / n
        out[i] = 100.0 if al == 0 else 100 - 100 / (1 + ag / al)
    return out


def indicators(b):
    c, h, l, v = b["c"], b["h"], b["l"], b["v"]
    return {
        "sma5": sma(c, 5), "sma50": sma(c, 50), "sma200": sma(c, 200),
        "atr": atr(h, l, c, 14), "rsi2": rsi(c, 2),
        "vol20": sma([float(x) for x in v], 20),
        "dv20": sma([c[i] * v[i] for i in range(len(c))], 20),
    }


def regime_map(index_bars):
    """date -> True when the index closed above its 50-day average."""
    if not index_bars:
        return {}
    s50 = sma(index_bars["c"], 50)
    return {d: (s50[i] is not None and index_bars["c"][i] > s50[i]) for i, d in enumerate(index_bars["d"])}


# ---------------------------------------------------------------- setups

def signals_at(b, ind, i, cfg, market, regime=None):
    """Setups that fired at the close of bar i (enter at the next open)."""
    if i < 200 or ind["atr"][i] is None or ind["sma200"][i] is None:
        return []
    c, o, h, l, v = b["c"], b["o"], b["h"], b["l"], b["v"]
    if c[i] < cfg["min_price"][market] or (ind["dv20"][i] or 0) < cfg["min_dollar_volume"][market]:
        return []
    rv = v[i] / ind["vol20"][i - 1] if ind["vol20"][i - 1] else 0
    rng = h[i] - l[i]
    pos = (c[i] - l[i]) / rng if rng > 0 else 0.5
    reg = True if (regime is None or not cfg.get("regime_filter", True)) else regime.get(b["d"][i], False)
    out = []
    if reg and c[i] > max(h[i - 20:i]) and c[i] > ind["sma50"][i] and rv >= 1.5 and pos >= 0.6:
        out.append("breakout")
    if c[i] > ind["sma200"][i] and ind["rsi2"][i] is not None and ind["rsi2"][i] < 10:
        out.append("pullback")
    if reg and o[i] >= c[i - 1] * 1.03 and c[i] > o[i] and rv >= 2.0 and c[i] > ind["sma200"][i]:
        out.append("gapgo")
    return out


def levels(setup, entry, atr_val, cfg):
    stop = entry - cfg["stop_atr"] * atr_val
    target = entry + cfg["target_atr"] * atr_val
    return stop, target


def cost_pct(market, cfg):
    """Round-trip cost as a fraction of the position: slippage both sides + commission both sides."""
    cc = cfg["costs"][market]
    pos_local = cfg["backtest_position_aud"] / fx_to_aud(market, cfg)
    return 2 * cc.get("commission", 0) / pos_local if pos_local else 0.0, cc.get("slippage_bps", 0) / 10000.0


def simulate(b, ind, i, setup, cfg, market):
    """Trade from signal bar i. Returns dict, or None if there is no next bar."""
    if i + 1 >= len(b["c"]):
        return None
    comm, slip = cost_pct(market, cfg)
    raw_entry = b["o"][i + 1]
    entry = raw_entry * (1 + slip)
    stop, target = levels(setup, raw_entry, ind["atr"][i], cfg)
    if stop <= 0 or entry <= stop:
        return None
    hold = cfg["hold_days"]
    exit_px = reason = None
    j = i + 1
    last = min(i + hold, len(b["c"]) - 1)
    for j in range(i + 1, last + 1):
        o, h, l, c = b["o"][j], b["h"][j], b["l"][j], b["c"][j]
        if j > i + 1 and o <= stop:
            exit_px, reason = o, "stop (gap)"
        elif l <= stop:
            exit_px, reason = stop, "stop"
        elif setup != "pullback" and j > i + 1 and o >= target:
            exit_px, reason = o, "target (gap)"
        elif setup != "pullback" and h >= target:
            exit_px, reason = target, "target"
        elif setup == "pullback" and ind["sma5"][j] is not None and c > ind["sma5"][j]:
            exit_px, reason = c, "back above 5-day avg"
        elif j == i + hold:
            exit_px, reason = c, "time (end of week)"
        if exit_px is not None:
            break
    if exit_px is None:
        return {"open": True, "symbol": None, "setup": setup, "signal_date": b["d"][i],
                "entry": entry, "stop": stop, "target": target}
    exit_net = exit_px * (1 - slip)
    ret = exit_net / entry - 1 - comm
    risk = (entry - stop) / entry
    return {"setup": setup, "signal_date": b["d"][i], "entry_date": b["d"][i + 1], "exit_date": b["d"][j],
            "entry": round(entry, 4), "stop": round(stop, 4), "target": round(target, 4),
            "exit": round(exit_net, 4), "reason": reason, "days": j - i, "ret": ret, "R": ret / risk,
            "exit_index": j}


# ---------------------------------------------------------------- backtest

def stats(trades):
    n = len(trades)
    if not n:
        return {"n": 0}
    rets = [t["ret"] for t in trades]
    rs = [t["R"] for t in trades]
    wins = [r for r in rets if r > 0]
    gp = sum(r for r in rets if r > 0)
    gl = -sum(r for r in rets if r < 0)
    streak = worst = 0
    for r in rets:
        streak = streak + 1 if r <= 0 else 0
        worst = max(worst, streak)
    return {
        "n": n, "win_rate": len(wins) / n, "avg_ret": sum(rets) / n, "avg_R": sum(rs) / n,
        "median_R": statistics.median(rs), "profit_factor": (gp / gl) if gl > 0 else float("inf"),
        "avg_days": sum(t["days"] for t in trades) / n, "worst_losing_streak": worst,
        "best_R": max(rs), "worst_R": min(rs),
    }


def split_halves(trades):
    ts = sorted(trades, key=lambda t: t["signal_date"])
    k = (2 * len(ts)) // 3
    return ts[:k], ts[k:]


def verdict(st, first, second):
    if st.get("n", 0) < 30:
        return "not enough history"
    ok_all = st["avg_R"] > 0.05 and st["profit_factor"] > 1.15
    ok_oos = second.get("n", 0) >= 10 and second["avg_R"] > 0 and first.get("n", 0) and first["avg_R"] > 0
    if ok_all and ok_oos:
        return "edge (held up in the most recent third)"
    if ok_all:
        return "weak: profitable overall but not in the most recent third"
    return "no edge"


def load_market(market, cfg, symbols=None, quiet=False, refresh=False):
    quiet = quiet or not sys.stderr.isatty()
    syms = symbols or sorted(set(UNIVERSE[market] + list(cfg.get("watchlist", {}).get(market, []))))
    out, failed = {}, []
    for k, s in enumerate(syms):
        if not quiet:
            sys.stderr.write("\r  %s data %d/%d %-10s" % (market, k + 1, len(syms), s))
            sys.stderr.flush()
        try:
            b = fetch(s, market, rng=cfg["history"], refresh=refresh)
            if len(b["c"]) >= 220:
                out[s] = b
            else:
                failed.append(s + " (short history)")
        except WTError as e:
            failed.append("%s (%s)" % (s, e))
    if not quiet:
        sys.stderr.write("\r" + " " * 50 + "\r")
    try:
        idx = fetch(MARKETS[market]["index"], market, rng=cfg["history"], refresh=refresh)
    except WTError as e:
        idx = None
        failed.append("index %s (%s)" % (MARKETS[market]["index"], e))
    return out, idx, failed


def backtest_market(market, cfg, data, idx, setups=None, symbol=None):
    reg = regime_map(idx) if idx else None
    trades = []
    for s, b in data.items():
        if symbol and s.upper() != symbol.upper():
            continue
        ind = indicators(b)
        busy_until = {}
        for i in range(200, len(b["c"]) - 1):
            for setup in signals_at(b, ind, i, cfg, market, reg):
                if setups and setup not in setups:
                    continue
                if i <= busy_until.get(setup, -1):
                    continue
                t = simulate(b, ind, i, setup, cfg, market)
                if not t or t.get("open"):
                    continue
                t["symbol"] = s
                t["market"] = market
                busy_until[setup] = t["exit_index"]
                trades.append(t)
    result = {"market": market, "generated": dt.datetime.now().isoformat(timespec="seconds"),
              "symbols": len(data), "config": {k: cfg[k] for k in ("hold_days", "stop_atr", "target_atr", "regime_filter")},
              "setups": {}, "baseline": baseline(data, cfg, market)}
    for setup in (setups or SETUPS):
        ts = [t for t in trades if t["setup"] == setup]
        first, second = split_halves(ts)
        st, s1, s2 = stats(ts), stats(first), stats(second)
        by_sym = {}
        for t in ts:
            by_sym.setdefault(t["symbol"], []).append(t)
        result["setups"][setup] = {
            "all": st, "first_two_thirds": s1, "recent_third": s2,
            "verdict": verdict(st, s1, s2),
            "by_symbol": {k: stats(v) for k, v in by_sym.items()},
            "recent_trades": sorted(({k: v for k, v in t.items() if k != "exit_index"} for t in ts),
                                    key=lambda t: t["signal_date"])[-15:],
        }
    return result


def baseline(data, cfg, market):
    """Buy any day at the open, sell at the close `hold_days` later, same costs: what doing nothing clever earns."""
    comm, slip = cost_pct(market, cfg)
    rets = []
    hold = cfg["hold_days"]
    for b in data.values():
        c, o = b["c"], b["o"]
        for i in range(200, len(c) - hold - 1, 3):
            e = o[i + 1] * (1 + slip)
            x = c[i + hold] * (1 - slip)
            rets.append(x / e - 1 - comm)
    if not rets:
        return {}
    return {"n": len(rets), "avg_ret": sum(rets) / len(rets), "win_rate": sum(1 for r in rets if r > 0) / len(rets)}


def market_stats(market, cfg, data=None, idx=None, refresh=False, quiet=False):
    p = path("stats_%s.json" % market)
    cached = load_json(p)
    if cached and not refresh and cached.get("config") == {k: cfg[k] for k in ("hold_days", "stop_atr", "target_atr", "regime_filter")}:
        age = dt.datetime.now() - dt.datetime.fromisoformat(cached["generated"])
        if age < dt.timedelta(hours=20):
            return cached
    if data is None:
        data, idx, _ = load_market(market, cfg, quiet=quiet)
    res = backtest_market(market, cfg, data, idx)
    save_json(p, res)
    return res


# ---------------------------------------------------------------- planning

def add_trading_days(d, n):
    while n > 0:
        d += dt.timedelta(days=1)
        if d.weekday() < 5:
            n -= 1
    return d


def size_trade(market, entry, stop, cfg, fx=None):
    fx = fx if fx is not None else fx_to_aud(market, cfg)
    risk_aud = cfg["account_aud"] * cfg["risk_per_trade_pct"] / 100.0
    per_share_aud = (entry - stop) * fx
    if per_share_aud <= 0:
        raise WTError("stop must be below entry")
    shares = int(risk_aud // per_share_aud)
    cap_aud = cfg["account_aud"] * cfg["max_position_pct"] / 100.0
    capped = False
    if shares * entry * fx > cap_aud:
        shares = int(cap_aud // (entry * fx))
        capped = True
    notes = []
    if capped:
        notes.append("size capped at %s%% of the account" % cfg["max_position_pct"])
    value_aud = shares * entry * fx
    if shares == 0:
        notes.append("too expensive for these limits: one share risks more than your max loss or exceeds the position cap")
    if market == "ASX" and 0 < value_aud < 500:
        notes.append("under the ASX $500 minimum for a new holding")
    return {"shares": shares, "value_aud": value_aud, "risk_aud": shares * per_share_aud,
            "max_risk_aud": risk_aud, "fx_aud_per_unit": fx, "notes": notes}


def make_plan(symbol, market, b, ind, setup, cfg, entry=None, stat=None):
    i = len(b["c"]) - 1
    a = ind["atr"][i]
    est_entry = entry if entry else b["c"][i]
    stop, target = levels(setup, est_entry, a, cfg)
    sz = size_trade(market, est_entry, stop, cfg)
    last = dt.date.fromisoformat(b["d"][i])
    entry_day = add_trading_days(last, 1)
    sell_by = add_trading_days(entry_day, cfg["hold_days"] - 1)
    cur = MARKETS[market]["currency"]
    comm = cfg["costs"][market].get("commission", 0)
    plan = {
        "symbol": symbol, "market": market, "currency": cur, "setup": setup,
        "why": SETUPS[setup], "signal_date": b["d"][i], "last_close": round(b["c"][i], 4),
        "entry": round(est_entry, 4), "entry_note": "buy at the next open (" + entry_day.isoformat() + "); the price used is the last close, so the real fill will differ",
        "stop": round(stop, 4), "target": round(target, 4) if setup != "pullback" else None,
        "exit_rule": ("sell when it closes back above its 5-day average (about %.2f now), or at the stop" % ind["sma5"][i])
        if setup == "pullback" else "sell at the target or the stop, whichever comes first",
        "sell_by": sell_by.isoformat(), "sell_by_note": "if neither has happened, sell at the close on this day",
        "atr": round(a, 4), "shares": sz["shares"], "position_aud": round(sz["value_aud"], 2),
        "risk_aud": round(sz["risk_aud"], 2), "max_risk_aud": round(sz["max_risk_aud"], 2),
        "reward_aud": round(sz["shares"] * (target - est_entry) * sz["fx_aud_per_unit"], 2) if setup != "pullback" else None,
        "commission_round_trip": "%s %.2f" % (cur, 2 * comm), "notes": sz["notes"],
    }
    if stat:
        plan["history"] = stat
    return plan


# ---------------------------------------------------------------- scanning and ranking

def score_signal(setup_res, symbol):
    allst = setup_res["all"]
    if allst.get("n", 0) < 30:
        return None, "not enough history for this setup"
    if setup_res["verdict"] == "no edge":
        return None, "this setup has not made money on this market"
    score = allst["avg_R"]
    if not setup_res["verdict"].startswith("edge"):
        score *= 0.5
    sym = setup_res["by_symbol"].get(symbol)
    if sym and sym["n"] >= 5:
        score = 0.7 * score + 0.3 * max(min(sym["avg_R"], 1.0), -1.0)
    return score, setup_res["verdict"]


def scan_market(market, cfg, refresh=False, quiet=False):
    data, idx, failed = load_market(market, cfg, quiet=quiet, refresh=refresh)
    st = market_stats(market, cfg, data, idx, refresh=refresh, quiet=quiet)
    reg = regime_map(idx) if idx else None
    regime_now = None
    if idx:
        regime_now = reg.get(idx["d"][-1])
    found = []
    for s, b in data.items():
        ind = indicators(b)
        i = len(b["c"]) - 1
        for setup in signals_at(b, ind, i, cfg, market, reg):
            sres = st["setups"][setup]
            score, why = score_signal(sres, s)
            sym = sres["by_symbol"].get(s)
            found.append({
                "symbol": s, "market": market, "setup": setup, "date": b["d"][i],
                "close": round(b["c"][i], 4), "score": score, "edge": why,
                "setup_history": {k: sres["all"].get(k) for k in ("n", "win_rate", "avg_R", "profit_factor")},
                "symbol_history": sym, "stale": bool(b.get("stale")),
            })
    found.sort(key=lambda r: (r["score"] is None, -(r["score"] or 0)))
    return {"market": market, "regime_up": regime_now, "index": MARKETS[market]["index"],
            "as_of": idx["d"][-1] if idx else None, "signals": found, "failed": failed, "stats": st, "data": data}


# ---------------------------------------------------------------- journal

def journal():
    return load_json(path("journal.json"), {"trades": []})


def journal_save(j):
    save_json(path("journal.json"), j)


def review_open(cfg, quiet=True):
    j = journal()
    out = []
    today = dt.date.today()
    for t in j["trades"]:
        if t.get("status") != "open":
            continue
        row = dict(t)
        try:
            b = fetch(t["symbol"], t["market"], rng="3mo", max_age_h=1)
        except WTError as e:
            row["action"] = "CHECK MANUALLY (%s)" % e
            out.append(row)
            continue
        since = [k for k, d in enumerate(b["d"]) if d >= t["opened"]]
        last = b["c"][-1]
        row["last"] = round(last, 4)
        row["last_date"] = b["d"][-1]
        lo = min((b["l"][k] for k in since), default=last)
        hi = max((b["h"][k] for k in since), default=last)
        act = "HOLD"
        if lo <= t["stop"]:
            act = "SELL: stop %.2f was hit (low %.2f)" % (t["stop"], lo)
        elif t.get("target") and hi >= t["target"]:
            act = "SELL: target %.2f was hit (high %.2f)" % (t["target"], hi)
        elif t.get("setup") == "pullback":
            s5 = sma(b["c"], 5)[-1]
            if s5 and last > s5:
                act = "SELL: closed above its 5-day average (%.2f)" % s5
        if act == "HOLD" and t.get("sell_by") and today.isoformat() >= t["sell_by"]:
            act = "SELL: end of the week (sell-by %s)" % t["sell_by"]
        row["action"] = act
        fx = fx_to_aud(t["market"], cfg)
        row["open_pnl_aud"] = round((last - t["entry"]) * t["shares"] * fx, 2)
        out.append(row)
    return out


# ---------------------------------------------------------------- output helpers

def pct(x):
    return "—" if x is None else "%.1f%%" % (100 * x)


def num(x, d=2):
    return "—" if x is None else ("∞" if x == float("inf") else "%.*f" % (d, x))


def markets_arg(a, cfg):
    if not a or a.lower() == "both":
        return [m for m in cfg["markets"] if m in MARKETS]
    m = a.upper()
    if m not in MARKETS:
        raise WTError("market must be US, ASX or both")
    return [m]


def guess_market(symbol, given):
    if given and given.lower() != "both":
        return given.upper()
    s = symbol.upper()
    if s.endswith(".AX") or s in UNIVERSE["ASX"]:
        return "ASX"
    return "US"


def setup_table(res):
    lines = ["| setup | trades | win rate | avg per trade | avg R | profit factor | recent third avg R | verdict |",
             "|---|---|---|---|---|---|---|---|"]
    for name, s in res["setups"].items():
        a, r = s["all"], s["recent_third"]
        lines.append("| %s | %s | %s | %s | %s | %s | %s | %s |" % (
            name, a.get("n", 0), pct(a.get("win_rate")), pct(a.get("avg_ret")), num(a.get("avg_R")),
            num(a.get("profit_factor")), num(r.get("avg_R")), s["verdict"]))
    b = res.get("baseline") or {}
    if b:
        lines.append("\nBaseline (buy any day, sell %d days later, same costs): avg %s per trade, %s winners, %d samples." % (
            res["config"]["hold_days"], pct(b["avg_ret"]), pct(b["win_rate"]), b["n"]))
    return "\n".join(lines)


def plan_text(p):
    cur = "A$" if p["market"] == "ASX" else "US$"
    L = ["%s (%s) — %s" % (p["symbol"], p["market"], p["setup"]),
         "  why: %s" % p["why"],
         "  buy  %d share%s at about %s%.2f (%s)" % (p["shares"], "" if p["shares"] == 1 else "s", cur, p["entry"], p["entry_note"]),
         "  stop %s%.2f" % (cur, p["stop"]) + ("   target %s%.2f" % (cur, p["target"]) if p["target"] else ""),
         "  exit: %s; %s %s" % (p["exit_rule"], p["sell_by_note"], p["sell_by"]),
         "  position A$%.2f · max loss at the stop A$%.2f (limit A$%.2f)%s" % (
             p["position_aud"], p["risk_aud"], p["max_risk_aud"],
             (" · reward at target A$%.2f" % p["reward_aud"]) if p.get("reward_aud") is not None else "")]
    for n in p["notes"]:
        L.append("  note: " + n)
    return "\n".join(L)


# ---------------------------------------------------------------- commands

def cmd_init(a):
    p = path("config.json")
    if os.path.exists(p) and not a.force:
        print("Config already exists: %s (use --force to reset)" % p)
        return
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    if a.account:
        cfg["account_aud"] = a.account
    if a.risk:
        cfg["risk_per_trade_pct"] = a.risk
    save_json(p, cfg)
    print("Wrote %s" % p)
    print("Account A$%s, risk %.2f%% per trade (A$%.2f), hold up to %d trading days." % (
        cfg["account_aud"], cfg["risk_per_trade_pct"], cfg["account_aud"] * cfg["risk_per_trade_pct"] / 100, cfg["hold_days"]))


def cmd_doctor(a):
    cfg = load_config()
    print("Week Trader %s · Python %s · data folder %s" % (VERSION, sys.version.split()[0], HOME))
    ok = True
    for sym, m in (("AAPL", "US"), ("BHP", "ASX"), ("SPY", "US"), ("^AXJO", "ASX")):
        try:
            b = fetch(sym, m, rng="1mo", refresh=True)
            print("  OK   %-7s %s bars, last %s close %.2f%s" % (sym, len(b["c"]), b["d"][-1], b["c"][-1], " (stale cache)" if b.get("stale") else ""))
        except WTError as e:
            ok = False
            print("  FAIL %-7s %s" % (sym, e))
    fx, src = aud_per_usd(cfg)
    print("  FX   A$%.4f per US$1 (%s)" % (fx, src))
    print("All good." if ok else "Some data failed: check your internet connection, then try again in a minute (Yahoo rate-limits).")


def cmd_backtest(a):
    cfg = load_config()
    for k in ("hold_days", "stop_atr", "target_atr"):
        if getattr(a, k) is not None:
            cfg[k] = getattr(a, k)
    if a.no_regime:
        cfg["regime_filter"] = False
    setups = [a.setup] if a.setup else None
    if a.setup and a.setup not in SETUPS:
        raise WTError("setup must be one of: " + ", ".join(SETUPS))
    out = {}
    for m in markets_arg(a.market, cfg):
        syms = [a.symbol.upper().replace(".AX", "")] if a.symbol else None
        data, idx, failed = load_market(m, cfg, symbols=syms, refresh=a.refresh)
        res = backtest_market(m, cfg, data, idx, setups=setups, symbol=None)
        if not a.symbol and not setups and not any(getattr(a, k) is not None for k in ("hold_days", "stop_atr", "target_atr")) and not a.no_regime:
            save_json(path("stats_%s.json" % m), res)
        out[m] = res
        if a.json:
            continue
        span = ""
        if data:
            first = min(b["d"][200] for b in data.values() if len(b["d"]) > 200)
            last = max(b["d"][-1] for b in data.values())
            span = " · signals %s to %s" % (first, last)
        print("\n## %s — %d stocks%s" % (MARKETS[m]["label"], len(data), span))
        print("Rules: hold up to %d trading days, stop %.1f ATR, target %.1f ATR, market filter %s, costs included.\n" % (
            cfg["hold_days"], cfg["stop_atr"], cfg["target_atr"], "on" if cfg["regime_filter"] else "off"))
        print(setup_table(res))
        if failed:
            print("\nSkipped %d: %s" % (len(failed), ", ".join(failed[:12]) + (" …" if len(failed) > 12 else "")))
    if a.json:
        print(json.dumps(out, indent=2, default=str))


def cmd_scan(a):
    cfg = load_config()
    allres = []
    for m in markets_arg(a.market, cfg):
        r = scan_market(m, cfg, refresh=a.refresh)
        allres.append(r)
        if a.json:
            continue
        print("\n## %s — data to %s · market %s" % (MARKETS[m]["label"], r["as_of"],
              "UP (index above its 50-day average)" if r["regime_up"] else "DOWN (breakout and gap setups are switched off)"))
        if not r["signals"]:
            print("No setups fired on the last close.")
        for s in r["signals"]:
            sh = s["setup_history"]
            print("  %-6s %-9s close %-9.2f %s · setup history: %s trades, %s win, avg %s R" % (
                s["symbol"], s["setup"], s["close"],
                ("score %.2f" % s["score"]) if s["score"] is not None else "not ranked: " + s["edge"],
                sh.get("n"), pct(sh.get("win_rate")), num(sh.get("avg_R"))))
    if a.json:
        print(json.dumps([{k: v for k, v in r.items() if k not in ("data", "stats")} for r in allres], indent=2, default=str))


def cmd_plan(a):
    cfg = load_config()
    m = guess_market(a.symbol, a.market)
    sym = a.symbol.upper().replace(".AX", "")
    b = fetch(sym, m, rng=cfg["history"], refresh=a.refresh)
    ind = indicators(b)
    setup = a.setup
    st = load_json(path("stats_%s.json" % m))
    if not setup:
        idx = None
        try:
            idx = fetch(MARKETS[m]["index"], m, rng=cfg["history"])
        except WTError:
            pass
        fired = signals_at(b, ind, len(b["c"]) - 1, cfg, m, regime_map(idx) if idx else None)
        setup = fired[0] if fired else "breakout"
        if not fired:
            print("Note: no setup fired on %s's last close; planning it as a breakout for the numbers only." % sym)
    hist = None
    if st and setup in st.get("setups", {}):
        hist = {"setup_all": st["setups"][setup]["all"], "verdict": st["setups"][setup]["verdict"],
                "this_stock": st["setups"][setup]["by_symbol"].get(sym)}
    p = make_plan(sym, m, b, ind, setup, cfg, entry=a.entry, stat=hist)
    if a.json:
        print(json.dumps(p, indent=2, default=str))
        return
    print(plan_text(p))
    if hist:
        s = hist["setup_all"]
        print("  history of this setup on %s: %s trades, %s winners, avg %s R — %s" % (
            m, s.get("n"), pct(s.get("win_rate")), num(s.get("avg_R")), hist["verdict"]))
    else:
        print("  (run backtest first to see how this setup has done)")


def cmd_week(a):
    cfg = load_config()
    top_n = a.top or cfg["top_n"]
    today = dt.date.today().isoformat()
    md = ["# Week Trader — %s" % today, "",
          "Account A$%s · risk %.2f%% per trade (A$%.2f) · hold up to %d trading days · not financial advice." % (
              cfg["account_aud"], cfg["risk_per_trade_pct"], cfg["account_aud"] * cfg["risk_per_trade_pct"] / 100, cfg["hold_days"]), ""]
    # 1. what to sell
    sells = review_open(cfg)
    md.append("## 1. Open trades: what to sell")
    if not sells:
        md.append("No open trades in the journal.")
    for t in sells:
        md.append("- **%s** (%s, %s) bought %s at %.2f · last %s · open P&L A$%s · **%s**" % (
            t["symbol"], t["market"], t.get("setup", "?"), t["opened"], t["entry"], num(t.get("last")),
            num(t.get("open_pnl_aud")), t["action"]))
    md.append("")
    # 2. what to buy
    candidates, notes = [], []
    open_n = len(sells)
    for m in markets_arg(a.market, cfg):
        r = scan_market(m, cfg, refresh=a.refresh)
        md.append("## %s — data to %s · market %s" % (MARKETS[m]["label"], r["as_of"], "UP" if r["regime_up"] else "DOWN"))
        md.append("")
        md.append(setup_table(r["stats"]))
        md.append("")
        ranked = [s for s in r["signals"] if s["score"] is not None and s["score"] > 0]
        unranked = [s for s in r["signals"] if s not in ranked]
        if unranked:
            notes.append("%s: %d other signal(s) not ranked (%s)." % (
                m, len(unranked), "; ".join(sorted({u["edge"] for u in unranked}))))
        for s in ranked:
            b = r["data"][s["symbol"]]
            ind = indicators(b)
            sres = r["stats"]["setups"][s["setup"]]
            hist = {"setup_all": sres["all"], "verdict": sres["verdict"], "this_stock": sres["by_symbol"].get(s["symbol"])}
            p = make_plan(s["symbol"], m, b, ind, s["setup"], cfg, stat=hist)
            p["score"] = s["score"]
            candidates.append(p)
        if r["failed"]:
            notes.append("%s: skipped %d symbol(s) with no data." % (m, len(r["failed"])))
    candidates = [c for c in candidates if c["shares"] > 0]
    candidates.sort(key=lambda c: -c["score"])
    slots = max(cfg["max_open_trades"] - open_n, 0)
    picks = candidates[:min(top_n, slots)] if slots else []
    md.append("## 2. Best trades to buy this week")
    if not slots:
        md.append("You already hold %d trades, your maximum. Sell before adding more." % open_n)
    elif not picks:
        md.append("**No trade passes today.** Nothing fired on a setup that has made money in the backtest. Sitting out is a valid result.")
    for k, p in enumerate(picks, 1):
        cur = "A$" if p["market"] == "ASX" else "US$"
        h = p["history"]["setup_all"]
        ts = p["history"].get("this_stock")
        md += ["### %d. %s (%s) — %s · score %.2f" % (k, p["symbol"], p["market"], p["setup"], p["score"]),
               "- Why: %s" % p["why"],
               "- Buy **%d share%s** at the next open (about %s%.2f) · position A$%.2f" % (p["shares"], "" if p["shares"] == 1 else "s", cur, p["entry"], p["position_aud"]),
               "- Stop **%s%.2f**%s" % (cur, p["stop"], (" · target **%s%.2f**" % (cur, p["target"])) if p["target"] else ""),
               "- Exit: %s · otherwise sell at the close on **%s**" % (p["exit_rule"], p["sell_by"]),
               "- Max loss at the stop A$%.2f%s" % (p["risk_aud"], (" · reward at target A$%.2f" % p["reward_aud"]) if p.get("reward_aud") is not None else ""),
               "- History: this setup on %s — %s trades, %s winners, avg %s R (%s)%s" % (
                   p["market"], h.get("n"), pct(h.get("win_rate")), num(h.get("avg_R")), p["history"]["verdict"],
                   ("; on %s itself — %d trades, avg %s R" % (p["symbol"], ts["n"], num(ts["avg_R"]))) if ts else "")]
        for n in p["notes"]:
            md.append("- Note: " + n)
        md.append("")
    if len(candidates) > len(picks):
        md.append("Also qualified but not picked: " + ", ".join("%s (%s %s, %.2f)" % (c["symbol"], c["market"], c["setup"], c["score"]) for c in candidates[len(picks):len(picks) + 8]))
        md.append("")
    if notes:
        md.append("## Notes")
        md += ["- " + n for n in notes]
        md.append("")
    md += ["## Read this", "- Prices are from the last close; the real fill at the open will differ, so recompute the stop if it gaps.",
           "- A stop is a plan, not a guarantee: a gap can fill well past it.",
           "- Backtests use daily bars and assume the stop hits first on a day that touches both. Past results do not predict future ones.",
           "- Log each trade you take: `journal add`. The journal is the only honest scorecard."]
    report = "\n".join(md)
    rp = path("reports", "week-%s.md" % today)
    os.makedirs(os.path.dirname(rp), exist_ok=True)
    with open(rp, "w") as f:
        f.write(report + "\n")
    save_json(path("reports", "week-%s.json" % today), {"sell": sells, "picks": picks, "candidates": candidates})
    print(report)
    print("\nSaved: %s" % rp)


def cmd_journal(a):
    cfg = load_config()
    j = journal()
    if a.action == "add":
        if not all([a.symbol, a.entry, a.shares, a.stop]):
            raise WTError("journal add needs SYMBOL --entry --shares --stop (and ideally --target --setup)")
        m = guess_market(a.symbol, a.market)
        opened = a.date or dt.date.today().isoformat()
        sell_by = a.sell_by or add_trading_days(dt.date.fromisoformat(opened), cfg["hold_days"] - 1).isoformat()
        tid = 1 + max([t["id"] for t in j["trades"]] or [0])
        t = {"id": tid, "symbol": a.symbol.upper().replace(".AX", ""), "market": m, "setup": a.setup or "manual",
             "opened": opened, "entry": a.entry, "shares": a.shares, "stop": a.stop, "target": a.target,
             "sell_by": sell_by, "status": "open", "paper": not a.real, "note": a.note or ""}
        j["trades"].append(t)
        journal_save(j)
        print("Logged #%d: %s %d %s at %.2f, stop %.2f%s, sell by %s (%s)." % (
            tid, "bought", a.shares, t["symbol"], a.entry, a.stop, (", target %.2f" % a.target) if a.target else "", sell_by,
            "paper" if t["paper"] else "REAL money"))
    elif a.action == "close":
        if not a.id or a.exit is None:
            raise WTError("journal close needs --id and --exit PRICE")
        t = next((t for t in j["trades"] if t["id"] == a.id), None)
        if not t or t["status"] != "open":
            raise WTError("no open trade #%s" % a.id)
        fx = fx_to_aud(t["market"], cfg)
        comm = cfg["costs"][t["market"]].get("commission", 0) * 2
        pnl = (a.exit - t["entry"]) * t["shares"] - comm
        t.update({"status": "closed", "closed": a.date or dt.date.today().isoformat(), "exit": a.exit,
                  "pnl": round(pnl, 2), "pnl_aud": round(pnl * fx, 2),
                  "R": round(pnl / ((t["entry"] - t["stop"]) * t["shares"]), 2) if t["entry"] > t["stop"] else None,
                  "exit_reason": a.note or ""})
        journal_save(j)
        print("Closed #%d %s at %.2f: P&L A$%.2f (%s R)." % (t["id"], t["symbol"], a.exit, t["pnl_aud"], num(t["R"])))
    elif a.action == "list":
        if not j["trades"]:
            print("Journal is empty.")
        for t in j["trades"]:
            print("#%-3d %-6s %-4s %-9s %s %3d @ %-9.2f stop %-8.2f %s%s" % (
                t["id"], t["symbol"], t["market"], t.get("setup", ""), t["opened"], t["shares"], t["entry"], t["stop"],
                t["status"].upper(), (" exit %.2f · A$%.2f · %s R" % (t["exit"], t["pnl_aud"], num(t.get("R")))) if t["status"] == "closed" else " sell by " + t["sell_by"]))
    elif a.action == "review":
        rows = review_open(cfg)
        if not rows:
            print("No open trades.")
        for t in rows:
            print("#%-3d %-6s last %-9s open P&L A$%-9s %s" % (t["id"], t["symbol"], num(t.get("last")), num(t.get("open_pnl_aud")), t["action"]))
    elif a.action == "stats":
        closed = [t for t in j["trades"] if t["status"] == "closed"]
        if not closed:
            print("No closed trades yet.")
            return
        def summ(ts, label):
            pn = [t["pnl_aud"] for t in ts]
            rs = [t["R"] for t in ts if t.get("R") is not None]
            w = [p for p in pn if p > 0]
            print("%-12s %3d trades · %s winners · total A$%.2f · avg A$%.2f · avg %s R" % (
                label, len(ts), pct(len(w) / len(ts)), sum(pn), sum(pn) / len(ts), num(sum(rs) / len(rs)) if rs else "—"))
        summ(closed, "All")
        for key in ("paper", "setup", "market"):
            groups = {}
            for t in closed:
                groups.setdefault(str(t.get(key)), []).append(t)
            if len(groups) > 1:
                for g, ts in sorted(groups.items()):
                    summ(ts, "%s=%s" % (key, g))
        if len(closed) < 30:
            print("Fewer than 30 trades: too few to judge. Keep paper trading.")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="wt.py", description="Week Trader: find, test, plan and journal week-long swing trades (US + ASX).")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("init"); p.add_argument("--account", type=float); p.add_argument("--risk", type=float); p.add_argument("--force", action="store_true"); p.set_defaults(fn=cmd_init)
    p = sub.add_parser("doctor"); p.set_defaults(fn=cmd_doctor)
    for name, fn in (("week", cmd_week), ("scan", cmd_scan)):
        p = sub.add_parser(name); p.add_argument("--market", default="both"); p.add_argument("--refresh", action="store_true"); p.add_argument("--json", action="store_true")
        if name == "week":
            p.add_argument("--top", type=int)
        p.set_defaults(fn=fn)
    p = sub.add_parser("backtest"); p.add_argument("--market", default="both"); p.add_argument("--setup"); p.add_argument("--symbol")
    p.add_argument("--hold-days", dest="hold_days", type=int); p.add_argument("--stop-atr", dest="stop_atr", type=float); p.add_argument("--target-atr", dest="target_atr", type=float)
    p.add_argument("--no-regime", action="store_true"); p.add_argument("--refresh", action="store_true"); p.add_argument("--json", action="store_true"); p.set_defaults(fn=cmd_backtest)
    p = sub.add_parser("plan"); p.add_argument("symbol"); p.add_argument("--market"); p.add_argument("--setup", choices=list(SETUPS)); p.add_argument("--entry", type=float)
    p.add_argument("--refresh", action="store_true"); p.add_argument("--json", action="store_true"); p.set_defaults(fn=cmd_plan)
    p = sub.add_parser("journal"); p.add_argument("action", choices=["add", "close", "list", "review", "stats"]); p.add_argument("symbol", nargs="?")
    p.add_argument("--market"); p.add_argument("--setup"); p.add_argument("--entry", type=float); p.add_argument("--shares", type=int); p.add_argument("--stop", type=float)
    p.add_argument("--target", type=float); p.add_argument("--date"); p.add_argument("--sell-by", dest="sell_by"); p.add_argument("--real", action="store_true")
    p.add_argument("--note"); p.add_argument("--id", type=int); p.add_argument("--exit", type=float); p.set_defaults(fn=cmd_journal)
    a = ap.parse_args(argv)
    try:
        a.fn(a)
    except WTError as e:
        print("Error: %s" % e, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
