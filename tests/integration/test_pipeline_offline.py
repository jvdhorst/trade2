"""End-to-end ingest -> value -> report -> crosscheck with fake providers (no network)."""

from datetime import UTC, date

import pandas as pd
import pytest

from valuation.engine import run_book_value
from valuation.ingest.pipeline import ingest
from valuation.providers import yahoo as yahoo_mod
from valuation.qa.crosscheck import crosscheck_book_value
from valuation.report.screen import write_report
from valuation.store.db import connect

AS_OF = date(2026, 11, 9)
CORE = ("prices", "profiles", "yahoo_fundamentals", "edgar_filings")


def _core(stats):
    return {k: stats[k] for k in CORE}


def _ts(day: str) -> int:
    from datetime import datetime

    return int(datetime.fromisoformat(day).replace(hour=14, minute=30,
                                                   tzinfo=UTC).timestamp())


class FakeYahoo:
    def __init__(self, data):
        self.data = data
        self.calls = {"info": 0, "fundamentals": 0, "closes": 0}
        self.chart_calls: list[tuple[str, str]] = []
        self.dividends: dict[str, list[tuple[str, float]]] = {}

    def info(self, t):
        self.calls["info"] += 1
        return self.data[t]["info"]

    def fundamentals(self, t, ccy):
        self.calls["fundamentals"] += 1
        return yahoo_mod.parse_balance_sheet(t, self.data[t]["bs"], ccy)

    def closes(self, tickers):
        self.calls["closes"] += 1
        bars = []
        for t in tickers:  # "MISSING" tickers simulate symbols the batch omits
            if not self.data[t].get("batch_missing"):
                bars += yahoo_mod.parse_history(t, self.history(t))
        return bars

    def history(self, t):
        return [{"Date": "2026-11-06", "Close": self.data[t]["price"]},
                {"Date": "2026-11-12", "Close": 999.0}]  # after as_of: must be ignored

    def chart(self, t, range_):
        self.chart_calls.append((t, range_))
        days = ["2026-11-05", "2026-11-06", "2026-11-12"]  # the last is after as_of
        p = self.data[t]["price"]
        return {"chart": {"result": [{
            "meta": {"currency": self.data[t]["info"]["currency"]},
            "timestamp": [_ts(d) for d in days],
            "indicators": {"quote": [{"close": [p, p, 999.0], "volume": [1e6, 2e6, 0]}],
                           "adjclose": [{"adjclose": [p * 0.99, p, 999.0]}]},
            "events": {"dividends": {str(_ts(d)): {"amount": a, "date": _ts(d)}
                                     for d, a in self.dividends.get(t, [])}},
        }]}}


class FakeEdgar:
    def __init__(self, payloads):
        self.payloads = payloads

        self.accessions: dict[str, str] = {}
        self.fetched = 0

    def latest_accessions(self, as_of):
        return self.accessions

    def companyfacts(self, cik):
        self.fetched += 1
        return self.payloads[cik]


class FakeBoc:
    def usdcad(self, end):
        return {"observations": [{"d": "2026-11-06", "FXUSDCAD": {"v": "1.40"}}]}


class FakeRates:
    def __init__(self):
        self.calls = []

    def boc_10y(self, end, days):
        self.calls.append(("boc", days))
        return {"observations": [{"d": "2026-11-06", "BD.CDN.10YR.DQ.YLD": {"v": "3.25"}}]}

    def fred_10y(self, end, days):
        self.calls.append(("fred", days))
        return "observation_date,DGS10\n2026-11-05,4.10\n2026-11-06,.\n2026-11-10,4.20\n"


def _bs(period, equity, shares, pref=0.0):
    return {period: {"Stockholders Equity": equity, "Common Stock Equity": equity - pref,
                     "Total Equity Gross Minority Interest": equity,
                     "Ordinary Shares Number": shares}}


@pytest.fixture
def con(tmp_path):
    universe = tmp_path / "u.csv"
    universe.write_text(
        "ticker,exchange,cik,share_classes,share_conversion,gics_sector\n"
        "CAN.TO,TSX,,1,1,Financials\nUSR.TO,TSX,,1,1,\nUSA,NYSE,0000000001,1,1,\n"
        "BRK,NYSE,,2,1500,\n"
    )
    yahoo = FakeYahoo({
        "CAN.TO": {"info": {"currency": "CAD", "financialCurrency": "CAD", "sector": "S",
                            "bookValue": 10.0, "priceToBook": 1.5},
                   "bs": _bs("2026-07-31", 1100.0, 100.0, pref=100.0), "price": 15.0},
        "USR.TO": {"info": {"currency": "CAD", "financialCurrency": "USD", "sector": "S",
                            "bookValue": 10.0},
                   "bs": _bs("2026-06-30", 500.0, 50.0), "price": 21.0},
        "USA": {"info": {"currency": "USD", "financialCurrency": "USD", "sector": "S",
                         "bookValue": 20.0},
                "bs": _bs("2026-06-30", 1.0, 1.0), "price": 30.0},
        "BRK": {"info": {"currency": "USD", "financialCurrency": "USD", "sector": "S",
                         "bookValue": 3000.0},
                "bs": _bs("2026-06-30", 3000.0, 1.0), "price": 3.0},
    })
    edgar = FakeEdgar({"0000000001": {"facts": {"us-gaap": {
        "StockholdersEquity": {"units": {"USD": [
            {"val": 2000.0, "end": "2026-09-30", "accn": "Q3", "filed": "2026-11-01",
             "form": "10-Q"},
            {"val": 9999.0, "end": "2026-12-31", "accn": "K", "filed": "2027-02-01",
             "form": "10-K"}]}},  # filed after as_of: look-ahead, must be ignored
        "CommonStockSharesOutstanding": {"units": {"shares": [
            {"val": 100.0, "end": "2026-09-30", "accn": "Q3", "filed": "2026-11-01",
             "form": "10-Q"},
            {"val": 100.0, "end": "2026-12-31", "accn": "K", "filed": "2027-02-01",
             "form": "10-K"}]}},
    }, "dei": {}}}})
    yahoo.dividends = {"CAN.TO": [("2026-11-05", 0.5), ("2025-11-01", 9.0)]}  # 2nd: > 1y ago
    edgar.payloads["0000000001"]["facts"]["us-gaap"]["NetIncomeLoss"] = {"units": {"USD": [
        {"start": s_, "end": e_, "val": v_, "accn": a_, "filed": f_, "form": "10-Q"}
        for s_, e_, v_, a_, f_ in [
            ("2025-10-01", "2025-12-31", 10.0, "Q4", "2026-02-01"),
            ("2026-01-01", "2026-03-31", 20.0, "Q1", "2026-05-01"),
            ("2026-04-01", "2026-06-30", 30.0, "Q2", "2026-08-01"),
            ("2026-07-01", "2026-09-30", 40.0, "Q3", "2026-11-01"),
            ("2026-10-01", "2026-12-31", 99.0, "K", "2027-02-01"),  # filed after as_of
        ]]}}
    c = connect(":memory:")
    counts = []
    for _ in range(2):  # second ingest must be a no-op (idempotent upserts)
        res = ingest(c, universe, AS_OF, cache_root=tmp_path / "raw",
                     yahoo_provider=yahoo, edgar_provider=edgar, boc_provider=FakeBoc(),
                     rates_provider=FakeRates())
        assert res["errors"] == {}
        counts.append([c.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in
                       ("securities", "prices_daily", "fundamentals", "fx_daily", "dividends",
                        "rates_daily")])
    assert counts[0] == counts[1]
    return c


def test_values_and_sources(con, tmp_path):
    df, info = run_book_value(con, AS_OF, min_peers=1)
    assert info["skipped"] == {}
    r = df.set_index("ticker")
    # CAD reporter, preferreds removed: (1100-100)/100 = 10, price 15 -> P/B 1.5
    assert r.loc["CAN.TO", "bvps"] == pytest.approx(10.0)
    assert r.loc["CAN.TO", "pb"] == pytest.approx(1.5)
    # USD reporter listed in CAD: 500/50 = 10 USD * 1.40 = 14 CAD
    assert r.loc["USR.TO", "bvps"] == pytest.approx(14.0)
    assert "FX_CONVERTED" in r.loc["USR.TO", "flags"]
    # EDGAR Q3 is fresher than Yahoo Q2 and wins; the 10-K filed after as_of is ignored
    assert r.loc["USA", "source"] == "sec_edgar"
    assert r.loc["USA", "bvps"] == pytest.approx(20.0)
    assert r.loc["USA", "price"] == 30.0  # the post-as_of price is ignored
    # Class conversion: 3000 / (1 reported share * 1500) = 2 per listed share
    assert r.loc["BRK", "bvps"] == pytest.approx(2.0)

    csv_path, pq_path = write_report(df, AS_OF, tmp_path / "reports")
    back = pd.read_parquet(pq_path)
    assert list(back["ticker"]) == list(df["ticker"])
    assert csv_path.read_text().startswith("# book_value model")

    cc = crosscheck_book_value(con, AS_OF).set_index("ticker")
    assert cc.loc["CAN.TO", "pass"] and cc.loc["USA", "pass"] and cc.loc["BRK", "pass"]
    # Yahoo BV for USR.TO is in USD; we match after undoing the FX conversion
    assert cc.loc["USR.TO", "d_bvps_rep_ccy"] == pytest.approx(0.0)


def test_valuation_rows_persisted_with_inputs(con):
    run_book_value(con, AS_OF, min_peers=1)
    n, versions = con.execute(
        "SELECT count(*), list(DISTINCT model_version) FROM valuations").fetchone()
    assert n == 4 and versions == ["book_value/0.2.0"]
    nulls = con.execute("SELECT count(*) FROM valuations WHERE inputs_json IS NULL").fetchone()
    assert nulls[0] == 0



def test_peer_sector_prefers_gics_over_yahoo(con):
    df, _ = run_book_value(con, AS_OF, min_peers=1)
    sectors = df.set_index("ticker")["sector"]
    assert sectors["CAN.TO"] == "Financials"  # GICS from the universe file
    assert sectors["USA"] == "S"  # no GICS: Yahoo sector


def test_universe_filter_and_coverage(con):
    df, info = run_book_value(con, AS_OF, min_peers=1, tickers={"CAN.TO", "USA", "GHOST"})
    assert set(df["ticker"]) == {"CAN.TO", "USA"}
    cov = info["coverage"].set_index("ticker")
    assert cov.loc["GHOST", "status"] == "skipped" and cov.loc["GHOST", "reason"] == "not ingested"
    assert cov.loc["USA", "source"] == "sec_edgar" and not cov.loc["USA", "filed_estimated"]
    assert cov.loc["CAN.TO", "filed_estimated"]


# ---------------------------------------------------------------------------
# Incremental refresh policy
# ---------------------------------------------------------------------------

def _two_ticker_setup(tmp_path):
    universe = tmp_path / "u2.csv"
    universe.write_text("ticker,exchange,cik,share_classes,share_conversion,gics_sector\n"
                        "AAA.TO,TSX,,1,1,Energy\nUSB,NYSE,0000000002,1,1,Financials\n")
    yahoo = FakeYahoo({
        "AAA.TO": {"info": {"currency": "CAD", "financialCurrency": "CAD", "sector": "E"},
                   "bs": _bs("2026-09-30", 100.0, 10.0), "price": 12.0},
        "USB": {"info": {"currency": "USD", "financialCurrency": "USD", "sector": "F"},
                "bs": _bs("2026-09-30", 50.0, 5.0), "price": 20.0, "batch_missing": True},
    })
    edgar = FakeEdgar({"0000000002": {"facts": {"us-gaap": {
        "StockholdersEquity": {"units": {"USD": [
            {"val": 60.0, "end": "2026-09-30", "accn": "A1", "filed": "2026-10-30",
             "form": "10-Q"}]}},
        "CommonStockSharesOutstanding": {"units": {"shares": [
            {"val": 5.0, "end": "2026-09-30", "accn": "A1", "filed": "2026-10-30",
             "form": "10-Q"}]}}}, "dei": {}}}})
    edgar.accessions = {"0000000002": "A1"}
    return universe, yahoo, edgar


def _run(c, universe, yahoo, edgar, tmp_path, when, **kw):
    return ingest(c, universe, AS_OF, cache_root=tmp_path / "raw", yahoo_provider=yahoo,
                  edgar_provider=edgar, boc_provider=FakeBoc(), rates_provider=FakeRates(),
                  now=when, **kw)


def test_second_run_only_fetches_prices(tmp_path):
    from datetime import datetime

    universe, yahoo, edgar = _two_ticker_setup(tmp_path)
    c = connect(":memory:")
    t0 = datetime(2026, 11, 9, 18, 0)
    first = _run(c, universe, yahoo, edgar, tmp_path, t0)
    assert first["errors"] == {}
    assert _core(first["stats"]) == {"prices": 2, "profiles": 2, "yahoo_fundamentals": 2,
                                     "edgar_filings": 1}
    # USB was missing from the batch -> per-ticker history fallback priced it
    assert c.execute("SELECT count(*) FROM prices_daily WHERE ticker='USB'").fetchone()[0] > 0

    second = _run(c, universe, yahoo, edgar, tmp_path, t0.replace(day=10))
    assert _core(second["stats"]) == {"prices": 2, "profiles": 0, "yahoo_fundamentals": 0,
                                      "edgar_filings": 0}
    assert yahoo.calls["info"] == 2 and yahoo.calls["fundamentals"] == 2 and edgar.fetched == 1

    edgar.accessions = {"0000000002": "A2"}  # a new filing appears in the frames
    third = _run(c, universe, yahoo, edgar, tmp_path, t0.replace(day=11))
    assert third["stats"]["edgar_filings"] == 1 and edgar.fetched == 2

    forced = _run(c, universe, yahoo, edgar, tmp_path, t0.replace(day=11),
                  refresh_fundamentals=True)
    assert forced["stats"]["yahoo_fundamentals"] == 2 and forced["stats"]["edgar_filings"] == 1


def test_prices_only_skips_fundamentals_but_onboards_new_tickers(tmp_path):
    from datetime import datetime

    universe, yahoo, edgar = _two_ticker_setup(tmp_path)
    c = connect(":memory:")
    res = _run(c, universe, yahoo, edgar, tmp_path, datetime(2026, 11, 9), prices_only=True)
    # New tickers still get a profile + fundamentals so they can be valued; EDGAR is skipped.
    assert res["stats"]["profiles"] == 2 and res["stats"]["edgar_filings"] == 0
    res2 = _run(c, universe, yahoo, edgar, tmp_path, datetime(2026, 12, 30), prices_only=True)
    assert _core(res2["stats"]) == {"prices": 2, "profiles": 0, "yahoo_fundamentals": 0,
                                    "edgar_filings": 0}


def test_fundamentals_due_policy():
    from datetime import datetime

    from valuation.ingest.pipeline import yahoo_fundamentals_due as due

    now = datetime(2026, 11, 9, 18)
    recent = datetime(2026, 11, 8, 18)
    assert due(None, None, now)
    assert due(recent, date(2026, 9, 30), now, force=True)
    assert not due(recent, date(2026, 9, 30), now)  # fresh, no new quarter expected
    assert due(datetime(2026, 11, 1), date(2026, 9, 30), now)  # older than 7 days
    # Jun 30 quarter + 136 days = Nov 13: not yet due on Nov 9, due on Nov 14 (daily re-check)
    assert not due(recent, date(2026, 6, 30), now)
    assert due(datetime(2026, 11, 13, 9), date(2026, 6, 30), datetime(2026, 11, 14, 18))


# ---------------------------------------------------------------------------
# Phase 6: price history / volume, dividends, rates, TTM, eligibility columns
# ---------------------------------------------------------------------------

def test_history_volume_dividends_and_rates_stored(con):
    rows = con.execute("SELECT date, close, adj_close, volume, source FROM prices_daily "
                       "WHERE ticker='CAN.TO' ORDER BY date").fetchall()
    assert [r[0] for r in rows] == [date(2026, 11, 5), date(2026, 11, 6)]  # no look-ahead bar
    assert rows[0][2:] == (pytest.approx(15.0 * 0.99), 1e6, "yahoo_chart")
    # spark/history bar for Nov 6 arrived first; the chart bar's volume is kept afterwards too
    assert rows[1][3] == 2e6
    divs = con.execute("SELECT ex_date, amount, currency FROM dividends WHERE ticker='CAN.TO' "
                       "ORDER BY ex_date").fetchall()
    assert divs[-1] == (date(2026, 11, 5), 0.5, "CAD")
    rates = dict(con.execute("SELECT series, rate FROM rates_daily").fetchall())
    assert rates == {"CA10Y": pytest.approx(0.0325), "US10Y": pytest.approx(0.041)}  # 11-10 > as_of


def test_spark_upsert_never_clears_chart_volume():
    from valuation.ingest.pipeline import upsert_prices
    from valuation.providers.base import PriceBar

    c = connect(":memory:")
    upsert_prices(c, [PriceBar("X", AS_OF, 10.0, 9.9, 500.0, "yahoo_chart")])
    upsert_prices(c, [PriceBar("X", AS_OF, 10.5, None, None, "yahoo")])
    assert c.execute("SELECT close, adj_close, volume, source FROM prices_daily").fetchone() == (
        10.5, 9.9, 500.0, "yahoo_chart")


def test_history_backfill_then_daily_then_event_refetch(tmp_path):
    from datetime import datetime

    from valuation.ingest.pipeline import HISTORY_BACKFILL_RANGE, HISTORY_DAILY_RANGE

    universe, yahoo, edgar = _two_ticker_setup(tmp_path)
    c = connect(":memory:")
    t0 = datetime(2026, 11, 9, 18)
    first = _run(c, universe, yahoo, edgar, tmp_path, t0)
    assert first["stats"]["backfills"] == 2
    assert {r for _, r in yahoo.chart_calls} == {HISTORY_BACKFILL_RANGE}

    yahoo.chart_calls.clear()
    second = _run(c, universe, yahoo, edgar, tmp_path, t0.replace(day=10))
    assert second["stats"]["backfills"] == 0 and second["stats"]["history"] == 2
    assert {r for _, r in yahoo.chart_calls} == {HISTORY_DAILY_RANGE}

    yahoo.chart_calls.clear()  # a new dividend (adj_close rebased) -> full refetch for that name
    yahoo.dividends = {"AAA.TO": [("2026-11-06", 0.25)]}
    third = _run(c, universe, yahoo, edgar, tmp_path, t0.replace(day=11))
    assert third["stats"]["backfills"] == 1
    assert ("AAA.TO", HISTORY_BACKFILL_RANGE) in yahoo.chart_calls
    assert ("USB", HISTORY_BACKFILL_RANGE) not in yahoo.chart_calls

    yahoo.chart_calls.clear()  # same event seen again: no refetch
    fourth = _run(c, universe, yahoo, edgar, tmp_path, t0.replace(day=12))
    assert fourth["stats"]["backfills"] == 0


def test_history_policy_helpers():
    from datetime import datetime

    from valuation.ingest.pipeline import events_changed, history_needs_backfill

    assert history_needs_backfill(None, None, AS_OF)
    assert history_needs_backfill(datetime(2026, 11, 1), date(2026, 10, 31), AS_OF)  # 9-day gap
    assert not history_needs_backfill(datetime(2026, 11, 1), date(2026, 11, 6), AS_OF)
    assert history_needs_backfill(datetime(2026, 11, 1), date(2026, 11, 6), AS_OF, force=True)
    assert events_changed("200", "100") and events_changed("100", None)
    assert not events_changed("100", "100") and not events_changed("", "100")


def test_report_has_eligibility_ttm_dps_rf(con, tmp_path):
    cfg = tmp_path / "liq.yaml"
    cfg.write_text(
        "tsx: {min_mdtv: 1, min_market_cap: 1, min_price: 1, mdtv_window: 2,"
        " max_zero_volume_days: 3, max_stale_sessions: 5, min_history_sessions: 273}\n"
        "us: {min_mdtv: 1000000000000, min_market_cap: 1, min_price: 1, mdtv_window: 2,"
        " max_zero_volume_days: 3, max_stale_sessions: 5, min_history_sessions: 273}\n")
    from valuation.models.eligibility import load_config

    liq = load_config(cfg)
    df, info = run_book_value(con, AS_OF, min_peers=1, liquidity=liq)
    r = df.set_index("ticker")
    assert r.loc["CAN.TO", "eligible"] and r.loc["CAN.TO", "elig_fail_reason"] == ""
    assert not r.loc["USA", "eligible"] and r.loc["USA", "elig_fail_reason"] == "E2"
    assert "SHORT_HISTORY" in r.loc["CAN.TO", "elig_flags"]
    assert r.loc["CAN.TO", "mdtv"] == pytest.approx(15.0 * 1.5e6)  # median of 15e6, 30e6
    assert r.loc["USA", "ni_common_ttm"] == pytest.approx(100.0)  # 10+20+30+40; 10-K ignored
    assert r.loc["USA", "ni_ttm_source"] == "sec_edgar"
    assert r.loc["CAN.TO", "ttm_flags"] == "NO_TTM"
    assert r.loc["CAN.TO", "dps_ttm"] == pytest.approx(0.5)  # the 2025-11-01 payment is >1y old
    assert r.loc["CAN.TO", "rf"] == pytest.approx(0.0325)
    assert r.loc["USA", "rf"] == pytest.approx(0.041)
    cov = info["coverage"].set_index("ticker")
    assert {"eligible", "elig_fail_reason"} <= set(cov.columns)
    assert info["removal"]["US"]["by_rule"]["E2"] == 2
    assert info["removal"]["TSX"]["n_ineligible"] == 0
    import json

    cfg_json = json.loads(con.execute(
        "SELECT config_json FROM runs WHERE run_id=?", [info["run_id"]]).fetchone()[0])
    assert cfg_json["liquidity"] == liq
