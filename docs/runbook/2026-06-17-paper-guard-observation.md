# 2026-06-17 Paper Guard Observation

目的: `codex/execution-safety-gates` の execution safety 変更を production paper で
1 営業日観測し、live guard 昇格前の判断材料を残す。

当日の記入用チェックリストは
[`2026-06-17-paper-guard-checklist.md`](2026-06-17-paper-guard-checklist.md) を使う。

## Runtime Settings

- `TRADE_MODE=paper`
- `OMS_LIVE_DRY_RUN=true`
- `OMS_LIVE_STOP_MONITOR_ENABLED=false`
- `PAPER_DAY_STOP_MONITOR_ENABLED=true`
- `PUBSUB_SUBSCRIPTION_RAW_MARKET_DATA=oms-paper-raw-books` in `oms-paper`
- `PUBSUB_SUBSCRIPTION_RAW_MARKET_DATA=oms-live-raw-books` in `oms-live`
- `MARKET_REGIME_GATEWAY_LOG_ONLY_ENABLED=true`
- `MARKET_REGIME_GATEWAY_GUARD_ENABLED=false`
- `MARKET_REGIME_PAPER_GUARD_ENABLED=true`
- `EXECUTION_GATE_LOG_ONLY_ENABLED=true`
- `EXECUTION_GATE_GUARD_ENABLED=false`
- `EXECUTION_GATE_MAX_SPREAD_BPS=30`
- `EXECUTION_GATE_MAX_SPREAD_TICKS=2`
- `EXECUTION_GATE_MIN_ASK_DEPTH_MULTIPLIER=3`

## Preopen Checklist

Run from repository root after kabu Station / Windows proxy is available:

```bash
set -a && . infra/.op.service-account.env && set +a
op run --env-file infra/env.production -- \
  uv run python scripts/production-preopen-check.py \
    --timeout 30 \
    --expected-trade-mode paper \
    --refresh-kabu-token
```

If kabu Station is intentionally offline, replace `--refresh-kabu-token` with
`--kabu-offline`.

Ready condition:

- `NG 0`
- `TRADE_MODE=paper`
- `OMS_LIVE_DRY_RUN=true`
- `PAPER_DAY_STOP_MONITOR_ENABLED=true`
- `oms-paper-raw-books` and `oms-live-raw-books` Pub/Sub filters OK
- managed Pub/Sub smoke OK
- `system_status.trade_mode=paper`
- feeder/kabu checks OK or expected WARN only

## During Market

Watch these events:

- `market_regime_would_reject`
- `signal_rejected` with `reason="market_regime_risk_off"` and `trade_mode="paper"`
- `execution_gate_would_reject`
- OMS Paper no-fill / partial-fill logs
- `day_stop_exit`
- `day_stop_trail`
- `live_stop_exit` / `live_stop_trail` should be absent while
  `OMS_LIVE_STOP_MONITOR_ENABLED=false`
- day closeout / SELL flow

Expected behavior:

- `RISK_OFF` / `CRASH` BUY is rejected only in paper mode.
- live mode remains protected by `OMS_LIVE_DRY_RUN=true`.
- `EXECUTION_GATE_*` is log-only and does not reject.
- SELL / closeout is not blocked by market regime or execution gate.
- Paper day positions can exit via `day_stop_exit` without waiting for a
  strategy SELL.
- Paper day trailing stop updates emit `day_stop_trail` and should not create a
  `trades_paper` row.
- Live stop monitor remains disabled until separate HITL approval.

## Quick Queries

One-command Supabase observation summary:

```bash
set -a && . infra/.op.service-account.env && set +a
op run --env-file infra/env.production -- \
  uv run python scripts/report-paper-observation.py --timeout 30
```

For a specific JST date:

```bash
op run --env-file infra/env.production -- \
  uv run python scripts/report-paper-observation.py --date YYYY-MM-DD --timeout 30
```

Use Supabase SQL editor or `psql` with the target JST date converted to UTC.
For 2026-06-18 JST, use `2026-06-17 15:00:00+00` to
`2026-06-18 15:00:00+00`.

Paper stop/closeout SELL rows created outside aggregator signals:

```sql
select symbol, side, quantity, price, signal_source, unified_signal_id, executed_at
from trades_paper
where executed_at >= timestamptz '2026-06-17 15:00:00+00'
  and executed_at <  timestamptz '2026-06-18 15:00:00+00'
  and side = 'SELL'
  and unified_signal_id is null
order by executed_at;
```

Open paper positions:

```sql
select symbol, quantity, entry_price, current_price, unrealized_pnl,
       holding_type, stop_loss_price, target_price, trailing_stop_pct, opened_at
from positions
where trade_type = 'paper'
order by symbol;
```

Paper fill count and gross turnover:

```sql
select side, count(*) as fills, sum(quantity) as shares,
       sum(quantity * price) as notional
from trades_paper
where executed_at >= timestamptz '2026-06-17 15:00:00+00'
  and executed_at <  timestamptz '2026-06-18 15:00:00+00'
group by side
order by side;
```

Cloud Logging filters:

```text
logName:"roboinvest"
jsonPayload.service="oms-paper"
(jsonPayload.event="day_stop_exit" OR jsonPayload.event="day_stop_trail")
```

```text
logName:"roboinvest"
jsonPayload.service="oms-live"
(jsonPayload.event="live_stop_exit" OR jsonPayload.event="live_stop_trail")
```

## Post-Close Checks

Collect:

- paper order archive
- feature/order-book archive
- OMS Paper fills and no-fills
- paper day stop exits/trails
- `backtest_report.json`
- paper PnL and open positions

Evaluate:

- `no_fill_count`
- `no_fill_rate`
- `limit_no_fill_count`
- `average_fill_ratio`
- `partial_fill_count`
- `average_spread_bps`
- `max_spread_bps`
- `average_spread_ticks`
- `max_spread_ticks`
- number of `market_regime_risk_off` paper rejects
- whether rejected BUYs would have helped or hurt PnL

Do not promote live guard until at least several paper sessions have passed and
SELL / closeout behavior is confirmed.
