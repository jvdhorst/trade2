# System architecture (SW)

Status: as built on 2026-11-09 (book_value 0.2.0, Phase 6 data batch 1 + eligibility 0.1.0, Phase 7 Model A
residual_income 0.1.0 + Model B peer_regression 0.1.0 book branch + report v2, Phase 8 backtest 0.1.0, Phase 9
data batch 2 (ttm 0.2.0) + Model C composite 0.1.0 + benchmarks 0.1.1; revision round v2: A 0.2.0, B 0.2.0 diagnostic,
C 0.2.0, H shareholder_yield 0.1.0, eligibility 0.1.1, frozen params `models/frozen.py`, one-shot holdout guard;
2026-11-11: J ebit_yield_roc 0.1.0; data batch 3 (ttm 0.3.0) + G altman_z 0.1.0; J and G not frozen, not backtested;
2026-11-12: D dividend_sustainability 0.1.0, E piotroski 0.1.0, F accruals 0.1.0; not frozen, not backtested). Planned
components are dashed.

## Data flow
```mermaid
flowchart LR
    subgraph EXT["External sources"]
        YQ["Yahoo spark<br/>batch prices (20/call)"]
        YT["Yahoo fundamentals-timeseries<br/>TSX balance sheet + currency"]
        YP["Yahoo quoteSummary<br/>profile (monthly, crumb)"]
        YC["Yahoo chart<br/>daily volume + adj_close + dividends<br/>(2y backfill, then 5d)"]
        EF["SEC EDGAR frames<br/>who filed? (1 call/quarter)"]
        EC["SEC EDGAR companyfacts<br/>US fundamentals + filed dates"]
        BOC["Bank of Canada Valet<br/>USD/CAD + 10y yield"]
        XIC["iShares XIC holdings<br/>TSX Composite + GICS"]
        WIKI["Wikipedia<br/>S&P 500 + GICS + CIK"]
        IR["Issuer supplementary packs<br/>RY / TD / BNS / MFC PDFs<br/>BVPS + ROE lines"]
        CSM[/"universe/insurer_csm.csv<br/>manual insurer CSM (MFC)"/]
        FRED["FRED DGS10<br/>US 10y yield"]
    end

    subgraph ING["Ingestion (src/valuation/ingest, providers)"]
        UB["universe_build<br/>valuation universe"]
        PIPE["pipeline.ingest<br/>refresh policy + fetch_log<br/>4 threads, rate limiters"]
        CACHE[("data/raw/&lt;source&gt;/&lt;day&gt;<br/>raw response cache")]
    end

    UNI[/"universe/*.csv<br/>reference (14) · tsx_composite (215) · sp500 (503)"/]

    subgraph DB["DuckDB data/valuation.duckdb"]
        SEC[(securities)]
        PR[(prices_daily)]
        FU[(fundamentals<br/>keyed by filed_date<br/>NI, CFO, capex, revenue, GP, EBIT quarters<br/>assets, debt, cash)]
        BD[(benchmarks_daily<br/>benchmark_dists)]
        DV[(dividends)]
        RT[(rates_daily)]
        FX[(fx_daily)]
        REF[(reference_metrics)]
        FL[(fetch_log)]
        VAL[(valuations + runs)]
        SG[(signals<br/>payload per ticker)]
    end

    subgraph MOD["Valuation (engine.py, models/)"]
        ENG["engine.build_inputs<br/>select_latest_filing (no look-ahead)<br/>FX, share-class conversion"]
        BV["book_value 0.2.0<br/>BVPS, P/B, peer median FV"]
        RI["Model A residual_income 0.1.0<br/>ROE fade to r = 10y + 5% ERP<br/>MoS, r ± 1pp"]
        PB["Model B peer_regression 0.1.0<br/>Theil–Sen ln(P/B) ~ ROE per GICS sector<br/>(book branch; EV branch planned)"]
        CMP["Model C composite 0.2.0<br/>V/Q/M/R z per (market, sector), equal weights<br/>B/P in book sectors only; quintiles per market + vetoes"]
        SHY["Model H shareholder_yield 0.1.0<br/>SY = DPS/P + net buyback yield (same-source shares, split-adjusted)"]
        EYR["Model J ebit_yield_roc 0.1.0 (unvalidated)<br/>rank EBIT/EV + rank EBIT/IC per market<br/>ex Financials/Utilities/Real Estate; rf-floored quintiles"]
        ALZ["Model G altman_z 0.1.0 (unvalidated)<br/>Z'' = 6.56·X1 + 3.26·X2 + 6.72·X3 + 1.05·X4 per name<br/>SELL if Z'' below 1.10 (no BUY); CASH_RICH_OVERRIDE; distress_veto reported, no vote"]
        DIV["Model D dividend_sustainability 0.1.0 (unvalidated)<br/>payout, g_sust ≤ 6%, k = DY(1+g)+g, spread = k − r per name<br/>DIV_CUT / IRREGULAR_DIV; V_DDM informational"]
        PIO["Model E piotroski 0.1.0 (unvalidated)<br/>9 binary signals vs prior year (same-source filings t−4q, t−8q)<br/>BUY F ≥ 8 (≥ 7 at n=8), SELL F ≤ 2; ex Financials"]
        ACC["Model F accruals 0.1.0 (unvalidated)<br/>(NI − CFO)/avg TA, percentile per (market, sector) ≥ 12<br/>SELL top quintile ≥ +5%, BUY bottom ≤ −5% with NI > 0"]
        BMK["benchmarks 0.1.1<br/>SPX_TR, SPY/IVV/VOO, TSX, XIC/XIU/ZCN<br/>dist yield vs 10y, TR 1m-5y"]
        ELIG["eligibility 0.1.0<br/>config/liquidity.yaml"]
        TTM["ttm 0.3.0: flow_ttm (NI, CFO, capex, GP, ...), DPS_TTM"]
    end

    subgraph BTS["Backtest (src/valuation/backtest, spec backtest.md)"]
        WH["Wikipedia: Historical components<br/>of the S&P 500 (changes table)"]
        MEM["membership: PIT S&P 500 intervals<br/>(backward walk from today's list)"]
        BDB[("data/backtest.duckdb<br/>as-traded prices 2012→ + splits<br/>EDGAR history, index_membership")]
        BT["engine: month-end 2014→<br/>live run_signals per date (≤ d only)<br/>4 worker processes, resumable"]
        MET["metrics: fwd 3/6/12m returns, sector-neutral spread,<br/>hit rates, rank IC + Newey–West t, turnover,<br/>pass bar, low-P/B baseline; holdout locked"]
        BTR["reports/backtest/*.md<br/>docs/qa/backtest_model_a/b.md"]
    end

    subgraph OUT["Outputs (reports/&lt;date&gt;/)"]
        SCR["screen_book_value.csv / .parquet"]
        COV["coverage_book_value.csv<br/>+ eligible, elig_fail_reason"]
        SIG["screen_signals.csv / .parquet<br/>Models A/B/C: BUY / HOLD / SELL / NO_SIGNAL / NOT_ELIGIBLE<br/>benchmark header block (no vote until Phase 10)"]
        BCSV["benchmarks.csv"]
    end

    subgraph QA["QA (src/valuation/qa, tests/)"]
        XC["crosscheck vs Yahoo"]
        IC["crosscheck --issuer<br/>vs issuer BVPS + ROE"]
        T["pytest: spec tests from AN docs<br/>parser fixtures, offline pipeline"]
    end

    XIC --> UB
    WIKI --> UB
    UB --> UNI --> PIPE
    YQ & YT & YP & YC & EF & EC & BOC --> PIPE
    FRED --> PIPE
    PIPE <--> CACHE
    PIPE --> SEC & PR & FU & FX & REF & FL & DV & RT
    SEC & PR & FU & FX & DV & RT --> ENG
    ENG --> BV --> VAL
    ENG --> ELIG --> VAL
    ENG --> TTM --> VAL
    ELIG --> RI & PB
    ELIG --> CMP
    TTM --> RI
    TTM --> CMP
    RI -->|"b0, roe_raw, NI"| CMP
    YC --> BD --> BMK
    BMK --> SIG & BCSV
    BD -->|"SPX_TR / XIC fwd returns"| MET
    RT --> RI
    CSM --> RI
    ENG --> RI
    RI -->|"b0, ROE_raw"| PB
    RI & PB --> SG --> SIG
    CMP --> SG
    WH --> MEM --> BDB
    YC -->|"period1/period2, daily"| BDB
    EC -->|"full history"| BDB
    BDB --> BT
    RI & PB --> BT
    CMP --> BT
    BT --> MET --> BTR
    SG --> IC
    VAL --> SCR & COV
    VAL --> XC
    REF --> XC
    VAL --> IC
    IR --> IC

    classDef planned stroke-dasharray: 5 5,fill:#f6f6f6,color:#666;
```

## Daily run (incremental)
```mermaid
sequenceDiagram
    autonumber
    participant CLI as valuation CLI
    participant P as ingest pipeline
    participant Y as Yahoo
    participant E as SEC EDGAR
    participant D as DuckDB
    CLI->>P: ingest --universe ... (or --prices-only)
    P->>Y: spark batch closes (36 calls for 718 tickers)
    Y-->>P: closes
    P->>D: upsert prices_daily
    P->>Y: chart per ticker (5d; 2y on first load / new dividend or split), ≈4 req/s
    Y-->>P: close, adj_close, volume, dividends
    P->>D: upsert prices_daily (volume), dividends
    P->>D: read fetch_log, latest period_end
    alt fundamentals due (new / >7 days / new quarter expected)
        P->>Y: fundamentals-timeseries (threads, ≈4 req/s)
        P->>D: upsert fundamentals, fetch_log
    end
    P->>E: frames (latest accession per CIK)
    alt new accession
        P->>E: companyfacts (trimmed before caching)
        P->>D: upsert fundamentals (+ quarterly NI), fetch_log
    end
    P->>P: BoC 10y + FRED DGS10 → rates_daily
    CLI->>D: value --universe ... (engine + book_value)
    D-->>CLI: valuations, runs
    CLI->>CLI: eligibility filter, TTM NI, DPS, rf; write screen + coverage reports
    P->>Y: chart for 9 benchmarks (6y) → benchmarks_daily
    CLI->>CLI: Model A per ticker, Model B per sector, Model C cross-section (per market), Model H per ticker, Model J cross-section (per market), Model G per ticker, Models D and E per ticker, Model F cross-section (per market, sector); write screen_signals + benchmarks.csv
    CLI->>D: crosscheck / crosscheck --issuer (QA)
```

## Key properties
| Property | How |
|---|---|
| No look-ahead | Fundamentals keyed by `filed_date`; `select_latest_filing` and a guard in `compute_book_value`; quarterly NI emitted per filing date (a derived Q4 gets the 10-K date); prices, dividends and rates `≤ as_of` (not even stored beyond it) |
| Reproducible | Raw responses cached per day; each valuation row stores `inputs_json`, `model_version`, and the run's git SHA |
| Idempotent | `INSERT OR REPLACE` upserts on natural keys; re-runs are a no-op |
| Bulk upserts | Facts, bars and dividends are inserted as registered DataFrames (P8-8): S&P 500 first load 502 s (was 36 min), TSX 200 s |
| Fast daily run | Batch prices, one 5-day chart call per ticker for volume, and a refresh policy (`fetch_log`): 251 s for 718 tickers (≤ 5 min target); first load ~45 min incl. the 2-year history backfill |
| Signals are not advice | Every screen_signals file starts with the disclaimer line (Parquet: metadata `disclaimer`); per-model labels only, no vote before Phase 10 |
| Backtest point in time | Own DB; prices stored as traded (Yahoo split adjustment undone) so price × shares-as-filed is a real market cap; shares × splits in (filed, d], dividends ÷ splits in (ex, d]; facts `filed_date ≤ d` (+30 days for estimated Yahoo dates); membership on d from the Wikipedia changes table; holdout (last 36 month-ends): a plain US `run`/`report` never touches those dates; only `backtest run --holdout --freeze docs/models/revision_v2_freeze.md` computes them, once: it refuses unless `FROZEN_V2` hashes to the freeze doc's SHA-256, the doc's JSON hashes to it too, `docs/qa/holdout_v2.md` does not exist and `bt_runs` has no holdout row for the frozen versions (`backtest/holdout.py`); `bt_runs` records thresholds hash + params hash |
| Spec ↔ code | AN spec → QA tests written from the spec → SW implementation → external cross-checks |
