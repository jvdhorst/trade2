# Pre-registration: supplementary holdout confirmation test for Model H (v1)

Owner: PM · Decided by the user on 2026-11-11 · Status: **frozen before any holdout computation**

> Screening-signal validation only; not investment advice.

## Why this exists
`revision_v2_freeze.md` §4.4 judges every model by the unchanged `backtest.md` pass bar. Its IC leg (mean 12m IC > 0.03
with Newey–West t > 2) is **in-sample**. The R2 in-sample re-run (`docs/qa/r2-insample.md`) shows A 0.2.0, C 0.2.0 and
H 0.1.0 all failing that leg already, so the holdout run cannot turn any of them into a PASS, and the Phase 10 gate is
unreachable this round.

Model H is the only model with a consistently positive in-sample profile:
- 12m IC 0.030 (t 0.80)
- spread > 0 in 8 of 10 years
- BUY excess vs SPX_TR +0.65%/yr

The user therefore chose to spend the one-shot holdout on a **pre-registered confirmation test for H**, defined
here before anyone computes or sees a holdout-period number.

This document **adds** a supplementary test. It does **not** change `revision_v2_freeze.md`:
- the frozen parameters and hash `6c6354c5…088e` are unchanged
- the `pass_bar` verdicts are reported exactly as that document specifies
- the Phase 10 gate (≥ 2 PASS under the bar) is unchanged

This is the only exception to freeze §5 ("does not add criteria"), and it is recorded here with its reason.

## The run
Exactly the run in freeze §4.2: one `valuation backtest run --market US --holdout --freeze docs/models/revision_v2_freeze.md
--today 2026-11-10`, using the guarded command. It covers A, B, C and H together; the holdout is spent for all of them.
- The git commit containing this file must be an ancestor of the commit the run uses.
- The run report records that commit SHA next to the run id and both hashes.

## Supplementary test for H 0.1.0 (decided now; no other variant, horizon or threshold may be substituted)
**Holdout dates:** the 36 month-ends 2023-11-30 … 2026-10-31. **Primary horizon:** 12 months (`PASS_HORIZON`).

A holdout date counts toward the test only if its 12m forward returns are complete (`forward_returns` flag ok) for the
names involved. Expect about 24 of the 36 dates (2023-11 … ~2025-10). Let `n12` be the number of such dates with a
defined value for each metric below.

H is **CONFIRMED (supplementary)** if and only if **all three** hold over the holdout dates at h = 12:

| # | Metric (as defined in `backtest.md`) | Criterion |
|---|---|---|
| H1 | mean 12m rank IC of `h_score` vs forward return (`rank_ic`, min 20 names) | **> 0** |
| H2 | mean 12m sector-neutral BUY−SELL spread (`spread_by_date`) | **> 0** |
| H3 | mean 12m BUY-bucket excess vs the primary US benchmark SPX_TR (`excess_vs_benchmark`, `benchmarks.md`) | **> 0** |

Rules:
- **Strict inequalities:** a value of exactly 0 fails.
- **Minimum data:** if any of H1–H3 has `n12 < 18` defined dates, the result is **INCONCLUSIVE**. The holdout is spent all the same.
- **No other outcome counts:** neither a significance test, nor another horizon, nor a sub-period.
- **Reported, not part of the test:**
  - NW t of the holdout IC
  - BUY/SELL hit rates
  - 3m and 6m versions of H1–H3
  - the IC of the BBY and DY components
  - n12 per metric
  - turnover

**Outcome labels:** `CONFIRMED (supplementary)`, `NOT CONFIRMED`, or `INCONCLUSIVE`.

## Consequences (decided now)
- **CONFIRMED:**
  - H is labelled *"holdout-supported (supplementary); not validated under the backtest.md bar"* in the screen,
    report and docs.
  - Its signals may be shown first in the human review queue, with that label and the survivor-bias caveat.
  - The Phase 10 vote stays blocked: H is not a PASS.
- **NOT CONFIRMED or INCONCLUSIVE:** H stays *unvalidated*, like A and C.
- **A 0.2.0, C 0.2.0 and B:** their holdout numbers and `pass_bar` verdicts are reported for the record only. They
  cannot pass (freeze §4.4), and no conclusion beyond "unvalidated" is drawn.
- **Afterwards:**
  - No model may be evaluated again on 2023-11 … 2026-10 (freeze §4.5).
  - Further validation needs new out-of-sample month-ends (from 2026-11-30) or survivorship-free data plus a fresh period.

## Caveats stated in advance
- **Survivorship:** the holdout carries the same survivor bias as phase8 §3 (about 4% of member-dates missing in 2023–2026).
- **Sample size:** about 24 12-month observations that overlap heavily give low statistical power. CONFIRMED means "the in-sample sign held
  out of sample", not "statistically significant".
- **One market:** US only. The TSX history is too short for a holdout.
