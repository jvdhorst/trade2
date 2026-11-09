"""End-to-end ingest -> value -> report -> crosscheck with fake providers (no network)."""

from datetime import date

import pandas as pd
import pytest

from valuation.engine import run_book_value
from valuation.ingest.pipeline import ingest
from valuation.providers import yahoo as yahoo_mod
from valuation.qa.crosscheck import crosscheck_book_value
from valuation.report.screen import write_report
from valuation.store.db import connect

AS_OF = date(2026, 11, 9)


class FakeYahoo:
    def __init__(self, data):
        self.data = data
        self.calls = {"info": 0, "fundamentals": 0, "closes": 0}

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
    c = connect(":memory:")
    counts = []
    for _ in range(2):  # second ingest must be a no-op (idempotent upserts)
        res = ingest(c, universe, AS_OF, cache_root=tmp_path / "raw",
                     yahoo_provider=yahoo, edgar_provider=edgar, boc_provider=FakeBoc())
        assert res["errors"] == {}
        counts.append([c.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in
                       ("securities", "prices_daily", "fundamentals", "fx_daily")])
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
                  edgar_provider=edgar, boc_provider=FakeBoc(), now=when, **kw)


def test_second_run_only_fetches_prices(tmp_path):
    from datetime import datetime

    universe, yahoo, edgar = _two_ticker_setup(tmp_path)
    c = connect(":memory:")
    t0 = datetime(2026, 11, 9, 18, 0)
    first = _run(c, universe, yahoo, edgar, tmp_path, t0)
    assert first["errors"] == {}
    assert first["stats"] == {"prices": 2, "profiles": 2, "yahoo_fundamentals": 2,
                              "edgar_filings": 1}
    # USB was missing from the batch -> per-ticker history fallback priced it
    assert c.execute("SELECT count(*) FROM prices_daily WHERE ticker='USB'").fetchone()[0] > 0

    second = _run(c, universe, yahoo, edgar, tmp_path, t0.replace(day=10))
    assert second["stats"] == {"prices": 2, "profiles": 0, "yahoo_fundamentals": 0,
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
    assert res2["stats"] == {"prices": 2, "profiles": 0, "yahoo_fundamentals": 0,
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
