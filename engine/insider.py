"""Insider-buying test for Week Trader (US). Standard library only.

Source: the SEC's quarterly Insider Transactions Data Sets (Forms 3/4/5, flattened to
tab-separated tables). An "event" is one or more open-market purchases (transaction
code P) of a company's shares by its own officers or directors, reported on the same
filing date, worth at least `insider_min_value` US dollars in total.

The test: buy at the open of the first trading day after the filing date, sell at the
close 5 and 21 trading days later, and compare with SPY over exactly the same days.
Events are grouped by the week they were filed and the t-stat uses one average per
week, so a busy week can't masquerade as many independent results. The older 2/3 of
weeks and the newest 1/3 are reported separately: a real effect shows up in both.
"""

import csv
import datetime as dt
import io
import math
import os
import time
import urllib.error
import urllib.request
import zipfile

BASES = [
    "https://www.sec.gov/files/structureddata/data/insider-transactions-data-sets/%s_form345.zip",
    "https://www.sec.gov/files/datastandardsinnovation/data/insider-transactions-data-sets/%s_form345.zip",
]
MONTHS = {m: i for i, m in enumerate(["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"], 1)}
BAD_TICKERS = {"", "NONE", "N/A", "NA", "NULL", "-", "--"}


class InsiderError(Exception):
    pass


def parse_date(s):
    s = (s or "").strip()
    if not s:
        return None
    try:
        if len(s) == 11 and s[2] == "-" and s[6] == "-":  # 15-MAR-2024
            return dt.date(int(s[7:]), MONTHS[s[3:6].upper()], int(s[:2]))
        return dt.date.fromisoformat(s[:10])
    except (ValueError, KeyError):
        return None


def quarters_back(n, today=None):
    today = today or dt.date.today()
    y, q = today.year, (today.month - 1) // 3 + 1
    out = []
    for _ in range(n + 1):  # +1: the current quarter is usually not published yet
        out.append("%dq%d" % (y, q))
        q -= 1
        if q == 0:
            y, q = y - 1, 4
    return out


def download_quarter(q, folder, ua):
    os.makedirs(folder, exist_ok=True)
    p = os.path.join(folder, q + ".zip")
    if os.path.exists(p) and os.path.getsize(p) > 1000:
        return p
    last = None
    for base in BASES:
        req = urllib.request.Request(base % q, headers={"User-Agent": ua, "Accept-Encoding": "identity"})
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                body = r.read()
            if not body.startswith(b"PK"):
                last = "not a zip"
                continue
            with open(p + ".tmp", "wb") as f:
                f.write(body)
            os.replace(p + ".tmp", p)
            time.sleep(0.5)
            return p
        except urllib.error.HTTPError as e:
            last = "HTTP %s" % e.code
            if e.code == 403:
                raise InsiderError("SEC refused the download (HTTP 403). Set sec_user_agent in weektrader/config.json "
                                   "to your name and email, e.g. \"Jane Citizen jane@example.com\".")
        except (urllib.error.URLError, OSError) as e:
            last = str(e)
    return None if last and last.startswith("HTTP 404") else _raise(q, last)


def _raise(q, why):
    raise InsiderError("%s: download failed (%s)" % (q, why))


def _table(z, name):
    """Open the member whose file name starts with `name` (e.g. SUBMISSION.tsv)."""
    for info in z.infolist():
        base = os.path.basename(info.filename).upper()
        if base.startswith(name + ".") or base == name:
            return csv.DictReader(io.TextIOWrapper(z.open(info), encoding="utf-8", errors="replace"),
                                  delimiter="\t", quoting=csv.QUOTE_NONE)
    raise InsiderError("table %s missing from %s" % (name, z.filename))


def purchases_from_zip(path):
    """Open-market purchases by officers or directors: list of dicts."""
    with zipfile.ZipFile(path) as z:
        subs = {}
        for r in _table(z, "SUBMISSION"):
            if (r.get("DOCUMENT_TYPE") or "").strip() not in ("4", "4/A"):
                continue
            t = (r.get("ISSUERTRADINGSYMBOL") or "").strip().upper()
            fd = parse_date(r.get("FILING_DATE"))
            if t in BAD_TICKERS or not fd or " " in t or len(t) > 6:
                continue
            subs[r["ACCESSION_NUMBER"]] = (t, fd, (r.get("ISSUERNAME") or "").strip())
        roles = {}
        for r in _table(z, "REPORTINGOWNER"):
            acc = r.get("ACCESSION_NUMBER")
            if acc not in subs:
                continue
            rel = (r.get("RPTOWNER_RELATIONSHIP") or "").upper()
            if "OFFICER" in rel or "DIRECTOR" in rel:
                roles.setdefault(acc, set()).add((r.get("RPTOWNERCIK") or "").strip())
        out = []
        for r in _table(z, "NONDERIV_TRANS"):
            acc = r.get("ACCESSION_NUMBER")
            if acc not in subs or acc not in roles:
                continue
            if (r.get("TRANS_CODE") or "").strip().upper() != "P" or (r.get("TRANS_ACQUIRED_DISP_CD") or "").strip().upper() != "A":
                continue
            try:
                sh = float(r.get("TRANS_SHARES") or 0)
                px = float(r.get("TRANS_PRICEPERSHARE") or 0)
            except ValueError:
                continue
            if sh <= 0 or px <= 0:
                continue
            t, fd, name = subs[acc]
            out.append({"ticker": t, "filed": fd.isoformat(), "trade_date": (parse_date(r.get("TRANS_DATE")) or fd).isoformat(),
                        "value": sh * px, "price": px, "insiders": sorted(roles[acc]), "issuer": name, "acc": acc})
        return out


def events_from_purchases(purchases, min_value, min_price):
    """Group by (ticker, filing date)."""
    g = {}
    for p in purchases:
        k = (p["ticker"], p["filed"])
        e = g.setdefault(k, {"ticker": p["ticker"], "filed": p["filed"], "issuer": p["issuer"], "value": 0.0,
                             "insiders": set(), "prices": []})
        e["value"] += p["value"]
        e["insiders"].update(p["insiders"])
        e["prices"].append(p["price"])
    out = []
    for e in g.values():
        avg_px = sum(e["prices"]) / len(e["prices"])
        if e["value"] < min_value or avg_px < min_price:
            continue
        out.append({"ticker": e["ticker"], "filed": e["filed"], "issuer": e["issuer"], "value": round(e["value"], 2),
                    "n_insiders": len(e["insiders"]), "avg_price": round(avg_px, 4)})
    out.sort(key=lambda e: (e["filed"], e["ticker"]))
    return out


def tstat(xs):
    n = len(xs)
    if n < 3:
        return 0.0
    m = sum(xs) / n
    sd = math.sqrt(sum((x - m) ** 2 for x in xs) / (n - 1))
    return m / (sd / math.sqrt(n)) if sd > 0 else 0.0


def measure(events, prices, spy, horizons=(5, 21), min_dollar_volume=1e6, sma=None):
    """Attach excess returns vs SPY to each event that has prices. Returns the events that could be measured."""
    spy_pos = {d: i for i, d in enumerate(spy["d"])}
    measured = []
    for e in events:
        b = prices.get(e["ticker"])
        if not b:
            continue
        ds = b["d"]
        # last bar on or before the filing date; entry is the next bar's open
        lo, hi = 0, len(ds) - 1
        i = -1
        while lo <= hi:
            mid = (lo + hi) // 2
            if ds[mid] <= e["filed"]:
                i, lo = mid, mid + 1
            else:
                hi = mid - 1
        if i < 20 or i + 1 >= len(ds):
            continue
        dv = sum(b["c"][k] * b["v"][k] for k in range(i - 19, i + 1)) / 20
        if dv < min_dollar_volume:
            continue
        entry = b["o"][i + 1]
        if entry <= 0:
            continue
        row = dict(e, entry_date=ds[i + 1], dollar_volume=dv)
        ok = False
        for h in horizons:
            j = i + h
            if j >= len(ds):
                continue
            si, sj = spy_pos.get(ds[i + 1]), spy_pos.get(ds[j])
            if si is None or sj is None:
                continue
            r = b["c"][j] / entry - 1
            m = spy["c"][sj] / spy["o"][si] - 1
            row["ret%d" % h] = r
            row["excess%d" % h] = r - m
            ok = True
        if ok:
            measured.append(row)
    return measured


def weekly(rows, key):
    wk = {}
    for r in rows:
        if key not in r:
            continue
        y, w, _ = dt.date.fromisoformat(r["filed"]).isocalendar()
        wk.setdefault((y, w), []).append(r[key])
    return [sum(v) / len(v) for _, v in sorted(wk.items())]


def summarise(rows, h):
    key = "excess%d" % h
    rs = [r for r in rows if key in r]
    w = weekly(rs, key)
    if not w:
        return {"events": 0, "weeks": 0}
    k = (2 * len(w)) // 3
    old, new = w[:k], w[k:]
    vals = [r[key] for r in rs]
    return {
        "events": len(rs), "weeks": len(w),
        "avg_excess": sum(vals) / len(vals), "beat_spy_rate": sum(1 for v in vals if v > 0) / len(vals),
        "weekly_avg": sum(w) / len(w), "t": tstat(w),
        "older_avg": sum(old) / len(old) if old else None, "older_t": tstat(old),
        "recent_avg": sum(new) / len(new) if new else None, "recent_t": tstat(new),
    }


def verdict(s):
    if s.get("weeks", 0) < 40:
        return "not enough events"
    if s["t"] >= 2 and (s["older_avg"] or 0) > 0 and (s["recent_avg"] or 0) > 0:
        return "works: insider-bought stocks beat SPY, in both the older and the recent part"
    if s["t"] >= 2:
        return "mixed: beat SPY overall but not in both halves"
    if s["weekly_avg"] > 0 and s["t"] >= 1:
        return "promising but not proven (t %.1f, needs 2)" % s["t"]
    return "does not work (t %.1f)" % s["t"]


def subgroups(rows, h):
    """Pre-specified splits from the research literature: several insiders at once, and smaller companies."""
    key = "excess%d" % h
    rs = [r for r in rows if key in r]
    if not rs:
        return {}
    dvs = sorted(r["dollar_volume"] for r in rs)
    med = dvs[len(dvs) // 2]
    groups = {
        "2+ insiders buying together": [r for r in rs if r["n_insiders"] >= 2],
        "one insider": [r for r in rs if r["n_insiders"] < 2],
        "smaller companies (below-median trading value)": [r for r in rs if r["dollar_volume"] < med],
        "larger companies": [r for r in rs if r["dollar_volume"] >= med],
        "big purchases (US$250k+)": [r for r in rs if r["value"] >= 250000],
    }
    return {k: summarise(v, h) for k, v in groups.items()}
