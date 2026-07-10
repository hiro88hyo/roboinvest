# Event Cluster Paper Publish

Status: **paper publication is blocked as of 2026-07-10**. This runbook currently
covers causal dry-run detection and watchlist capture only for
`event_cluster_earnings_dividend_value_guard_fixed20_stop_v1_research`.

The old publisher used the future T+1 open as `StrategySignal.price` and as the
basis of an absolute stop. `--publish-paper` now fails closed until a fresh,
timestamped observed-price path exists and OMS Paper anchors the 10% stop to the
actual fill. This does not change the candidate parameters or the live gate.

## Preconditions

- Use the latest available TSE signal date.
- Do not pass `--publish-paper`; the option is intentionally blocked.
- Do not interpret a candidate artifact as an executable order or a
  profitability result.

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
even if present in the CSV, is not consulted for candidate features.

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
  uv run python scripts/production-preopen-check.py \
    --timeout 30 \
    --expected-trade-mode paper \
    --target-date "$TARGET_DATE" \
    --gcp-credentials /tmp/roboinvest-gcp-pubsub-sa.json \
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

## Paper Publish Block

There is currently no supported publish command. Passing `--publish-paper`
exits before writing an artifact, inserting `strategy_logs`, or calling Pub/Sub.

Publication may be restored only after a separate implementation carries all
of the following through tests:

- a fresh best ask (or an explicitly approved next-open order semantic) with an
  observed timestamp;
- stale/missing market data rejection;
- a relative 10% stop intent propagated through StrategySignal, Aggregator, and
  OrderRequest;
- OMS Paper setting the absolute stop from the actual fill price;
- `holding_type=swing` surviving through OrderRequest into the stored position.

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

Key statuses:

- `dry_run_only`: candidate was never published.
- `missing_strategy_log`: publish artifact exists but the required FK source log
  is missing.
- `missing_aggregator_log`: Aggregator did not record a unified signal.
- `missing_buy_fill`: Gateway/OMS Paper has not produced a BUY fill.
- `open_position`: BUY filled and a paper position remains open.
- `closed_or_exited`: BUY filled and a SELL row is visible.

`position_unrealized_pnl` is open-position PnL only. Do not count it as
confirmed realized paper execution evidence.
