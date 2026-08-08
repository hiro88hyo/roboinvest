#!/usr/bin/env bash
set -euo pipefail

TASK_REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$TASK_REPO_ROOT"

if [[ ! -r infra/.op.service-account.env ]]; then
  echo "production_preopen_check status=failed reason=missing_1password_service_account_env" >&2
  exit 1
fi

set -a
. infra/.op.service-account.env
set +a

export TZ=Asia/Tokyo
TASK_TARGET_DATE="$(date '+%Y-%m-%d')"
echo "production_preopen_check status=started target_date=$TASK_TARGET_DATE"

if op run --env-file infra/env.production -- \
  uv run python scripts/production-preopen-check.py \
    --timeout 30 \
    --expected-trade-mode paper \
    --refresh-kabu-token \
    --skip-non-business-day \
    --quiet \
    "$@"; then
  echo "production_preopen_check status=completed target_date=$TASK_TARGET_DATE"
else
  TASK_EXIT_CODE=$?
  echo "production_preopen_check status=failed target_date=$TASK_TARGET_DATE exit_code=$TASK_EXIT_CODE" >&2
  exit "$TASK_EXIT_CODE"
fi
