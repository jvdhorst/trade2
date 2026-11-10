"""Spec tests for the frozen v2 parameter set (docs/models/revision_v2_freeze.md §2).

`valuation.models.frozen.FROZEN_V2` must be built from the live module constants and its
canonical JSON must hash to the SHA-256 stated in the freeze doc. Any difference blocks the
holdout run. Written from the specs only.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import pytest
import yaml

import valuation.models as models_pkg
import valuation.models.composite as comp
import valuation.models.peer_regression as pr
import valuation.models.residual_income as ri
import valuation.models.shareholder_yield as sy
from valuation.models.frozen import FROZEN_V2, canonical_json, params_sha256

REPO_ROOT = Path(__file__).resolve().parents[2]
FREEZE_DOC = REPO_ROOT / "docs" / "models" / "revision_v2_freeze.md"
FROZEN_SHA = "6c6354c540bebc970709bc9a150b29db9e15d61a2b03f744de04ad21d0aa088e"


def doc_text() -> str:
    return FREEZE_DOC.read_text(encoding="utf-8")


def doc_json_block() -> str:
    m = re.search(r"```json\n(.*?)\n```", doc_text(), re.S)
    assert m, "no json block in freeze doc"
    return m.group(1)


def doc_stated_sha() -> str:
    m = re.search(r"\*\*SHA-256 = `([0-9a-f]{64})`\*\*", doc_text())
    assert m, "no stated SHA-256 in freeze doc"
    return m.group(1)


def ref_canonical(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


# ---------------------------------------------------------------------------
# Helpers and hash
# ---------------------------------------------------------------------------


def test_package_exposes_same_object():
    assert models_pkg.FROZEN_V2 is FROZEN_V2


@pytest.mark.parametrize("obj", [
    {"b": 1, "a": [1, 2.5, "x"]}, {"z": {"y": None, "x": True}}, [], {"u": "é"},
])
def test_canonical_json_definition(obj):
    assert canonical_json(obj) == ref_canonical(obj)
    assert canonical_json(obj).isascii()
    exp = hashlib.sha256(ref_canonical(obj).encode("utf-8")).hexdigest()
    assert params_sha256(obj) == exp


def test_frozen_hash_equals_stated_value():
    assert params_sha256(FROZEN_V2) == FROZEN_SHA


def test_doc_states_frozen_hash():
    assert doc_stated_sha() == FROZEN_SHA


def test_doc_json_block_is_canonical_and_hashes_to_stated_value():
    block = doc_json_block()
    obj = json.loads(block)
    assert ref_canonical(obj) == block
    assert hashlib.sha256(block.encode("utf-8")).hexdigest() == doc_stated_sha()


def test_frozen_equals_doc_json():
    assert json.loads(canonical_json(FROZEN_V2)) == json.loads(doc_json_block())
    assert canonical_json(FROZEN_V2) == doc_json_block()


def test_top_level_keys():
    assert set(FROZEN_V2) == {"backtest_bar", "composite", "liquidity", "peer_regression",
                              "residual_income", "shareholder_yield"}


# ---------------------------------------------------------------------------
# Built from the live code: cross-check against module constants
# ---------------------------------------------------------------------------


def _norm(v):
    """Sets / tuples as the JSON stores them (sets as sorted lists, tuples as lists)."""
    if isinstance(v, (set, frozenset)):
        return sorted(v)
    if isinstance(v, tuple):
        return list(v)
    if isinstance(v, dict):
        return {k: _norm(x) for k, x in v.items()}
    return v


RI_NAMES = ["ERP", "HORIZON", "ROE_FLOOR", "ROE_CAP", "DOMINANT_SHARE", "MODEL_VERSION",
            "RETENTION_DEFAULT", "G_CAP", "R_SHIFT", "BUY_MOS", "SELL_MOS", "PB_MAX",
            "PB_MAX_ASSET_LIGHT", "BOOK_SECTORS", "CYCLICAL_SECTORS", "ASSET_LIGHT_SECTORS"]
PR_NAMES = ["MIN_FIT", "MIN_R2", "WINSOR", "BOOK_SECTORS", "EV_SUB_INDUSTRIES", "STATUS",
            "MODEL_VERSION", "BUY_PCT", "SELL_PCT", "BUY_UPSIDE", "SELL_UPSIDE"]
COMP_NAMES = ["MODEL_VERSION", "BOOK_SECTORS", "MARKETS", "WINSOR", "MIN_SECTOR_NAMES",
              "MIN_FACTORS", "BUY_PCT", "SELL_PCT", "MOM_VETO_PCT", "MOM_SKIP", "MOM_LOOKBACK",
              "VOL_WINDOW", "ANNUALIZE", "STD_EPS", "FINANCIALS", "FACTORS"]
SY_NAMES = ["MODEL_VERSION", "BUY_SY", "SELL_SY", "MAX_ABS_BBY", "MAX_PAYOUT",
            "PREV_WINDOW_DAYS"]


@pytest.mark.parametrize("section,module,names", [
    ("residual_income", ri, RI_NAMES),
    ("peer_regression", pr, PR_NAMES),
    ("composite", comp, COMP_NAMES),
    ("shareholder_yield", sy, SY_NAMES),
], ids=["A", "B", "C", "H"])
def test_values_match_module_constants(section, module, names):
    for name in names:
        assert FROZEN_V2[section][name] == _norm(getattr(module, name)), (section, name)


def test_ev_sub_industries_sorted_list():
    assert FROZEN_V2["peer_regression"]["EV_SUB_INDUSTRIES"] == sorted(pr.EV_SUB_INDUSTRIES)


def test_sets_are_sorted_lists():
    for section, key in [("residual_income", "BOOK_SECTORS"), ("residual_income",
                         "ASSET_LIGHT_SECTORS"), ("residual_income", "CYCLICAL_SECTORS"),
                         ("composite", "BOOK_SECTORS"), ("peer_regression", "BOOK_SECTORS"),
                         ("peer_regression", "EV_SUB_INDUSTRIES")]:
        v = FROZEN_V2[section][key]
        assert isinstance(v, list) and v == sorted(v), (section, key)


def test_shared_book_sectors():
    assert (FROZEN_V2["residual_income"]["BOOK_SECTORS"]
            == FROZEN_V2["peer_regression"]["BOOK_SECTORS"]
            == FROZEN_V2["composite"]["BOOK_SECTORS"])


def test_liquidity_is_parsed_config():
    cfg = yaml.safe_load((REPO_ROOT / "config" / "liquidity.yaml").read_text())
    assert FROZEN_V2["liquidity"] == cfg


def test_hash_moves_when_a_value_moves():
    obj = json.loads(canonical_json(FROZEN_V2))
    obj["residual_income"]["ERP"] = 0.051
    assert params_sha256(obj) != FROZEN_SHA
