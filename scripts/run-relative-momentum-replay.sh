#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

export UV_CACHE_DIR="${UV_CACHE_DIR:-/tmp/uv-cache-roboinvest}"
mkdir -p "$UV_CACHE_DIR"

ARCHIVE_DIR=""
DATE=""
OUT_DIR=""
CAPITAL="1000000"
BUY_LIMIT_OFFSET_TICKS="0"
MODE="strict"

usage() {
  cat <<'USAGE'
Usage: bash scripts/run-relative-momentum-replay.sh --archive-dir DIR --date YYYY-MM-DD [options]

Runs relative_momentum replay through:
  enrich features -> strategy-rule -> aggregator -> gateway -> oms-paper

Options:
  --archive-dir DIR             Paper archive directory, e.g. out/paper-archive-2026-06-22
  --date YYYY-MM-DD             Trading date label used in output filenames/state timestamps
  --out-dir DIR                 Output directory. Default: /tmp/relative-momentum-replay-YYYY-MM-DD
  --capital AMOUNT              Gateway capital. Default: 1000000
  --buy-limit-offset-ticks N    BUY limit offset ticks. Default: 0
  --base                        Use base thresholds: open +100bps, peer >=0.80, vwap +20bps
  --strict                      Use strict thresholds: open +300bps, peer >=0.90, vwap +30bps (default)
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
    --capital)
      CAPITAL="$2"
      shift 2
      ;;
    --buy-limit-offset-ticks)
      BUY_LIMIT_OFFSET_TICKS="$2"
      shift 2
      ;;
    --base)
      MODE="base"
      shift
      ;;
    --strict)
      MODE="strict"
      shift
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
  OUT_DIR="/tmp/relative-momentum-replay-${DATE}"
fi
mkdir -p "$OUT_DIR"

case "$MODE" in
  strict)
    MIN_RETURN_FROM_OPEN_BPS="${RELATIVE_MOMENTUM_MIN_RETURN_FROM_OPEN_BPS:-300}"
    MIN_PEER_PERCENTILE="${RELATIVE_MOMENTUM_MIN_PEER_PERCENTILE:-0.90}"
    MIN_VWAP_DISTANCE_BPS="${RELATIVE_MOMENTUM_MIN_VWAP_DISTANCE_BPS:-30}"
    ;;
  base)
    MIN_RETURN_FROM_OPEN_BPS="${RELATIVE_MOMENTUM_MIN_RETURN_FROM_OPEN_BPS:-100}"
    MIN_PEER_PERCENTILE="${RELATIVE_MOMENTUM_MIN_PEER_PERCENTILE:-0.80}"
    MIN_VWAP_DISTANCE_BPS="${RELATIVE_MOMENTUM_MIN_VWAP_DISTANCE_BPS:-20}"
    ;;
  *)
    echo "invalid mode: $MODE" >&2
    exit 2
    ;;
esac

ENRICHED="${OUT_DIR}/features-relative.jsonl"
SIGNALS="${OUT_DIR}/signals.jsonl"
UNIFIED="${OUT_DIR}/unified.jsonl"
STATE="${OUT_DIR}/gateway-state.json"
ORDERS="${OUT_DIR}/orders.jsonl"
REJECTED="${OUT_DIR}/gateway-rejected.jsonl"
FILLS="${OUT_DIR}/fills.jsonl"
POSITIONS="${OUT_DIR}/positions.json"
NOFILLS="${OUT_DIR}/nofills.jsonl"
REPORT="${OUT_DIR}/backtest-report.json"

printf '=== relative momentum replay date=%s mode=%s out=%s ===\n' "$DATE" "$MODE" "$OUT_DIR"

uv run python scripts/enrich-relative-momentum-features.py \
  --input "$FEATURES_PATH" \
  --output "$ENRICHED"

STRATEGIES_ENABLED=relative_momentum \
RELATIVE_MOMENTUM_MIN_RETURN_FROM_OPEN_BPS="$MIN_RETURN_FROM_OPEN_BPS" \
RELATIVE_MOMENTUM_MIN_PEER_PERCENTILE="$MIN_PEER_PERCENTILE" \
RELATIVE_MOMENTUM_MIN_VWAP_DISTANCE_BPS="$MIN_VWAP_DISTANCE_BPS" \
ENTRY_MAX_SPREAD_BPS=30 \
ENTRY_MAX_SPREAD_TICKS=2 \
ENTRY_MIN_ASK_DEPTH_5=1000 \
  uv run python -m strategy_rule backtest \
    --input "$ENRICHED" \
    --output "$SIGNALS"

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
    --buy-limit-offset-ticks "$BUY_LIMIT_OFFSET_TICKS"

uv run python -m oms_paper backtest \
  --orders "$ORDERS" \
  --books "$BOOKS_PATH" \
  --output-fills "$FILLS" \
  --output-positions "$POSITIONS" \
  --output-rejected "$NOFILLS" \
  --output-report "$REPORT" \
  --default-holding-type day

printf '=== report: %s ===\n' "$REPORT"
sed -n '1,220p' "$REPORT"
