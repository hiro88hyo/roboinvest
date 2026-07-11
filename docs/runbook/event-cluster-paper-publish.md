# Event Cluster Paper Publish

Status: the dedicated paper-only publisher is implemented and passes the local
Pub/Sub + Supabase E2E as of 2026-07-10, but **activation against the target
environment remains blocked**. The target OMS Paper migration/RPC health and
event-paper claim CAS RPC health, plus the managed `event-paper-raw-books`
subscription, must be verified. More importantly, the implemented publisher is
`opening_transport_stress_v1`: the local close-session profile is now wired to
the frozen 20th-session `15:30 JST` exit, but matched-random evidence and
target-environment readiness are still absent. It cannot be activated as
frozen-v1 paper evidence.

The old publisher used the future T+1 open as `StrategySignal.price` and as the
basis of an absolute stop. The detector's old `--publish-paper` path remains
fail closed. Execution is isolated in `strategy_rule event-paper-publish`, which
uses a fresh observed ask, a relative 10% stop anchored by OMS Paper to the
actual fill, and an immutable `PAPER_ONLY` routing ceiling. This does not change
the candidate parameters or the live gate. Its signal/receipt use a separate
stress identity and record `comparable_to_registered_backtest=false`.

## Preconditions

- Use the latest available TSE signal date.
- Do not pass `--publish-paper` to the detector; that inline option is
  intentionally blocked.
- Do not interpret a candidate artifact as an executable order or a
  profitability result.
- Require artifact `schema_version=3`, an exact `target_date` match, and a
  separate publication receipt. Never edit the detector artifact to make it
  executable.

## Dry Run

First append the latest J-Quants data to the local research archives. Keep
`SIGNAL_DATE` on the latest available TSE business day, not on a weekend or
holiday. The final financial-summary fetch used for an operational artifact
must run after `SIGNAL_DATE` has ended in JST and before 09:00 JST on the next
TSE business day. This prevents a complete-but-early HTTP response from being
mistaken for complete coverage of the disclosure date.

```bash
cd /home/hiroyuki/workspaces/roboinvest
SIGNAL_DATE=YYYY-MM-DD
set -a && . infra/.op.service-account.env && set +a
op run --env-file infra/env.production -- \
  uv run python scripts/export-jquants-financial-summaries-jsonl.py \
    --start-date "$SIGNAL_DATE" \
    --end-date "$SIGNAL_DATE" \
    --output out/event-research/financial-summaries-20210628-20260624-clean.jsonl \
    --log-every-dates 1 \
    --concurrency 1 \
    --sleep-seconds 0.2
op run --env-file infra/env.production -- \
  uv run python scripts/export-jquants-daily-ohlcv-csv.py \
    --start-date YYYY-MM-DD \
    --end-date "$SIGNAL_DATE" \
    --output data/reference/daily_ohlcv_20210625_20260624_bydate.csv \
    --resume \
    --concurrency 1 \
    --sleep-seconds 0.2
```

Then run detection without publish flags:

```bash
cd /home/hiroyuki/workspaces/roboinvest
SIGNAL_DATE=YYYY-MM-DD
uv run python scripts/detect-event-cluster-paper-candidates.py \
  --financial-summary-jsonl out/event-research/financial-summaries-20210628-20260624-clean.jsonl \
  --ohlcv data/reference/daily_ohlcv_20210625_20260624_bydate.csv \
  --output-json "out/event-paper-observation/candidates-${SIGNAL_DATE}.json" \
  --output-csv "out/event-paper-observation/candidates-${SIGNAL_DATE}.csv" \
  --signal-date "$SIGNAL_DATE"
```

Confirm `causality_verified=true`, `publish_enabled=false`, and
`causality.candidate_artifact_contains_entry_price=false`. A candidate may now
be detected with OHLCV ending on `SIGNAL_DATE`; T+1 OHLCV is not required and,
even if present in the CSV, is not consulted for candidate features. The
valuation bar must be the one required at the original disclosure-time
`feature_cutoff_at`; a next-morning fetch is recorded separately as
`source_received_at` and must never advance that cutoff.

For each disclosure, the detector requires exactly one daily OHLCV session:
the signal-date bar at or after 15:30 JST, otherwise the preceding TSE business
session. If that required row is absent, it does not substitute an older bar or
evaluate the PER guard with one. Instead it preserves the frozen event
occurrence as a reportable `feature_data_complete=false` candidate with
`selection_status=incomplete_required_ohlcv_session`. Strict pre-open,
watchlist preparation, and publisher checks must reject it for execution until
the source archive is complete. `missing_required_ohlcv_session_count` counts
these operationally ineligible candidate rows.

The financial-summary exporter records `_roboinvest_fetched_at` on each source
row and writes a fetch-metadata row, including for a date with zero disclosures.
The detector preserves that source-receipt provenance instead of substituting
its own execution time. Confirm the artifact reports
`causality.receipt_provenance=export_metadata` and
`causality.fetch_completion_verified=true`, and
`causality.source_coverage_window_verified=true`. A fetch before the signal
date ends is incomplete coverage; a fetch at or after 09:00 JST on the intended
entry date is late. Neither artifact is operationally valid. Late event rows
are recorded as `late_data_receipt` instead of being backdated into the cohort.

Before changing the watchlist, run the canonical production pre-open check on
the artifact:

```bash
cd /home/hiroyuki/workspaces/roboinvest
SIGNAL_DATE=YYYY-MM-DD
TARGET_DATE=YYYY-MM-DD
set -a && . infra/.op.service-account.env && set +a
op run --env-file infra/env.production -- \
  env PUBSUB_EMULATOR_HOST= \
  uv run python scripts/production-preopen-check.py \
    --timeout 30 \
    --expected-trade-mode paper \
    --target-date "$TARGET_DATE" \
    --swing-candidates-json "out/event-paper-observation/candidates-${SIGNAL_DATE}.json"
```

Do not continue to watchlist capture if candidate-artifact validation is `NG`.

## Watchlist Capture

Before the entry session, add event symbols to the watchlist so Feeder registers
them and Feature Engine can capture 1-minute data:

```bash
cd /home/hiroyuki/workspaces/roboinvest
SIGNAL_DATE=YYYY-MM-DD
set -a && . infra/.op.service-account.env && set +a
op run --env-file infra/env.production -- \
  uv run python scripts/upsert-event-candidates-watchlist.py \
    --candidates-json "out/event-paper-observation/candidates-${SIGNAL_DATE}.json" \
    --output-json out/event-paper-observation/event-watchlist-upsert.json
```

Run this before Feeder's pre-open watchlist poll. The script inserts only
missing event symbols and does not overwrite Universe Scanner rows.

## Dedicated Paper Publisher And Activation Gate

Passing `--publish-paper` to
`scripts/detect-event-cluster-paper-candidates.py` exits before writing an
artifact, inserting `strategy_logs`, or calling Pub/Sub. The only supported
implementation is the separately gated one-shot command:

```text
python -m strategy_rule event-paper-publish
```

It requires both the CLI `--publish-paper` latch and
`EVENT_CLUSTER_PAPER_PUBLISH_ENABLED=true`. The committed example environment
keeps the flag `false`. The command is also constrained to 09:00–09:30 JST on
the artifact's exact entry date. At present this command is a transport stress
only and target use is not authorized.

The completed and remaining safety work is tracked separately below. Completed
items do not authorize publication by themselves.

Already implemented while publication remains blocked:

- `StrategySignal`, `UnifiedTradeSignal`, and `OrderRequest` accept a positive
  `stop_loss_pct` below 1 and reject simultaneous absolute and relative stops;
- Gateway preserves holding and exit metadata, sizes against the relative stop,
  and rejects a live BUY carrying that stop intent;
- OMS Paper fixes a new BUY's absolute stop to its actual fill and carries
  `holding_type=swing`, `max_hold_days`, `scheduled_exit_date`, and the frozen
  event-only `scheduled_exit_time=15:30 JST`; and
- the 14:50 day closeout ignores swing positions;
- live Feeder books carry a separate `received_at`, and OMS Paper uses its wall
  clock for stale/future checks while requiring that provenance for PAPER_ONLY;
- `routing_intent=PAPER_ONLY` is preserved through Aggregator/Gateway/Order and
  rejected at every live boundary; and
- `strategy_key` plus per-occurrence `candidate_id` isolates pairing, while
  StrategySignal/UnifiedTradeSignal/Order IDs are deterministic on redelivery.
  The execution key is
  `<frozen-selection-key>__opening_transport_stress_v1`, so these rows cannot be
  silently pooled with frozen-v1 evidence;
- the isolated event-paper path persists its immutable input/output payload
  before Aggregator or Gateway publishes. A confirmed replay is suppressed;
  before Gateway resumes a prepared journal, it requires the current signal's
  canonical input payload/hash to match the stored value;
  an uncheckpointed external attempt becomes `ambiguous` and is never
  automatically re-published;
- OMS Paper persists every normal/closeout/swing/day-stop fill and its position
  transition through one `oms_paper_apply_fill` transaction, keyed primarily by
  `trades_paper.order_id`; actual-RPC tests cover concurrent BUYs, redelivery,
  partial/full SELL, rollback, `opened_at` ABA rejection, persistent
  `position_generation_id` lineage, and role restrictions; and
- health check verifies `positions.scheduled_exit_time`, `trades_paper.order_id`
  / `position_generation_id` plus safe executable presence of
  `event_paper_cas_strategy_reasoning`,
  `event_paper_stage_dispatch`, `oms_paper_apply_fill`, and generation-checked
  `oms_paper_update_stop_loss`;
- the one-shot publisher consumes only `event-paper-raw-books`, uses a targeted
  seek, validates a `received_at`-proven fresh best ask (age at most 10 seconds,
  future skew at most 5 seconds), and emits RULE/SWING/PAPER_ONLY with frozen
  confidence `0.5`, `stop_loss_pct=0.10`, and `max_hold_days=20`;
- preflight requires `trade_mode=paper`, trading allowed, the atomic fill/stop
  RPC capabilities, and no due or unscheduled max-hold swing positions;
- the first selected quote is inserted once as a versioned claim in
  `strategy_logs.reasoning`. A body-based
  `event_paper_cas_strategy_reasoning` RPC atomically marks one publication
  attempt immediately before the single external Pub/Sub RPC, then checkpoints
  success (topic, message ID, publication time) into that same claim. A failure
  before the attempt marker can recover the exact signal and price while it is
  still eligible. Once an attempt is marked, neither SDK nor process retry may
  issue another publish: a missing success checkpoint is reported as
  `ambiguous`, because the broker may already have accepted the first request.
- the real local pipeline test covers duplicate publisher delivery through
  Aggregator and Gateway, exactly one OMS Paper BUY fill plus duplicate skip,
  a fill-anchored stop, deterministic scheduled exit, partial then full exit,
  position deletion, and zero messages on `live-orders`.

Still required before any target publication can be reconsidered:

- replace or formally resolve the cited same-symbol random evidence: random
  rows used an 8% stop while selected rows used the frozen 10% stop. Do not
  rerun/inspect the locked OOS window without ADR-required approval;
- deployment of `contracts/sql/018_oms_paper_apply_fill_rpc.sql`,
  `contracts/sql/019_event_paper_claim_cas_rpc.sql`,
  `contracts/sql/020_event_paper_stage_dispatch_journal.sql`, and
  `contracts/sql/021_oms_paper_position_generation_lineage.sql`, and
  `contracts/sql/022_positions_scheduled_exit_time.sql` to the target
  Supabase project, with `scripts/health-check.py --check supabase` reporting
  both Paper OMS RPCs, `event_paper_cas_strategy_reasoning`, and
  `event_paper_stage_dispatch` as `OK`;
- verification that the managed `event-paper-raw-books` subscription exists,
  is filtered to book messages, and is owned only by one designated one-shot
  coordinator host. Never run different occurrences concurrently from separate
  hosts: they share one seek/ack cursor. The CLI lock serializes invocations in
  the coordinator filesystem namespace but is intentionally not a distributed
  lease; and
- a pre-open operational run that completes due swing exits before this
  publisher's preflight. The publisher fails closed rather than entering while
  a due exit remains.

For identity, the artifact `strategy_key` is the frozen selection definition,
the published signal `strategy_key` is the separate stress execution profile,
and `candidate_id` is the concrete cluster/observation occurrence. Do not merge
these roles or repeated candidates/evidence profiles would collide.

### Local safety verification

The canonical local test starts from a schema-applied Supabase stack and seeded
Pub/Sub emulator:

```bash
cd /home/hiroyuki/workspaces/roboinvest
docker compose -f infra/docker-compose.dev.yml up -d pubsub pubsub-init
test "$(docker wait trade-ai-pubsub-init)" = "0"
supabase start --workdir infra
eval "$(supabase status -o env --workdir infra)"
PUBSUB_PROJECT_ID=trade-ai-dev \
PUBSUB_EMULATOR_HOST=127.0.0.1:8085 \
SUPABASE_URL="$API_URL" \
SUPABASE_SECRET_KEY="$SERVICE_ROLE_KEY" \
  uv run pytest -q \
    services/strategy-rule/tests/integration/test_event_paper_pipeline_e2e.py
```

`--no-seek` exists only for emulator tests. It is rejected when
`PUBSUB_EMULATOR_HOST` is absent, and a non-empty emulator host is rejected
unless that explicit test flag is present. This stress path additionally
requires a loopback `PUBSUB_EMULATOR_HOST`, loopback `SUPABASE_URL`, and
`PUBSUB_PROJECT_ID=trade-ai-dev` or `local-dev`; it rejects remote emulators,
cloud Supabase, and production project IDs before client construction. Its
Supabase client ignores ambient proxy variables so service-role headers cannot
leave the loopback path through `HTTP_PROXY`; emulator gRPC channels likewise
disable both the name-based and address-based HTTP proxy mappers. Target
commands clear the emulator variable so an inherited shell or root `.env`
cannot divert publication. The current binary then rejects managed Pub/Sub
altogether until execution-contract alignment is implemented.

### Future target command shape (currently prohibited)

Do not run this for frozen-v1 observation. There is currently no authorized
target invocation: execution-timing and matched-random blockers remain even if
the migration, subscription, and pre-open checks are green. After those
research/execution gates receive an explicit future decision, first capture an
all-`OK` target health result:

The current CLI also rejects managed Pub/Sub before constructing network
clients. Enabling a future target command requires a reviewed code change after
the frozen execution contract is implemented; changing env flags is not enough.

```bash
set -a && . infra/.op.service-account.env && set +a
op run --env-file infra/env.production -- \
  uv run python scripts/health-check.py --check supabase --timeout 30
```

The production pre-open check above must also report `sub:event-paper-raw-books`
and its book filter as `OK`. The guarded command shape is retained below for
review, not as current authorization:

```bash
SIGNAL_DATE=YYYY-MM-DD
TARGET_DATE=YYYY-MM-DD
OCCURRENCE_ID='cluster-id:observation-id'
set -a && . infra/.op.service-account.env && set +a
install -d -m 700 /dev/shm/roboinvest
GCP_CREDENTIALS_HOST_PATH="$(mktemp /dev/shm/roboinvest/gcp-pubsub-sa.XXXXXX)"
chmod 600 "$GCP_CREDENTIALS_HOST_PATH"
trap 'rm -f -- "$GCP_CREDENTIALS_HOST_PATH"' EXIT
if ! op read op://roboinvest/production/GOOGLE_APPLICATION_CREDENTIALS_JSON \
  > "$GCP_CREDENTIALS_HOST_PATH"; then
  echo "failed to materialize Pub/Sub credentials" >&2
  exit 1
fi
uv run python -c \
  'import json, pathlib, sys; p=json.loads(pathlib.Path(sys.argv[1]).read_text()); assert p.get("type") == "service_account" and p.get("project_id") and p.get("private_key")' \
  "$GCP_CREDENTIALS_HOST_PATH" || exit 1
op run --env-file infra/env.production -- \
  env \
  GOOGLE_APPLICATION_CREDENTIALS="$GCP_CREDENTIALS_HOST_PATH" \
  PUBSUB_EMULATOR_HOST= \
  EVENT_CLUSTER_PAPER_PUBLISH_ENABLED=true \
  uv run python -m strategy_rule event-paper-publish \
    --candidates-json "out/event-paper-observation/candidates-${SIGNAL_DATE}.json" \
    --target-date "$TARGET_DATE" \
    --execution-candidate-id "$OCCURRENCE_ID" \
    --output-json \
      "out/event-paper-observation/publish-receipt-${TARGET_DATE}-${OCCURRENCE_ID}.json" \
    --publish-paper
```

The receipt is written atomically without replacing an existing path, remains
separate from the causal candidate artifact, and records the artifact SHA-256,
occurrence identity, selected ask/book time, durable attempt, and confirmed
Pub/Sub metadata when available. It also records
`execution_profile=opening_transport_stress_v1` and
`comparable_to_registered_backtest=false`; its trades/PnL do not satisfy v1
paper/live gates. An existing output path and a concurrent
invocation in the coordinator's filesystem namespace are both rejected before
network I/O. Exactly one
occurrence is allowed per invocation;
a multi-candidate artifact without `--execution-candidate-id` is rejected
before Supabase or Pub/Sub access. Run separate invocations with unique receipt
paths for separate occurrences, sequentially on the designated coordinator.
If a prior external attempt has no success checkpoint, the command still writes
its no-clobber receipt but logs `ambiguous` and exits `3`; automation must treat
that as unresolved, never as publish success or permission to resend.

## Observation Report

While publishing is blocked, generate a candidate-only report:

```bash
cd /home/hiroyuki/workspaces/roboinvest
SIGNAL_DATE=YYYY-MM-DD
set -a && . infra/.op.service-account.env && set +a
op run --env-file infra/env.production -- \
  uv run python scripts/report-event-paper-observation.py \
    --candidates-json "out/event-paper-observation/candidates-${SIGNAL_DATE}.json" \
    --output-json out/event-paper-observation/observation-report.json \
    --output-csv out/event-paper-observation/observation-report.csv \
    --skip-supabase
```

No new event-cluster downstream rows should exist while publication is blocked.

After an authorized publication, reconcile the separate receipt rather than
looking for `published` rows inside the causal artifact:

```bash
SIGNAL_DATE=YYYY-MM-DD
TARGET_DATE=YYYY-MM-DD
OCCURRENCE_ID='cluster-id:observation-id'
set -a && . infra/.op.service-account.env && set +a
op run --env-file infra/env.production -- \
  uv run python scripts/report-event-paper-observation.py \
    --candidates-json "out/event-paper-observation/candidates-${SIGNAL_DATE}.json" \
    --publish-receipt-json \
      "out/event-paper-observation/publish-receipt-${TARGET_DATE}-${OCCURRENCE_ID}.json" \
    --output-json \
      "out/event-paper-observation/observation-report-${TARGET_DATE}-${OCCURRENCE_ID}.json" \
    --output-csv \
      "out/event-paper-observation/observation-report-${TARGET_DATE}-${OCCURRENCE_ID}.csv"
```

The reporter verifies the artifact digest, target date, occurrence coverage,
frozen selection/stress execution identities, topic, and deterministic signal
IDs. A BUY is recognized only through `receipt signal_id -> aggregator
strategy_signal_id_a -> paper unified_signal_id`; a same-symbol unrelated BUY
or later position is not event evidence. Scheduled/stop SELL rows are attributed
only by the linked BUY's persisted `position_generation_id`, so every partial
and final exit in that exact generation is reported. A non-origin event BUY or
any later BUY added to that generation, or legacy null lineage is marked
unverifiable rather than inferred from time.

The report sets `execution_profile=opening_transport_stress_v1` and
`comparable_to_registered_backtest=false`. Do not aggregate its PnL/trade count
into frozen-v1 paper-success or live-promotion evidence.

Key statuses:

- `dry_run_only`: candidate was never published.
- `not_selected_in_receipt`: this occurrence was not part of the supplied
  per-occurrence receipt; inspect its own receipt/report before concluding it
  was not published.
- `published_unqueried`: receipt exists but Supabase was deliberately skipped
  or unavailable, so downstream state was not assessed.
- `publication_ambiguous`: a durable external attempt exists without a success
  checkpoint and no downstream Aggregator row has proved delivery. Do not
  republish; investigate Pub/Sub/strategy lineage and retain this as unresolved
  observation evidence.
- `missing_strategy_log`: publication receipt exists but the required source log
  is missing.
- `missing_aggregator_log`: Aggregator did not record a unified signal.
- `missing_buy_fill`: Gateway/OMS Paper has not produced a BUY fill.
- `open_position`: BUY filled and a paper position remains open.
- `closed_or_exited`: BUY filled and a SELL row is visible.
- `no_open_position_no_sell`: a linked BUY exists, but neither its exact
  position generation nor an attributable exit is visible; investigate rather
  than treating it as a confirmed close.
- `unverifiable_generation_lineage`: the event BUY was a later add-on to an
  existing generation, its generation later received another BUY, or it predates
  persisted lineage; do not attribute exits or position state.

`position_unrealized_pnl` is open-position PnL only. Do not count it as
confirmed realized paper execution evidence.
