#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

DATE=""
OUT_DIR=""
COMPOSE_FILE="infra/docker-compose.prod.yml"
TRADE_MODE="paper"
SYMBOLS=""
POSITIONS=""
SKIP_EXPORT=0
GATE_ARGS=()

usage() {
  cat <<'USAGE'
Usage: bash scripts/run-paper-postmortem.sh --date YYYY-MM-DD [options]

Export archived paper orders/books from Docker volumes, run OMS Paper archive
backtest, evaluate the gate, and write a Markdown summary.

Options:
  --date YYYY-MM-DD        Trading date.
  --output-dir DIR         Output directory. Default: out/paper-archive-YYYY-MM-DD.
  --compose-file FILE      Docker Compose file. Default: infra/docker-compose.prod.yml.
  --trade-mode MODE        Archive trade mode. Default: paper.
  --symbols A,B            Optional symbols filter for book export/backtest.
  --positions FILE         Optional initial positions JSON.
  --skip-export            Reuse OUT/orders and OUT/books without docker compose cp.
  --gate-arg ARG           Extra gate/backtest arg. Repeat for args with values.
  -h, --help              Show this help.

Example:
  bash scripts/run-paper-postmortem.sh \
    --date 2026-06-15 \
    --gate-arg --min-profit-factor --gate-arg 1.2 \
    --gate-arg --max-average-spread-bps --gate-arg 20
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
    --trade-mode)
      if [ "$#" -lt 2 ]; then
        echo "missing value for --trade-mode" >&2
        exit 2
      fi
      TRADE_MODE="$2"
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
    --positions)
      if [ "$#" -lt 2 ]; then
        echo "missing value for --positions" >&2
        exit 2
      fi
      POSITIONS="$2"
      shift 2
      ;;
    --skip-export)
      SKIP_EXPORT=1
      shift
      ;;
    --gate-arg)
      if [ "$#" -lt 2 ]; then
        echo "missing value for --gate-arg" >&2
        exit 2
      fi
      GATE_ARGS+=("$2")
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

if [ "$SKIP_EXPORT" -eq 1 ]; then
  if [ ! -d "$OUT_DIR/orders" ] || [ ! -d "$OUT_DIR/books" ]; then
    echo "--skip-export requires existing $OUT_DIR/orders and $OUT_DIR/books" >&2
    exit 2
  fi
  echo "[postmortem] skip export; reusing $OUT_DIR/orders and $OUT_DIR/books"
else
  bash scripts/export-paper-archives.sh \
    --date "$DATE" \
    --output-dir "$OUT_DIR" \
    --compose-file "$COMPOSE_FILE"
fi

cmd=(
  uv run python scripts/run-paper-archive-backtest.py
  --date "$DATE"
  --trade-mode "$TRADE_MODE"
  --orders-dir "$OUT_DIR/orders"
  --book-dir "$OUT_DIR/books"
  --output-dir "$OUT_DIR/backtest"
  --run-gate
  --summary
)

if [ -n "$SYMBOLS" ]; then
  cmd+=(--symbols "$SYMBOLS")
fi

if [ -n "$POSITIONS" ]; then
  cmd+=(--positions "$POSITIONS")
fi

if [ "${#GATE_ARGS[@]}" -gt 0 ]; then
  cmd+=("${GATE_ARGS[@]}")
fi

echo "[postmortem] ${cmd[*]}"
set +e
"${cmd[@]}"
rc="$?"
set -e

cat <<EOF

postmortem artifacts:
  summary : $OUT_DIR/backtest/summary.md
  gate    : $OUT_DIR/backtest/gate_report.json
  report  : $OUT_DIR/backtest/backtest_report.json

record template:
  docs/reports/paper-postmortem-template.md
EOF

exit "$rc"
