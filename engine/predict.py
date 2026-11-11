"""Climb predictor for Week Trader. Standard library only.

Question it answers: from today's close, which stocks are about to climb?

"About to climb" is measured against the other stocks in the same market over the
same days: buy at the next open, sell at the close HORIZON trading days later, and
see whether it rose more than the median stock. Comparing with the same days removes
the market's own moves, and it cannot be gamed by volatility (an earlier version
measured "+2 ATR before -1 ATR", and noise in ATR made random data look predictable).

13 features are measured at each close. A logistic model is trained on the older
two-thirds of the history and graded only on the most recent third, which it never
saw. A model "works" only if, week by week on that unseen third, its top 10% of
stocks beat the average stock, with a t-stat of at least 2.
"""

import datetime as dt
import math
import random
from collections import deque

FEATURES = [
    ("ret5", "1-week return"),
    ("ret20", "1-month return"),
    ("ret126", "6-month return"),
    ("from_high", "distance below the 52-week high"),
    ("rel_vol", "today's volume vs normal"),
    ("vol_trend", "volume last week vs last 2 months (quiet buying)"),
    ("squeeze", "volatility now vs its usual (quiet before a move)"),
    ("range_pos", "where it closed in today's range"),
    ("rsi2", "2-day RSI (short-term oversold/overbought)"),
    ("rs_index", "1-month return vs the market index"),
    ("vs_sma50", "distance above the 50-day average"),
    ("atr_pct", "how volatile it is (ATR as % of price)"),
    ("gap", "today's gap from yesterday's close"),
]
NAMES = [f for f, _ in FEATURES]
LABELS = dict(FEATURES)


def rolling_max(xs, n):
    out, q = [None] * len(xs), deque()
    for i, x in enumerate(xs):
        while q and xs[q[-1]] <= x:
            q.pop()
        q.append(i)
        if q[0] <= i - n:
            q.popleft()
        if i >= n - 1:
            out[i] = xs[q[0]]
    return out


def extra_indicators(b, wt):
    ind = b.get("_ind") or wt.indicators(b)
    b["_ind"] = ind
    if "hi252" not in ind:
        ind["hi252"] = rolling_max(b["h"], 252)
        ind["atr100"] = wt.atr(b["h"], b["l"], b["c"], 100)
        vf = [float(x) for x in b["v"]]
        ind["vol5"] = wt.sma(vf, 5)
        ind["vol50"] = wt.sma(vf, 50)
    return ind


def features_at(b, ind, i, idx_close):
    c = b["c"]
    if i < 252 or ind["atr"][i] is None or ind["atr100"][i] is None or ind["hi252"][i] is None:
        return None
    a = ind["atr"][i]
    if not a or not ind["vol20"][i - 1] or not ind["vol50"][i] or not ind["atr100"][i]:
        return None
    d = b["d"][i]
    d20 = b["d"][i - 20]
    rs = 0.0
    if idx_close and d in idx_close and d20 in idx_close:
        rs = (c[i] / c[i - 20] - 1) - (idx_close[d] / idx_close[d20] - 1)
    rng = b["h"][i] - b["l"][i]
    return [
        c[i] / c[i - 5] - 1,
        c[i] / c[i - 20] - 1,
        c[i] / c[i - 126] - 1,
        c[i] / ind["hi252"][i] - 1,
        math.log(max(b["v"][i], 1) / ind["vol20"][i - 1]),
        math.log(max(ind["vol5"][i], 1) / ind["vol50"][i]),
        a / ind["atr100"][i],
        (c[i] - b["l"][i]) / rng if rng > 0 else 0.5,
        ind["rsi2"][i] if ind["rsi2"][i] is not None else 50.0,
        rs,
        c[i] / ind["sma50"][i] - 1,
        a / c[i],
        b["o"][i] / c[i - 1] - 1,
    ]


def forward_return(b, i, horizon):
    """Buy at the next open, sell at the close `horizon` trading days later."""
    if i + horizon >= len(b["c"]):
        return None
    return b["c"][i + horizon] / b["o"][i + 1] - 1


def build_rows(data, idx, cfg, market, wt, train_step=2):
    """Rows of (date, symbol, features, label, forward_return, excess). Label 1 = beat the median stock that week."""
    idx_close = dict(zip(idx["d"], idx["c"])) if idx else {}
    hz = cfg["climb_horizon"]
    by_date = {}
    for s, b in data.items():
        ind = extra_indicators(b, wt)
        for i in range(252, len(b["c"]) - hz - 1):
            if b["c"][i] < cfg["min_price"][market] or (ind["dv20"][i] or 0) < cfg["min_dollar_volume"][market]:
                continue
            f = features_at(b, ind, i, idx_close)
            if f is None:
                continue
            fr = forward_return(b, i, hz)
            if fr is None:
                continue
            by_date.setdefault(b["d"][i], []).append((s, f, fr))
    rows = []
    for d in sorted(by_date):
        group = by_date[d]
        if len(group) < 8:
            continue
        frs = sorted(g[2] for g in group)
        med = frs[len(frs) // 2] if len(frs) % 2 else (frs[len(frs) // 2 - 1] + frs[len(frs) // 2]) / 2
        mean = sum(frs) / len(frs)
        for s, f, fr in group:
            rows.append((d, s, f, 1 if fr > med else 0, fr, fr - mean))
    return rows


def standardise(rows):
    k = len(NAMES)
    mu = [0.0] * k
    for r in rows:
        for j in range(k):
            mu[j] += r[2][j]
    mu = [m / len(rows) for m in mu]
    sd = [0.0] * k
    for r in rows:
        for j in range(k):
            sd[j] += (r[2][j] - mu[j]) ** 2
    sd = [math.sqrt(v / len(rows)) or 1.0 for v in sd]
    return mu, sd


def zrow(f, mu, sd):
    return [max(-4.0, min(4.0, (f[j] - mu[j]) / sd[j])) for j in range(len(f))]


def sigmoid(x):
    if x < -30:
        return 1e-13
    if x > 30:
        return 1 - 1e-13
    return 1 / (1 + math.exp(-x))


def train_logistic(X, y, epochs=40, lr=0.5, l2=1e-3, seed=7):
    """Mini-batch gradient descent on log-loss. X: list of z-scored rows."""
    k = len(X[0])
    w = [0.0] * (k + 1)
    base = sum(y) / len(y)
    w[k] = math.log(base / (1 - base)) if 0 < base < 1 else 0.0
    order = list(range(len(X)))
    rnd = random.Random(seed)
    bs = 256
    for ep in range(epochs):
        rnd.shuffle(order)
        step = lr / (1 + ep * 0.1)
        for s in range(0, len(order), bs):
            batch = order[s:s + bs]
            g = [0.0] * (k + 1)
            for i in batch:
                x = X[i]
                z = w[k]
                for j in range(k):
                    z += w[j] * x[j]
                e = sigmoid(z) - y[i]
                for j in range(k):
                    g[j] += e * x[j]
                g[k] += e
            m = len(batch)
            for j in range(k):
                w[j] -= step * (g[j] / m + l2 * w[j])
            w[k] -= step * g[k] / m
    return w


def predict_p(w, x):
    k = len(x)
    z = w[k]
    for j in range(k):
        z += w[j] * x[j]
    return sigmoid(z)


def auc(scores, ys):
    pairs = sorted(zip(scores, ys))
    pos = sum(ys)
    neg = len(ys) - pos
    if not pos or not neg:
        return 0.5
    rank_sum, r = 0.0, 0
    while r < len(pairs):
        s = r
        while s + 1 < len(pairs) and pairs[s + 1][0] == pairs[r][0]:
            s += 1
        avg_rank = (r + s) / 2 + 1
        for t in range(r, s + 1):
            if pairs[t][1]:
                rank_sum += avg_rank
        r = s + 1
    return (rank_sum - pos * (pos + 1) / 2) / (pos * neg)


def tstat(xs):
    if len(xs) < 3:
        return 0.0
    sd = (sum((x - sum(xs) / len(xs)) ** 2 for x in xs) / (len(xs) - 1)) ** 0.5
    return (sum(xs) / len(xs)) / (sd / math.sqrt(len(xs))) if sd > 0 else 0.0


def by_date(rows):
    out = {}
    for r in rows:
        out.setdefault(r[0], []).append(r)
    return out


def bucket_report(train, test_dates):
    """Per feature, on the unseen period: how much stocks in its top / bottom fifth (cut-offs set on the training data)
    beat the average stock over the next week. t uses one value per week, so it is honest about overlap."""
    out = []
    for j, name in enumerate(NAMES):
        vals = sorted(r[2][j] for r in train)
        lo, hi = vals[len(vals) // 5], vals[(4 * len(vals)) // 5]
        top_w, bot_w = [], []
        for d, rs in test_dates.items():
            t = [r[5] for r in rs if r[2][j] >= hi]
            b = [r[5] for r in rs if r[2][j] <= lo]
            if t:
                top_w.append(sum(t) / len(t))
            if b:
                bot_w.append(sum(b) / len(b))
        th = sum(top_w) / len(top_w) if top_w else None
        bh = sum(bot_w) / len(bot_w) if bot_w else None
        tt, bt = tstat(top_w), tstat(bot_w)
        best_side = "high" if (th or -9) >= (bh or -9) else "low"
        out.append({"feature": name, "meaning": LABELS[name], "high_fifth_excess": th, "low_fifth_excess": bh,
                    "better_side": best_side, "best_excess": th if best_side == "high" else bh,
                    "t": tt if best_side == "high" else bt})
    out.sort(key=lambda r: -(r["best_excess"] or -9))
    return out


def fit_and_grade(rows, cfg, train_step=2, test_step=None):
    """Train on the older 2/3 of dates, grade on the newest 1/3 using one non-overlapping date per `horizon` days."""
    if len(rows) < 2000:
        return None
    hz = cfg["climb_horizon"]
    test_step = test_step or hz
    dates = sorted({r[0] for r in rows})
    cut = dates[(2 * len(dates)) // 3]
    gap_end = dates[min(len(dates) - 1, (2 * len(dates)) // 3 + hz)]
    train = [r for r in rows if r[0] < cut]
    test_days = sorted({r[0] for r in rows if r[0] > gap_end})[::test_step]
    keep = set(test_days)
    test = [r for r in rows if r[0] in keep]
    tr_sample = train[::train_step]
    mu, sd = standardise(tr_sample)
    X = [zrow(r[2], mu, sd) for r in tr_sample]
    y = [r[3] for r in tr_sample]
    w = train_logistic(X, y, epochs=cfg.get("climb_epochs", 40))
    scored = [(predict_p(w, zrow(r[2], mu, sd)), r) for r in test]
    a = auc([p for p, _ in scored], [r[3] for _, r in scored])
    # each unseen week: the model's top 10% of stocks vs that week's average stock
    weekly_top, weekly_hit, dec_sum, dec_n, cutoffs = [], [], [0.0] * 10, [0] * 10, []
    groups = {}
    for p, r in scored:
        groups.setdefault(r[0], []).append((p, r))
    for d, g in groups.items():
        g.sort(key=lambda x: -x[0])
        k = max(1, len(g) // 10)
        top = g[:k]
        weekly_top.append(sum(r[5] for _, r in top) / k)
        weekly_hit.append(sum(r[3] for _, r in top) / k)
        cutoffs.append(top[-1][0])
        for q in range(10):
            part = g[q * len(g) // 10:(q + 1) * len(g) // 10]
            if part:
                dec_sum[q] += sum(r[5] for _, r in part) / len(part)
                dec_n[q] += 1
    edge = sum(weekly_top) / len(weekly_top)
    t = tstat(weekly_top)
    hit = sum(weekly_hit) / len(weekly_hit)
    deciles = [dec_sum[q] / dec_n[q] if dec_n[q] else None for q in range(10)]
    works = t >= 2.0 and edge > 0.002 and a >= 0.52
    per = "%d days" % hz
    if works:
        verdict = "works: on weeks it never saw, its top picks beat the average stock by %+.2f%% per %s (t %.1f) and beat the median stock %.0f%% of the time" % (100 * edge, per, t, 100 * hit)
    elif edge > 0 and t >= 1.0:
        verdict = "promising but not proven: top picks beat the average stock by %+.2f%% per %s, t %.1f (needs 2)" % (100 * edge, per, t)
    else:
        verdict = "does not work: top picks did not beat the average stock (%+.2f%% per %s, t %.1f)" % (100 * edge, per, t)
    weights = sorted(({"feature": NAMES[j], "meaning": LABELS[NAMES[j]], "weight": w[j]} for j in range(len(NAMES))),
                     key=lambda r: -abs(r["weight"]))
    cutoffs.sort()
    return {
        "train": {"from": train[0][0], "to": train[-1][0], "samples": len(tr_sample)},
        "test": {"from": test_days[0] if test_days else None, "to": test_days[-1] if test_days else None,
                 "weeks": len(test_days), "samples": len(test)},
        "top10": {"excess": edge, "t": t, "beat_median_rate": hit, "weeks": len(weekly_top),
                  "p_cutoff": cutoffs[len(cutoffs) // 2] if cutoffs else 1.0},
        "deciles_best_to_worst": deciles, "auc": a, "works": works, "verdict": verdict,
        "weights": weights, "features": bucket_report(train, by_date(test)),
        "model": {"w": w, "mu": mu, "sd": sd, "names": NAMES},
    }


def score_today(data, idx, model, cfg, market, wt):
    idx_close = dict(zip(idx["d"], idx["c"])) if idx else {}
    out = []
    for s, b in data.items():
        ind = extra_indicators(b, wt)
        i = len(b["c"]) - 1
        if b["c"][i] < cfg["min_price"][market] or (ind["dv20"][i] or 0) < cfg["min_dollar_volume"][market]:
            continue
        f = features_at(b, ind, i, idx_close)
        if f is None:
            continue
        p = predict_p(model["w"], zrow(f, model["mu"], model["sd"]))
        out.append({"symbol": s, "p": p, "date": b["d"][i], "close": b["c"][i],
                    "why": explain(model, f)})
    out.sort(key=lambda r: -r["p"])
    return out


def explain(model, f):
    """The three features pushing this stock's score up the most."""
    z = zrow(f, model["mu"], model["sd"])
    contrib = sorted(((model["w"][j] * z[j], NAMES[j], f[j]) for j in range(len(z))), reverse=True)[:3]
    return [{"feature": n, "meaning": LABELS[n], "value": v, "push": c} for c, n, v in contrib if c > 0]
