# 2026-07-05 Paper Ready Handoff

Purpose: 2026-07-06 JST paper observation pre-open readiness handoff.

## Current State

- Universe Scanner completed for `valid_date=2026-07-06`.
- Terminal log confirmed `done: valid_date=2026-07-06 watchlist_size=30`.
- `daily_ohlcv` was caught up through `2026-07-03`.
- `OMS_LIVE_ALLOWED_SYMBOLS` was synced from the 2026-07-06 scanner-gated watchlist.
- Production compose services were started in paper mode.
- Final check passed with `OK 127 / WARN 2 / NG 0 / SKIP 0`.
- The two WARNs were expected because the check was run with `--kabu-offline`:
  feeder restart and kabu websocket `HTTP 502`.

Important mode/safety values:

- `TRADE_MODE=paper`
- `OMS_LIVE_DRY_RUN=true`
- `OMS_LIVE_STOP_MONITOR_ENABLED=false`
- `PAPER_DAY_STOP_MONITOR_ENABLED=true`
- `STRATEGIES_ENABLED=relative_momentum`
  - Paper observation days must not run with an empty strategy set.
  - `production-preopen-check.py` should fail if `strategy-rule` is no-op.

## Credential Note

The normal host credential path `/dev/shm/roboinvest/gcp-pubsub-sa.json` was a
root-owned directory and could not be replaced without sudo. A temporary host
credential file was materialized instead:

```bash
/tmp/roboinvest-gcp-pubsub-sa.json
```

It is required for the currently running compose bind mount. Do not delete it
until the stack is stopped or recreated with another valid
`GOOGLE_APPLICATION_CREDENTIALS_HOST_PATH`.

When using `op run --env-file infra/env.production`, pass the alternate path
inside the command with `env`, because `infra/env.production` otherwise
overrides shell-level `GOOGLE_APPLICATION_CREDENTIALS_HOST_PATH`.

## Tomorrow Morning Check

After kabu station / Windows proxy is up, run this from the repo root:

```bash
set -a && . infra/.op.service-account.env && set +a
op run --env-file infra/env.production -- \
  env GOOGLE_APPLICATION_CREDENTIALS_HOST_PATH=/tmp/roboinvest-gcp-pubsub-sa.json \
    uv run python scripts/production-preopen-check.py \
      --timeout 30 \
      --expected-trade-mode paper \
      --target-date 2026-07-06 \
      --refresh-kabu-token \
      --gcp-credentials /tmp/roboinvest-gcp-pubsub-sa.json
```

Expected result:

- `NG 0`
- feeder/kabu is no longer only `--kabu-offline` WARN
- Pub/Sub smoke still passes
- `watchlist target-date` is `30 rows valid_date=2026-07-06`
- `oms-live allowed scanner gate` is `30 symbols`

If `--refresh-kabu-token` is rejected because the command is outside the
allowed pre-open window, rerun without it and inspect feeder/kabu logs. Use
`--allow-market-hours-refresh` only with explicit intent.

## If Compose Needs Recreate

Use this form so the alternate credential path is mounted:

```bash
set -a && . infra/.op.service-account.env && set +a
op run --env-file infra/env.production -- \
  env GOOGLE_APPLICATION_CREDENTIALS_HOST_PATH=/tmp/roboinvest-gcp-pubsub-sa.json \
    docker compose --env-file infra/env.production -f infra/docker-compose.prod.yml up -d
```

Check status:

```bash
op run --env-file infra/env.production -- \
  env GOOGLE_APPLICATION_CREDENTIALS_HOST_PATH=/tmp/roboinvest-gcp-pubsub-sa.json \
    docker compose --env-file infra/env.production -f infra/docker-compose.prod.yml ps
```

## Start-of-Session Files

Read these first tomorrow:

1. `AGENTS.md`
2. `docs/HANDOFF.md`
3. This file
4. `docs/runbook/paper-open-quickstart.md`
5. `docs/runbook/paper-open-checklist.md`
