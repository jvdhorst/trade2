"""Frozen parameter set of revision round v2 (docs/models/revision_v2_freeze.md §2).

`FROZEN_V2` is built from the modules' own constants, never typed in by hand, so a code change to
any frozen constant changes `params_sha256(FROZEN_V2)` and blocks the one-shot holdout run.
"""

from __future__ import annotations

import hashlib
import json

from ..backtest import engine as bte
from ..backtest import metrics as btm
from . import composite as cm
from . import eligibility as elig
from . import peer_regression as pr
from . import residual_income as ri
from . import shareholder_yield as sy

FROZEN_SHA256 = "6c6354c540bebc970709bc9a150b29db9e15d61a2b03f744de04ad21d0aa088e"
FROZEN_VERSIONS = (ri.MODEL_VERSION, pr.MODEL_VERSION, cm.MODEL_VERSION, sy.MODEL_VERSION)


def canonical_json(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def params_sha256(obj) -> str:
    return hashlib.sha256(canonical_json(obj).encode("utf-8")).hexdigest()


def _pick(module, names: list[str]) -> dict:
    out = {}
    for k in names:
        v = getattr(module, k)
        if isinstance(v, frozenset | set):
            v = sorted(v)
        elif isinstance(v, tuple):
            v = list(v)
        out[k] = v
    return out


def build_frozen() -> dict:
    composite = _pick(cm, ["ANNUALIZE", "BOOK_SECTORS", "BP_SECTORS", "BUY_PCT", "CROSS_SECTION",
                           "FINANCIALS", "MARKETS", "MIN_FACTORS", "MIN_SECTOR_NAMES",
                           "MODEL_VERSION", "MOM_LOOKBACK", "MOM_SKIP", "MOM_VETO_PCT",
                           "SELL_PCT", "STD_EPS", "VOL_WINDOW", "WEIGHTS", "WINSOR"])
    composite["FACTORS"] = {k: list(v) for k, v in cm.FACTORS.items()}
    return {
        "backtest_bar": {"HOLDOUT_N": bte.HOLDOUT_MONTHS, "PASS_HORIZON": btm.PASS_HORIZON,
                         "PASS_IC": btm.PASS_IC, "PASS_T": btm.PASS_T,
                         "PASS_YEAR_SHARE": btm.PASS_YEAR_SHARE},
        "composite": composite,
        "liquidity": elig.load_config(),
        "peer_regression": _pick(pr, ["BOOK_SECTORS", "BUY_PCT", "BUY_UPSIDE",
                                      "EV_SUB_INDUSTRIES", "MIN_FIT", "MIN_R2", "MODEL_VERSION",
                                      "SELL_PCT", "SELL_UPSIDE", "STATUS", "WINSOR"]),
        "residual_income": _pick(ri, [
            "ASSET_LIGHT_SECTORS", "BOOK_SECTORS", "BUY_BLOCKED_IF_ROE_CLIPPED", "BUY_MOS",
            "CYCLICAL_SECTORS", "DOMINANT_SHARE", "ERP", "G_CAP", "HORIZON", "MODEL_VERSION",
            "PB_MAX", "PB_MAX_ASSET_LIGHT", "RETENTION_DEFAULT", "ROE_CAP", "ROE_FLOOR",
            "R_SHIFT", "SELL_MOS", "SELL_SHOWN_AS"]),
        "shareholder_yield": _pick(sy, ["BUY_SY", "MAX_ABS_BBY", "MAX_PAYOUT", "MODEL_VERSION",
                                        "PREV_WINDOW_DAYS", "SELL_SY"]),
    }


FROZEN_V2 = build_frozen()
