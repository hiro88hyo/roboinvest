#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

COMPOSE_FILE="infra/docker-compose.prod.yml"
DATE=""
OUT_DIR=""

usage() {
  cat <<'USAGE'
Usage: bash scripts/export-paper-archives.sh --date YYYY-MM-DD [options]

Copy archived Gateway orders, feature-engine order books, and processed features
from production Docker volumes to a host directory for archive backtesting.

Options:
  --date YYYY-MM-DD        Trading date used for default output path.
  --output-dir DIR         Output directory. Default: out/paper-archive-YYYY-MM-DD.
  --compose-file FILE      Docker Compose file. Default: infra/docker-compose.prod.yml.
  -h, --help              Show this help.

After export, run:
  uv run python scripts/run-paper-archive-backtest.py \
    --date YYYY-MM-DD \
    --orders-dir OUT/orders \
    --book-dir OUT/books \
    --output-dir OUT/backtest \
    --run-gate
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
    --output-dir)
      if [ "$#" -lt 2 ]; then
        echo "missing value for --output-dir" >&2
        exit 2
      fi
      OUT_DIR="$2"
      shift 2
      ;;
    --compose-file)
      if [ "$#" -lt 2 ]; then
        echo "missing value for --compose-file" >&2
        exit 2
      fi
      COMPOSE_FILE="$2"
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

if [ -z "$DATE" ]; then
  echo "--date is required" >&2
  usage >&2
  exit 2
fi

if ! [[ "$DATE" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]]; then
  echo "--date must be YYYY-MM-DD: $DATE" >&2
  exit 2
fi

if [ -z "$OUT_DIR" ]; then
  OUT_DIR="out/paper-archive-$DATE"
fi

if [ -e "$OUT_DIR/orders" ] || [ -e "$OUT_DIR/books" ] || [ -e "$OUT_DIR/features" ]; then
  echo "refusing to overwrite existing archive export: $OUT_DIR" >&2
  exit 2
fi

mkdir -p "$OUT_DIR"

echo "[1/3] copy gateway orders archive -> $OUT_DIR/orders"
docker compose -f "$COMPOSE_FILE" cp gateway:/data/orders "$OUT_DIR/orders"

echo "[2/3] copy feature-engine books archive -> $OUT_DIR/books"
docker compose -f "$COMPOSE_FILE" cp feature-engine:/data/books "$OUT_DIR/books"

echo "[3/3] copy feature-engine feature archive -> $OUT_DIR/features"
if docker compose -f "$COMPOSE_FILE" exec -T feature-engine test -d /data/features; then
  docker compose -f "$COMPOSE_FILE" cp feature-engine:/data/features "$OUT_DIR/features"
else
  echo "warning: /data/features is not present in feature-engine container; skipping" >&2
fi

cat <<EOF
exported paper archives:
  orders: $OUT_DIR/orders
  books : $OUT_DIR/books
  features: $OUT_DIR/features

next:
  uv run python scripts/run-paper-archive-backtest.py \\
    --date $DATE \\
    --orders-dir $OUT_DIR/orders \\
    --book-dir $OUT_DIR/books \\
    --output-dir $OUT_DIR/backtest \\
    --run-gate

  uv run python scripts/collect-feature-archive.py \\
    --date $DATE \\
    --features-dir $OUT_DIR/features \\
    --output $OUT_DIR/features.jsonl
EOF
