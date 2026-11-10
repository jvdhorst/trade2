"""Valuation models. `FROZEN_V2` (revision_v2_freeze.md §2) loads lazily (no import cycle)."""


def __getattr__(name):
    if name in ("FROZEN_V2", "FROZEN_SHA256", "FROZEN_VERSIONS"):
        from . import frozen

        return getattr(frozen, name)
    raise AttributeError(name)
