"""Bank of Canada Valet API: daily USD/CAD (CAD per 1 USD)."""

from __future__ import annotations

from datetime import date, timedelta

import requests

from .base import RawCache

SOURCE = "boc"
URL = "https://www.bankofcanada.ca/valet/observations/{series}/json"


def parse_observations(payload: dict, series: str = "FXUSDCAD") -> list[tuple[date, float]]:
    out = []
    for obs in payload.get("observations", []):
        v = obs.get(series, {}).get("v")
        if v not in (None, ""):
            out.append((date.fromisoformat(obs["d"]), float(v)))
    return out


class BocProvider:
    def __init__(self, cache: RawCache):
        self.cache = cache

    def usdcad(self, end: date, days: int = 30) -> dict:
        key = f"FXUSDCAD_{end.isoformat()}_{days}"
        raw = self.cache.get(SOURCE, key)
        if raw is None:
            params = {"start_date": (end - timedelta(days=days)).isoformat(),
                      "end_date": end.isoformat()}
            r = requests.get(URL.format(series="FXUSDCAD"), params=params, timeout=30)
            r.raise_for_status()
            raw = r.json()
            self.cache.put(SOURCE, key, raw)
        return raw
