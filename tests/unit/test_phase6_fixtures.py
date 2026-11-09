"""Parsers on recorded real responses (trimmed), recorded 2026-11-09.

P6-3 acceptance: AAPL and JPM TTM NI to common vs the 10-K / 10-Q values (±0.5%; exact here).
"""

import json
from datetime import date
from pathlib import Path

import pytest

from valuation.providers import rates, yahoo
from valuation.providers.sec_edgar import parse_quarterly_ni
from valuation.ttm import dps_ttm, ttm

FIX = Path(__file__).parents[1] / "fixtures"


@pytest.fixture(scope="module")
def edgar():
    return json.loads((FIX / "edgar_ni_aapl_jpm.json").read_text())


def test_aapl_ttm_equals_fy2026_10k(edgar):
    facts = parse_quarterly_ni("AAPL", edgar["AAPL"])
    r = ttm(facts, date(2026, 11, 9))
    # 10-K FY2026 (filed 2026-11-03) NetIncomeLoss = 132,175M; Q4 derived = 132175 - 101464
    assert r.value == pytest.approx(132_175e6, rel=0.005)
    assert r.value == 132_175e6
    q4 = [f for f in facts if f.period_end == date(2026, 9, 26)]
    assert [(f.filed_date, f.value) for f in q4] == [(date(2026, 11, 3), 30_711e6)]
    # Before the 10-K: TTM = FY2025 Q4 (112010 - 84544) + Q1..Q3 FY2026
    assert ttm(facts, date(2026, 11, 2)).value == (27_466 + 42_097 + 29_578 + 29_789) * 1e6


def test_jpm_ttm_uses_available_to_common_and_derived_q4(edgar):
    facts = parse_quarterly_ni("JPM", edgar["JPM"])
    r = ttm(facts, date(2026, 11, 9))
    # Q4 2025 = FY 55,681 - 9M 42,991 (10-K 2026-02-13); Q1-Q3 2026 from the 10-Qs
    assert r.value == pytest.approx(64_526e6, rel=0.005)
    assert r.value == (12_690 + 16_148 + 20_752 + 14_936) * 1e6
    assert r.quarter_ends[0] == date(2026, 9, 30) and r.filed_date == date(2026, 11, 3)
    # Yahoo's independent trailingNetIncomeCommonStockholders on 2026-11-09: 64,527M
    assert r.value == pytest.approx(64_527e6, rel=0.0001)


def test_chart_ry_to_bars_and_dividends():
    payload = json.loads((FIX / "chart_ry_to.json").read_text())
    bars, divs = yahoo.parse_chart("RY.TO", payload)
    assert len(bars) == 8 and all(b.volume and b.adj_close for b in bars)
    assert all(b.source == "yahoo_chart" for b in bars)
    assert max(d.ex_date for d in divs) == date(2026, 10, 26)  # 09:30 ET open
    assert all(d.currency == "CAD" for d in divs)
    assert dps_ttm(divs, date(2026, 11, 9)) == pytest.approx(1.64 + 1.64 + 1.76 + 1.76)
    assert yahoo.chart_events_marker(payload) == "1793021400"


def test_fred_and_boc_10y():
    us = rates.parse_fred_csv((FIX / "fred_dgs10.csv").read_text())
    assert us[0] == (date(2026, 10, 26), "US10Y", pytest.approx(0.05), "fred")
    assert us[-1][0] == date(2026, 11, 5) and us[-1][2] == pytest.approx(0.0484)
    ca = rates.parse_boc_yield(json.loads((FIX / "boc_10y.json").read_text()))
    assert ca[-1] == (date(2026, 11, 6), "CA10Y", pytest.approx(0.0365), "boc")


def test_fred_missing_values_and_old_header():
    text = "DATE,DGS10\n2024-01-01,.\n2024-01-02,3.95\n2024-01-03,\n"
    assert rates.parse_fred_csv(text) == [(date(2024, 1, 2), "US10Y", pytest.approx(0.0395),
                                           "fred")]
    assert rates.parse_fred_csv("") == []


def test_fred_chunks_concatenate(monkeypatch, tmp_path):
    from valuation.providers.base import RawCache

    urls = []

    def fake_get(url):
        urls.append(url)
        d = url.split("cosd=")[1][:10]
        return f"observation_date,DGS10\n{d},4.00\n"

    rp = rates.RatesProvider(RawCache(tmp_path, date(2026, 11, 9)))
    monkeypatch.setattr(rp, "_get_text", fake_get)
    rows = rates.parse_fred_csv(rp.fred_10y(date(2026, 11, 9), 3 * 365))
    assert len(urls) == 2 and len(rows) == 2  # 1095 days -> two <=730-day chunks
    assert all("coed=" in u for u in urls)
