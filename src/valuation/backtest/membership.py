"""Point-in-time S&P 500 membership from Wikipedia. Spec: docs/models/backtest.md §Membership.

The "Selected changes to the list of S&P 500 components" table (id `changes`) lists, per effective
date, the added and the removed ticker. Membership is rebuilt backwards from the current list.
"""

from __future__ import annotations

import io
import logging
import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, timedelta

import pandas as pd

log = logging.getLogger(__name__)
INDEX_NAME = "SP500"
SOURCE = "wikipedia:List_of_S&P_500_companies"
MEMBERSHIP_START = date(2013, 1, 1)
_FOOTNOTE = re.compile(r"\[[^\]]*\]")


@dataclass(frozen=True)
class Change:
    date: date
    added_ticker: str | None
    added_name: str | None
    removed_ticker: str | None
    removed_name: str | None


@dataclass(frozen=True)
class Interval:
    ticker: str
    start: date
    end: date | None  # inclusive; None = still a member


def norm_ticker(raw) -> str | None:
    """Wikipedia symbol -> Yahoo form: footnotes dropped, '.' -> '-', upper case."""
    if raw is None or (isinstance(raw, float) and pd.isna(raw)):
        return None
    t = _FOOTNOTE.sub("", str(raw)).strip().upper().replace(".", "-")
    t = re.sub(r"\s+", "", t)
    return None if not t or t in ("—", "-", "NAN") else t


norm = norm_ticker


def _clean(raw) -> str | None:
    if raw is None or (isinstance(raw, float) and pd.isna(raw)):
        return None
    s = _FOOTNOTE.sub("", str(raw)).strip()
    return s or None


def parse_current(html: str) -> list[str]:
    """Current constituents (first table, `Symbol` column), Yahoo form, sorted."""
    tables = pd.read_html(io.StringIO(html), attrs={"id": "constituents"})
    df = tables[0]
    col = next(c for c in df.columns if "symbol" in str(c).lower())
    out: list[str] = []
    for x in df[col]:
        t = norm_ticker(x)
        if t and t not in out:
            out.append(t)
    return out  # page order


def _flat(col) -> str:
    parts = col if isinstance(col, tuple) else (col,)
    return " ".join(str(p) for p in parts).lower()


def parse_changes(html: str) -> list[Change]:
    """Rows of the `changes` table in page order, read by position: Date | Added ticker,
    security | Removed ticker, security | Reason. Rows whose date is not "%B %d, %Y" are skipped."""
    df = pd.read_html(io.StringIO(html), attrs={"id": "changes"})[0]
    out = []
    for row in df.itertuples(index=False):
        raw = _clean(row[0])
        try:
            d = datetime.strptime(raw or "", "%B %d, %Y").date()
        except ValueError:
            log.info("changes table: skipped row with date %r", raw)
            continue
        ch = Change(d, norm_ticker(row[1]), _clean(row[2]), norm_ticker(row[3]), _clean(row[4]))
        if ch.added_ticker or ch.removed_ticker:
            out.append(ch)
    return out


def build_intervals(current: Iterable[str], changes: Iterable[Change],
                    start: date = MEMBERSHIP_START) -> list[Interval]:
    """Walk changes newest -> oldest from today's list (spec §1)."""
    open_: dict[str, date | None] = {t: None for t in current}
    out: list[Interval] = []
    by_date: dict[date, list[Change]] = {}
    for c in changes:
        if c.date >= start:
            by_date.setdefault(c.date, []).append(c)
    for d in sorted(by_date, reverse=True):
        day = by_date[d]
        added = {c.added_ticker for c in day if c.added_ticker}
        removed = {c.removed_ticker for c in day if c.removed_ticker}
        both = added & removed
        for x in sorted(added - both):
            if x in open_:
                out.append(Interval(x, d, open_.pop(x)))
            else:
                log.info("membership: %s added %s but not a member afterwards (skipped)", x, d)
        for y in sorted(removed - both):
            if y not in open_:
                open_[y] = d - timedelta(days=1)
            else:
                log.info("membership: %s removed %s but already a member before (skipped)", y, d)
        for z in sorted(both):
            if z not in open_:
                open_[z] = d - timedelta(days=1)
    for t, end in open_.items():
        out.append(Interval(t, start, end))
    out.sort(key=lambda i: (i.ticker, i.start))
    merged: list[Interval] = []
    for iv in out:
        prev = merged[-1] if merged else None
        if (prev and prev.ticker == iv.ticker and prev.end is not None
                and iv.start == prev.end + timedelta(days=1)):
            merged[-1] = Interval(iv.ticker, prev.start, iv.end)
        else:
            merged.append(iv)
    return merged


def members_on(intervals: Iterable[Interval], d: date) -> set[str]:
    return {i.ticker for i in intervals if i.start <= d and (i.end is None or d <= i.end)}


def store(con, intervals: Iterable[Interval], names: dict[str, str], fetched_on: date,
          source: str = SOURCE) -> None:
    con.execute("""CREATE TABLE IF NOT EXISTS index_membership (
        index_name VARCHAR, ticker VARCHAR, name VARCHAR, start_date DATE, end_date DATE,
        source VARCHAR, fetched_on DATE, PRIMARY KEY (index_name, ticker, start_date))""")
    con.execute("DELETE FROM index_membership WHERE index_name=?", [INDEX_NAME])
    con.executemany("INSERT INTO index_membership VALUES (?,?,?,?,?,?,?)", [
        [INDEX_NAME, i.ticker, names.get(i.ticker), i.start, i.end, source, fetched_on]
        for i in intervals])


def load(con) -> list[Interval]:
    return [Interval(t, s, e) for t, s, e in con.execute(
        "SELECT ticker, start_date, end_date FROM index_membership WHERE index_name=? "
        "ORDER BY ticker, start_date", [INDEX_NAME]).fetchall()]
