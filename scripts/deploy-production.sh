#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

REF="main"
DRY_RUN="true"
POLL_SECONDS=10
POST_CHECK=1
KABU_OFFLINE=0
LOG_TAIL=80
GCP_CREDENTIALS=""
NO_PUBSUB_SMOKE=0
EXPECTED_TRADE_MODE="live"

usage() {
  cat <<'USAGE'
Usage: bash scripts/deploy-production.sh [options]

Dispatch and monitor the Deploy Production GitHub Actions workflow.

Options:
  --apply             Restart production services after validation/build.
  --ref REF           Git ref to deploy. Default: main.
  --kabu-offline      Run post-check with kabu connectivity treated as offline.
  --skip-post-check   Skip production-preopen-check after a successful deploy.
  --gcp-credentials PATH
                      Host path to GCP credentials for post-check Pub/Sub checks.
  --no-pubsub-smoke   Skip post-check Pub/Sub smoke publish/pull/ack.
  --expected-trade-mode MODE
                      Expected post-check TRADE_MODE/system_status.trade_mode: live or paper.
  --log-tail N        Tail gateway/oms-live logs after deploy. Use 0 to skip.
  --poll-seconds N    Poll GitHub Actions every N seconds. Default: 10.
  -h, --help          Show this help.

Examples:
  bash scripts/deploy-production.sh
  bash scripts/deploy-production.sh --apply --kabu-offline
USAGE
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --apply)
      DRY_RUN="false"
      shift
      ;;
    --ref)
      if [ "$#" -lt 2 ]; then
        echo "missing value for --ref" >&2
        exit 2
      fi
      REF="$2"
      shift 2
      ;;
    --kabu-offline)
      KABU_OFFLINE=1
      shift
      ;;
    --skip-post-check)
      POST_CHECK=0
      shift
      ;;
    --gcp-credentials)
      if [ "$#" -lt 2 ]; then
        echo "missing value for --gcp-credentials" >&2
        exit 2
      fi
      GCP_CREDENTIALS="$2"
      shift 2
      ;;
    --no-pubsub-smoke)
      NO_PUBSUB_SMOKE=1
      shift
      ;;
    --expected-trade-mode)
      if [ "$#" -lt 2 ]; then
        echo "missing value for --expected-trade-mode" >&2
        exit 2
      fi
      EXPECTED_TRADE_MODE="$2"
      shift 2
      ;;
    --log-tail)
      if [ "$#" -lt 2 ]; then
        echo "missing value for --log-tail" >&2
        exit 2
      fi
      LOG_TAIL="$2"
      shift 2
      ;;
    --poll-seconds)
      if [ "$#" -lt 2 ]; then
        echo "missing value for --poll-seconds" >&2
        exit 2
      fi
      POLL_SECONDS="$2"
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

require_command() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "missing command: $1" >&2
    exit 1
  fi
}

require_command gh

if ! [[ "$POLL_SECONDS" =~ ^[0-9]+$ ]] || [ "$POLL_SECONDS" -lt 1 ]; then
  echo "--poll-seconds must be a positive integer" >&2
  exit 2
fi

if ! [[ "$LOG_TAIL" =~ ^[0-9]+$ ]]; then
  echo "--log-tail must be a non-negative integer" >&2
  exit 2
fi

if [ "$EXPECTED_TRADE_MODE" != "live" ] && [ "$EXPECTED_TRADE_MODE" != "paper" ]; then
  echo "--expected-trade-mode must be live or paper" >&2
  exit 2
fi

if [ "$DRY_RUN" = "false" ] && [ "$REF" != "main" ]; then
  echo "--apply is only allowed with --ref main" >&2
  exit 2
fi

dirty="$(git status --short)"
if [ -n "$dirty" ]; then
  echo "[warn] local working tree has uncommitted changes; deploy uses committed GitHub ref:"
  printf '%s\n' "$dirty" | sed 's/^/[warn]   /'
fi

echo "[1/4] dispatch Deploy Production ref=${REF} dry_run=${DRY_RUN}"
gh workflow run "Deploy Production" -f "ref=${REF}" -f "dry_run=${DRY_RUN}"

sleep 3
run_id="$(
  gh run list \
    --workflow deploy-production.yml \
    --branch "$REF" \
    --event workflow_dispatch \
    --limit 1 \
    --json databaseId \
    --jq '.[0].databaseId'
)"

if [ -z "$run_id" ] || [ "$run_id" = "null" ]; then
  echo "failed to resolve workflow run id" >&2
  exit 1
fi

run_url="$(gh run view "$run_id" --json url --jq '.url')"
echo "[2/4] monitor run_id=${run_id}"
echo "[info] ${run_url}"

last_state=""
while true; do
  state="$(
    gh run view "$run_id" \
      --json status,conclusion \
      --jq '.status + " " + (.conclusion // "")'
  )"
  if [ "$state" != "$last_state" ]; then
    echo "[status] ${state}"
    last_state="$state"
  fi

  status="${state%% *}"
  conclusion="${state#* }"
  if [ "$status" = "completed" ]; then
    if [ "$conclusion" != "success" ]; then
      echo "[fail] deploy workflow conclusion=${conclusion}" >&2
      exit 1
    fi
    break
  fi
  sleep "$POLL_SECONDS"
done

echo "[3/4] workflow success"

if [ "$LOG_TAIL" -gt 0 ] || [ "$POST_CHECK" -eq 1 ]; then
  require_command op
  require_command docker
  if [ -f infra/.op.service-account.env ] && [ -z "${OP_SERVICE_ACCOUNT_TOKEN:-}" ]; then
    set -a
    . infra/.op.service-account.env
    set +a
  fi
fi

if [ "$LOG_TAIL" -gt 0 ]; then
  echo "[post] docker compose ps"
  op run --env-file infra/env.production -- \
    docker compose --env-file infra/env.production -f infra/docker-compose.prod.yml ps

  echo "[post] gateway/oms-live logs --tail=${LOG_TAIL}"
  op run --env-file infra/env.production -- \
    docker compose --env-file infra/env.production -f infra/docker-compose.prod.yml \
      logs --tail "$LOG_TAIL" gateway oms-live
fi

if [ "$POST_CHECK" -eq 1 ]; then
  echo "[4/4] production post-check"
  check_args=(--timeout 30 --expected-trade-mode "$EXPECTED_TRADE_MODE")
  if [ "$KABU_OFFLINE" -eq 1 ]; then
    check_args+=(--kabu-offline)
  fi
  if [ -n "$GCP_CREDENTIALS" ]; then
    check_args+=(--gcp-credentials "$GCP_CREDENTIALS")
  fi
  if [ "$NO_PUBSUB_SMOKE" -eq 1 ]; then
    check_args+=(--no-pubsub-smoke)
  fi
  op run --env-file infra/env.production -- \
    uv run python scripts/production-preopen-check.py "${check_args[@]}"
else
  echo "[4/4] post-check skipped"
fi

echo "[done] deploy production run ${run_id}"
