# Model spec: "Well-traded" eligibility filter (`eligibility`, v0.1.1)

Owner: AN · Tests: QA (`tests/unit/test_eligibility_spec.py`) · Code: `src/valuation/models/eligibility.py` ·
Config: `config/liquidity.yaml`

## Purpose
A gate that runs before the BUY / HOLD / SELL models (proposal `docs/models/proposals/buy_hold_sell.md` §1). It keeps
out names a buy-and-hold investor cannot build a position in, and names whose prices are stale or easy to move.
A name that fails any of E1–E5 gets `NOT_ELIGIBLE` and no signal. E6 does **not** make a name ineligible: it only
blocks Model C (`NO_SIGNAL`), while Models A and B may still run.

## Inputs (per ticker, at valuation date `as_of`)
| Field | Meaning | Unit |
|---|---|---|
| `ticker` | Listed symbol (for multi-class names, the **listed class we report on**, e.g. BRK-B, CTC-A.TO) | str |
| `as_of` | Valuation date | date |
| `market` | `"TSX"` (trading currency CAD) or `"US"` (USD). Any other value → `ValueError` | str |
| `index_member` | E1: the ticker is in the universe file on `as_of` (the universe file is the point-in-time membership for now) | bool |
| `bars` | Daily bars of **this listing**, each with `date`, `close`, `adj_close`, `volume` (`close`, `adj_close`, `volume` may be `None`). Any order; dates must be unique, else `ValueError` | trading ccy, shares |
| `market_cap` | `price × shares_outstanding`, **all classes**, as already computed; may be `None` | trading ccy |
| `market_sessions` | Optional. Dates on which the market traded (e.g. the union of bar dates over all tickers of that market) | list[date] |

### Look-ahead rule
Bars with `date > as_of` are dropped before anything else and never affect any output. Sessions with
`d > as_of` are likewise never counted.

## Config (`config/liquidity.yaml`)
All money values are in the trading currency of the market.
```yaml
tsx:
  min_mdtv: 5_000_000
  min_market_cap: 1_000_000_000
  min_price: 2
  mdtv_window: 63
  max_zero_volume_days: 3
  max_stale_sessions: 5
  min_history_sessions: 274
us:
  min_mdtv: 25_000_000
  min_market_cap: 5_000_000_000
  min_price: 5
  mdtv_window: 63
  max_zero_volume_days: 3
  max_stale_sessions: 5
  min_history_sessions: 274
```
`load_config` raises `ValueError` if a section (`tsx`, `us`) or any of the seven keys is missing, or if a value is
not a number (PyYAML reads `1e9` as a string; write `1_000_000_000` or `1.0e+9`). The thresholds
section used is `config["tsx"]` for `market == "TSX"` and `config["us"]` for `"US"`. The full file contents are
stored in `runs.config_json` under the key `"liquidity"` for every run.

## Derived quantities
Let `B` = the bars with `date <= as_of`, sorted by date ascending.
```
window            = the last mdtv_window bars of B (fewer if B is shorter)
tv_d              = close_d × volume_d            for bars in window with close and volume both not None
mdtv              = statistics.median(tv_d)       if count(tv_d) >= mdtv_window, else None
zero_volume_days  = count(bars in window with volume == 0)
last_close_bar    = the latest bar in B with close not None (None if there is none)
price             = last_close_bar.close          (None if no such bar)
stale_sessions    = count(d in market_sessions with last_close_bar.date < d <= as_of)
                    if market_sessions is None: count(Mon–Fri dates d with last_close_bar.date < d <= as_of)
                    None if last_close_bar is None
history_sessions  = count(bars in B with adj_close not None)
```
- `statistics.median` semantics: for an even count, the mean of the two middle values.
- `volume None` means missing data: the bar is left out of `tv_d` and is **not** a zero-volume day.
  `volume == 0` means no trading: it enters `tv_d` as 0 and **is** a zero-volume day.
- E2 needs a full window: every one of the last `mdtv_window` bars must have both `close` and `volume`. If not,
  `mdtv = None`, E2 fails and the flag `INSUFFICIENT_VOLUME_HISTORY` is set.
- The window is counted in the ticker's own bars, not in market sessions. A stale name's window ends at its last
  bar; E5's staleness test catches that case.

## Rules
All six rules are always evaluated (no short-circuit), so the removal report can count every failing rule.
Equality passes (≥ for minimums, ≤ for maximums).

| Rule | Passes when | `None` input |
|---|---|---|
| E1 | `index_member is True` | — |
| E2 | `mdtv >= min_mdtv` | `mdtv None` → fail |
| E3 | `market_cap >= min_market_cap` | `market_cap None` → fail |
| E4 | `price >= min_price` | `price None` → fail |
| E5 | `zero_volume_days <= max_zero_volume_days` **and** `stale_sessions <= max_stale_sessions` | `stale_sessions None` → fail |
| E6 | `history_sessions >= min_history_sessions` | — |

```
eligible           = E1 and E2 and E3 and E4 and E5
fail_rules         = tuple of the failing codes among "E1".."E5", in that order
elig_fail_reason   = ";".join(fail_rules)          ("" when eligible)
model_c_history_ok = E6
```

### Flags
| Flag | Condition |
|---|---|
| `SHORT_HISTORY` | E6 fails. Model C → `NO_SIGNAL`; does not change `eligible` |
| `INSUFFICIENT_VOLUME_HISTORY` | fewer than `mdtv_window` bars in the window with non-null `close` and `volume` |

No bars at all: `mdtv = price = stale_sessions = None`, `zero_volume_days = 0`, `history_sessions = 0` →
E2, E4, E5 fail; flags `{SHORT_HISTORY, INSUFFICIENT_VOLUME_HISTORY}`.

Multi-class names are tested on the listed class (its own bars and price); `market_cap` stays the all-class value.

## Worked examples (golden values for QA)
Unless stated otherwise: **config override** `mdtv_window: 5` and `min_history_sessions: 6` in both sections, all
other keys at the defaults above; `market_sessions = None` (business-day fallback); `as_of = 2026-03-13` (Fri);
`adj_close = close` on every bar. Dates are 2026. "Base bars" = 10 bars on Mar 2, 3, 4, 5, 6, 9, 10, 11, 12, 13.
Assert money values against the exact expressions (relative tolerance 1e-9).

**Ex 1: US pass.** US, member, market_cap 6e9. Base bars, close 100 on all. Volumes: Mar 2–6 all 300 000;
Mar 9–13 = 300 000, 250 000, 400 000, 260 000, 350 000. tv = 30M, 25M, 40M, 26M, 35M →
mdtv **100×300000 = 30 000 000**, price 100, zero 0, stale 0, history 10 →
eligible **True**, fail_rules `()`, reason `""`, model_c_history_ok True, flags ∅.

**Ex 2: TSX, E2 only.** TSX, member, market_cap 2e9. Base bars; Mar 9–13 closes 10, 11, 12, 13, 14, volumes
400 000, 500 000, 300 000, 450 000, 340 000 (earlier bars close 10, volume 400 000). tv = 4.0M, 5.5M, 3.6M,
5.85M, 4.76M; sorted 3.6M, 4.0M, **4.76M**, 5.5M, 5.85M → mdtv **14×340000 = 4 760 000** < 5M, price 14 →
eligible False, fail_rules `("E2",)`, reason `"E2"`.

**Ex 3: even-count median.** Override `mdtv_window: 4` (US section). US, member, market_cap 6e9. Base bars, close
50; Mar 10–13 volumes 600 000, 400 000, 500 000, 700 000 (others 600 000). tv sorted 20M, 25M, 30M, 35M →
mdtv **(50×500000 + 50×600000)/2 = 27 500 000** → eligible True.

**Ex 4: boundary equality.** Override `mdtv_window: 7` (US section). US, member, market_cap
**5 000 000 000**. Base bars, close **5**, volume **5 000 000**, except volume **0** on Mar 5, 6, 9. Window =
Mar 5, 6, 9, 10, 11, 12, 13; tv sorted 0, 0, 0, 25M, 25M, 25M, 25M → mdtv **5×5000000 = 25 000 000**, price 5,
zero_volume_days **3**, stale 0 → eligible **True** (mdtv, cap and price equal the minimums; zero days equal the max).

**Ex 4b: staleness boundary.** US, member, market_cap 6e9. Bars on Feb 23–27 and Mar 2–6 (10 bars), close 100,
volume 300 000. Last bar Mar 6; business days after it up to Mar 13: Mar 9, 10, 11, 12, 13 → stale **5** →
eligible True.

**Ex 5: zero-volume days, E2 and E5.** TSX, member, market_cap 2e9. Base bars, close 20, volume 1 000 000;
Mar 9–12 volume **0**. tv = 0, 0, 0, 0, 20M → mdtv **0**, zero_volume_days **4** > 3 →
fail_rules `("E2", "E5")`, reason `"E2;E5"`.

**Ex 6: stale quote, E5.** US, member, market_cap 6e9. Bars on Feb 23–27 and Mar 2–5 (9 bars), close 100,
volume 300 000. Last bar Mar 5; business days in (Mar 5, Mar 13]: Mar 6, 9, 10, 11, 12, 13 → stale **6** > 5 →
fail_rules `("E5",)`, reason `"E5"`, mdtv 30 000 000, history 9.
Same bars with `market_sessions = [Mar 5, 6, 10, 11, 12, 13]` (Mar 9 closed) → stale **5** → eligible True.

**Ex 7: price and missing cap, E3 + E4.** TSX, member, market_cap **None**. Base bars, close 1.50, volume
4 000 000 → mdtv **1.5×4000000 = 6 000 000** (passes), price 1.5 < 2 → fail_rules `("E3", "E4")`,
reason `"E3;E4"`.

**Ex 8: look-ahead.** Ex 1 plus a bar on Mar 16: close 1, adj_close 1, volume 0. Every output equals Ex 1.
Ex 1 with `as_of = Mar 12`: window Mar 6–12 = volumes 300 000, 300 000, 250 000, 400 000, 260 000 → mdtv
**30 000 000**, stale 0, history 9 → eligible True.

**Ex 9: short history stays eligible.** Ex 1 with only the bars of Mar 9–13 → history **5** < 6 →
eligible **True**, fail_rules `()`, model_c_history_ok **False**, flags `{SHORT_HISTORY}`.
Ex 1 with `adj_close = None` on Mar 2–6 → same result (history counts non-null `adj_close` only).

**Ex 10: not an index member.** Ex 1 with `index_member = False` → fail_rules `("E1",)`, reason `"E1"`.

**Ex 11: missing volume.** Ex 1 with `volume = None` on Mar 11 → only 4 tv values → mdtv **None**, E2 fails,
flags `{INSUFFICIENT_VOLUME_HISTORY}`, zero_volume_days **0** (None is not a zero day) → reason `"E2"`.

**Ex 12: no bars.** TSX, member, market_cap 2e9, `bars = []` → mdtv/price/stale None, zero 0, history 0 →
fail_rules `("E2", "E4", "E5")`, reason `"E2;E4;E5"`, flags `{SHORT_HISTORY, INSUFFICIENT_VOLUME_HISTORY}`.

## Removal-rate report
`removal_report(results)` over the results of one universe and one `as_of` (the CLI calls it once per market):
```
n               = len(results)
n_ineligible    = count(not r.eligible)
pct_ineligible  = 100 × n_ineligible / n            (0.0 when n == 0)
by_rule         = {"E1": k1, ..., "E5": k5}          k = names whose fail_rules contain the rule; all five keys
                                                      always present; a name counts once per failing rule
short_history   = count(not r.model_c_history_ok)    (eligible or not)
```
Example: results of Ex 1, 2, 5, 7, 9 → `n 5, n_ineligible 3, pct_ineligible 60.0,
by_rule {E1: 0, E2: 2, E3: 1, E4: 1, E5: 1}, short_history 1`.

Expected removal: under 10% of the S&P 500, about 15–25% of the TSX. The run report prints `REVIEW THRESHOLDS`
when `pct_ineligible` is above 15% (US) or 37.5% (TSX), i.e. 1.5× the upper expectation. That is a prompt for PM
review; the thresholds are **not** tuned automatically.

## API contract (for SW and QA)
```python
from valuation.models.eligibility import (
    Bar, EligibilityInputs, EligibilityResult, load_config, evaluate, removal_report, MODEL_VERSION,
)
```
- `Bar(date: date, close: float | None, adj_close: float | None, volume: float | None)`: a NamedTuple. `evaluate`
  also accepts any objects with these four attributes.
- `EligibilityInputs(ticker, as_of, market, index_member, bars, market_cap, market_sessions=None)`: dataclass.
- `load_config(path) -> dict`: parses the YAML and validates it as above.
- `evaluate(inputs: EligibilityInputs, config: dict) -> EligibilityResult`.
- `EligibilityResult` (frozen dataclass): `ticker, as_of, market, eligible: bool, fail_rules: tuple[str, ...],
  elig_fail_reason: str, mdtv, zero_volume_days: int, stale_sessions: int | None, history_sessions: int,
  price, market_cap, model_c_history_ok: bool, flags: frozenset[str]`.
- `removal_report(results: list[EligibilityResult]) -> dict` with keys `n, n_ineligible, pct_ineligible, by_rule,
  short_history`.
- `MODEL_VERSION = "eligibility/0.1.0"`

## Changelog
- **0.1.1:** revision round v2 (P9-6): `min_history_sessions` 273 → 274 in both markets, so Model C's MOM
  (t−21 / t−273, 274 bars) exists for every E6-passing name. `config/liquidity.yaml` changes with the implementation.
- **0.1.0:** initial version, from proposal §1.
