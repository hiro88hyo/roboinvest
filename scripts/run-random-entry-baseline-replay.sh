#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

export UV_CACHE_DIR="${UV_CACHE_DIR:-/tmp/uv-cache-roboinvest}"
mkdir -p "$UV_CACHE_DIR"

ARCHIVE_DIR=""
DATE=""
OUT_DIR=""
SEED="1"
SAMPLES="6"
CAPITAL="1000000"
BUY_LIMIT_OFFSET_TICKS="0"
MAX_NOTIONAL_PER_ORDER_PCT="0.20"

usage() {
  cat <<'USAGE'
Usage: bash scripts/run-random-entry-baseline-replay.sh --archive-dir DIR --date YYYY-MM-DD [options]

Runs a random-entry baseline through:
  random StrategySignal generator -> aggregator -> gateway -> oms-paper

Options:
  --archive-dir DIR             Paper archive directory, e.g. out/paper-archive-2026-06-24
  --date YYYY-MM-DD             Trading date label used in output filenames/state timestamps
  --out-dir DIR                 Output directory. Default: /tmp/random-entry-baseline-YYYY-MM-DD-seed-N
  --seed N                      Random seed. Default: 1
  --samples N                   Random BUY samples before Gateway. Default: 6
  --capital AMOUNT              Gateway capital. Default: 1000000
  --max-notional-per-order-pct PCT
                                BUY per-order notional cap as capital ratio. Default: 0.20
  --buy-limit-offset-ticks N    BUY limit offset ticks. Default: 0
  -h, --help                    Show this help.
USAGE
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --archive-dir)
      ARCHIVE_DIR="$2"
      shift 2
      ;;
    --date)
      DATE="$2"
      shift 2
      ;;
    --out-dir)
      OUT_DIR="$2"
      shift 2
      ;;
    --seed)
      SEED="$2"
      shift 2
      ;;
    --samples)
      SAMPLES="$2"
      shift 2
      ;;
    --capital)
      CAPITAL="$2"
      shift 2
      ;;
    --max-notional-per-order-pct)
      MAX_NOTIONAL_PER_ORDER_PCT="$2"
      shift 2
      ;;
    --buy-limit-offset-ticks)
      BUY_LIMIT_OFFSET_TICKS="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if [ -z "$ARCHIVE_DIR" ] || [ -z "$DATE" ]; then
  usage >&2
  exit 2
fi

FEATURES_PATH="${ARCHIVE_DIR%/}/features.jsonl"
BOOKS_PATH="${ARCHIVE_DIR%/}/backtest/books.jsonl"
if [ ! -f "$FEATURES_PATH" ]; then
  echo "missing features: $FEATURES_PATH" >&2
  exit 2
fi
if [ ! -f "$BOOKS_PATH" ]; then
  echo "missing books: $BOOKS_PATH" >&2
  exit 2
fi

if [ -z "$OUT_DIR" ]; then
  OUT_DIR="/tmp/random-entry-baseline-${DATE}-seed-${SEED}"
fi
mkdir -p "$OUT_DIR"

SIGNALS="${OUT_DIR}/signals.jsonl"
UNIFIED="${OUT_DIR}/unified.jsonl"
STATE="${OUT_DIR}/gateway-state.json"
ORDERS="${OUT_DIR}/orders.jsonl"
REJECTED="${OUT_DIR}/gateway-rejected.jsonl"
FILLS="${OUT_DIR}/fills.jsonl"
POSITIONS="${OUT_DIR}/positions.json"
NOFILLS="${OUT_DIR}/nofills.jsonl"
REPORT="${OUT_DIR}/backtest-report.json"
FILTERED_BOOKS="${OUT_DIR}/books-filtered.jsonl"

printf '=== random entry baseline date=%s seed=%s samples=%s out=%s ===\n' \
  "$DATE" "$SEED" "$SAMPLES" "$OUT_DIR"

uv run python scripts/generate-random-entry-signals.py \
  --features "$FEATURES_PATH" \
  --output "$SIGNALS" \
  --seed "$SEED" \
  --samples "$SAMPLES" \
  --max-spread-bps "${ENTRY_MAX_SPREAD_BPS:-10}" \
  --max-spread-ticks "${ENTRY_MAX_SPREAD_TICKS:-1}" \
  --min-ask-depth-5 "${ENTRY_MIN_ASK_DEPTH_5:-1000}" \
  --min-minutes-from-open "${RANDOM_ENTRY_MIN_MINUTES_FROM_OPEN:-15}" \
  --min-minutes-to-close "${RANDOM_ENTRY_MIN_MINUTES_TO_CLOSE:-60}" \
  --max-book-age-seconds "${RANDOM_ENTRY_MAX_BOOK_AGE_SECONDS:-300}" \
  --max-price "${RANDOM_ENTRY_MAX_PRICE:-2000}" \
  --min-vwap-distance-bps "${RANDOM_ENTRY_MIN_VWAP_DISTANCE_BPS:-0}" \
  --max-vwap-distance-bps "${RANDOM_ENTRY_MAX_VWAP_DISTANCE_BPS:-160}" \
  --max-stop-risk-bps "${RANDOM_ENTRY_MAX_STOP_RISK_BPS:-160}" \
  --target-r-multiple "${RANDOM_ENTRY_TARGET_R_MULTIPLE:-1.5}"

MIN_CONFIDENCE_RULE_ONLY=0.5 \
  uv run python -m aggregator backtest \
    --input-a "$SIGNALS" \
    --output "$UNIFIED"

printf '%s\n' \
  "{\"is_trading_allowed\":true,\"trade_mode\":\"paper\",\"trading_style\":\"day\",\"daily_pnl\":\"0\",\"weekly_pnl\":\"0\",\"monthly_pnl\":\"0\",\"daily_loss_limit\":\"100000\",\"weekly_loss_limit\":\"300000\",\"monthly_loss_limit\":\"1000000\",\"updated_at\":\"${DATE}T09:00:00+09:00\"}" \
  > "$STATE"

CAPITAL="$CAPITAL" \
  uv run python -m gateway backtest \
    --input "$UNIFIED" \
    --state "$STATE" \
    --output-approved "$ORDERS" \
    --output-rejected "$REJECTED" \
    --max-notional-per-order-pct "$MAX_NOTIONAL_PER_ORDER_PCT" \
    --buy-limit-offset-ticks "$BUY_LIMIT_OFFSET_TICKS"

uv run python scripts/filter-order-books-for-orders.py \
  --orders "$ORDERS" \
  --books "$BOOKS_PATH" \
  --output "$FILTERED_BOOKS"

uv run python -m oms_paper backtest \
  --orders "$ORDERS" \
  --books "$FILTERED_BOOKS" \
  --output-fills "$FILLS" \
  --output-positions "$POSITIONS" \
  --output-rejected "$NOFILLS" \
  --output-report "$REPORT" \
  --default-holding-type day

printf '=== report: %s ===\n' "$REPORT"
sed -n '1,220p' "$REPORT"
