"""One-shot holdout guard (docs/models/revision_v2_freeze.md §3, §4).

`check_holdout_guard` must pass before ANY holdout computation starts. It refuses when:
  (a) the code's frozen parameters (`FROZEN_V2`) don't hash to the SHA-256 stated in the freeze doc;
  (b) the freeze doc's own JSON block doesn't hash to its stated value (the doc was edited);
  (c) the holdout result file already exists (the holdout is spent);
  (d) `bt_runs` already has a kind='holdout' row for any of the four frozen model versions.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

DEFAULT_RESULT = "docs/qa/holdout_v2.md"
_SHA_RE = re.compile(r"\*\*SHA-256 = `([0-9a-f]{64})`\*\*")
_JSON_RE = re.compile(r"```json\n(.*?)\n```", re.S)


class HoldoutRefused(Exception):
    """The one-shot holdout run is not allowed (reason in the message)."""


def stated_sha256(freeze_path) -> str:
    text = Path(freeze_path).read_text()
    hits = _SHA_RE.findall(text)
    if len(hits) != 1:
        raise HoldoutRefused(f"freeze doc {freeze_path}: expected exactly one stated SHA-256, "
                             f"found {len(hits)}")
    return hits[0]


def _doc_json(freeze_path) -> dict:
    hits = _JSON_RE.findall(Path(freeze_path).read_text())
    if len(hits) != 1:
        raise HoldoutRefused(f"freeze doc {freeze_path}: expected exactly one JSON block, "
                             f"found {len(hits)}")
    return json.loads(hits[0])


def check_holdout_guard(freeze_path, result_path, con=None) -> str:
    """Return the verified parameter hash, or raise HoldoutRefused."""
    from ..models.frozen import FROZEN_V2, FROZEN_VERSIONS, params_sha256

    if freeze_path is None or not Path(freeze_path).is_file():
        raise HoldoutRefused(f"freeze doc not found: {freeze_path}")
    stated = stated_sha256(freeze_path)
    doc_hash = params_sha256(_doc_json(freeze_path))
    if doc_hash != stated:
        raise HoldoutRefused(f"freeze doc JSON hashes to {doc_hash}, doc states {stated}")
    code_hash = params_sha256(FROZEN_V2)
    if code_hash != stated:
        raise HoldoutRefused(f"code parameters hash to {code_hash}, freeze doc states {stated}")
    if result_path is not None and Path(result_path).exists():
        raise HoldoutRefused(f"holdout result already exists: {result_path} (one shot only)")
    if con is not None:
        has = con.execute("SELECT count(*) FROM information_schema.tables "
                          "WHERE table_name = 'bt_runs'").fetchone()[0]
        if has:
            rows = con.execute("SELECT run_id, model_version FROM bt_runs "
                               "WHERE kind = 'holdout'").fetchall()
            for run_id, mv in rows:
                if any(v in (mv or "") for v in FROZEN_VERSIONS):
                    raise HoldoutRefused(f"bt_runs already has holdout run {run_id} for the "
                                         f"frozen versions ({mv}); the holdout is spent")
    return code_hash
