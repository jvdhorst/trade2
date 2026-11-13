"""Spec tests for the screen integration of the per-company fit (docs/models/per_company_fit.md
v0.1.0 §9, §6.4 last paragraph, §10 `valuation value --fit`, golden G6).

Written from the spec only:
    from valuation.report.screen import attach_fit
        # (df, fit: DataFrame | None, live_versions: dict) -> (DataFrame, list[str])
The screen frame uses the live `valuation.engine.SIGNAL_COLUMNS` (unchanged public API).
`attach_fit` is imported inside each test so each fails on its own until it exists.
"""

from __future__ import annotations

import math

import pandas as pd
import pytest

from valuation.engine import SIGNAL_COLUMNS

FIT_HASH = "4d0a9e579c7d41a703ba4f2b0fdfd08f4883cb8315cb9b82e4c172313cd4b926"
MODELS = ("A", "B", "C", "H", "J", "G", "D", "E", "F")
FIT_COLS = [f"{k.lower()}_fit" for k in MODELS]
HL_COLS = [f"{k.lower()}_highlight" for k in MODELS]
NEW_COLS = FIT_COLS + HL_COLS + ["highlight", "highlight_models"]
assert NEW_COLS[:10] == ["a_fit", "b_fit", "c_fit", "h_fit", "j_fit", "g_fit", "d_fit", "e_fit",
                         "f_fit", "a_highlight"]
FIT_COLUMNS = [
    "run_id", "fit_version", "fit_params_hash", "market", "ticker", "model", "model_version",
    "variant", "is_primary",
    "sel_n_months", "sel_n_active", "sel_n_switches", "sel_ann_excess", "sel_t", "sel_p", "sel_q",
    "conf_n_months", "conf_n_active", "conf_n_switches", "conf_ann_excess", "conf_t", "conf_p",
    "status", "reason"]
LIVE = {"A": "residual_income/0.2.0", "B": "peer_regression/0.2.0", "C": "composite/0.2.0",
        "H": "shareholder_yield/0.1.0", "J": "ebit_yield_roc/0.1.0", "G": "altman_z/0.1.0",
        "D": "dividend_sustainability/0.1.0", "E": "piotroski/0.1.0", "F": "accruals/0.1.0"}
RUN_ID = "abc123def456"
RUN_LINE = (f"fit: company_model_fit run {RUN_ID} (fit/0.1.0, params {FIT_HASH[:16]}); "
            "CONFIRMED = worked in both in-sample windows (2014-2019, 2020-2023), not a "
            "forecast; survivor-biased")
NO_FIT_LINE = "fit: no company_model_fit results; all <m>_fit = NOT_TESTED"
G_STALE_LINE = "fit: model G fitted on altman_z/0.0.9, live altman_z/0.1.0 -> NOT_TESTED"


def attach():
    from valuation.report.screen import attach_fit

    return attach_fit


def live_versions_from_modules():
    from fit_db_fixture import live_versions

    return live_versions()


def test_live_fixture_versions_match_modules():  # guards the LIVE constant used below
    assert live_versions_from_modules() == LIVE


def screen_row(ticker, market="US", **labels):
    r = {c: None for c in SIGNAL_COLUMNS}
    r.update(ticker=ticker, market=market, sector="Energy", eligible=True, flags="")
    for c in SIGNAL_COLUMNS:
        if c.endswith("_signal"):
            r[c] = "NO_SIGNAL"
    r.update(labels)
    return r


def screen(rows):
    return pd.DataFrame(rows, columns=SIGNAL_COLUMNS)


def fit_row(ticker, model, status, variant="SELL_EXIT", version=None, run_id=RUN_ID):
    r = {c: math.nan for c in FIT_COLUMNS}
    r.update(run_id=run_id, fit_version="fit/0.1.0", fit_params_hash=FIT_HASH, market="US",
             ticker=ticker, model=model, model_version=version or LIVE[model], variant=variant,
             is_primary=variant == "SELL_EXIT", status=status, reason="OK")
    return r


def fit_frame(rows):
    return pd.DataFrame(rows, columns=FIT_COLUMNS)


def strip(lines):
    # SPEC-QUESTION: §9.2 prints the header lines with a leading "# " (CSV comment form); whether
    # attach_fit returns them with or without that prefix is not stated. Both are accepted.
    return [ln[2:] if ln.startswith("# ") else ln for ln in lines]


def g6():
    df = screen([
        screen_row("XOM", a_signal="HOLD", a_model_signal="SELL", c_signal="HOLD",
                   g_signal="HOLD"),
        screen_row("MSFT", b_signal="BUY", e_signal="NO_SIGNAL"),
        screen_row("RY", market="TSX", a_signal="BUY", a_model_signal="BUY", c_signal="BUY"),
    ])
    fit = fit_frame([
        fit_row("XOM", "A", "CONFIRMED"),
        fit_row("XOM", "C", "NOT_CONFIRMED"),
        fit_row("XOM", "G", "CONFIRMED", version="altman_z/0.0.9"),
        fit_row("MSFT", "B", "CONFIRMED"),
        fit_row("MSFT", "E", "CONFIRMED"),
        # a US fit row for a TSX screen row's ticker must not be used (§9.1, non-US market)
        fit_row("RY", "A", "CONFIRMED"), fit_row("RY", "C", "CONFIRMED"),
    ])
    return df, fit


def by_ticker(out):
    return {r["ticker"]: r for r in out.to_dict("records")}


# ---------------------------------------------------------------------------
# Columns
# ---------------------------------------------------------------------------


def test_columns_appended_after_flags_existing_unchanged():
    df, fit = g6()
    out, _ = attach()(df.copy(), fit, LIVE)
    assert list(out.columns) == SIGNAL_COLUMNS + NEW_COLS
    assert SIGNAL_COLUMNS[-1] == "flags"
    pd.testing.assert_frame_equal(out[SIGNAL_COLUMNS].reset_index(drop=True),
                                  df.reset_index(drop=True))
    assert list(out["ticker"]) == ["XOM", "MSFT", "RY"]  # row order unchanged


def test_value_types():
    df, fit = g6()
    out, _ = attach()(df, fit, LIVE)
    for c in FIT_COLS:
        assert set(out[c]) <= {"CONFIRMED", "NOT_CONFIRMED", "INSUFFICIENT", "NOT_TESTED"}
    for c in HL_COLS + ["highlight"]:
        assert all(isinstance(v, bool) for v in out[c].tolist()) or out[c].dtype == bool, c
    assert all(isinstance(v, str) for v in out["highlight_models"])


# ---------------------------------------------------------------------------
# G6 golden
# ---------------------------------------------------------------------------


def test_g6_xom():
    df, fit = g6()
    out, header = attach()(df, fit, LIVE)
    x = by_ticker(out)["XOM"]
    assert x["a_fit"] == "CONFIRMED"
    assert x["c_fit"] == "NOT_CONFIRMED"
    assert x["g_fit"] == "NOT_TESTED"  # model_version mismatch (stale fit)
    for k in ("b", "h", "j", "d", "e", "f"):
        assert x[f"{k}_fit"] == "NOT_TESTED", k
    assert bool(x["a_highlight"]) is True
    for k in ("b", "c", "h", "j", "g", "d", "e", "f"):
        assert bool(x[f"{k}_highlight"]) is False, k
    assert bool(x["highlight"]) is True
    assert x["highlight_models"] == "A"


def test_g6_msft_b_never_sets_row_flag():
    df, fit = g6()
    out, _ = attach()(df, fit, LIVE)
    x = by_ticker(out)["MSFT"]
    assert x["b_fit"] == "CONFIRMED" and x["e_fit"] == "CONFIRMED"
    assert bool(x["b_highlight"]) is True
    assert bool(x["e_highlight"]) is False  # NO_SIGNAL is no instruction
    assert bool(x["highlight"]) is False
    assert x["highlight_models"] == ""


def test_g6_ry_non_us_all_not_tested():
    df, fit = g6()
    out, _ = attach()(df, fit, LIVE)
    x = by_ticker(out)["RY"]
    for c in FIT_COLS:
        assert x[c] == "NOT_TESTED", c
    for c in HL_COLS + ["highlight"]:
        assert bool(x[c]) is False, c
    assert x["highlight_models"] == ""


def test_g6_header_lines():
    df, fit = g6()
    _, header = attach()(df, fit, LIVE)
    lines = strip(header)
    assert RUN_LINE in lines
    assert lines.count(G_STALE_LINE) == 1
    assert NO_FIT_LINE not in lines
    assert not any(ln.startswith("fit: model ") and ln != G_STALE_LINE for ln in lines)


def test_fit_none():
    df, _ = g6()
    out, header = attach()(df, None, LIVE)
    assert list(out.columns) == SIGNAL_COLUMNS + NEW_COLS
    assert (out[FIT_COLS] == "NOT_TESTED").all().all()
    for c in HL_COLS + ["highlight"]:
        assert not out[c].astype(bool).any(), c
    assert (out["highlight_models"] == "").all()
    assert strip(header) == [NO_FIT_LINE]


# ---------------------------------------------------------------------------
# §9.1 NOT_TESTED fallbacks and SELL_EXIT only
# ---------------------------------------------------------------------------


def test_ticker_missing_from_fit_is_not_tested():
    df = screen([screen_row("AAPL", c_signal="BUY")])
    fit = fit_frame([fit_row("XOM", "C", "CONFIRMED")])
    out, _ = attach()(df, fit, LIVE)
    assert (out[FIT_COLS] == "NOT_TESTED").all().all()
    assert not bool(out["highlight"].iloc[0])


def test_buy_only_status_never_drives_fit_column():  # §6.4: status used = SELL_EXIT status
    df = screen([screen_row("XOM", c_signal="BUY", h_signal="BUY")])
    fit = fit_frame([fit_row("XOM", "C", "CONFIRMED", variant="BUY_ONLY"),
                     fit_row("XOM", "C", "NOT_CONFIRMED", variant="SELL_EXIT"),
                     fit_row("XOM", "H", "CONFIRMED", variant="BUY_ONLY")])
    out, _ = attach()(df, fit, LIVE)
    r = by_ticker(out)["XOM"]
    assert r["c_fit"] == "NOT_CONFIRMED"
    assert r["h_fit"] == "NOT_TESTED"  # only a BUY_ONLY row: no SELL_EXIT row -> NOT_TESTED
    assert not bool(r["highlight"])


@pytest.mark.parametrize("status", ["CONFIRMED", "NOT_CONFIRMED", "INSUFFICIENT"])
def test_fit_column_copies_status(status):
    df = screen([screen_row("XOM", j_signal="HOLD")])
    out, _ = attach()(df, fit_frame([fit_row("XOM", "J", status)]), LIVE)
    assert out["j_fit"].iloc[0] == status
    assert bool(out["j_highlight"].iloc[0]) is (status == "CONFIRMED")


@pytest.mark.parametrize("model", MODELS)
def test_stale_version_per_model(model):
    df = screen([screen_row("XOM", **{f"{model.lower()}_signal": "SELL",
                                      "a_model_signal": "SELL"})])
    fit = fit_frame([fit_row("XOM", model, "CONFIRMED", version="old/0.0.1")])
    out, header = attach()(df, fit, LIVE)
    assert out[f"{model.lower()}_fit"].iloc[0] == "NOT_TESTED"
    lines = strip(header)
    exp = f"fit: model {model} fitted on old/0.0.1, live {LIVE[model]} -> NOT_TESTED"
    assert lines.count(exp) == 1


def test_stale_header_one_line_per_model_not_per_ticker():
    df = screen([screen_row("XOM", g_signal="HOLD"), screen_row("CVX", g_signal="SELL")])
    fit = fit_frame([fit_row("XOM", "G", "CONFIRMED", version="altman_z/0.0.9"),
                     fit_row("CVX", "G", "NOT_CONFIRMED", version="altman_z/0.0.9")])
    out, header = attach()(df, fit, LIVE)
    assert (out["g_fit"] == "NOT_TESTED").all()
    assert strip(header).count(G_STALE_LINE) == 1


# ---------------------------------------------------------------------------
# §9.2 highlights
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("label,hl", [("BUY", True), ("HOLD", True), ("SELL", True),
                                      ("NO_SIGNAL", False), ("NOT_ELIGIBLE", False),
                                      (None, False)])
def test_highlight_needs_instruction_label(label, hl):
    df = screen([screen_row("XOM", d_signal=label)])
    out, _ = attach()(df, fit_frame([fit_row("XOM", "D", "CONFIRMED")]), LIVE)
    assert bool(out["d_highlight"].iloc[0]) is hl
    assert bool(out["highlight"].iloc[0]) is hl
    assert out["highlight_models"].iloc[0] == ("D" if hl else "")


def test_a_uses_a_model_signal_not_screen_a_signal():
    # Rule-pinning rows: the label that counts for A is a_model_signal (§9.2).
    df = screen([screen_row("P1", a_signal="HOLD", a_model_signal="NO_SIGNAL"),
                 screen_row("P2", a_signal="NO_SIGNAL", a_model_signal="BUY"),
                 screen_row("P3", a_signal="HOLD", a_model_signal="SELL")])
    fit = fit_frame([fit_row(t, "A", "CONFIRMED") for t in ("P1", "P2", "P3")])
    out, _ = attach()(df, fit, LIVE)
    r = by_ticker(out)
    assert bool(r["P1"]["a_highlight"]) is False
    assert bool(r["P2"]["a_highlight"]) is True
    assert bool(r["P3"]["a_highlight"]) is True
    assert r["P3"]["highlight_models"] == "A"


def test_highlight_models_in_fit_models_order():
    df = screen([screen_row("XOM", h_signal="BUY", j_signal="SELL", g_signal="HOLD",
                            f_signal="BUY", b_signal="BUY", c_signal="NO_SIGNAL")])
    fit = fit_frame([fit_row("XOM", k, "CONFIRMED") for k in ("F", "G", "J", "H", "B", "C")])
    out, _ = attach()(df, fit, LIVE)
    r = by_ticker(out)["XOM"]
    assert r["highlight_models"] == "H,J,G,F"  # FIT_MODELS order, B excluded, C no instruction
    assert bool(r["b_highlight"]) is True and bool(r["c_highlight"]) is False
    assert bool(r["highlight"]) is True


def test_only_b_confirmed_never_highlights_row():
    df = screen([screen_row("XOM", b_signal="SELL")])
    out, _ = attach()(df, fit_frame([fit_row("XOM", "B", "CONFIRMED")]), LIVE)
    assert bool(out["b_highlight"].iloc[0]) is True
    assert bool(out["highlight"].iloc[0]) is False
    assert out["highlight_models"].iloc[0] == ""


def test_empty_screen_frame():
    out, header = attach()(screen([]), fit_frame([fit_row("XOM", "A", "CONFIRMED")]), LIVE)
    assert list(out.columns) == SIGNAL_COLUMNS + NEW_COLS and len(out) == 0
    assert RUN_LINE in strip(header)


# ---------------------------------------------------------------------------
# §10 CLI flag
# ---------------------------------------------------------------------------


def test_value_has_fit_flag(capsys):
    from valuation.cli import main

    with pytest.raises(SystemExit) as ei:
        main(["value", "--help"])
    assert ei.value.code == 0
    out = capsys.readouterr().out
    assert "--fit" in out
