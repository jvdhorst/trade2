# trade2: working rules for Claude sessions

This is a transparent equity screener for TSX Composite and S&P 500 buy-and-hold candidates. Its output is
**screening signals for human review, not investment advice.** Python 3.13, uv, DuckDB, pytest.

**Start here:** read [`docs/session_handoff.md`](docs/session_handoff.md) for the current state, open work, pending
decisions and history. Then read `docs/backlog.md`, `docs/architecture.md` and `README.md`.

## Team and workflow
- **Roles:** PM (plans and selects work), AN (specs, models), SW (implementation), QA (tests, validation).
- **Separate sessions** are titled `<role> - <request>`. AN work may use the Fable model (`claude-fable-5-1`).
- **Per model or phase:** AN writes the spec in `docs/models/<name>.md`, with golden examples verified by a throwaway
  script. QA then writes tests **from the spec, not from the code**. SW implements. QA runs external checks in `docs/qa/`.
- **One branch and one PR into `main` per phase or task.** Worker sessions open the PR and **stop; they never merge.**
  The user merges, or tells the coordinating session to merge.
- **Push after every logical step,** because usage-limit stops have lost unpushed work before.

## Hard rules (never break)
- **No look-ahead:** fundamentals require `filed_date <= as_of`, and prices must be dated on or before `as_of`.
- **No tuning on backtest results.** Parameter changes are proposals for the PM, decided and frozen *before* testing.
- **The holdout is locked:** the month-ends 2023-11-30 … 2026-10-31 must not be evaluated by anything except the
  one-shot command for the frozen R2 models. Run that only on the user's explicit go-ahead.
- **Frozen models** A 0.2.0, C 0.2.0 and H 0.1.0 (B 0.2.0 is diagnostic only) must not change. The parameter SHA-256
  is `6c6354c540bebc970709bc9a150b29db9e15d61a2b03f744de04ad21d0aa088e` (`docs/models/revision_v2_freeze.md`).
  The FROZEN_V2 hash test must stay green. H's pre-registered confirmation test is in freeze §4.7 and §4a, plus
  `docs/models/holdout_h_preregistration.md`.
- **Keep all tests green** (`uv run pytest -q`, `uv run ruff check src tests`). Point out data anomalies; never fix
  them silently.
- **SEC requests:** use `VALUATION_SEC_USER_AGENT` from the environment. Never put a personal email in code or requests.
- **Large data builds and backtests run on the user's machine.** Yahoo throttles the cloud container (HTTP 401/429).
  Cloud sessions test on fixtures.
