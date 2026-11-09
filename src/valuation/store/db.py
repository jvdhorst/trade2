"""DuckDB storage: connection + idempotent schema creation."""

from __future__ import annotations

from pathlib import Path

import duckdb

DEFAULT_DB = Path("data/valuation.duckdb")

SCHEMA = [
    """CREATE TABLE IF NOT EXISTS securities (
        ticker VARCHAR PRIMARY KEY,
        exchange VARCHAR,
        name VARCHAR,
        currency VARCHAR,
        financial_currency VARCHAR,
        sector VARCHAR,
        industry VARCHAR,
        cik VARCHAR,
        share_classes INTEGER DEFAULT 1,
        share_conversion DOUBLE DEFAULT 1,  -- listed shares per reported share (BRK-B: 1500)
        updated_at TIMESTAMP
    )""",
    """CREATE TABLE IF NOT EXISTS prices_daily (
        ticker VARCHAR, date DATE, close DOUBLE, adj_close DOUBLE, volume DOUBLE,
        source VARCHAR, PRIMARY KEY (ticker, date)
    )""",
    """CREATE TABLE IF NOT EXISTS fundamentals (
        ticker VARCHAR, period_end DATE, filed_date DATE, item VARCHAR,
        value DOUBLE, unit VARCHAR, source VARCHAR, filed_estimated BOOLEAN,
        PRIMARY KEY (ticker, period_end, filed_date, item, source)
    )""",
    """CREATE TABLE IF NOT EXISTS fx_daily (
        date DATE, pair VARCHAR, rate DOUBLE, PRIMARY KEY (date, pair)
    )""",
    """CREATE TABLE IF NOT EXISTS reference_metrics (
        ticker VARCHAR, as_of DATE, source VARCHAR, metric VARCHAR, value DOUBLE,
        PRIMARY KEY (ticker, as_of, source, metric)
    )""",
    """CREATE TABLE IF NOT EXISTS runs (
        run_id VARCHAR PRIMARY KEY, started_at TIMESTAMP, as_of DATE, model VARCHAR,
        git_sha VARCHAR, config_json VARCHAR, status VARCHAR
    )""",
    """CREATE TABLE IF NOT EXISTS valuations (
        run_id VARCHAR, ticker VARCHAR, as_of DATE, model VARCHAR, model_version VARCHAR,
        price DOUBLE, currency VARCHAR, bvps DOUBLE, tbvps DOUBLE, pb DOUBLE,
        peer_level VARCHAR, n_peers INTEGER, peer_median_pb DOUBLE, fair_value DOUBLE,
        upside DOUBLE, pb_percentile DOUBLE, flags VARCHAR, inputs_json VARCHAR,
        PRIMARY KEY (run_id, ticker)
    )""",
]


def connect(path: str | Path = DEFAULT_DB) -> duckdb.DuckDBPyConnection:
    path = Path(path)
    if str(path) != ":memory:":
        path.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(path))
    init_schema(con)
    return con


# Additive migrations for databases created by earlier versions.
MIGRATIONS = [
    "ALTER TABLE securities ADD COLUMN IF NOT EXISTS gics_sector VARCHAR",
    "ALTER TABLE securities ADD COLUMN IF NOT EXISTS gics_sub_industry VARCHAR",
]


def init_schema(con: duckdb.DuckDBPyConnection) -> None:
    for stmt in SCHEMA + MIGRATIONS:
        con.execute(stmt)
