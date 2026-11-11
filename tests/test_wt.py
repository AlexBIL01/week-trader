"""Offline tests for the Week Trader engine. Run: python3 -m unittest discover -s tests -v"""
import datetime as dt
import importlib.util
import io
import json
import math
import os
import random
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

HERE = os.path.dirname(os.path.abspath(__file__))
ENGINE = os.path.join(HERE, "..", "engine", "wt.py")


def dumpj(obj, p):
    with open(p, "w") as f:
        json.dump(obj, f)


def load_engine(home, offline=None):
    os.environ["WT_HOME"] = home
    if offline:
        os.environ["WT_OFFLINE_DIR"] = offline
    else:
        os.environ.pop("WT_OFFLINE_DIR", None)
    spec = importlib.util.spec_from_file_location("wt_%d" % random.randrange(10**9), ENGINE)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def flat_bars(n, price=100.0, vol=1_000_000, start=dt.date(2020, 1, 1)):
    d, out = start, {"d": [], "o": [], "h": [], "l": [], "c": [], "v": []}
    while len(out["d"]) < n:
        if d.weekday() < 5:
            out["d"].append(d.isoformat())
            out["o"].append(price); out["h"].append(price + 1); out["l"].append(price - 1)
            out["c"].append(price); out["v"].append(vol)
        d += dt.timedelta(days=1)
    return out


def random_walk(n, seed, drift=0.0004, vol=0.02, price=50.0):
    rnd = random.Random(seed)
    b = flat_bars(n, price)
    p = price
    for i in range(n):
        o = p * (1 + rnd.gauss(0, vol / 3))
        c = o * (1 + rnd.gauss(drift, vol))
        h = max(o, c) * (1 + abs(rnd.gauss(0, vol / 2)))
        l = min(o, c) * (1 - abs(rnd.gauss(0, vol / 2)))
        b["o"][i], b["h"][i], b["l"][i], b["c"][i] = o, h, l, c
        b["v"][i] = int(1_000_000 * math.exp(rnd.gauss(0, 0.5)))
        p = c
    return b


class Base(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp()
        self.wt = load_engine(self.home)
        self.cfg = self.wt.load_config()
        self.cfg["aud_per_usd"] = 1.5

    def tearDown(self):
        shutil.rmtree(self.home, ignore_errors=True)


class Indicators(Base):
    def test_sma(self):
        self.assertEqual(self.wt.sma([1, 2, 3, 4, 5], 3), [None, None, 2, 3, 4])

    def test_atr_constant_range(self):
        b = flat_bars(30)
        a = self.wt.atr(b["h"], b["l"], b["c"], 14)
        self.assertIsNone(a[12])
        self.assertAlmostEqual(a[13], 2.0)
        self.assertAlmostEqual(a[-1], 2.0)

    def test_rsi_extremes(self):
        up = [float(i) for i in range(1, 20)]
        self.assertEqual(self.wt.rsi(up, 2)[-1], 100.0)
        down = [float(20 - i) for i in range(20)]
        self.assertAlmostEqual(self.wt.rsi(down, 2)[-1], 0.0)


class Simulate(Base):
    def make(self, after):
        """250 flat bars at 100 (ATR 2), signal on bar 249, then the given (o,h,l,c) bars."""
        b = flat_bars(250 + len(after))
        for k, (o, h, l, c) in enumerate(after):
            i = 250 + k
            b["o"][i], b["h"][i], b["l"][i], b["c"][i] = o, h, l, c
        self.cfg["costs"]["US"] = {"commission": 0, "slippage_bps": 0}
        return b, self.wt.indicators(b)

    def test_stop_hit(self):
        b, ind = self.make([(100, 101, 96, 97), (97, 98, 96, 97)])
        t = self.wt.simulate(b, ind, 249, "breakout", self.cfg, "US")
        self.assertEqual(t["reason"], "stop")
        self.assertAlmostEqual(t["exit"], 97.0)          # 100 - 1.5 * 2
        self.assertAlmostEqual(t["R"], -1.0)

    def test_target_hit(self):
        b, ind = self.make([(100, 103, 99, 102), (102, 107, 101, 106)])
        t = self.wt.simulate(b, ind, 249, "breakout", self.cfg, "US")
        self.assertEqual(t["reason"], "target")
        self.assertAlmostEqual(t["exit"], 106.0)         # 100 + 3 * 2
        self.assertAlmostEqual(t["R"], 2.0)

    def test_gap_through_stop_fills_at_open(self):
        b, ind = self.make([(100, 101, 99, 100), (94, 95, 93, 94)])
        t = self.wt.simulate(b, ind, 249, "breakout", self.cfg, "US")
        self.assertEqual(t["reason"], "stop (gap)")
        self.assertAlmostEqual(t["exit"], 94.0)
        self.assertLess(t["R"], -1.0)

    def test_both_touched_same_day_counts_as_stop(self):
        b, ind = self.make([(100, 110, 90, 100)])
        t = self.wt.simulate(b, ind, 249, "breakout", self.cfg, "US")
        self.assertEqual(t["reason"], "stop")

    def test_time_exit_after_hold_days(self):
        b, ind = self.make([(100, 101, 99, 101)] * 6)
        t = self.wt.simulate(b, ind, 249, "breakout", self.cfg, "US")
        self.assertEqual(t["reason"], "time (end of week)")
        self.assertEqual(t["days"], 5)
        self.assertEqual(t["exit_date"], b["d"][254])

    def test_costs_reduce_return(self):
        b, ind = self.make([(100, 101, 99, 101)] * 6)
        t0 = self.wt.simulate(b, ind, 249, "breakout", self.cfg, "US")
        self.cfg["costs"]["US"] = {"commission": 5, "slippage_bps": 10}
        t1 = self.wt.simulate(b, ind, 249, "breakout", self.cfg, "US")
        self.assertLess(t1["ret"], t0["ret"])

    def test_pullback_exits_above_sma5(self):
        b, ind = self.make([(100, 101, 99, 99.5), (99.5, 102, 99.5, 101.5)])
        t = self.wt.simulate(b, ind, 249, "pullback", self.cfg, "US")
        self.assertEqual(t["reason"], "back above 5-day avg")


class Signals(Base):
    def test_breakout_fires(self):
        b = flat_bars(260)
        for i in range(200, 259):
            b["c"][i] = b["o"][i] = 100 + (i - 200) * 0.05
            b["h"][i], b["l"][i] = b["c"][i] + 0.5, b["c"][i] - 0.5
        i = 259
        b["o"][i], b["l"][i], b["h"][i], b["c"][i], b["v"][i] = 103, 102.8, 106, 105.8, 3_000_000
        ind = self.wt.indicators(b)
        self.assertIn("breakout", self.wt.signals_at(b, ind, i, self.cfg, "US", None))

    def test_regime_filter_blocks_breakout(self):
        b = flat_bars(260)
        i = 259
        b["o"][i], b["l"][i], b["h"][i], b["c"][i], b["v"][i] = 100, 100, 106, 105.8, 3_000_000
        ind = self.wt.indicators(b)
        self.assertNotIn("breakout", self.wt.signals_at(b, ind, i, self.cfg, "US", {b["d"][i]: False}))

    def test_liquidity_filter(self):
        b = flat_bars(260, vol=1000)
        b["o"][259], b["h"][259], b["c"][259], b["v"][259] = 100, 106, 105.8, 3000
        ind = self.wt.indicators(b)
        self.assertEqual(self.wt.signals_at(b, ind, 259, self.cfg, "US", None), [])


class Momentum(Base):
    def test_week_ends(self):
        days = ["2026-11-09", "2026-11-10", "2026-11-13", "2026-11-16", "2026-11-20"]
        self.assertEqual(self.wt.week_ends(days), {"2026-11-13", "2026-11-20"})
        self.assertEqual(self.wt.week_ends(days[:4]), {"2026-11-13"})   # last day is a Monday: not a week end yet

    def test_leaders_pick_strongest(self):
        data = {}
        for k, s in enumerate(["AAA", "BBB", "CCC"]):
            b = flat_bars(300)
            for i in range(300):
                b["c"][i] = b["o"][i] = 50 * (1 + 0.001 * (k + 1)) ** i
                b["h"][i], b["l"][i] = b["c"][i] * 1.01, b["c"][i] * 0.99
            data[s] = b
        self.cfg["momentum_top"] = 2
        lead = self.wt.momentum_leaders(data, self.cfg, "US", data["AAA"]["d"])
        last_week_end = max(lead)
        self.assertEqual(lead[last_week_end], {"BBB", "CCC"})

    def test_momentum_holds_to_time_exit_no_target(self):
        b = flat_bars(260)
        for i in range(250, 260):
            b["o"][i], b["h"][i], b["l"][i], b["c"][i] = 100 + i - 250, 120, 99, 101 + i - 250
        self.cfg["costs"]["US"] = {"commission": 0, "slippage_bps": 0}
        t = self.wt.simulate(b, self.wt.indicators(b), 249, "momentum", self.cfg, "US")
        self.assertEqual(t["reason"], "time (end of week)")
        self.assertIsNone(t["target"])

    def test_month_ends(self):
        days = ["2026-10-29", "2026-10-30", "2026-11-02", "2026-11-27", "2026-11-30"]
        self.assertEqual(self.wt.month_ends(days), {"2026-10-30", "2026-11-30"})   # 30 Nov 2026 is a Monday, next weekday is December

    def test_momentum_month_holds_21_days(self):
        b = flat_bars(280)
        self.cfg["costs"]["US"] = {"commission": 0, "slippage_bps": 0}
        t = self.wt.simulate(b, self.wt.indicators(b), 249, "momentum_month", self.cfg, "US")
        self.assertEqual(t["days"], 21)
        self.assertEqual(t["reason"], "time (end of month)")
        self.assertAlmostEqual(t["stop"], 90.0)   # 100 - 5 x ATR 2

    def test_random_data_has_no_edge(self):
        """Pure random walks must not produce an 'edge' verdict for any setup."""
        data = {"S%02d" % k: random_walk(1260, seed=1000 + k, drift=0.0006) for k in range(30)}
        idx = random_walk(1260, seed=999, drift=0.0006)
        self.cfg["min_dollar_volume"] = {"US": 1, "ASX": 1}
        res = self.wt.backtest_market("US", self.cfg, data, idx)
        for name, sres in res["setups"].items():
            self.assertFalse(sres["verdict"].startswith("edge"), "%s: %s" % (name, sres["verdict"]))

    def test_beats_baseline_rule(self):
        st = {"n": 100, "avg_R": 0.3, "profit_factor": 1.5, "avg_ret": 0.001}
        self.assertTrue(self.wt.verdict(st, {"n": 60, "avg_R": 0.2}, {"n": 40, "avg_R": 0.2}).startswith("edge"))


def planted_walk(n, seed):
    """Random walk where a volume spike is followed by a few days of extra climb: a real, findable warning sign."""
    rnd = random.Random(seed)
    b = flat_bars(n, 50)
    p, boost = 50.0, 0.0
    for i in range(n):
        spike = rnd.random() < 0.04
        o = p * (1 + rnd.gauss(0, 0.005))
        c = o * (1 + rnd.gauss(0.0003 + boost, 0.02))
        b["o"][i], b["c"][i] = o, c
        b["h"][i] = max(o, c) * (1 + abs(rnd.gauss(0, 0.01)))
        b["l"][i] = min(o, c) * (1 - abs(rnd.gauss(0, 0.01)))
        b["v"][i] = int(1e6 * math.exp(rnd.gauss(0, 0.3)) * (4 if spike else 1))
        boost = 0.012 if spike else max(0.0, boost - 0.003)
        p = c
    return b


class ClimbPredictor(Base):
    def setUp(self):
        super().setUp()
        self.cfg["min_dollar_volume"] = {"US": 1, "ASX": 1}
        self.pm = self.wt._predict_mod()

    def grade(self, gen, seed):
        data = {"S%02d" % k: gen(1260, seed * 100 + k) for k in range(40)}
        idx = random_walk(1260, seed + 5000, drift=0.0004)
        return self.pm.fit_and_grade(self.pm.build_rows(data, idx, self.cfg, "US", self.wt), self.cfg)

    def test_random_data_is_not_predictable(self):
        for seed in (0, 1, 2):
            res = self.grade(lambda n, s: random_walk(n, s, drift=0.0004), seed)
            self.assertFalse(res["works"], res["verdict"])

    def test_planted_signal_is_found(self):
        res = self.grade(planted_walk, 0)
        self.assertTrue(res["works"], res["verdict"])
        self.assertIn(res["features"][0]["feature"], ("vol_trend", "rel_vol"))
        self.assertGreater(res["deciles_best_to_worst"][0], res["deciles_best_to_worst"][-1])

    def test_rolling_max(self):
        self.assertEqual(self.pm.rolling_max([1, 3, 2, 5, 4, 1], 3), [None, None, 3, 5, 5, 5])

    def test_auc(self):
        self.assertAlmostEqual(self.pm.auc([0.1, 0.4, 0.35, 0.8], [0, 0, 1, 1]), 0.75)


class Forward(Base):
    def test_only_new_days_count_and_planted_volume_shows(self):
        self.cfg["min_dollar_volume"] = {"US": 1, "ASX": 1}
        data = {}
        for k in range(40):
            b = planted_walk(1300, 300 + k)
            b2 = flat_bars(1300, start=dt.date(2022, 6, 1))     # same bars, dates ending well after 2026-11-11
            b["d"] = b2["d"]
            data["S%02d" % k] = b
        idx = random_walk(1300, 77, drift=0.0004)
        idx["d"] = data["S00"]["d"]
        rule = dict(self.wt.FORWARD_RULES[0])
        r = self.wt.forward_eval(rule, data, idx, self.cfg)
        self.assertTrue(all(w["date"] > rule["registered"] for w in r["weeks"]))
        self.assertGreater(r["n"], 20)
        self.assertGreater(r["mean"], 0)          # volume spikes were planted to precede climbs
        self.assertTrue(r["today"])


def fake_form345(path, rows):
    """rows: (acc, ticker, filed 'DD-MON-YYYY', relationship, code, shares, price)"""
    import zipfile
    sub = ["ACCESSION_NUMBER\tFILING_DATE\tDOCUMENT_TYPE\tISSUERCIK\tISSUERNAME\tISSUERTRADINGSYMBOL"]
    own = ["ACCESSION_NUMBER\tRPTOWNERCIK\tRPTOWNERNAME\tRPTOWNER_RELATIONSHIP"]
    tr = ["ACCESSION_NUMBER\tTRANS_DATE\tTRANS_CODE\tTRANS_SHARES\tTRANS_PRICEPERSHARE\tTRANS_ACQUIRED_DISP_CD"]
    for k, (acc, t, filed, rel, code, sh, px) in enumerate(rows):
        sub.append("%s\t%s\t4\t1\t%s Inc\t%s" % (acc, filed, t, t))
        own.append("%s\t%d\tPerson %d\t%s" % (acc, 100 + k, k, rel))
        tr.append("%s\t%s\t%s\t%s\t%s\tA" % (acc, filed, code, sh, px))
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("SUBMISSION.tsv", "\n".join(sub) + "\n")
        z.writestr("REPORTINGOWNER.tsv", "\n".join(own) + "\n")
        z.writestr("NONDERIV_TRANS.tsv", "\n".join(tr) + "\n")


class Insider(Base):
    def setUp(self):
        super().setUp()
        self.ins = self.wt._insider_mod()

    def test_parse_and_group(self):
        zp = os.path.join(self.home, "q.zip")
        fake_form345(zp, [
            ("a1", "ABC", "15-MAR-2024", "Director", "P", 1000, 30),     # 30k, director: kept
            ("a2", "ABC", "15-MAR-2024", "Officer", "P", 500, 30),       # same day: grouped, 2 insiders
            ("a3", "XYZ", "16-MAR-2024", "TenPercentOwner", "P", 9999, 50),  # fund, not an insider: dropped
            ("a4", "QQQ", "17-MAR-2024", "Director", "S", 5000, 50),     # a sale: dropped
            ("a5", "TIN", "18-MAR-2024", "Director", "P", 100, 20),      # 2k: below the minimum
        ])
        ps = self.ins.purchases_from_zip(zp)
        self.assertEqual(sorted({p["ticker"] for p in ps}), ["ABC", "TIN"])
        ev = self.ins.events_from_purchases(ps, 25000, 3)
        self.assertEqual(len(ev), 1)
        self.assertEqual(ev[0]["ticker"], "ABC")
        self.assertEqual(ev[0]["n_insiders"], 2)
        self.assertAlmostEqual(ev[0]["value"], 45000)
        self.assertEqual(ev[0]["filed"], "2024-03-15")

    def test_measure_finds_planted_drift(self):
        spy = flat_bars(400, 100)
        prices, events = {}, []
        rnd = random.Random(3)
        for k in range(60):
            b = random_walk(400, 50 + k, drift=0.0, vol=0.01)
            f = rnd.randrange(30, 360)
            for j in range(f + 1, min(f + 22, 400)):            # after the filing: +0.5% a day for 21 days
                for key in ("o", "h", "l", "c"):
                    b[key][j] *= 1.005 ** (j - f)
            for j in range(f + 22, 400):
                for key in ("o", "h", "l", "c"):
                    b[key][j] *= 1.005 ** 21
            b["v"] = [10 ** 6] * 400
            prices["T%02d" % k] = b
            events.append({"ticker": "T%02d" % k, "filed": b["d"][f], "issuer": "x", "value": 50000, "n_insiders": 1, "avg_price": 50})
        rows = self.ins.measure(events, prices, spy)
        s21 = self.ins.summarise(rows, 21)
        self.assertGreater(s21["avg_excess"], 0.05)
        self.assertGreater(s21["t"], 2)

    def test_quarters_back(self):
        self.assertEqual(self.ins.quarters_back(2, dt.date(2026, 11, 11)), ["2026q4", "2026q3", "2026q2"])

    def test_cli_offline(self):
        off = tempfile.mkdtemp()
        os.environ["WT_OFFLINE_DIR"] = off
        try:
            wt = load_engine(self.home, off)
            with open(os.path.join(self.home, "config.json"), "w") as f:
                json.dump({"sec_user_agent": "Test Person test@example.com", "insider_min_dollar_volume": 1}, f)
            os.makedirs(os.path.join(self.home, "sec"), exist_ok=True)
            spy = flat_bars(1300, 100, start=dt.date(2021, 9, 1))
            dumpj(spy, os.path.join(off, "SPY.json"))
            rows = []
            for k in range(30):
                b = random_walk(1300, k, drift=0.0003)
                b["d"] = spy["d"]
                dumpj(b, os.path.join(off, "T%02d.json" % k))
                d = dt.date.fromisoformat(spy["d"][1100 + k])
                rows.append(("acc%d" % k, "T%02d" % k, d.strftime("%d-%b-%Y").upper(), "Director", "P", 2000, 40))
            qs = wt._insider_mod().quarters_back(12)
            fake_form345(os.path.join(self.home, "sec", qs[2] + ".zip"), rows)
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = wt.main(["insider"])
            self.assertEqual(code, 0)
            self.assertIn("Insider buying test", buf.getvalue())
        finally:
            os.environ.pop("WT_OFFLINE_DIR", None)
            shutil.rmtree(off, ignore_errors=True)


class Sizing(Base):
    def test_risk_sizing_aud_account_us_stock(self):
        # A$10,000, 1% = A$100 risk. Entry 50, stop 48 -> US$2/share = A$3/share -> 33 shares.
        s = self.wt.size_trade("US", 50, 48, self.cfg, fx=1.5)
        self.assertEqual(s["shares"], 33)
        self.assertLessEqual(s["risk_aud"], 100)

    def test_position_cap(self):
        s = self.wt.size_trade("ASX", 10, 9.95, self.cfg, fx=1.0)   # would be 2000 shares = A$20k
        self.assertEqual(s["shares"], 250)                          # 25% of A$10k / A$10
        self.assertTrue(any("capped" in n for n in s["notes"]))

    def test_asx_min_parcel_note(self):
        s = self.wt.size_trade("ASX", 40, 0.5, self.cfg, fx=1.0)
        self.assertTrue(any("$500" in n for n in s["notes"]))

    def test_sell_by_skips_weekend(self):
        self.assertEqual(self.wt.add_trading_days(dt.date(2026, 11, 13), 1), dt.date(2026, 11, 16))  # Fri -> Mon
        self.assertEqual(self.wt.add_trading_days(dt.date(2026, 11, 16), 4), dt.date(2026, 11, 20))  # Mon -> Fri


class EndToEnd(unittest.TestCase):
    """Whole pipeline on simulated stocks, no network."""

    @classmethod
    def setUpClass(cls):
        cls.home = tempfile.mkdtemp()
        cls.off = tempfile.mkdtemp()
        wt = load_engine(cls.home, cls.off)
        for m, syms in (("US", wt.UNIVERSE["US"][:25] + ["SPY"]), ("ASX", wt.UNIVERSE["ASX"][:20] + ["^AXJO"])):
            for k, s in enumerate(syms):
                b = random_walk(1260, seed=hash((m, s)) % 10**6, drift=0.0006, price=60 if m == "US" else 25)
                with open(os.path.join(cls.off, wt.yahoo_symbol(s, m) + ".json"), "w") as f:
                    json.dump(b, f)
        cfg = wt.load_config()
        cfg["aud_per_usd"] = 1.5
        cfg["min_dollar_volume"] = {"US": 1, "ASX": 1}
        os.makedirs(cls.home, exist_ok=True)
        with open(os.path.join(cls.home, "config.json"), "w") as f:
            json.dump(cfg, f)
        cls.wt = wt

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.home, ignore_errors=True)
        shutil.rmtree(cls.off, ignore_errors=True)
        os.environ.pop("WT_OFFLINE_DIR", None)

    def run_cli(self, *args):
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = self.wt.main(list(args))
        return code, buf.getvalue()

    def test_1_backtest(self):
        code, out = self.run_cli("backtest", "--market", "both")
        self.assertEqual(code, 0)
        self.assertIn("breakout", out)
        self.assertIn("Baseline", out)
        with open(os.path.join(self.home, "stats_US.json")) as f:
            st = json.load(f)
        self.assertGreater(st["setups"]["pullback"]["all"]["n"], 30)
        self.assertGreater(st["setups"]["momentum"]["all"]["n"], 30)
        self.assertGreater(st["setups"]["momentum_month"]["all"]["n"], 30)
        self.assertIn("avg_ret", st["baseline_month"])

    def test_2_week_report(self):
        code, out = self.run_cli("week")
        self.assertEqual(code, 0)
        self.assertIn("Best trades to buy this week", out)
        self.assertTrue(os.path.exists(os.path.join(self.home, "reports")))

    def test_3_plan(self):
        code, out = self.run_cli("plan", "AAPL", "--setup", "breakout")
        self.assertEqual(code, 0)
        self.assertIn("stop", out)

    def test_3b_predict(self):
        code, out = self.run_cli("predict", "--market", "US", "--top", "3")
        self.assertEqual(code, 0)
        self.assertIn("Climb predictor", out)
        self.assertIn("Verdict", out)

    def test_3c_predict_one_symbol(self):
        code, out = self.run_cli("predict", "AAPL")
        self.assertEqual(code, 0)
        self.assertIn("Model score", out)
        self.assertIn("Rank:", out)

    def test_4_journal_round_trip(self):
        code, out = self.run_cli("journal", "add", "BHP", "--market", "ASX", "--setup", "breakout",
                                 "--entry", "25", "--shares", "40", "--stop", "24", "--target", "27", "--date", "2026-11-16")
        self.assertEqual(code, 0)
        code, out = self.run_cli("journal", "review")
        self.assertIn("BHP", out)
        code, out = self.run_cli("journal", "close", "--id", "1", "--exit", "26.5")
        self.assertIn("A$50.00", out)   # 1.5 x 40 = 60 - $5 x 2 commission
        code, out = self.run_cli("journal", "stats")
        self.assertIn("1 trades", out)


if __name__ == "__main__":
    unittest.main()
