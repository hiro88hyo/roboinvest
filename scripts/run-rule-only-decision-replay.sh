#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

DATE=""
FEATURES_DIR=""
OUT_DIR=""
SYMBOLS=""

usage() {
  cat <<'USAGE'
Usage: bash scripts/run-rule-only-decision-replay.sh --date YYYY-MM-DD --features-dir DIR [options]

Replay archived ProcessedFeatures through strategy-rule and aggregator RULE-only path.

Options:
  --date YYYY-MM-DD        Trading date to replay.
  --features-dir DIR       Feature archive root, usually out/paper-archive-YYYY-MM-DD/features.
  --output-dir DIR         Output directory. Default: out/rule-only-replay-YYYY-MM-DD.
  --symbols LIST           Optional comma-separated symbols.
  -h, --help               Show this help.

Outputs:
  features.jsonl           Sorted ProcessedFeatures input.
  signals-rule.jsonl       StrategySignal output from strategy-rule.
  unified-rule.jsonl       UnifiedTradeSignal output from aggregator with RULE only.
USAGE
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --date)
      if [ "$#" -lt 2 ]; then
        echo "missing value for --date" >&2
        exit 2
      fi
      DATE="$2"
      shift 2
      ;;
    --features-dir)
      if [ "$#" -lt 2 ]; then
        echo "missing value for --features-dir" >&2
        exit 2
      fi
      FEATURES_DIR="$2"
      shift 2
      ;;
    --output-dir)
      if [ "$#" -lt 2 ]; then
        echo "missing value for --output-dir" >&2
        exit 2
      fi
      OUT_DIR="$2"
      shift 2
      ;;
    --symbols)
      if [ "$#" -lt 2 ]; then
        echo "missing value for --symbols" >&2
        exit 2
      fi
      SYMBOLS="$2"
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

if [ -z "$DATE" ] || [ -z "$FEATURES_DIR" ]; then
  echo "--date and --features-dir are required" >&2
  usage >&2
  exit 2
fi

if ! [[ "$DATE" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]]; then
  echo "--date must be YYYY-MM-DD: $DATE" >&2
  exit 2
fi

if [ -z "$OUT_DIR" ]; then
  OUT_DIR="out/rule-only-replay-$DATE"
fi

mkdir -p "$OUT_DIR"

COLLECT_ARGS=(
  --date "$DATE"
  --features-dir "$FEATURES_DIR"
  --output "$OUT_DIR/features.jsonl"
)
if [ -n "$SYMBOLS" ]; then
  COLLECT_ARGS+=(--symbols "$SYMBOLS")
fi

uv run python scripts/collect-feature-archive.py "${COLLECT_ARGS[@]}"
uv run python -m strategy_rule backtest \
  --input "$OUT_DIR/features.jsonl" \
  --output "$OUT_DIR/signals-rule.jsonl"
uv run python -m aggregator backtest \
  --input-a "$OUT_DIR/signals-rule.jsonl" \
  --output "$OUT_DIR/unified-rule.jsonl"

cat <<EOF
rule-only decision replay complete:
  features: $OUT_DIR/features.jsonl
  signals : $OUT_DIR/signals-rule.jsonl
  unified : $OUT_DIR/unified-rule.jsonl
EOF
