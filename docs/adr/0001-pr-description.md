# PR: ADR-0001 production deployment foundation

## Summary

Implements the ADR-0001 production deployment foundation for running the Python services on a LAN host while using managed GCP Pub/Sub, Supabase Cloud, Vercel, and 1Password-based secret injection.

This PR keeps live trading gated. The production compose defaults to `TRADE_MODE=paper` and `OMS_LIVE_DRY_RUN=true`, and the GitHub Actions deploy workflow defaults to `dry_run=true`.

## What Changed

- Added production Docker Compose for the 9 Python services:
  - `infra/docker-compose.prod.yml`
  - `infra/env.production.tpl`
  - `services/Dockerfile`
- Moved service Pub/Sub clients to a shared `google-cloud-pubsub` wrapper:
  - `contracts/python/trade_contracts/pubsub_client.py`
  - service-level `clients/pubsub.py` modules now re-export the shared wrapper
  - local/unit tests keep the old REST-compatible path when `httpx` transport is injected
  - emulator support is explicit when `emulator_host` is passed
- Added managed GCP Pub/Sub admin tooling:
  - `scripts/gcp-pubsub-admin.py`
  - topic/subscription runbook
- Added production runbooks for:
  - 1Password setup and service account token rotation
  - GCP Pub/Sub
  - Supabase Cloud
  - Production Compose
  - Dashboard / Vercel
  - Paper production trial
  - GitHub Actions self-hosted deploy
- Added GitHub Actions production deploy workflow:
  - `workflow_dispatch` only
  - verifies successful `ci.yml` for the target ref
  - runs on `[self-hosted, roboinvest-prod]`
  - uses `production` environment approval
  - defaults to `dry_run=true`
- Added Dashboard/Vercel production verification notes and anon read policy for browser Realtime.
- Updated ADR-0001 implementation checklist and next-session handoff notes.

## Verified

- Pub/Sub client unit tests: `79 passed`
- `python3 -m py_compile contracts/python/trade_contracts/pubsub_client.py scripts/gcp-pubsub-admin.py`
- `uv run ruff format --check contracts/python/trade_contracts/pubsub_client.py scripts/gcp-pubsub-admin.py`
- `uv run ruff check contracts/python/trade_contracts/pubsub_client.py scripts/gcp-pubsub-admin.py services/*/src/*/clients/pubsub.py`
- `uv run mypy contracts/python/trade_contracts/pubsub_client.py`
- `git diff main...HEAD --check`
- Production compose config:
  - normal profile OK
  - batch profile OK
  - no `PUBSUB_EMULATOR_HOST`
  - no raw `op://` references after `op run`
- 9 service container `--help` checks OK, including `universe-scanner` batch profile
- Cloud Supabase health check OK:
  - `system_status`
  - `positions`
  - `strategy_logs`
  - `aggregator_logs`
  - `trades_live`
  - `trades_paper`
  - `watchlist`
  - `master_stocks`
  - `daily_ohlcv`
- Managed GCP Pub/Sub check-only OK:
  - 7 topics
  - 9 subscriptions
- Dashboard local checks documented as OK:
  - lint
  - test
  - typecheck
  - build
  - service-role key not found in local build artifacts
- Vercel Preview route checks documented as OK:
  - `/`
  - `/positions?type=paper`
  - `/trades?type=paper`
  - `/signals`
  - `/system`

## Safety Notes

- This PR does not enable live trading.
- Production compose defaults:
  - `TRADE_MODE=paper`
  - `OMS_LIVE_DRY_RUN=true`
  - `KABU_DEFAULT_EXCHANGE=9`
  - `FEEDER_KABU_DEFAULT_EXCHANGE=1`
- `infra/env.production`, `infra/.op.service-account.env`, and `infra/secrets/` are gitignored.
- The GCP service account JSON is materialized only as `infra/secrets/gcp-pubsub-sa.json` for the current trial approach.
- self-hosted runner security is documented; actual runner install and dry-run workflow execution are still pending.
- 1Password service account token rotation is documented because a token was exposed in terminal output during setup; actual rotation is still pending.

## Not Done / Follow-Up

- LAN host preflight inventory:
  - OS / CPU / RAM / SSD / Docker version
  - kabu Windows Caddy `28080` / `28081` read-only probes
- GCP Pub/Sub smoke test from LAN host with `--smoke-test --cleanup-smoke`
- 1Password service account token actual rotation
- Remove or rotate materialized GCP key after trial as appropriate
- Install repo-scoped self-hosted runner
- Run Deploy Production workflow with `dry_run=true`
- Confirm GitHub Actions logs do not contain secret values
- 14:50 JST paper day closeout actual observation
- Live readiness gate
- First live cutover

## Reviewer Focus

- `contracts/python/trade_contracts/pubsub_client.py`
  - managed Pub/Sub behavior
  - emulator behavior
  - REST test-transport compatibility
- `infra/docker-compose.prod.yml`
  - env defaults
  - mounted secret file
  - KABU token cache sharing between Feeder and OMS Live
  - OMS Live safety knobs
- `.github/workflows/deploy-production.yml`
  - no automatic deploy trigger
  - CI success requirement
  - environment approval
  - secret handling
- runbooks:
  - whether the recovery/rollback and token/key handling steps are operationally clear

## Commits Of Interest

- `8b8f26c feat: add ADR-0001 production compose`
- `8d24d01 ci: add production deploy workflow`
- `244a3b2 docs: add 1password token rotation runbook`
- `77c8093 docs: document production runner security`
- `df72f36 docs: add pr readiness handoff`
