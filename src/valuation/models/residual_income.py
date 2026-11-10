"""Model A: residual income value. Spec: docs/models/residual_income.md (v0.2.0).

Screening signal only, not investment advice. Labels: BUY / HOLD / SELL / NO_SIGNAL / NOT_ELIGIBLE.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

MODEL_VERSION = "residual_income/0.2.0"

ERP = 0.05
HORIZON = 10
ROE_FLOOR = -0.10
ROE_CAP = 0.35
RETENTION_DEFAULT = 0.6
G_CAP = 0.04
R_SHIFT = 0.01
DOMINANT_SHARE = 0.5  # v0.2.0: one quarter > half of a positive TTM NI is a one-off
BUY_MOS = 0.25
SELL_MOS = -0.20
PB_MAX = 8.0
PB_MAX_ASSET_LIGHT = 3.0
BUY_BLOCKED_IF_ROE_CLIPPED = True  # v0.2.0 (R2)
SELL_SHOWN_AS = "HOLD"  # v0.2.0 (R1): screen_signal for a model SELL

BOOK_SECTORS = frozenset({"Financials", "Utilities", "Real Estate", "Energy", "Materials"})
CYCLICAL_SECTORS = frozenset({"Energy", "Materials"})
ASSET_LIGHT_SECTORS = frozenset({
    "Information Technology", "Communication Services", "Health Care", "Industrials",
    "Consumer Discretionary", "Consumer Staples",
})

BUY, HOLD, SELL, NO_SIGNAL, NOT_ELIGIBLE = "BUY", "HOLD", "SELL", "NO_SIGNAL", "NOT_ELIGIBLE"

# flags
ROE_CLIPPED = "ROE_CLIPPED"
ROE_SPOT_EQUITY = "ROE_SPOT_EQUITY"
CYCLICAL_ROE = "CYCLICAL_ROE"
FAIR_VALUE_BOOK = "FAIR_VALUE_BOOK"
CSM_ADJUSTED = "CSM_ADJUSTED"
PIT_ESTIMATED = "PIT_ESTIMATED"
NO_TTM = "NO_TTM"
ONE_OFF_QUARTER = "ONE_OFF_QUARTER"
BUY_BLOCKED_CLIP = "BUY_BLOCKED_CLIP"
SELL_ZONE = "SELL_ZONE"


@dataclass(frozen=True)
class RIInputs:
    ticker: str
    as_of: date
    price: float
    bvps: float | None
    sector: str | None
    eligible: bool | None
    rf: float | None
    ni_common_ttm: float | None
    common_equity_end: float | None
    common_equity_start: float | None
    shares_outstanding: float
    fx_rate: float = 1.0
    dps_ttm: float = 0.0
    market: str = "US"
    ttm_flags: frozenset[str] = field(default_factory=frozenset)
    data_flags: frozenset[str] = field(default_factory=frozenset)
    filed_estimated: bool = False
    roe_history: tuple = ()
    csm_relevant: bool = False
    csm_after_tax: float | None = None
    ni_currency: str | None = None
    reporting_currency: str | None = None
    ni_quarters: tuple | None = None  # v0.2.0: the 4 quarterly NI values behind ni_common_ttm


@dataclass(frozen=True)
class RIResult:
    ticker: str
    as_of: date
    market: str
    sector: str | None
    price: float
    signal: str
    no_signal_reason: str | None
    roe_raw: float | None
    roe0: float | None
    roe_basis: str | None
    r: float | None
    b: float | None
    eps_ttm: float | None
    b0: float | None
    pb: float | None
    v_ri: float | None
    mos: float | None
    v_ri_up: float | None
    mos_up: float | None
    v_ri_down: float | None
    mos_down: float | None
    justified_pb: float | None
    flags: frozenset[str]
    screen_signal: str | None = None  # v0.2.0: what the screen shows (SELL -> HOLD)
    ni_norm: float | None = None
    roe_norm: float | None = None
    one_off: bool = False


def roe_raw(ni: float | None, ce_end: float | None,
            ce_start: float | None = None) -> tuple[float | None, frozenset[str]]:
    if ni is None or ce_end is None:
        return None, frozenset()
    if ce_start is None:
        avg, flags = ce_end, frozenset({ROE_SPOT_EQUITY})
    else:
        avg, flags = (ce_end + ce_start) / 2, frozenset()
    if avg <= 0:
        return None, flags
    return ni / avg, flags


def clip_roe(x: float) -> tuple[float, bool]:
    y = min(max(x, ROE_FLOOR), ROE_CAP)
    return y, y != x


def retention(dps_ttm: float, eps_ttm: float) -> float:
    if eps_ttm <= 0:
        return RETENTION_DEFAULT
    return min(max(1 - dps_ttm / eps_ttm, 0.0), 1.0)


def fade_path(roe0: float, r: float, horizon: int = HORIZON) -> list[float]:
    return [r + (roe0 - r) * (horizon - t) / horizon for t in range(1, horizon + 1)]


def ri_value(b0: float, roe0: float, r: float, b: float, horizon: int = HORIZON) -> float:
    book, value = b0, b0
    for t, roe_t in enumerate(fade_path(roe0, r, horizon), 1):
        value += (roe_t - r) * book / (1 + r) ** t
        book *= 1 + roe_t * b
    return value


def justified_pb(roe0: float, r: float, b: float) -> float | None:
    g = min(b * roe0, G_CAP)
    if r <= g:
        return None
    return (roe0 - g) / (r - g)


def map_signal(mos: float, mos_up: float, mos_down: float) -> str:
    if mos >= BUY_MOS and mos_up > 0:
        return BUY
    if mos <= SELL_MOS and mos_down < 0:
        return SELL
    return HOLD


def one_off_quarter(ni: float | None, quarters) -> tuple[bool, float | None]:
    """v0.2.0 dominant-quarter rule: (one_off, ni_norm)."""
    if ni is None:
        return False, None
    q = None if quarters is None else list(quarters)
    if (ni > 0 and q is not None and len(q) == 4 and all(x is not None for x in q)
            and max(q) > DOMINANT_SHARE * ni):
        return True, (ni - max(q)) * 4 / 3
    return False, ni


def evaluate(inp: RIInputs) -> RIResult:
    if inp.price is None or inp.price <= 0:
        raise ValueError(f"{inp.ticker}: price must be > 0")
    if inp.shares_outstanding is None or inp.shares_outstanding <= 0:
        raise ValueError(f"{inp.ticker}: shares_outstanding must be > 0")
    if inp.fx_rate is None or inp.fx_rate <= 0:
        raise ValueError(f"{inp.ticker}: fx_rate must be > 0")
    if inp.market not in ("TSX", "US"):
        raise ValueError(f"{inp.ticker}: market must be TSX or US, got {inp.market!r}")

    flags: set[str] = set(inp.ttm_flags) | set(inp.data_flags)
    ccy_mismatch = bool(inp.ni_currency and inp.reporting_currency
                        and inp.ni_currency != inp.reporting_currency)
    csm_missing = inp.csm_relevant and inp.csm_after_tax is None
    csm_applies = inp.csm_relevant and inp.csm_after_tax is not None
    add = inp.csm_after_tax if csm_applies else 0.0

    ni = None if ccy_mismatch else inp.ni_common_ttm
    eps = None if ni is None else ni / inp.shares_outstanding * inp.fx_rate
    b = None if eps is None else retention(inp.dps_ttm, eps)

    if csm_missing:
        b0 = None
    elif csm_applies:
        b0 = (None if inp.common_equity_end is None
              else (inp.common_equity_end + add) / inp.shares_outstanding * inp.fx_rate)
        flags.add(CSM_ADJUSTED)
    else:
        b0 = inp.bvps
    pb = inp.price / b0 if b0 is not None and b0 > 0 else None

    one_off, ni_norm = one_off_quarter(ni, inp.ni_quarters)
    if csm_missing:
        r_raw, rflags = None, frozenset()
        r_norm = None
    else:
        ce_end = None if inp.common_equity_end is None else inp.common_equity_end + add
        ce_start = None if inp.common_equity_start is None else inp.common_equity_start + add
        r_raw, rflags = roe_raw(ni, ce_end, ce_start)
        r_norm = roe_raw(ni_norm, ce_end, ce_start)[0] if one_off else r_raw
        if r_raw is None:
            r_norm = None
    flags |= rflags
    if one_off:
        flags.add(ONE_OFF_QUARTER)

    cyclical = inp.sector in CYCLICAL_SECTORS
    if cyclical:
        flags.add(CYCLICAL_ROE)
    h = inp.roe_history
    roe_base, basis = r_norm, (None if r_norm is None else "ttm")
    if (r_norm is not None and cyclical and len(h) >= 2
            and h[0] is not None and h[1] is not None):
        roe_base, basis = (r_norm + h[0] + h[1]) / 3, "3y_avg"
    roe0 = None
    if roe_base is not None:
        roe0, clipped = clip_roe(roe_base)
        if clipped:
            flags.add(ROE_CLIPPED)

    r = None if inp.rf is None else inp.rf + ERP
    v = v_up = v_dn = mos = mos_up = mos_dn = jpb = None
    if roe0 is not None and r is not None and b is not None:
        jpb = justified_pb(roe0, r, b)
        if pb is not None:
            v = ri_value(b0, roe0, r, b)
            v_up = ri_value(b0, roe0, r + R_SHIFT, b)
            v_dn = ri_value(b0, roe0, r - R_SHIFT, b)
            mos, mos_up, mos_dn = (v / inp.price - 1, v_up / inp.price - 1,
                                   v_dn / inp.price - 1)

    if inp.sector == "Real Estate" and inp.market == "TSX":
        flags.add(FAIR_VALUE_BOOK)
    if inp.filed_estimated:
        flags.add(PIT_ESTIMATED)
    if inp.ni_common_ttm is None:
        flags.add(NO_TTM)

    reason = None
    if inp.eligible is not True:
        signal = NOT_ELIGIBLE
    else:
        sector_ok = bool(inp.sector) and (inp.sector in BOOK_SECTORS
                                          or inp.sector in ASSET_LIGHT_SECTORS)
        checks = [
            ("NO_SECTOR", not sector_ok),
            ("NEEDS_CSM_INPUT", csm_missing),
            ("NEG_BOOK", b0 is None or b0 <= 0),
            ("PB_TOO_HIGH", pb is not None and pb > PB_MAX),
            ("ASSET_LIGHT", inp.sector in ASSET_LIGHT_SECTORS and pb is not None
             and pb > PB_MAX_ASSET_LIGHT),
            ("CCY_MISMATCH", ccy_mismatch),
            ("NO_TTM", inp.ni_common_ttm is None),
            ("NO_EQUITY", inp.common_equity_end is None),
            ("NEG_AVG_EQUITY", r_raw is None),
            ("NO_RF", inp.rf is None),
        ]
        reason = next((code for code, hit in checks if hit), None)
        if reason is not None:
            signal = NO_SIGNAL
        else:
            signal = map_signal(mos, mos_up, mos_dn)
            if signal == BUY and ROE_CLIPPED in flags and BUY_BLOCKED_IF_ROE_CLIPPED:
                signal = HOLD
                flags.add(BUY_BLOCKED_CLIP)
    screen = signal
    if signal == SELL:
        screen = SELL_SHOWN_AS
        flags.add(SELL_ZONE)

    return RIResult(
        ticker=inp.ticker, as_of=inp.as_of, market=inp.market, sector=inp.sector,
        price=inp.price, signal=signal, no_signal_reason=reason, roe_raw=r_raw, roe0=roe0,
        roe_basis=basis, r=r, b=b, eps_ttm=eps, b0=b0, pb=pb, v_ri=v, mos=mos, v_ri_up=v_up,
        mos_up=mos_up, v_ri_down=v_dn, mos_down=mos_dn, justified_pb=jpb,
        flags=frozenset(flags), screen_signal=screen, ni_norm=ni_norm, roe_norm=r_norm,
        one_off=one_off,
    )
