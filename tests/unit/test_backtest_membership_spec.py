"""Spec tests for point-in-time S&P 500 membership (docs/models/backtest.md §1, §8 M1/M2).

Written from the spec only (API contract):
    from valuation.backtest.membership import (Change, Interval, norm, parse_changes,
        parse_current, build_intervals, members_on)
"""

from __future__ import annotations

import dataclasses
import math
from datetime import date, timedelta
from pathlib import Path

import pytest

from valuation.backtest.membership import (
    Change,
    Interval,
    build_intervals,
    members_on,
    norm,
    parse_changes,
    parse_current,
)

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "wiki_sp500_changes.html"
START = date(2013, 1, 1)  # MEMBERSHIP_START


def ch(d, added=None, removed=None, added_name=None, removed_name=None):
    return Change(d, added, added_name, removed, removed_name)


def tup(intervals):
    return [(i.ticker, i.start, i.end) for i in intervals]


def as_tuples(changes):
    return [(c.date, c.added_ticker, c.added_name, c.removed_ticker, c.removed_name)
            for c in changes]


# ---------------------------------------------------------------------------
# norm
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("raw, expected", [
    ("brk.b[5] ", "BRK-B"),          # spec example
    ("MMM", "MMM"),
    ("  abc  ", "ABC"),
    ("BF.B", "BF-B"),
    ("XYZ[a][12]", "XYZ"),            # every footnote removed
    ("[3]", None),                    # empty after removing the footnote
    ("", None),
    ("   ", None),
    ("—", None),                      # em dash placeholder
])
def test_norm(raw, expected):
    assert norm(raw) == expected


def test_norm_nan_is_none():
    assert norm(float("nan")) is None
    assert norm(math.nan) is None


# ---------------------------------------------------------------------------
# parse_current / parse_changes: golden M2 (inline HTML, exactly the spec's rows)
# ---------------------------------------------------------------------------

M2_HTML = """<html><body>
<table class="wikitable sortable" id="constituents"><tbody>
<tr><th>Symbol</th><th>Security</th><th>GICS Sector</th><th>GICS Sub-Industry</th>
<th>Headquarters Location</th><th>Date added</th><th>CIK</th><th>Founded</th></tr>
<tr><td><a href="#">MMM</a></td><td>3M</td><td>Industrials</td><td>Industrial Conglomerates</td>
<td>Saint Paul, Minnesota</td><td>1957-03-04</td><td>0000066740</td><td>1902</td></tr>
<tr><td><a href="#">BRK.B</a></td><td>Berkshire Hathaway</td><td>Financials</td>
<td>Multi-Sector Holdings</td><td>Omaha, Nebraska</td><td>2010-02-16</td><td>0001067983</td>
<td>1839</td></tr>
</tbody></table>
<table class="wikitable sortable" id="changes"><tbody>
<tr><th rowspan="2">Date</th><th colspan="2">Added</th><th colspan="2">Removed</th>
<th rowspan="2">Reason</th></tr>
<tr><th>Ticker</th><th>Security</th><th>Ticker</th><th>Security</th></tr>
<tr><td rowspan="2">July 9, 2024</td><td>ABC<sup class="reference"><a href="#n3">[3]</a></sup></td>
<td>Abc Corp</td><td>xyz.a</td><td>Xyz Inc</td><td rowspan="2">Rebalance.</td></tr>
<tr><td>NEWT</td><td>New T</td><td></td><td></td></tr>
<tr><td>June 3, 2019</td><td></td><td></td><td>OLD</td><td>Old Co</td><td>Acquired.</td></tr>
</tbody></table>
</body></html>"""


def test_m2_parse_current():
    assert parse_current(M2_HTML) == ["MMM", "BRK-B"]


def test_m2_parse_changes():
    got = parse_changes(M2_HTML)
    assert got == [
        Change(date(2024, 7, 9), "ABC", "Abc Corp", "XYZ-A", "Xyz Inc"),
        Change(date(2024, 7, 9), "NEWT", "New T", None, None),
        Change(date(2019, 6, 3), None, None, "OLD", "Old Co"),
    ]
    assert all(isinstance(c.date, date) for c in got)


def test_change_is_frozen():
    c = Change(date(2024, 7, 9), "ABC", "Abc Corp", None, None)
    with pytest.raises(dataclasses.FrozenInstanceError):
        c.added_ticker = "X"  # type: ignore[misc]


def test_parse_changes_skips_unparseable_date():
    html = M2_HTML.replace("<td>June 3, 2019</td>", "<td>TBA</td>")
    got = parse_changes(html)
    assert as_tuples(got) == [
        (date(2024, 7, 9), "ABC", "Abc Corp", "XYZ-A", "Xyz Inc"),
        (date(2024, 7, 9), "NEWT", "New T", None, None),
    ]


# ---------------------------------------------------------------------------
# Real-structure fixture with real index events
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def page():
    return FIXTURE.read_text(encoding="utf-8")


def test_fixture_parse_current(page):
    assert parse_current(page) == ["MMM", "BRK-B", "DELL", "ERIE", "PLTR", "SMCI", "DECK",
                                   "TSLA", "UBER"]


def test_fixture_parse_changes(page):
    assert as_tuples(parse_changes(page)) == [
        (date(2024, 9, 23), "PLTR", "Palantir Technologies", "AAL", "American Airlines Group"),
        (date(2024, 9, 23), "DELL", "Dell Technologies", "ETSY", "Etsy"),
        (date(2024, 9, 23), "ERIE", "Erie Indemnity", "BIO", "Bio-Rad Laboratories"),
        (date(2024, 3, 18), "SMCI", "Supermicro", "WHR", "Whirlpool Corporation"),
        (date(2024, 3, 18), "DECK", "Deckers Brands", "ZION", "Zions Bancorporation"),
        (date(2023, 12, 18), "UBER", "Uber", None, None),
        (date(2023, 5, 4), None, None, "FRC", "First Republic Bank"),
        (date(2023, 3, 15), None, None, "SIVB", "SVB Financial Group"),
        (date(2020, 12, 21), "TSLA", "Tesla, Inc.", "AIV", "Apartment Investment & Management"),
    ]


@pytest.fixture(scope="module")
def real_intervals(page):
    return build_intervals(parse_current(page), parse_changes(page), start=START)


# (ticker, effective date): a change dated D is effective at the open of D.
REAL_ADDITIONS = [("TSLA", date(2020, 12, 21)), ("PLTR", date(2024, 9, 23)),
                  ("SMCI", date(2024, 3, 18)), ("UBER", date(2023, 12, 18))]
REAL_REMOVALS = [("SIVB", date(2023, 3, 15)), ("FRC", date(2023, 5, 4)),
                 ("AIV", date(2020, 12, 21)), ("WHR", date(2024, 3, 18))]


@pytest.mark.parametrize("ticker, d", REAL_ADDITIONS)
def test_real_addition_flips_on_effective_date(real_intervals, ticker, d):
    assert ticker not in members_on(real_intervals, d - timedelta(days=1))
    assert ticker in members_on(real_intervals, d)


@pytest.mark.parametrize("ticker, d", REAL_REMOVALS)
def test_real_removal_flips_on_effective_date(real_intervals, ticker, d):
    assert ticker in members_on(real_intervals, d - timedelta(days=1))
    assert ticker not in members_on(real_intervals, d)


def test_real_intervals_exact(real_intervals):
    # The fixture is a "selected changes" subset: names removed in it with no earlier add row
    # are members from MEMBERSHIP_START.
    assert tup(real_intervals) == [
        ("AAL", START, date(2024, 9, 22)),
        ("AIV", START, date(2020, 12, 20)),
        ("BIO", START, date(2024, 9, 22)),
        ("BRK-B", START, None),
        ("DECK", date(2024, 3, 18), None),
        ("DELL", date(2024, 9, 23), None),
        ("ERIE", date(2024, 9, 23), None),
        ("ETSY", START, date(2024, 9, 22)),
        ("FRC", START, date(2023, 5, 3)),
        ("MMM", START, None),
        ("PLTR", date(2024, 9, 23), None),
        ("SIVB", START, date(2023, 3, 14)),
        ("SMCI", date(2024, 3, 18), None),
        ("TSLA", date(2020, 12, 21), None),
        ("UBER", date(2023, 12, 18), None),
        ("WHR", START, date(2024, 3, 17)),
        ("ZION", START, date(2024, 3, 17)),
    ]


def test_real_swap_day_sets(real_intervals):
    before = members_on(real_intervals, date(2024, 9, 22))
    on = members_on(real_intervals, date(2024, 9, 23))
    assert on - before == {"PLTR", "DELL", "ERIE"}
    assert before - on == {"AAL", "ETSY", "BIO"}


# ---------------------------------------------------------------------------
# build_intervals: golden M1
# ---------------------------------------------------------------------------

M1_CURRENT = ["AAA", "BBB", "CCC", "RE"]
M1_CHANGES = [
    ch(date(2021, 7, 1), "RE", "DDD"),
    ch(date(2019, 6, 3), "BBB", "BBB"),
    ch(date(2016, 5, 2), "DDD", "RE"),
    ch(date(2012, 12, 3), "CCC", "ZZZ"),
]


@pytest.fixture
def m1():
    return build_intervals(M1_CURRENT, M1_CHANGES, start=START)


def test_m1_intervals(m1):
    assert tup(m1) == [
        ("AAA", START, None),
        ("BBB", START, None),
        ("CCC", START, None),
        ("DDD", date(2016, 5, 2), date(2021, 6, 30)),
        ("RE", START, date(2016, 5, 1)),
        ("RE", date(2021, 7, 1), None),
    ]
    assert all(isinstance(i, Interval) for i in m1)


def test_m1_default_start_is_membership_start():
    assert tup(build_intervals(M1_CURRENT, M1_CHANGES)) == tup(
        build_intervals(M1_CURRENT, M1_CHANGES, start=START))


@pytest.mark.parametrize("d, expected", [
    (date(2016, 5, 1), {"AAA", "BBB", "CCC", "RE"}),
    (date(2016, 5, 2), {"AAA", "BBB", "CCC", "DDD"}),
    (date(2019, 6, 3), {"AAA", "BBB", "CCC", "DDD"}),
    (date(2021, 7, 1), {"AAA", "BBB", "CCC", "RE"}),
])
def test_m1_members_on(m1, d, expected):
    assert members_on(m1, d) == expected


def test_m1_members_on_boundaries(m1):
    # DDD [2016-05-02, 2021-06-30]: start inclusive, end inclusive, the day after excluded
    assert "DDD" not in members_on(m1, date(2016, 5, 1))
    assert "DDD" in members_on(m1, date(2016, 5, 2))
    assert "DDD" in members_on(m1, date(2021, 6, 30))
    assert "DDD" not in members_on(m1, date(2021, 7, 1))
    # RE [2013-01-01, 2016-05-01] and [2021-07-01, None]
    assert "RE" in members_on(m1, START)
    assert "RE" in members_on(m1, date(2016, 5, 1))
    assert "RE" not in members_on(m1, date(2018, 1, 1))
    assert "RE" in members_on(m1, date(2030, 1, 1))  # open interval
    # ZZZ (2012 change, before start) never appears
    assert all("ZZZ" not in members_on(m1, d) for d in (START, date(2012, 12, 3)))


def test_same_day_add_and_remove_no_gap(m1):
    assert [i for i in tup(m1) if i[0] == "BBB"] == [("BBB", START, None)]
    for d in (date(2019, 6, 2), date(2019, 6, 3), date(2019, 6, 4)):
        assert "BBB" in members_on(m1, d)


def test_same_day_add_and_remove_of_non_open_ticker():
    # z in A ∩ R and not open (not a member after D) -> open[z] = D - 1
    got = build_intervals(["AAA"], [ch(date(2019, 6, 3), "QQ", "QQ")], start=START)
    assert tup(got) == [("AAA", START, None), ("QQ", START, date(2019, 6, 2))]


def test_changes_before_start_ignored():
    changes = [ch(date(2012, 12, 31), "AAA", "OLD"), ch(date(2010, 1, 4), None, "AAA")]
    got = build_intervals(["AAA"], changes, start=START)
    assert tup(got) == [("AAA", START, None)]


def test_change_exactly_on_start_is_applied():
    # an addition dated exactly `start` is applied (interval starts at start either way), and a
    # removal dated exactly `start` makes the removed name a non-member on `start`.
    changes = [ch(START, "NEW", "GONE")]
    got = build_intervals(["NEW"], changes, start=START)
    assert ("NEW", START, None) in tup(got)
    assert members_on(got, START) == {"NEW"}
    # Spec ambiguity: the literal algorithm gives GONE an end of start - 1 day, i.e. an empty
    # interval Interval(GONE, start, start - 1). Only its observable effect (GONE is not a
    # member on start) is tested above.


def test_re_added_name_has_two_intervals():
    changes = [ch(date(2020, 3, 2), "XX", None), ch(date(2017, 9, 18), None, "XX")]
    got = build_intervals(["XX"], changes, start=START)
    assert tup(got) == [("XX", START, date(2017, 9, 17)), ("XX", date(2020, 3, 2), None)]
    assert "XX" not in members_on(got, date(2018, 6, 1))
    assert "XX" in members_on(got, date(2017, 9, 17))
    assert "XX" not in members_on(got, date(2017, 9, 18))
    assert "XX" not in members_on(got, date(2020, 3, 1))
    assert "XX" in members_on(got, date(2020, 3, 2))


def test_inconsistent_rows_skipped():
    # add of a ticker that is not open (not a current member, never removed later): skipped
    # remove of a ticker that is already open (removed twice going backwards): skipped
    changes = [ch(date(2020, 1, 2), "GHOST", None), ch(date(2018, 1, 2), None, "AAA")]
    got = build_intervals(["AAA"], changes, start=START)
    assert tup(got) == [("AAA", START, None)]


def test_output_sorted_and_no_touching_or_overlap():
    # Merge rule (step 4): intervals of one ticker never touch or overlap in the output.
    # Note: with the backward walk two intervals of one ticker can only touch through a
    # same-date add+remove, which step 2 already handles as "no gap" (see BBB in M1).
    changes = [
        ch(date(2022, 6, 21), "AA", "BB"),
        ch(date(2020, 1, 2), "BB", "AA"),
        ch(date(2019, 6, 3), "AA", "AA"),
        ch(date(2017, 3, 20), "AA", "CC"),
        ch(date(2015, 3, 23), "CC", "AA"),
    ]
    got = build_intervals(["AA"], changes, start=START)
    keys = [(i.ticker, i.start) for i in got]
    assert keys == sorted(keys)
    by: dict[str, list] = {}
    for i in got:
        by.setdefault(i.ticker, []).append(i)
    for ivs in by.values():
        for a, b in zip(ivs, ivs[1:], strict=False):
            assert a.end is not None
            assert b.start > a.end + timedelta(days=1)
    assert tup(got) == [
        ("AA", START, date(2015, 3, 22)),
        ("AA", date(2017, 3, 20), date(2020, 1, 1)),
        ("AA", date(2022, 6, 21), None),
        ("BB", date(2020, 1, 2), date(2022, 6, 20)),
        ("CC", date(2015, 3, 23), date(2017, 3, 19)),
    ]


def test_removal_of_current_member_is_inconsistent_and_skipped():
    got = build_intervals(["CC"], [ch(date(2017, 3, 20), None, "CC"),
                                   ch(date(2015, 3, 23), "CC", None)], start=START)
    # Backward: 2017-03-20 removes CC -> CC is a current member, so already open: skipped.
    # 2015-03-23 adds CC -> open -> Interval(CC, 2015-03-23, None).
    assert tup(got) == [("CC", date(2015, 3, 23), None)]


def test_members_on_with_constructed_intervals():
    ivs = [Interval("A", date(2020, 1, 1), date(2020, 1, 31)),
           Interval("B", date(2020, 2, 1), None)]
    assert members_on(ivs, date(2019, 12, 31)) == set()
    assert members_on(ivs, date(2020, 1, 1)) == {"A"}
    assert members_on(ivs, date(2020, 1, 31)) == {"A"}
    assert members_on(ivs, date(2020, 2, 1)) == {"B"}
    assert members_on([], date(2020, 2, 1)) == set()
