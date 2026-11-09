"""Offline parser tests on small synthetic payloads shaped like the real APIs."""

from datetime import date

from valuation.providers import boc, sec_edgar, yahoo
from valuation.providers.base import NCI, PREFERRED, SHARES, TOTAL_EQUITY


def _f(val, end, accn, filed, form="10-Q"):
    return {"val": val, "end": end, "accn": accn, "filed": filed, "form": form}


def _items(facts, pe=None):
    return {f.item: f.value for f in facts if pe is None or f.period_end == pe}


def test_edgar_primary_period_per_filing_and_cover_shares():
    payload = {"facts": {
        "us-gaap": {
            "StockholdersEquity": {"units": {"USD": [
                _f(900, "2025-12-31", "A1", "2026-02-01", "10-K"),
                _f(800, "2024-12-31", "A1", "2026-02-01", "10-K"),  # comparative column
                _f(950, "2026-03-31", "A2", "2026-05-01"),
                _f(1, "2026-03-31", "X", "2026-05-02", "8-K"),  # ignored form
            ]}},
            "MinorityInterest": {"units": {"USD": [_f(50, "2026-03-31", "A2", "2026-05-01")]}},
            "PreferredStockLiquidationPreferenceValue": {"units": {"USD": [
                _f(20, "2026-03-31", "A2", "2026-05-01")]}},
        },
        "dei": {"EntityCommonStockSharesOutstanding": {"units": {"shares": [
            _f(100, "2026-04-20", "A2", "2026-05-01"),
            _f(110, "2026-01-25", "A1", "2026-02-01", "10-K"),
        ]}}},
    }}
    facts = sec_edgar.parse_companyfacts("T", payload)
    periods = {(f.period_end, f.filed_date) for f in facts}
    assert periods == {
        (date(2025, 12, 31), date(2026, 2, 1)),
        (date(2026, 3, 31), date(2026, 5, 1)),
    }
    q1 = _items(facts, date(2026, 3, 31))
    assert q1[TOTAL_EQUITY] == 1000  # parent 950 + NCI 50
    assert q1[NCI] == 50
    assert q1[PREFERRED] == 20  # liquidation-preference fallback
    assert q1[SHARES] == 100  # cover-page shares from the same accession
    assert all(not f.filed_estimated for f in facts)


def test_edgar_prefers_total_equity_tag_and_preferred_order():
    payload = {"facts": {"us-gaap": {
        sec_edgar.EQUITY_TOTAL: {"units": {"USD": [_f(500, "2026-06-30", "B", "2026-08-01")]}},
        "StockholdersEquity": {"units": {"USD": [_f(480, "2026-06-30", "B", "2026-08-01")]}},
        "PreferredStockValue": {"units": {"USD": [_f(7, "2026-06-30", "B", "2026-08-01")]}},
        "PreferredStockLiquidationPreferenceValue": {"units": {"USD": [
            _f(9, "2026-06-30", "B", "2026-08-01")]}},
        "CommonStockSharesOutstanding": {"units": {"shares": [
            _f(40, "2026-06-30", "B", "2026-08-01")]}},
    }, "dei": {}}}
    it = _items(sec_edgar.parse_companyfacts("T", payload))
    assert it[TOTAL_EQUITY] == 500 and it[PREFERRED] == 7 and it[SHARES] == 40


def test_yahoo_balance_sheet_mapping():
    bs = {"2026-07-31": {
        "Total Equity Gross Minority Interest": 145.0, "Stockholders Equity": 145.0 - 0.5,
        "Common Stock Equity": 133.0, "Minority Interest": 0.5, "Goodwill": 19.0,
        "Other Intangible Assets": 7.0, "Ordinary Shares Number": 1.4, "Junk": None,
    }}
    facts = yahoo.parse_balance_sheet("RY.TO", bs, "CAD")
    it = _items(facts)
    assert it[TOTAL_EQUITY] == 145.0
    assert it[PREFERRED] == 144.5 - 133.0  # SE - common equity (prefs + other equity instr.)
    assert it[SHARES] == 1.4
    f = facts[0]
    assert f.filed_date == date(2026, 9, 14) and f.filed_estimated  # +45 days estimate
    assert {x.unit for x in facts if x.item != SHARES} == {"CAD"}


def test_yahoo_total_equity_fallback_and_nan():
    bs = {"2026-03-31": {"Stockholders Equity": 100.0, "Minority Interest": float("nan"),
                         "Preferred Stock Equity": 3.0, "Ordinary Shares Number": 10.0}}
    it = _items(yahoo.parse_balance_sheet("X", bs, "USD"))
    assert it[TOTAL_EQUITY] == 100.0 and NCI not in it and it[PREFERRED] == 3.0


def test_yahoo_profile_and_reference_metrics():
    info = {"currency": "CAD", "financialCurrency": "USD", "sector": "Basic Materials",
            "industry": "Gold", "shortName": "BARRICK", "bookValue": 23.3, "priceToBook": None}
    p = yahoo.parse_profile(info)
    assert p["financial_currency"] == "USD" and p["name"] == "BARRICK"
    assert yahoo.parse_reference_metrics(info) == {"bookValue": 23.3}


def test_yahoo_history_skips_missing_close():
    rows = [{"Date": "2026-11-06", "Close": 10.0, "Adj Close": 9.9, "Volume": 5},
            {"Date": "2026-11-07", "Close": None}]
    bars = yahoo.parse_history("X", rows)
    assert len(bars) == 1 and bars[0].date == date(2026, 11, 6)


def test_boc_observations():
    payload = {"observations": [{"d": "2026-11-06", "FXUSDCAD": {"v": "1.3912"}},
                                {"d": "2026-11-07", "FXUSDCAD": {"v": ""}}]}
    assert boc.parse_observations(payload) == [(date(2026, 11, 6), 1.3912)]
