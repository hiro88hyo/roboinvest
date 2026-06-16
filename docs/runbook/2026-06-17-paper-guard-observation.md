# 2026-06-17 Paper Guard Observation

目的: `codex/execution-safety-gates` の execution safety 変更を production paper で
1 営業日観測し、live guard 昇格前の判断材料を残す。

## Runtime Settings

- `TRADE_MODE=paper`
- `OMS_LIVE_DRY_RUN=true`
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
- managed Pub/Sub smoke OK
- `system_status.trade_mode=paper`
- feeder/kabu checks OK or expected WARN only

## During Market

Watch these events:

- `market_regime_would_reject`
- `signal_rejected` with `reason="market_regime_risk_off"` and `trade_mode="paper"`
- `execution_gate_would_reject`
- OMS Paper no-fill / partial-fill logs
- day closeout / SELL flow

Expected behavior:

- `RISK_OFF` / `CRASH` BUY is rejected only in paper mode.
- live mode remains protected by `OMS_LIVE_DRY_RUN=true`.
- `EXECUTION_GATE_*` is log-only and does not reject.
- SELL / closeout is not blocked by market regime or execution gate.

## Post-Close Checks

Collect:

- paper order archive
- feature/order-book archive
- OMS Paper fills and no-fills
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
