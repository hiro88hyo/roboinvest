# 2026-07-09 Swing Paper Plan

Purpose: run a swing paper observation day. Do not treat day
`relative_momentum` PnL as the result of this objective.

## Definition of Done

- Production preopen check passes in `paper` mode.
- `strategy-rule` is not no-op: `STRATEGIES_ENABLED=relative_momentum`.
- The event-cluster swing candidate detector has been run for the latest
  available signal date.
- `production-preopen-check.py` has validated the candidate artifact with
  `--swing-candidates-json`.
- If candidate count is zero, report "no swing entries today" explicitly and do
  not substitute day-strategy results.
- If candidate count is positive, publish only through
  `EVENT_CLUSTER_PAPER_PUBLISH_ENABLED=true` and verify downstream
  `strategy_logs -> aggregator_logs -> trades_paper -> positions`.
- A filled swing position must have `holding_type=swing`, `max_hold_days=20`,
  and a scheduled/stop exit path.

## Morning Sequence

Use JST date `2026-07-09` for readiness checks. Use signal date `2026-07-08`
unless the J-Quants export proves that date has no available financial-summary
data; if so, state that fact before falling back.

```bash
cd /home/hiroyuki/workspaces/roboinvest
set -a && . infra/.op.service-account.env && set +a

SIGNAL_DATE=2026-07-08
op run --env-file infra/env.production -- \
  uv run python scripts/export-jquants-financial-summaries-jsonl.py \
    --start-date "$SIGNAL_DATE" \
    --end-date "$SIGNAL_DATE" \
    --output out/event-research/financial-summaries-20210628-20260624-clean.jsonl \
    --resume \
    --log-every-dates 1 \
    --concurrency 1 \
    --sleep-seconds 0.2

op run --env-file infra/env.production -- \
  uv run python scripts/export-jquants-daily-ohlcv-csv.py \
    --start-date "$SIGNAL_DATE" \
    --end-date "$SIGNAL_DATE" \
    --output data/reference/daily_ohlcv_20210625_20260624_bydate.csv \
    --resume \
    --concurrency 1 \
    --sleep-seconds 0.2

uv run python scripts/detect-event-cluster-paper-candidates.py \
  --financial-summary-jsonl out/event-research/financial-summaries-20210628-20260624-clean.jsonl \
  --ohlcv data/reference/daily_ohlcv_20210625_20260624_bydate.csv \
  --output-json "out/event-paper-observation/candidates-${SIGNAL_DATE}.json" \
  --output-csv "out/event-paper-observation/candidates-${SIGNAL_DATE}.csv" \
  --signal-date "$SIGNAL_DATE"

op run --env-file infra/env.production -- \
  uv run python scripts/production-preopen-check.py \
    --timeout 30 \
    --expected-trade-mode paper \
    --target-date 2026-07-09 \
    --gcp-credentials /tmp/roboinvest-gcp-pubsub-sa.json \
    --swing-candidates-json "out/event-paper-observation/candidates-${SIGNAL_DATE}.json"
```

If `candidate_count > 0`, add event symbols to watchlist before market data
capture and then publish paper signals after due swing exits have run:

```bash
op run --env-file infra/env.production -- \
  uv run python scripts/upsert-event-candidates-watchlist.py \
    --candidates-json "out/event-paper-observation/candidates-${SIGNAL_DATE}.json" \
    --output-json out/event-paper-observation/event-watchlist-upsert-2026-07-09.json

op run --env-file infra/env.production -- \
  uv run python -m oms_paper opening-swing-exits --book-warmup-batches 3

op run --env-file infra/env.production -- \
  env EVENT_CLUSTER_PAPER_PUBLISH_ENABLED=true \
  uv run python scripts/detect-event-cluster-paper-candidates.py \
    --financial-summary-jsonl out/event-research/financial-summaries-20210628-20260624-clean.jsonl \
    --ohlcv data/reference/daily_ohlcv_20210625_20260624_bydate.csv \
    --output-json out/event-paper-observation/published-candidates-2026-07-09.json \
    --output-csv out/event-paper-observation/published-candidates-2026-07-09.csv \
    --signal-date "$SIGNAL_DATE" \
    --publish-paper
```

After publish:

```bash
op run --env-file infra/env.production -- \
  uv run python scripts/report-event-paper-observation.py \
    --candidates-json out/event-paper-observation/published-candidates-2026-07-09.json \
    --output-json out/event-paper-observation/observation-report-2026-07-09.json \
    --output-csv out/event-paper-observation/observation-report-2026-07-09.csv
```

## Reporting Rule

Report these as separate lines:

- Swing candidate count and publish count.
- Swing downstream status from `report-event-paper-observation.py`.
- Day `relative_momentum` signals/fills, explicitly labeled as day strategy.

Do not summarize the day as successful swing verification unless the event
cluster path produced and propagated swing paper signals.

## 2026-07-09 Close Summary

- Swing paper objective: not achieved as a live-data swing verification because
  the event-cluster detector produced `candidate_count=0` for signal date
  `2026-07-08`. No event-cluster swing paper signals were published.
- Day `relative_momentum` paper result, separate from the swing objective:
  4 paper trades, 0 open paper positions, realized PnL `-1300`.
  - `3415`: BUY 100 @ 412, SELL 100 @ 415, `+300`.
  - `5246`: BUY 100 @ 678, SELL 100 @ 662, `-1600`.
  - `4894`: CONSENSUS BUY order published but no fill
    (`limit_not_crossed`).
- No live trades and no live/paper positions remained open at close.
- No gateway `signal_rejected`, `ERROR`, `CRITICAL`, or traceback was observed
  during the close check.
- Observed OMS Paper no-fill reasons:
  - `limit_not_crossed`: 4894 BUY did not cross the limit.
  - `stale_book`: 3415 close attempts waited for a fresher book, then filled.
  - `no_position_for_sell`: 5246 duplicate SELL attempts after day stop had
    already closed the position. This was safe but noisy.
- Code changes made after close:
  - Gateway normal paper BUY log event renamed from
    `opening_swing_exit_sequence` to `paper_buy_order_sequence`.
  - OMS Paper position-update no-fill logs now use the concrete reason from
    `apply_fill` (for example `no_position_for_sell`) instead of the generic
    `apply_fill_error`.
  - Targeted tests passed for both changes.
  - Production `gateway` and `oms-paper` were rebuilt/recreated with
    `GOOGLE_APPLICATION_CREDENTIALS_HOST_PATH=/tmp/roboinvest-gcp-pubsub-sa.json`
    and confirmed `Up`.

## Next Session Checks

- Before compose operations, always pass
  `env GOOGLE_APPLICATION_CREDENTIALS_HOST_PATH=/tmp/roboinvest-gcp-pubsub-sa.json`
  to avoid mounting `/run/secrets/gcp-pubsub-sa.json` as a directory.
- In the next preopen check, verify `gateway` and `oms-paper` are still `Up`
  after the 2026-07-09 close-time recreate.
- If swing candidates are again zero, say so explicitly and do not treat day
  paper PnL as swing validation.
