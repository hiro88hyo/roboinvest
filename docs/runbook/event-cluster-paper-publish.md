# Event Cluster Paper Publish

Purpose: publish paper-only `StrategySignal` messages for
`event_cluster_earnings_dividend_value_guard_fixed20_stop_v1_research`.
This is not a live procedure and does not change the live gate.

## Preconditions

- Phase 0 paper observation exception is approved.
- `system_status.trade_mode = paper`.
- Gateway, Aggregator, and OMS Paper are running against the same Pub/Sub and
  Supabase environment.
- Due swing exits have already been processed with
  `oms-paper opening-swing-exits` for the session.
- `EVENT_CLUSTER_PAPER_PUBLISH_ENABLED=true` is set only for the publish
  command. Keep it unset for dry-run detection.
- Do not set or use any `live-orders` topic for this run.

## Dry Run

```bash
cd /home/hiroyuki/workspaces/roboinvest
uv run python scripts/detect-event-cluster-paper-candidates.py \
  --financial-summary-jsonl out/event-research-real-pit/financial-summaries.jsonl \
  --ohlcv data/reference/daily_ohlcv_20210625_20260624_bydate.csv \
  --output-json out/event-paper-observation/candidates.json \
  --output-csv out/event-paper-observation/candidates.csv \
  --signal-date YYYY-MM-DD
```

Confirm `publish_enabled=false` and inspect candidates/exclusions before any
publish run.

## Watchlist Capture

Before the entry session, add event symbols to the watchlist so Feeder registers
them and Feature Engine can capture 1-minute data:

```bash
cd /home/hiroyuki/workspaces/roboinvest
set -a && . infra/.op.service-account.env && set +a
op run --env-file infra/env.production -- \
  uv run python scripts/upsert-event-candidates-watchlist.py \
    --candidates-json out/event-paper-observation/candidates.json \
    --output-json out/event-paper-observation/event-watchlist-upsert.json
```

Run this before Feeder's pre-open watchlist poll. The script inserts only
missing event symbols and does not overwrite Universe Scanner rows.

## Paper Publish

The publish command still runs the same detector first. It only publishes if the
environment flag is enabled and Supabase preflight reads `trade_mode=paper`.

```bash
cd /home/hiroyuki/workspaces/roboinvest
set -a && . infra/.op.service-account.env && set +a
op run --env-file infra/env.production -- \
  env EVENT_CLUSTER_PAPER_PUBLISH_ENABLED=true \
  uv run python scripts/detect-event-cluster-paper-candidates.py \
    --financial-summary-jsonl out/event-research-real-pit/financial-summaries.jsonl \
    --ohlcv data/reference/daily_ohlcv_20210625_20260624_bydate.csv \
    --output-json out/event-paper-observation/published-candidates.json \
    --output-csv out/event-paper-observation/published-candidates.csv \
    --signal-date YYYY-MM-DD \
    --publish-paper
```

Default topic is `strategy-signals-a`. Override with `--pubsub-topic-signals`
only for an isolated test project.

Each published signal is:

- `source=RULE`
- `action=BUY`
- `holding_type=swing`
- `max_hold_days=20`
- `stop_loss_price=entry_price_assumption * (1 + CAT_STOP_PCT)`
- `CAT_STOP_PCT=-0.10`

The command upserts the corresponding `strategy_logs` row before Pub/Sub
publish. This is required because `aggregator_logs.strategy_signal_id_a`
references `strategy_logs.signal_id`.

## Fail-Closed Cases

No signal is published when:

- `--publish-paper` is omitted.
- `EVENT_CLUSTER_PAPER_PUBLISH_ENABLED` is unset or not true.
- `SUPABASE_URL`, `SUPABASE_SECRET_KEY`, or `PUBSUB_PROJECT_ID` is missing.
- `system_status.trade_mode != paper`.
- Pub/Sub publish fails.
- `strategy_logs` upsert fails.

## Verification

The output JSON must show:

```json
{
  "mode": "paper_publish",
  "publish_enabled": true,
  "summary": {
    "published_count": 1
  }
}
```

Then confirm downstream flow:

1. `strategy_logs` contains the emitted RULE signal.
2. Aggregator receives from `strategy-signals-a` and publishes `trade-signals`.
3. Gateway logs `trade_mode=paper` and publishes to `paper-orders`.
4. OMS Paper writes `trades_paper` and `positions`.
5. New paper position has `holding_type='swing'`, `max_hold_days=20`, and
   `stop_loss_price`.

If Gateway rejects the signal, record the reject reason in
`out/event-paper-observation/` and do not rerun with modified strategy
parameters.

## Observation Report

After publish and downstream processing, reconcile the detector output with
Supabase:

```bash
cd /home/hiroyuki/workspaces/roboinvest
set -a && . infra/.op.service-account.env && set +a
op run --env-file infra/env.production -- \
  uv run python scripts/report-event-paper-observation.py \
    --candidates-json out/event-paper-observation/published-candidates.json \
    --output-json out/event-paper-observation/observation-report.json \
    --output-csv out/event-paper-observation/observation-report.csv
```

Use `--skip-supabase` only to generate a candidate-only report. A complete
paper observation report should include `strategy_logs`, `aggregator_logs`,
`trades_paper`, and `positions`.

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
