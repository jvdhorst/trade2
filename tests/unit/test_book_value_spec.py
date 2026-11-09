"""Spec tests for the book_value model (docs/models/book_value.md, v0.1.0).

Written from the spec only (not from the implementation). Golden values are
expressed as exact arithmetic expressions rather than the 6-significant-figure
decimals printed in the spec, so that rel=1e-9 is meaningful.
"""

from __future__ import annotations

from datetime import date

import pytest

from valuation.models.book_value import (
    MODEL_VERSION,
    BookValueInputs,
    BookValueResult,  # noqa: F401  (part of the API contract; import must succeed)
    Filing,
    PeerValuation,  # noqa: F401  (part of the API contract; import must succeed)
    compute_book_value,
    peer_relative,
    rank,
    select_latest_filing,
)

REL = 1e-9
AS_OF = date(2026, 6, 30)


def approx(x):
    return pytest.approx(x, rel=REL)


def make_inputs(**overrides) -> BookValueInputs:
    """BookValueInputs with sensible, flag-free defaults."""
    kwargs = dict(
        ticker="TST",
        as_of=AS_OF,
        price=15.0,
        trading_currency="USD",
        reporting_currency="USD",
        total_equity=1000.0,
        shares_outstanding=100.0,
        period_end=date(2026, 3, 31),
        filed_date=date(2026, 5, 1),
    )
    kwargs.update(overrides)
    return BookValueInputs(**kwargs)


def compute(**overrides):
    return compute_book_value(make_inputs(**overrides))


# ---------------------------------------------------------------------------
# Meta / API contract
# ---------------------------------------------------------------------------


def test_model_version():
    assert MODEL_VERSION == "book_value/0.1.0"


def test_inputs_defaults():
    inp = make_inputs()
    assert inp.noncontrolling_interest == 0
    assert inp.preferred_equity == 0
    assert inp.goodwill == 0
    assert inp.intangibles == 0
    assert inp.fx_rate == 1.0
    assert inp.share_classes == 1
    assert inp.sector is None
    assert inp.industry is None


def test_result_carries_identity_fields():
    r = compute(ticker="XYZ", price=15.0, sector="Financials", industry="Banks")
    assert r.ticker == "XYZ"
    assert r.as_of == AS_OF
    assert r.price == approx(15.0)
    assert r.currency == "USD"
    assert r.sector == "Financials"
    assert r.industry == "Banks"
    assert isinstance(r.flags, frozenset)


def test_result_currency_is_trading_currency():
    r = compute(reporting_currency="USD", trading_currency="CAD", fx_rate=1.4)
    assert r.currency == "CAD"


# ---------------------------------------------------------------------------
# Worked examples 1-4
# ---------------------------------------------------------------------------


def test_ex1_simple():
    r = compute(
        total_equity=1000,
        noncontrolling_interest=0,
        preferred_equity=0,
        goodwill=200,
        intangibles=50,
        shares_outstanding=100,
        price=15,
    )
    assert r.common_equity == approx(1000)
    assert r.bvps == approx(10)
    assert r.tbvps == approx(7.5)
    assert r.pb == approx(1.5)
    assert r.flags == frozenset()


def test_ex2_bank_with_preferreds_and_nci():
    r = compute(
        total_equity=120_000,
        noncontrolling_interest=2_000,
        preferred_equity=8_000,
        shares_outstanding=1_400,
        price=140,
    )
    assert r.common_equity == approx(110_000)
    assert r.bvps == approx(110_000 / 1_400)
    assert r.pb == approx(140 / (110_000 / 1_400))
    # spec rounding sanity check (printed to 6 s.f.)
    assert r.bvps == pytest.approx(78.5714, rel=1e-5)
    assert r.pb == pytest.approx(1.78182, rel=1e-5)


def test_ex3_fx_conversion():
    r = compute(
        reporting_currency="USD",
        trading_currency="CAD",
        fx_rate=1.40,
        total_equity=500,
        shares_outstanding=50,
        price=21,
    )
    assert r.bvps == approx(14.0)
    assert r.pb == approx(1.5)
    assert r.currency == "CAD"
    assert r.flags == frozenset({"FX_CONVERTED"})


def test_ex3_tbvps_is_fx_converted():
    r = compute(
        reporting_currency="USD",
        trading_currency="CAD",
        fx_rate=1.40,
        total_equity=500,
        goodwill=100,
        shares_outstanding=50,
        price=21,
    )
    assert r.tbvps == approx((500 - 100) / 50 * 1.40)


def test_ex4_negative_equity():
    r = compute(total_equity=-200, shares_outstanding=100, price=300)
    assert r.bvps == approx(-2)
    assert r.pb is None
    assert r.flags == frozenset({"NEG_BOOK"})


def test_zero_book_is_neg_book():
    # bvps <= 0 -> NEG_BOOK, pb None (boundary bvps == 0)
    r = compute(total_equity=0, shares_outstanding=100, price=10)
    assert r.bvps == 0
    assert r.pb is None
    assert "NEG_BOOK" in r.flags


def test_neg_common_equity_via_preferreds_is_neg_book():
    # Positive total equity but preferreds+NCI exceed it -> common equity negative.
    r = compute(total_equity=100, noncontrolling_interest=60, preferred_equity=60)
    assert r.common_equity == approx(-20)
    assert r.pb is None
    assert "NEG_BOOK" in r.flags


def test_multi_class_flag():
    assert "MULTI_CLASS" in compute(share_classes=2).flags
    assert "MULTI_CLASS" not in compute(share_classes=1).flags


def test_fx_flag_absent_when_same_currency():
    assert "FX_CONVERTED" not in compute().flags


# ---------------------------------------------------------------------------
# ValueError
# ---------------------------------------------------------------------------
# The spec does not say whether validation happens at BookValueInputs
# construction or inside compute_book_value; accept either.


@pytest.mark.parametrize("shares", [0, -1, -100.0])
def test_nonpositive_shares_raises(shares):
    with pytest.raises(ValueError):
        compute_book_value(make_inputs(shares_outstanding=shares))


@pytest.mark.parametrize("price", [0, -0.01, -15])
def test_nonpositive_price_raises(price):
    with pytest.raises(ValueError):
        compute_book_value(make_inputs(price=price))


def test_nonpositive_price_raises_even_with_negative_book():
    with pytest.raises(ValueError):
        compute_book_value(make_inputs(price=0, total_equity=-200))


# ---------------------------------------------------------------------------
# Ex 5: filing selection (no look-ahead, amendments)
# ---------------------------------------------------------------------------

FILING_A = Filing(period_end=date(2025, 12, 31), filed_date=date(2026, 2, 20), data={"id": "A"})
FILING_B = Filing(period_end=date(2026, 3, 31), filed_date=date(2026, 5, 1), data={"id": "B"})
FILING_A2 = Filing(period_end=date(2025, 12, 31), filed_date=date(2026, 3, 10), data={"id": "A'"})


def test_ex5_before_b_is_filed_picks_a():
    assert select_latest_filing([FILING_A, FILING_B], date(2026, 4, 15)) == FILING_A


def test_ex5_on_b_filed_date_picks_b():
    # filed_date <= as_of is inclusive
    assert select_latest_filing([FILING_A, FILING_B], date(2026, 5, 1)) == FILING_B


def test_ex5_day_before_b_filed_picks_a():
    assert select_latest_filing([FILING_A, FILING_B], date(2026, 4, 30)) == FILING_A


def test_ex5_nothing_filed_yet_returns_none():
    assert select_latest_filing([FILING_A, FILING_B], date(2026, 1, 15)) is None


def test_ex5_empty_list_returns_none():
    assert select_latest_filing([], date(2026, 4, 15)) is None


@pytest.mark.parametrize(
    "order",
    [
        [FILING_A, FILING_A2, FILING_B],
        [FILING_B, FILING_A2, FILING_A],
        [FILING_A2, FILING_B, FILING_A],
    ],
)
def test_ex5_amendment_picks_later_filed(order):
    assert select_latest_filing(order, date(2026, 4, 15)) == FILING_A2


def test_ex5_amendment_not_yet_filed_is_ignored():
    # A' filed 2026-03-10; as of 2026-03-01 only the original A is public.
    assert select_latest_filing([FILING_A, FILING_A2, FILING_B], date(2026, 3, 1)) == FILING_A


def test_ex5_selection_is_order_independent():
    assert select_latest_filing([FILING_B, FILING_A], date(2026, 4, 15)) == FILING_A
    assert select_latest_filing([FILING_B, FILING_A], date(2026, 5, 1)) == FILING_B


# ---------------------------------------------------------------------------
# Ex 6: stale filing boundary
# ---------------------------------------------------------------------------


def test_ex6_day_count_premise():
    assert (date(2026, 5, 3) - date(2025, 1, 31)).days == 457
    assert (date(2026, 5, 2) - date(2025, 1, 31)).days == 456


def test_ex6_457_days_is_stale():
    r = compute(
        period_end=date(2025, 1, 31),
        filed_date=date(2025, 3, 1),
        as_of=date(2026, 5, 3),
    )
    assert "STALE_FILING" in r.flags


def test_ex6_456_days_is_not_stale():
    r = compute(
        period_end=date(2025, 1, 31),
        filed_date=date(2025, 3, 1),
        as_of=date(2026, 5, 2),
    )
    assert "STALE_FILING" not in r.flags


# ---------------------------------------------------------------------------
# Ex 7: peers
# ---------------------------------------------------------------------------

EX7_TABLE = [
    ("A", "Financials", "Banks", 1.0),
    ("B", "Financials", "Banks", 1.2),
    ("C", "Financials", "Banks", 1.4),
    ("D", "Financials", "Banks", 1.6),
    ("E", "Financials", "Banks", 1.8),
    ("F", "Financials", "Banks", 2.0),
    ("G", "Financials", "Insurance", 1.0),
    ("H", "Financials", "Insurance", 3.0),
    ("I", "Energy", "Oil & Gas", 1.0),
    ("N", "Financials", "Banks", None),  # NEG_BOOK, bvps -1
]
EX7_PB = {t: pb for t, _, _, pb in EX7_TABLE}


def ex7_inputs(ticker, sector, industry, pb):
    total_equity = -1.0 if pb is None else 50.0 / pb
    return make_inputs(
        ticker=ticker,
        price=50.0,
        total_equity=total_equity,
        shares_outstanding=1.0,
        sector=sector,
        industry=industry,
    )


def ex7_results(table=EX7_TABLE):
    return [compute_book_value(ex7_inputs(*row)) for row in table]


@pytest.fixture
def ex7():
    vals = peer_relative(ex7_results())
    return {v.ticker: v for v in vals}


def test_ex7_inputs_produce_intended_pb():
    for r in ex7_results():
        if EX7_PB[r.ticker] is None:
            assert r.bvps == approx(-1)
            assert r.pb is None
            assert "NEG_BOOK" in r.flags
        else:
            assert r.bvps == approx(50.0 / EX7_PB[r.ticker])
            assert r.pb == approx(EX7_PB[r.ticker])


def test_ex7_A(ex7):
    a = ex7["A"]
    assert a.n_peers == 5  # N (NEG_BOOK) must not be counted
    assert a.peer_level == "industry"
    assert a.peer_median_pb == approx(1.6)
    assert a.result.bvps == approx(50)
    assert a.fair_value == approx(80)
    assert a.upside == approx(0.6)
    assert a.pb_percentile == approx(0.0)
    assert "INDUSTRY_FALLBACK" not in a.flags
    assert "FEW_PEERS" not in a.flags


def test_ex7_D(ex7):
    d = ex7["D"]
    assert d.n_peers == 5
    assert d.peer_level == "industry"
    assert d.peer_median_pb == approx(1.4)
    assert d.result.bvps == approx(31.25)
    assert d.fair_value == approx(43.75)
    assert d.upside == approx(-0.125)
    assert d.pb_percentile == approx(0.6)


def test_ex7_F(ex7):
    f = ex7["F"]
    assert f.n_peers == 5
    assert f.peer_level == "industry"
    assert f.peer_median_pb == approx(1.4)
    assert f.result.bvps == approx(25)
    assert f.fair_value == approx(35)
    assert f.upside == approx(-0.3)
    assert f.pb_percentile == approx(1.0)


def test_ex7_G_sector_fallback(ex7):
    g = ex7["G"]
    assert g.peer_level == "sector"
    assert g.n_peers == 7  # A-F and H; not N, not I, not G itself
    assert g.peer_median_pb == approx(1.6)
    assert g.result.bvps == approx(50)
    assert g.fair_value == approx(80)
    assert g.upside == approx(0.6)
    assert g.pb_percentile == approx(0.0)
    assert g.flags >= {"INDUSTRY_FALLBACK"}
    assert "FEW_PEERS" not in g.flags


def test_ex7_H_sector_fallback(ex7):
    h = ex7["H"]
    assert h.peer_level == "sector"
    assert h.n_peers == 7  # A-G
    assert h.peer_median_pb == approx(1.4)
    assert h.result.bvps == approx(50 / 3)
    assert h.fair_value == approx(50 / 3 * 1.4)
    assert h.upside == approx(1.4 / 3 - 1)
    assert h.pb_percentile == approx(1.0)
    assert h.flags >= {"INDUSTRY_FALLBACK"}
    # spec rounding sanity check
    assert h.fair_value == pytest.approx(23.3333, rel=1e-5)
    assert h.upside == pytest.approx(-0.533333, rel=1e-5)


def test_ex7_I_few_peers(ex7):
    i = ex7["I"]
    assert i.fair_value is None
    assert i.upside is None
    assert i.peer_level is None
    assert i.flags >= {"FEW_PEERS"}
    assert "INDUSTRY_FALLBACK" not in i.flags


def test_ex7_N_neg_book(ex7):
    n = ex7["N"]
    assert n.fair_value is None
    assert n.upside is None
    assert n.pb_percentile is None
    assert "NEG_BOOK" in n.flags


@pytest.mark.parametrize(
    "ticker,expected_upside",
    [
        ("B", 1.6 / 1.2 - 1),
        ("C", 1.6 / 1.4 - 1),
        ("E", 1.4 / 1.8 - 1),
    ],
)
def test_ex7_BCE_upside(ex7, ticker, expected_upside):
    v = ex7[ticker]
    assert v.peer_level == "industry"
    assert v.n_peers == 5
    assert v.upside == approx(expected_upside)


def test_ex7_spec_printed_BCE_values():
    # cross-check the 6 s.f. values printed in the spec against the exact expressions
    assert 1.6 / 1.2 - 1 == pytest.approx(0.333333, rel=1e-5)
    assert 1.6 / 1.4 - 1 == pytest.approx(0.142857, rel=1e-5)
    assert 1.4 / 1.8 - 1 == pytest.approx(-0.222222, rel=1e-5)


def test_ex7_neg_book_never_a_peer():
    """Removing N must not change any other company's valuation."""
    with_n = {v.ticker: v for v in peer_relative(ex7_results())}
    without_n = {
        v.ticker: v for v in peer_relative(ex7_results([r for r in EX7_TABLE if r[0] != "N"]))
    }
    for t, v in without_n.items():
        w = with_n[t]
        assert w.n_peers == v.n_peers
        assert w.peer_level == v.peer_level
        if v.fair_value is None:
            assert w.fair_value is None
        else:
            assert w.fair_value == approx(v.fair_value)
            assert w.pb_percentile == approx(v.pb_percentile)


def test_ex7_rank_order():
    vals = peer_relative(ex7_results())
    ranked = rank(vals)
    assert [v.ticker for v in ranked] == ["A", "G", "B", "C", "D", "E", "F", "H", "I", "N"]


def test_ex7_rank_independent_of_input_order():
    rev = list(reversed(ex7_results()))
    ranked = rank(peer_relative(rev))
    assert [v.ticker for v in ranked] == ["A", "G", "B", "C", "D", "E", "F", "H", "I", "N"]


def test_peer_relative_preserves_input_order():
    results = ex7_results()
    order = ["N", "I", "H", "A", "G", "F", "B", "E", "C", "D"]
    by_t = {r.ticker: r for r in results}
    shuffled = [by_t[t] for t in order]
    vals = peer_relative(shuffled)
    assert [v.ticker for v in vals] == order
    for v, r in zip(vals, shuffled, strict=True):
        assert v.result == r


def test_peer_flags_include_company_flags():
    # A company flag (MULTI_CLASS) must be carried into PeerValuation.flags.
    results = ex7_results()
    results[0] = compute_book_value(
        make_inputs(
            ticker="A",
            price=50.0,
            total_equity=50.0,
            shares_outstanding=1.0,
            share_classes=2,
            sector="Financials",
            industry="Banks",
        )
    )
    a = {v.ticker: v for v in peer_relative(results)}["A"]
    assert a.flags >= {"MULTI_CLASS"}
    assert a.flags >= results[0].flags


def test_min_peers_parameter_lets_g_use_industry():
    # With min_peers=1, G's single industry peer H suffices.
    g = {v.ticker: v for v in peer_relative(ex7_results(), min_peers=1)}["G"]
    assert g.peer_level == "industry"
    assert g.n_peers == 1
    assert g.peer_median_pb == approx(3.0)
    assert g.fair_value == approx(150.0)
    assert "INDUSTRY_FALLBACK" not in g.flags


def test_min_peers_parameter_high_forces_few_peers():
    vals = peer_relative(ex7_results(), min_peers=8)
    by_t = {v.ticker: v for v in vals}
    # A has 5 industry peers and 7 sector peers (B-H) -> both < 8
    assert by_t["A"].fair_value is None
    assert "FEW_PEERS" in by_t["A"].flags
    # Everything is unvalued -> rank is pure ticker order.
    assert [v.ticker for v in rank(vals)] == sorted(by_t)


def test_empty_industry_is_not_a_peer_group():
    # Six companies share an empty-string industry; "non-empty industry" rule
    # means they must fall back to sector.
    table = [(t, "S", "", 1.0 + 0.1 * k) for k, t in enumerate("PQRSTU")]
    vals = peer_relative(ex7_results(table))
    for v in vals:
        assert v.peer_level == "sector"
        assert "INDUSTRY_FALLBACK" in v.flags
        assert v.n_peers == 5


def test_missing_sector_and_industry_gives_few_peers():
    table = [(t, None, None, 1.0 + 0.1 * k) for k, t in enumerate("PQRSTU")]
    vals = peer_relative(ex7_results(table))
    for v in vals:
        assert v.fair_value is None
        assert v.peer_level is None
        assert "FEW_PEERS" in v.flags


def test_even_peer_count_median_is_mean_of_middle_two():
    # 7 banks: each has 6 industry peers (even count).
    table = [(t, "Fin", "Banks", pb) for t, pb in zip("PQRSTUV", [1, 2, 3, 4, 5, 6, 7])]
    p = {v.ticker: v for v in peer_relative(ex7_results(table))}["P"]
    # peers pb 2..7 -> median (4+5)/2
    assert p.n_peers == 6
    assert p.peer_median_pb == approx(4.5)
    assert p.fair_value == approx(50.0 / 1 * 4.5)


# ---------------------------------------------------------------------------
# Properties
# ---------------------------------------------------------------------------

PROPERTY_CASES = [
    dict(total_equity=1000, shares_outstanding=100, price=15),
    dict(total_equity=120_000, noncontrolling_interest=2_000, preferred_equity=8_000,
         shares_outstanding=1_400, price=140),
    dict(total_equity=500, shares_outstanding=50, price=21, goodwill=30, intangibles=5),
    dict(total_equity=3.7e10, shares_outstanding=1.234e9, price=88.88, preferred_equity=1e9),
    dict(total_equity=0.5, shares_outstanding=3, price=0.07),
]


@pytest.mark.parametrize("case", PROPERTY_CASES)
def test_doubling_shares_halves_bvps_and_doubles_pb(case):
    base = compute(**case)
    doubled = compute(**{**case, "shares_outstanding": case["shares_outstanding"] * 2})
    assert doubled.bvps == approx(base.bvps / 2)
    assert doubled.tbvps == approx(base.tbvps / 2)
    assert doubled.pb == approx(base.pb * 2)


@pytest.mark.parametrize("case", PROPERTY_CASES)
@pytest.mark.parametrize("ccy,fx", [("CAD", 1.40), ("EUR", 0.92), ("JPY", 151.3)])
def test_currency_invariance(case, ccy, fx):
    """Same equity expressed in a foreign reporting currency with matching fx_rate.

    fx_rate = trading-ccy units per 1 reporting-ccy unit (trading ccy is USD here),
    so an amount X USD equals X / fx in the reporting currency.
    """
    base = compute(**case)
    money = ("total_equity", "noncontrolling_interest", "preferred_equity", "goodwill",
             "intangibles")
    foreign = {k: (v / fx if k in money else v) for k, v in case.items()}
    conv = compute(**foreign, reporting_currency=ccy, trading_currency="USD", fx_rate=fx)
    assert conv.bvps == approx(base.bvps)
    assert conv.tbvps == approx(base.tbvps)
    assert conv.pb == approx(base.pb)
    assert "FX_CONVERTED" in conv.flags


@pytest.mark.parametrize("min_peers", [1, 3, 5])
def test_fair_value_over_price_equals_median_over_pb(min_peers):
    for v in peer_relative(ex7_results(), min_peers=min_peers):
        if v.fair_value is None:
            continue
        assert v.fair_value / v.result.price == approx(v.peer_median_pb / v.result.pb)
        assert v.upside == approx(v.fair_value / v.result.price - 1)


def test_fair_value_property_on_irregular_data():
    pbs = [0.37, 0.91, 1.13, 1.77, 2.4, 3.05, 4.2, 0.66]
    prices = [12.5, 7.0, 103.2, 44.4, 9.99, 250.0, 1.5, 61.0]
    results = [
        compute_book_value(
            make_inputs(
                ticker=f"T{k}",
                price=price,
                total_equity=price / pb * 1000,
                shares_outstanding=1000,
                sector="S",
                industry="I",
            )
        )
        for k, (pb, price) in enumerate(zip(pbs, prices))
    ]
    vals = peer_relative(results)
    for v in vals:
        assert v.fair_value is not None
        assert v.fair_value / v.result.price == approx(v.peer_median_pb / v.result.pb)


# ---------------------------------------------------------------------------
# Spec v0.1.0 clarifications (added after QA review of the spec)
# ---------------------------------------------------------------------------

def test_filed_after_as_of_raises():
    with pytest.raises(ValueError):
        compute(as_of=date(2026, 6, 30), filed_date=date(2026, 7, 1))


def test_few_peers_empty_values(ex7):
    i = ex7["I"]
    assert i.n_peers == 0
    assert i.peer_median_pb is None and i.pb_percentile is None and i.peer_level is None


def test_neg_book_empty_values_without_few_peers(ex7):
    n = ex7["N"]
    assert n.n_peers == 0 and n.peer_level is None and n.peer_median_pb is None
    assert "FEW_PEERS" not in n.flags
