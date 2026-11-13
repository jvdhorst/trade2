#!/usr/bin/env bash
# Nightly market-data update for trade2 (Linux / macOS).
#
#   scripts/nightly_update.sh             ingest TSX + S&P 500, then write reports/<date>/
#   scripts/nightly_update.sh --dry-run   print the steps and exit (no lock, no log, no fetch)
#   scripts/nightly_update.sh --deep      also refetch price history (--refresh-history)
#
# Runs `valuation ingest` and `valuation value` only. It never runs a backtest command.
# Setup, scheduling and troubleshooting: docs/ops/nightly_update.md
#
# Environment (all optional):
#   TRADE2_UV                  path to the uv binary (default: first `uv` on PATH)
#   TRADE2_DEEP_REFRESH_DAY    ISO weekday 1-7 (1 = Mon, 6 = Sat) on which to add --refresh-history
#   TRADE2_RETRY_DELAY         seconds to wait before retrying a failed step once (default 300)
#   TRADE2_LOG_KEEP_DAYS       delete logs/nightly_*.log older than this (default 30)
#   TRADE2_NOTIFY_CMD          shell command run on failure; it gets TRADE2_STATUS, TRADE2_FAILED_STEPS
#                              and TRADE2_LOG in its environment and the log tail on stdin
#   VALUATION_SEC_USER_AGENT   passed through to SEC EDGAR ("<app> <contact email>"), never set here
set -uo pipefail

DRY_RUN=0
DEEP=0
for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY_RUN=1 ;;
    --deep) DEEP=1 ;;
    -h|--help) sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "nightly_update: unknown argument: $arg" >&2; exit 2 ;;
  esac
done

# Repo root = parent of this script's directory; no hard-coded paths.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT" || exit 1

# Schedulers (cron, launchd) start with a minimal PATH. Append the usual uv install locations;
# appending keeps anything the caller put on PATH first.
for d in "$HOME/.local/bin" "$HOME/.cargo/bin" /opt/homebrew/bin /usr/local/bin; do
  case ":$PATH:" in *":$d:"*) ;; *) [ -d "$d" ] && PATH="$PATH:$d" ;; esac
done
export PATH

RETRY_DELAY="${TRADE2_RETRY_DELAY:-300}"
KEEP_DAYS="${TRADE2_LOG_KEEP_DAYS:-30}"
TODAY="$(date +%Y-%m-%d)"
DOW="$(date +%u)"
UNIVERSES=(universe/tsx_composite.csv universe/sp500.csv)

if [ -n "${TRADE2_DEEP_REFRESH_DAY:-}" ] && [ "$TRADE2_DEEP_REFRESH_DAY" = "$DOW" ]; then
  DEEP=1
fi
INGEST_EXTRA=()
[ "$DEEP" = 1 ] && INGEST_EXTRA=(--refresh-history)

# The step list. Ingest per universe uses the default incremental policy (prices + whatever is
# due). One `value` run covers every ingested ticker (both universes): two runs would write the
# same reports/<date>/ files and the second would overwrite the first.
STEP_NAMES=()
STEP_ARGS=()
for u in "${UNIVERSES[@]}"; do
  STEP_NAMES+=("ingest $(basename "$u" .csv)")
  STEP_ARGS+=("ingest --universe $u${INGEST_EXTRA[*]:+ ${INGEST_EXTRA[*]}}")
done
STEP_NAMES+=("value")
STEP_ARGS+=("value --out reports")

UV="${TRADE2_UV:-$(command -v uv || true)}"

if [ "$DRY_RUN" = 1 ]; then
  echo "trade2 nightly update (dry run) in $REPO_ROOT"
  echo "uv: ${UV:-NOT FOUND}"
  echo "report dir: reports/$TODAY/"
  echo "log: logs/nightly_$TODAY.log (kept $KEEP_DAYS days)"
  echo "deep refresh: $([ "$DEEP" = 1 ] && echo yes || echo no)"
  echo "VALUATION_SEC_USER_AGENT: $([ -n "${VALUATION_SEC_USER_AGENT:-}" ] && echo set || echo 'not set (EDGAR default)')"
  i=1
  for a in "${STEP_ARGS[@]}"; do
    echo "step $i: uv run valuation $a"
    i=$((i + 1))
  done
  [ -n "$UV" ] && [ -x "$UV" ] || { echo "ERROR: uv not found; see docs/ops/nightly_update.md (PATH)" >&2; exit 127; }
  exit 0
fi

mkdir -p logs
LOG="logs/nightly_$TODAY.log"
LOCK="logs/.nightly.lock"

log() { printf '%s %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" | tee -a "$LOG"; }

# Lock: mkdir is atomic on every POSIX filesystem (flock is missing on macOS). A lock whose PID
# is gone (crash, reboot) is stale and taken over.
if ! mkdir "$LOCK" 2>/dev/null; then
  other="$(cat "$LOCK/pid" 2>/dev/null || true)"
  if [ -n "$other" ] && kill -0 "$other" 2>/dev/null; then
    log "SKIP: another nightly update is running (pid $other, lock $LOCK)"
    exit 0
  fi
  log "removing stale lock $LOCK (pid ${other:-unknown} not running)"
  rm -rf "$LOCK"
  if ! mkdir "$LOCK" 2>/dev/null; then
    log "SKIP: lock $LOCK was taken by another run"
    exit 0
  fi
fi
echo $$ > "$LOCK/pid"
trap 'rm -rf "$LOCK"' EXIT
trap 'log "interrupted"; exit 130' INT TERM

log "=== trade2 nightly update start (repo $REPO_ROOT, pid $$, deep=$DEEP) ==="

# Log retention.
find logs -maxdepth 1 -name 'nightly_*.log' -type f -mtime +"$KEEP_DAYS" -exec rm -f {} + 2>/dev/null || true

if [ -z "$UV" ] || [ ! -x "$UV" ]; then
  log "ERROR: uv not found on PATH ($PATH). Install uv or set TRADE2_UV / PATH; see docs/ops/nightly_update.md"
  FAILED="uv-missing"
  STATUS=127
else
  log "uv: $UV ($("$UV" --version 2>&1 | head -1))"
  [ -n "${VALUATION_SEC_USER_AGENT:-}" ] || log "note: VALUATION_SEC_USER_AGENT not set; EDGAR gets the default user agent"
  FAILED=""
  STATUS=0
  INGEST_FAILED=0

  run_step() {  # run_step <name> <args...>; one retry after RETRY_DELAY
    local name="$1"; shift
    local attempt rc
    for attempt in 1 2; do
      log "--- step '$name' attempt $attempt: uv run valuation $* ---"
      "$UV" run valuation "$@" 2>&1 | tee -a "$LOG"
      rc=${PIPESTATUS[0]}
      if [ "$rc" = 0 ]; then
        log "step '$name' ok"
        return 0
      fi
      log "step '$name' failed (exit $rc)"
      if [ "$attempt" = 1 ]; then
        log "retrying in ${RETRY_DELAY}s (Yahoo throttling / DB lock usually clears)"
        sleep "$RETRY_DELAY"
      fi
    done
    return "$rc"
  }

  for idx in "${!STEP_NAMES[@]}"; do
    name="${STEP_NAMES[$idx]}"
    if [ "$name" = value ] && [ "$INGEST_FAILED" = 1 ]; then
      log "skipping 'value': an ingest step failed, so no report for $TODAY"
      FAILED="${FAILED:+$FAILED, }value (skipped)"
      continue
    fi
    # shellcheck disable=SC2086  # STEP_ARGS entries are word lists without spaces in values
    if ! run_step "$name" ${STEP_ARGS[$idx]}; then
      STATUS=1
      FAILED="${FAILED:+$FAILED, }$name"
      case "$name" in ingest*) INGEST_FAILED=1 ;; esac
    fi
  done
fi

if [ "$STATUS" = 0 ]; then
  log "=== trade2 nightly update OK: reports/$TODAY/ ==="
else
  log "=== trade2 nightly update FAILED (exit $STATUS): $FAILED ==="
  NOTIFY="${TRADE2_NOTIFY_CMD:-}"
  if [ -n "$NOTIFY" ]; then
    log "running TRADE2_NOTIFY_CMD"
    TRADE2_STATUS="$STATUS" TRADE2_FAILED_STEPS="$FAILED" TRADE2_LOG="$REPO_ROOT/$LOG" \
      bash -c "$NOTIFY" < <(tail -n 40 "$LOG") >>"$LOG" 2>&1 \
      || log "warning: TRADE2_NOTIFY_CMD failed"
  fi
fi
exit "$STATUS"
