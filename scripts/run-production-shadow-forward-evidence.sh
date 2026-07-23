#!/usr/bin/env bash
set -euo pipefail

TASK_REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$TASK_REPO_ROOT"

if [[ ! -r infra/.op.service-account.env ]]; then
  echo "shadow_forward_daily error=missing_1password_service_account_env" >&2
  exit 1
fi

set -a
. infra/.op.service-account.env
set +a

exec op run --env-file infra/env.production -- \
  uv run python scripts/run-daily-shadow-forward-evidence.py "$@"
