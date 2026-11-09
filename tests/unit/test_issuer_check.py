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
