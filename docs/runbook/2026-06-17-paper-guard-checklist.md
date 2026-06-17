# 2026-06-17 Paper Guard Checklist

当日メモ用。目的は paper guard の挙動を、live 昇格判断に使える形で残すこと。
詳細な背景は `2026-06-17-paper-guard-observation.md` を参照。

## Morning Checklist

開始前に記入:

- JST date:
- Operator:
- Branch / commit: `main` / `1b541a6` or later
- Production stack rebuilt from main: yes

Checks:

- [ ] kabu Station is running.
- [ ] Windows Caddy reverse proxy is reachable.
- [ ] `infra/env.production` has `TRADE_MODE=paper`.
- [ ] `infra/env.production` has `OMS_LIVE_DRY_RUN=true`.
- [ ] `infra/env.production` has `OMS_LIVE_STOP_MONITOR_ENABLED=false`.
- [ ] `infra/env.production` has `PAPER_DAY_STOP_MONITOR_ENABLED=true`.
- [ ] `infra/env.production` has `MARKET_REGIME_PAPER_GUARD_ENABLED=true`.
- [ ] `infra/env.production` has `MARKET_REGIME_GATEWAY_GUARD_ENABLED=false`.
- [ ] `infra/env.production` has `SOFT_LOSS_THROTTLE_GUARD_ENABLED=true`.
- [ ] `infra/env.production` has `EXECUTION_GATE_LOG_ONLY_ENABLED=true`.
- [ ] `infra/env.production` has `EXECUTION_GATE_GUARD_ENABLED=true`.
- [ ] `system_status.is_trading_allowed=true`.
- [ ] `system_status.trade_mode=paper`.
- [ ] live positions are empty.
- [ ] `oms-paper` uses `PUBSUB_SUBSCRIPTION_RAW_MARKET_DATA=oms-paper-raw-books`.
- [ ] `oms-live` uses `PUBSUB_SUBSCRIPTION_RAW_MARKET_DATA=oms-live-raw-books`.
- [ ] managed Pub/Sub `oms-paper-raw-books` filter is `attributes.kind = "book"`.
- [ ] managed Pub/Sub `oms-live-raw-books` filter is `attributes.kind = "book"`.
- [ ] watchlist exists for the JST trading date.
- [ ] daily OHLCV exists for watchlist symbols.
- [ ] market regime row exists for the JST trading date.
- [ ] feeder has no recent kabu auth / websocket errors.

Preopen command:

```bash
set -a && . infra/.op.service-account.env && set +a
op run --env-file infra/env.production -- \
  uv run python scripts/production-preopen-check.py \
    --timeout 30 \
    --expected-trade-mode paper \
    --refresh-kabu-token
```

Preopen result:

- OK:
- WARN:
- NG:
- SKIP:
- Pub/Sub smoke:
- feeder/kabu:
- temporary GCP credential used: yes/no

Ready decision:

- [ ] `NG 0`
- [ ] `TRADE_MODE=paper`
- [ ] `OMS_LIVE_DRY_RUN=true`
- [ ] managed Pub/Sub smoke OK
- [ ] feeder/kabu checks OK
- [ ] `MARKET_REGIME_PAPER_GUARD_ENABLED=true` confirmed in gateway container
- [ ] Paper GO / NO-GO recorded

Decision:

- Paper GO:
- Reason if NO-GO:

## Intraday Checklist

9:00-9:15 JST:

- [ ] feeder receives market data.
- [ ] feature-engine publishes `processed-features`.
- [ ] strategy-rule publishes `strategy-signals-a`.
- [ ] strategy-ai trigger path is not erroring.
- [ ] aggregator publishes `trade-signals` or logs expected skips.
- [ ] gateway rejects opening BUY as expected until `LIVE_DAY_NEW_BUY_START_TIME=09:15`.
- [ ] no live order is published.

After 9:15 JST:

- [ ] gateway publishes only to `paper-orders`.
- [ ] OMS Paper consumes `paper-orders`.
- [ ] `trades_paper` records fills if orders occur.
- [ ] `positions(paper)` updates if fills occur.
- [ ] Paper day stop-loss exits emit `day_stop_exit` if stop is hit.
- [ ] Paper day trailing updates emit `day_stop_trail` if trail is raised.
- [ ] `live_stop_exit` / `live_stop_trail` are absent while live monitor is disabled.
- [ ] Execution gate rejects poor BUYs when `EXECUTION_GATE_GUARD_ENABLED=true`.
- [ ] `market_regime_risk_off` rejects only paper BUY.
- [ ] SELL signals are not blocked by market regime guard.
- [ ] closeout / exit orders are not blocked by execution gate.

Watch log events:

- [ ] `market_regime_would_reject`
- [ ] `signal_rejected reason=market_regime_risk_off trade_mode=paper`
- [ ] `signal_rejected reason=execution_*` if execution gate blocks poor BUYs
- [ ] `execution_gate_would_reject` only if execution gate guard is disabled for an experiment
- [ ] `order_published`
- [ ] `paper_order_filled` or OMS Paper fill logs
- [ ] `day_stop_exit`
- [ ] `day_stop_trail`
- [ ] no `live_stop_exit` unless live monitor was explicitly enabled
- [ ] no `ERROR`
- [ ] no `CRITICAL`
- [ ] no `Traceback`

Intraday notes:

```text
09:00:

09:15:

10:30:

11:30:

12:30:

14:30:

14:50:

15:30:
```

## Observation Memo

Market regime:

- regime:
- confidence:
- buy_enabled:
- position_size_multiplier:
- rationale:

Signal / order counts:

- processed feature count:
- rule signal count:
- AI trigger count:
- AI signal count:
- aggregator unified signal count:
- gateway approved count:
- gateway rejected count:
- paper order count:
- paper fill count:
- paper no-fill count:
- partial fill count:
- day stop exit count:
- day stop trail count:

Reject breakdown:

| reason | count | expected? | note |
| --- | ---: | --- | --- |
| `market_regime_risk_off` |  |  |  |
| `execution_spread_too_wide` |  | guard reject |  |
| `execution_spread_ticks_too_wide` |  | guard reject |  |
| `execution_insufficient_ask_depth` |  | guard reject |  |
| `opening_live_buy` / opening BUY guard |  |  |  |
| `late_live_buy` / late BUY guard |  |  |  |
| `market_closed` |  |  |  |
| `same_day_reentry_after_sell` |  |  |  |
| `missing_entry_price` |  | no |  |
| `no_quantity` |  | no |  |

Execution quality:

- average_fill_ratio:
- no_fill_count:
- no_fill_rate:
- limit_no_fill_count:
- partial_fill_count:
- average_spread_bps:
- max_spread_bps:
- average_spread_ticks:
- max_spread_ticks:
- average_order_book_imbalance:

PnL / positions:

- realized paper PnL:
- paper stop/closeout SELL rows with `unified_signal_id is null`:
- open paper positions after closeout:
- unrealized paper PnL:
- mark-to-market paper PnL:
- live positions after close:

Guard impact:

| symbol | side | source | rejected reason | would-have-filled? | later PnL estimate | note |
| --- | --- | --- | --- | --- | ---: | --- |
|  |  |  |  |  |  |  |

## Post-Close Checklist

- [ ] 14:50 closeout ran.
- [ ] DAY paper positions are closed or explicitly explained.
- [ ] live positions remain empty.
- [ ] `trades_paper` rows exported or counted.
- [ ] paper archives exported.
- [ ] OMS Paper no-fills exported or counted.
- [ ] `backtest_report.json` generated or archived.
- [ ] `market_regime_risk_off` rejects reviewed.
- [ ] execution gate rejects / would-reject events reviewed.
- [ ] Missed-profit versus avoided-loss notes written.
- [ ] Decision recorded: keep paper guard / adjust threshold / disable / continue observing.

Archive commands:

```bash
bash scripts/export-paper-archives.sh \
  --date 2026-06-17 \
  --output-dir out/paper-archive-2026-06-17
```

Replay command, if feature archive is available:

```bash
bash scripts/run-rule-only-decision-replay.sh \
  --date 2026-06-17 \
  --features-dir out/paper-archive-2026-06-17/features
```

Final decision:

- Continue paper observation:
- Change thresholds:
- Promote any live guard:
- Blockers:
- Next action:

Do not promote live guard from a single session. Use this memo as the first
paper observation sample.
