"""End-to-end ingest -> value -> report -> crosscheck with fake providers (no network)."""

from datetime import date

import pandas as pd
import pytest

from valuation.engine import run_book_value
from valuation.ingest.pipeline import ingest
from valuation.qa.crosscheck import crosscheck_book_value
from valuation.report.screen import write_report
from valuation.store.db import connect

AS_OF = date(2026, 11, 9)


class FakeYahoo:
    def __init__(self, data):
        self.data = data

    def info(self, t):
        return self.data[t]["info"]

    def balance_sheet(self, t):
        return self.data[t]["bs"]

    def history(self, t):
        return [{"Date": "2026-11-06", "Close": self.data[t]["price"]},
                {"Date": "2026-11-12", "Close": 999.0}]  # after as_of: must be ignored


class FakeEdgar:
    def __init__(self, payloads):
        self.payloads = payloads

    def companyfacts(self, cik):
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
        "ticker,exchange,cik,share_classes,share_conversion\n"
        "CAN.TO,TSX,,1,1\nUSR.TO,TSX,,1,1\nUSA,NYSE,0000000001,1,1\nBRK,NYSE,,2,1500\n"
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
    assert n == 4 and versions == ["book_value/0.1.0"]
    nulls = con.execute("SELECT count(*) FROM valuations WHERE inputs_json IS NULL").fetchone()
    assert nulls[0] == 0

