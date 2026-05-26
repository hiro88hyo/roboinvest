#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

LOCK_DIR="/tmp/roboinvest-universe-scanner.lock"
TARGET_DATE=""
RUN_BUILD=0
SKIP_HEALTH_CHECK=0
SKIP_OMS_LIVE_SYNC=0

usage() {
  cat <<'USAGE'
Usage: bash scripts/run-production-universe-scanner.sh [options]

Options:
  --date YYYY-MM-DD     Run for a specific JST business date.
  --build               Build the universe-scanner image before running.
  --skip-health-check   Skip the post-run Supabase health check.
  --skip-oms-live-sync  Skip syncing OMS_LIVE_ALLOWED_SYMBOLS from today's watchlist.
  -h, --help            Show this help.
USAGE
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --date)
      if [ "$#" -lt 2 ]; then
        echo "missing value for --date" >&2
        exit 2
      fi
      TARGET_DATE="$2"
      shift 2
      ;;
    --build)
      RUN_BUILD=1
      shift
      ;;
    --skip-health-check)
      SKIP_HEALTH_CHECK=1
      shift
      ;;
    --skip-oms-live-sync)
      SKIP_OMS_LIVE_SYNC=1
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


resolve_target_date() {
  if [ -n "$TARGET_DATE" ]; then
    printf '%s\n' "$TARGET_DATE"
  else
    date '+%Y-%m-%d'
  fi
}

sync_oms_live_allowed_symbols() {
  local valid_date="$1"
  local symbols

  symbols="$(op run --env-file infra/env.production -- \
    uv run python - "$valid_date" <<'PY'
import os
import sys

import httpx

valid_date = sys.argv[1]
url = os.environ["SUPABASE_URL"].rstrip("/")
key = os.environ["SUPABASE_SECRET_KEY"]
headers = {"apikey": key, "Authorization": f"Bearer {key}"}
path = (
    "/rest/v1/watchlist?select=symbol"
    f"&valid_date=eq.{valid_date}"
    "&order=symbol.asc"
)
with httpx.Client(timeout=30) as client:
    response = client.get(url + path, headers=headers)
    response.raise_for_status()
    rows = response.json()
print(",".join(row["symbol"] for row in rows))
PY
  )"

  if [ -z "$symbols" ]; then
    echo "[post] skip OMS_LIVE_ALLOWED_SYMBOLS sync: watchlist empty for ${valid_date}"
    return 0
  fi

  op run --env-file infra/env.production -- \
    uv run python - "$symbols" <<'PY'
from pathlib import Path
import sys

symbols = sys.argv[1]
path = Path("infra/env.production")
lines = path.read_text(encoding="utf-8").splitlines()
updated = False
for index, line in enumerate(lines):
    if line.startswith("OMS_LIVE_ALLOWED_SYMBOLS="):
        if line == f"OMS_LIVE_ALLOWED_SYMBOLS={symbols}":
            print("UNCHANGED OMS_LIVE_ALLOWED_SYMBOLS")
        else:
            lines[index] = f"OMS_LIVE_ALLOWED_SYMBOLS={symbols}"
            path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            print(f"UPDATED OMS_LIVE_ALLOWED_SYMBOLS count={len(symbols.split(','))}")
        updated = True
        break
if not updated:
    raise SystemExit("OMS_LIVE_ALLOWED_SYMBOLS line not found in infra/env.production")
PY

  echo "[post] validate production compose config after OMS live sync..."
  op run --env-file infra/env.production -- \
    docker compose -f infra/docker-compose.prod.yml --profile batch config >/dev/null

  if [ -n "$(op run --env-file infra/env.production -- docker compose -f infra/docker-compose.prod.yml ps --status running -q oms-live)" ]; then
    echo "[post] recreate running oms-live to apply OMS_LIVE_ALLOWED_SYMBOLS..."
    op run --env-file infra/env.production -- \
      docker compose -f infra/docker-compose.prod.yml up -d --no-deps oms-live
  else
    echo "[post] skip oms-live recreate: service is not running"
  fi
}

cleanup() {
  rm -rf "$LOCK_DIR"
}

if ! mkdir "$LOCK_DIR" 2>/dev/null; then
  echo "another universe-scanner run is already in progress: $LOCK_DIR" >&2
  exit 1
fi
trap cleanup EXIT INT TERM

if [ -f infra/.op.service-account.env ] && [ -z "${OP_SERVICE_ACCOUNT_TOKEN:-}" ]; then
  set -a
  . infra/.op.service-account.env
  set +a
fi

if [ ! -f infra/env.production ]; then
  echo "missing infra/env.production" >&2
  exit 1
fi

if [ ! -f /dev/shm/roboinvest/gcp-pubsub-sa.json ]; then
  echo "missing /dev/shm/roboinvest/gcp-pubsub-sa.json" >&2
  exit 1
fi

export TZ=Asia/Tokyo
STARTED_AT="$(date '+%Y-%m-%d %H:%M:%S %Z')"

echo "=== ${STARTED_AT} run-production-universe-scanner ==="

echo "[1/4] validate production compose config..."
op run --env-file infra/env.production -- \
  docker compose -f infra/docker-compose.prod.yml --profile batch config >/dev/null

echo "[2/4] validate production supabase connectivity..."
op run --env-file infra/env.production -- \
  uv run python scripts/health-check.py --check supabase --timeout 30

if [ "$RUN_BUILD" -eq 1 ]; then
  echo "[3/4] build universe-scanner image..."
  op run --env-file infra/env.production -- \
    docker compose -f infra/docker-compose.prod.yml --profile batch build universe-scanner
else
  echo "[3/4] skip image build (--build not set)..."
fi

echo "[4/4] run universe-scanner batch..."
run_args=(
  op run --env-file infra/env.production --
  docker compose -f infra/docker-compose.prod.yml --profile batch run --rm universe-scanner
)
if [ -n "$TARGET_DATE" ]; then
  run_args+=(--date "$TARGET_DATE")
fi
"${run_args[@]}"

if [ "$SKIP_OMS_LIVE_SYNC" -eq 0 ]; then
  echo "[post] sync OMS_LIVE_ALLOWED_SYMBOLS from watchlist..."
  sync_oms_live_allowed_symbols "$(resolve_target_date)"
else
  echo "[post] skip OMS_LIVE_ALLOWED_SYMBOLS sync (--skip-oms-live-sync set)..."
fi

if [ "$SKIP_HEALTH_CHECK" -eq 0 ]; then
  echo "[post] validate supabase after batch..."
  op run --env-file infra/env.production -- \
    uv run python scripts/health-check.py --check supabase --timeout 30
else
  echo "[post] skip health check (--skip-health-check set)..."
fi

echo "=== $(date '+%Y-%m-%d %H:%M:%S %Z') completed ==="
