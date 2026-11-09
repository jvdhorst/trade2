"""Issuer-disclosure parser, on the exact lines pypdf extracts from each issuer's pack."""

import pytest

from valuation.qa.issuer_check import extract_bvps

LINES = {
    "RY": "Book value per share 1 $96.73 $93.28 $92.12 $91.00 $88.30 $86.61 $86.89 $83.46",
    "TD": "Book value per common share ($)  1  17  69.69  68.22  68.20  68.78  67.13  66.75",
    "BNS": "Book value per common share ($)⁽¹⁾ 64.99      62.95        62.99      62.22",
    "MFC": "16   Book value per common share 28.06           27.48           26.30   8 % 28.06",
}


@pytest.mark.parametrize("key,column,expected", [
    ("RY", 0, 96.73), ("TD", 0, 69.69), ("BNS", 0, 64.99), ("MFC", 0, 28.06), ("MFC", 1, 27.48),
    ("TD", 1, 68.22),  # footnote "1" and page ref "17" are skipped
])
def test_extract_from_real_line_formats(key, column, expected):
    assert extract_bvps(["header", LINES[key], "footer"], column) == expected


def test_ignores_lookalike_labels():
    lines = [
        "18 Adjusted book value per common share 2 41.78 41.12",
        "20 Book value excluding goodwill per common share 23.79 23.24",
        "Market value to adjusted book value ratio 2 1.45 1.40",
        "CSM net of NCI is used in the adjusted book value per share calculation 12.34",
        LINES["MFC"],
    ]
    assert extract_bvps(lines) == 28.06


def test_missing_label_or_column():
    assert extract_bvps(["nothing here 12.34"]) is None
    assert extract_bvps([LINES["BNS"]], column=10) is None


# --- ROE parser (Model A external check, docs/models/residual_income.md §External check) ---

from valuation.qa.issuer_check import extract_roe  # noqa: E402

ROE_LINES = {
    "RY": "Return on common equity (ROE) 5 17.9% 17.2% 17.6% 16.8% 17.3% 14.2% 16.8% 14.3%",
    "TD": "Return on common equity – reported  1  3  15.8 %  14.7 %  13.6 %  10.7 %  11.3 %  "
          "39.1 %  10.1 %  13.4 %  (1.0) %  14.6 %",
    "BNS": "ROE (%)⁽¹⁾ 14.1                          13.1               11.1               "
           "11.0            12.2",
}
RY_LABEL = r"Return on common equity \(ROE\)"
TD_LABEL = r"Return on common equity . reported"
BNS_LABEL = r"ROE \(%\)"


@pytest.mark.parametrize("key,label,expected", [
    ("RY", RY_LABEL, [0.179, 0.172, 0.176, 0.168]),
    ("TD", TD_LABEL, [0.158, 0.147, 0.136, 0.107]),
    ("BNS", BNS_LABEL, [0.141, 0.131, 0.111, 0.110]),
])
def test_extract_roe_real_line_formats(key, label, expected):
    got = extract_roe(["header", ROE_LINES[key], "footer"], label, 4)
    assert got == pytest.approx(expected, rel=1e-12)
    assert extract_roe([ROE_LINES[key]], label) == pytest.approx(expected, rel=1e-12)


def test_extract_roe_td_negative_in_parentheses():
    got = extract_roe([ROE_LINES["TD"]], TD_LABEL, 9)
    assert len(got) == 9
    assert got[8] == pytest.approx(-0.010, rel=1e-12)
    assert got[:4] == pytest.approx([0.158, 0.147, 0.136, 0.107], rel=1e-12)


def test_extract_roe_bns_reported_line_first_wins():
    lines = [ROE_LINES["BNS"], "Adjusted results", "ROE (%) 14.2  13.2  11.3  11.2"]
    assert extract_roe(lines, BNS_LABEL, 4) == pytest.approx([0.141, 0.131, 0.111, 0.110])
    assert extract_roe(lines, BNS_LABEL, 2) == pytest.approx([0.141, 0.131])


def test_extract_roe_ignores_lookalike_label():
    lines = ["Adjusted ROE 4,5 18.1% 17.4% 17.9% 17.0%", ROE_LINES["RY"]]
    assert extract_roe(lines, RY_LABEL, 4) == pytest.approx([0.179, 0.172, 0.176, 0.168])
    assert extract_roe(["Adjusted ROE 4,5 18.1% 17.4% 17.9% 17.0%"], RY_LABEL, 4) is None


def test_extract_roe_too_few_values_or_missing():
    assert extract_roe(["ROE (%) 14.2  13.2"], BNS_LABEL, 4) is None
    assert extract_roe([ROE_LINES["BNS"]], BNS_LABEL, 6) is None
    assert extract_roe(["nothing here 12.3 %"], RY_LABEL, 1) is None
    assert extract_roe([], RY_LABEL, 4) is None
