# Data-Capture And 2M Shadow-Forward Operating Rule

Date: 2026-07-12

Status: Approved and active.

## Decision

Until a separately approved activation decision, daily operation is a
data-capture and prospective-research operation, not a day-trading paper
operation.

- Primary research capital: 2,000,000 JPY.
- Existing intraday RULE, AI judge, relative-momentum, and other rejected day
  strategies must not generate new BUY orders.
- Day-paper PnL is not collected as strategy evidence.
- Tick, order-book, feature, financial-summary, and daily-OHLCV collection
  continues.
- Event/swing candidates are evaluated as shadow forward observations only.
- Neither event paper publication nor live publication is authorized.

This rule does not weaken the project kill switch or ADR-0006 cooling-off.

## Required Daily State

Before market-data collection starts:

- TRADE_MODE=paper;
- existing day BUY strategies disabled/no-op;
- event target publisher disabled;
- managed-Pub/Sub event publication disabled in code;
- OMS_LIVE_DRY_RUN=true;
- live BUY route not intentionally exercised;
- watchlist, Pub/Sub, Supabase, feeder, and feature-engine ready.

A no-op strategy configuration is intentional in this mode. Report it as
data-capture mode, not as a completed strategy paper session.

## Continue Running

- Universe Scanner and daily watchlist generation.
- Feeder tick and order-book capture for the scanner-gated watchlist.
- Feature Engine calculation and Parquet/archive persistence.
- Pub/Sub, Supabase, Dashboard, and service-health monitoring.
- J-Quants financial-summary and daily-OHLCV export.
- Causal schema-v3 event detection and append-only forward-ledger recording.
- Opening-book provenance needed for future execution reconciliation.
- Storage-capacity and Pub/Sub-emulator memory monitoring.

The default capture scope remains the scanner-gated watchlist, currently around
30 symbols. Event candidate symbols, when any exist, must remain observable for
their registered tracking horizon.

## Do Not Run As Daily Strategy Validation

- Existing intraday RULE BUY.
- AI-judge day entry.
- Rejected relative-momentum or temporary research plugins.
- Synthetic positions created only to observe 14:50 closeout.
- Continuous LLM inference without a separately preregistered experiment.
- Event frozen_opening_close_v1 target publication.
- Any live order or live-capital test.

OMS Paper may remain available for infrastructure tests, but ordinary daily
operation must not intentionally send day orders.

## Daily Sequence

### Before market

1. Generate and validate the scanner-gated watchlist.
2. Check Pub/Sub, Supabase, feeder, feature-engine, and storage readiness.
3. Confirm paper mode, day BUY disabled, event publisher disabled, and OMS Live
   dry-run.
4. Start or retain market-data capture.

### During market

1. Capture ticks, books, and derived features.
2. Do not generate ordinary day BUY orders.
3. Monitor data gaps, stale feeds, service failures, and storage pressure.
4. Preserve opening-sequence data, especially 09:00 JST provenance.

### After the signal date ends and before the next TSE session at 09:00 JST

The normal path is the user systemd timer:

- `roboinvest-shadow-forward-evidence.timer`
- daily at `00:10 JST`
- previous JST calendar day selected automatically
- non-TSE business days and already-completed dates skipped successfully
- partial artifact/ledger state and out-of-window catch-up fail closed
- full source evidence runs through the last evaluable 2026-08-27 signal date
- from 2026-08-28 through 2026-09-30, only official OHLCV export, outcome
  finalization, and readiness refresh run so the final registered outcome can
  mature without adding decision-ineligible candidates

Install or refresh the timer with:

    bash scripts/install-shadow-forward-evidence-timer.sh

Inspect it with:

    systemctl --user status roboinvest-shadow-forward-evidence.timer --no-pager
    systemctl --user list-timers roboinvest-shadow-forward-evidence.timer --all --no-pager
    journalctl --user -u roboinvest-shadow-forward-evidence.service -n 100 --no-pager

For an explicitly authorized manual fallback, run through 1Password:

    set -a && . infra/.op.service-account.env && set +a
    op run --env-file infra/env.production -- \
      uv run python scripts/run-event-forward-evidence.py \
        --signal-date YYYY-MM-DD

This appends financial summaries and OHLCV, runs the causal detector, and
records the result in the hash-chain ledger. It then finalizes any causally due
outcomes and refreshes `out/event-forward-evidence/kill-switch-readiness.json`.
A complete zero-candidate artifact is a valid observation, not a zero-PnL
trade.

The runner accepts the signal date only from 00:00 JST on the following
calendar day until immediately before 09:00 JST on the next TSE business day.
Outside that window it fails before API access or artifact creation. Do not
catch up missed dates retrospectively.

### After official daily OHLCV is updated

Run the idempotent offline outcome finalizer:

    uv run python scripts/finalize-event-forward-outcomes.py

It appends a separate hash-chain outcome only when a candidate has reached its
registered 20th-session close or an earlier 10% stop is observable. Missing
completed-session bars fail closed. Immature candidates remain pending, while
zero-candidate days are a no-op. The output is registered-backtest shadow data;
it is explicitly ineligible as paper/live execution evidence and does not by
itself calculate the frozen 2M portfolio result.

Refresh the frozen 2M portfolio and deadline status independently when needed:

    uv run python scripts/report-project-kill-switch-readiness.py \
      --output-json out/event-forward-evidence/kill-switch-readiness.json

For the 2026-09-30 decision, every TSE signal date from 2026-07-21 through
2026-08-27 must have a causal source row. This is 27 dates. A missing date is
not a zero-candidate observation and makes the deadline decision fail closed.

## Evidence Interpretation

- Shadow-forward candidates use the frozen 2M portfolio assumptions.
- Outcomes remain pending until the registered 20th-session exit or 10% stop is
  causally observable.
- Finalized outcome rows compare the frozen per-candidate price model with
  official OHLCV; `paper_execution_observed=false` and
  `execution_evidence_eligible=false` must remain unchanged.
- Accidental paper-day trades are operational anomalies and must not be mixed
  into swing profitability evidence.
- 1M remains a small-capital/lot diagnostic.
- 5M remains a capacity and concentration diagnostic.
- No threshold changes are allowed after observation.

## Exceptions

Infrastructure E2E or smoke tests that create paper orders require an explicit,
bounded test plan and cleanup. Their trades are test evidence, not strategy
evidence.

Continuous paper-day strategy validation may resume only for a new,
preregistered day hypothesis that passes archive replay, comparable random
baselines, costs/fills, and normal promotion gates.

## Next Gates

- ADR-0006 cooling-off ends no earlier than 2026-07-19 JST.
- Cooling-off completion alone does not activate event paper.
- Forward evidence, official-open/close reconciliation, paper execution
  reproduction, and the 2026-09-30 project kill-switch assessment remain
  required.
