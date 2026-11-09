from valuation.ingest.universe_build import parse_sp500_html, parse_xic_csv, tsx_to_yahoo, validate

XIC = (
    '﻿Fund Holdings as of,"Nov 6, 2026"\n \n'
    "Ticker,Name,Sector,Asset Class,Market Value,Weight (%),Notional Value,Shares,Price,Location,"
    "Exchange,Currency,FX Rate,Market Currency\n"
    '"RY","ROYAL BANK OF CANADA","Financials","Equity","2,832","7.73","2,832","9,969","284.16",'
    '"Canada","Toronto Stock Exchange","CAD","1.00","CAD"\n'
    '"BBD.B","BOMBARDIER CLASS B","Industrials","Equity","1","0.1","1","1","150.00",'
    '"Canada","Toronto Stock Exchange","CAD","1.00","CAD"\n'
    '"2299955D","CONSTELLATION SOFTWARE INC","Information Technology","Equity","0.61","0.00",'
    '"0.61","60,915.00","0.00","Canada","Toronto Stock Exchange","CAD","1.00","CAD"\n'
    '"MLPFT","CASH COLLATERAL","Cash and/or Derivatives","Cash Collateral and Margins","2","0",'
    '"2","2","100.00","Canada","-","CAD","1.00","CAD"\n'
)

SP = """<table id="constituents"><tr><th>Symbol</th><th>Security</th><th>GICS Sector</th>
<th>GICS Sub-Industry</th><th>Headquarters Location</th><th>Date added</th><th>CIK</th>
<th>Founded</th></tr>
<tr><td>BRK.B</td><td>Berkshire Hathaway</td><td>Financials</td><td>Multi-Sector Holdings</td>
<td>Omaha</td><td>2010-02-16</td><td>1067983</td><td>1839</td></tr>
<tr><td>AAPL</td><td>Apple Inc.</td><td>Information Technology</td><td>Hardware</td>
<td>Cupertino</td><td>1982-11-30</td><td>320193</td><td>1977</td></tr></table>"""


def test_tsx_to_yahoo():
    assert tsx_to_yahoo("RY") == "RY.TO"
    assert tsx_to_yahoo("BBD.B") == "BBD-B.TO"
    assert tsx_to_yahoo("BIP.UN") == "BIP-UN.TO"


def test_parse_xic_filters_non_equity_and_placeholders():
    rows, as_of = parse_xic_csv(XIC)
    assert as_of == "2026-11-06"
    assert [r["ticker"] for r in rows] == ["RY.TO", "BBD-B.TO"]
    assert rows[0]["gics_sector"] == "Financials" and rows[0]["exchange"] == "TSX"


def test_parse_sp500_cik_classes():
    rows = {r["ticker"]: r for r in parse_sp500_html(SP, "2026-11-09")}
    assert rows["BRK-B"]["cik"] == "0001067983"
    assert rows["BRK-B"]["share_conversion"] == 1500.0 and rows["BRK-B"]["share_classes"] == 2
    assert rows["AAPL"]["share_classes"] == 1 and rows["AAPL"]["gics_sub_industry"] == "Hardware"


def test_validate():
    rows, _ = parse_xic_csv(XIC)
    assert validate(rows, 2) == []
    assert any("duplicate" in p for p in validate(rows + rows[:1], 2))
    assert any("only" in p for p in validate(rows, 200))


def test_xic_multi_class_flags():
    rows = {r["ticker"]: r for r in parse_xic_csv(XIC)[0]}
    assert rows["BBD-B.TO"]["share_classes"] == 2
    assert rows["RY.TO"]["share_classes"] == 1
    unit = XIC.replace('"BBD.B"', '"BIP.UN"')
    assert {r["ticker"]: r for r in parse_xic_csv(unit)[0]}["BIP-UN.TO"]["share_classes"] == 1
