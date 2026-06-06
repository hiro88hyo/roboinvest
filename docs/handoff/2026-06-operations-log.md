# June 2026 Operations Log

## 2026-06-06 MAX_HOLD_MINUTES follow-up note

PR #77 (`Add live risk guards for hold time and closeout`) merged `MAX_HOLD_MINUTES=45`
as an initial live/day safety guard. Treat this as a temporary tail-risk control, not as
the final profit-maximizing exit model.

Rationale:

- Weekly review for `2026-06-01` to `2026-06-05` showed short holds were profitable
  while long holds carried most of the downside:
  - `<=15m`: `+30,960円`
  - `15-45m`: `-7,020円`
  - `45-90m`: `-4,710円`
  - `>90m`: `-25,250円`
- The `6072` carry/late-exit loss (`-29,450円`) was a tail event that should be
  prevented by guardrails before trying to optimize signal quality.
- However, fixed 45-minute forced exits can also cut off valid winners such as longer
  momentum moves.

Future consideration:

- Observe several sessions of `exit_reason=max_hold_minutes` and compare realized PnL
  against hypothetical `30m / 45m / 60m` thresholds.
- Consider replacing the hard 45-minute market exit with a conditional rule:
  - force exit after 45 minutes only when unrealized PnL is negative or deteriorating,
  - switch profitable positions to tighter trailing stop after 45 minutes,
  - keep price-based stop-loss above time-based rules,
  - keep `14:50` day closeout as the final invariant.
- Keep swing positions excluded from `MAX_HOLD_MINUTES`.

## 2026-06-02 pre-open check note

Pre-open check around `08:06 JST`:

- Ran `op run --env-file infra/env.production -- uv run python scripts/production-preopen-check.py --timeout 30`.
- Result was `OK 59 / WARN 0 / NG 1 / SKIP 0` because `feeder kabu logs` reported `kabu auth (HTTP 401)`.
- Feeder had actually recovered: after the initial `PUT /kabusapi/register` returned `401`, it invalidated the token, fetched a new token with `POST /kabusapi/token` `200`, ran `PUT /kabusapi/unregister/all` `200`, then registered the 2026-06-02 watchlist with `PUT /kabusapi/register` `200`.
- 2026-06-02 watchlist was populated with 30 symbols, `daily_ohlcv` latest date was `2026-06-01`, live positions were empty, and OMS Live allowed symbols matched the watchlist.
- Follow-up after close: update `scripts/production-preopen-check.py` so `kabusapi/register "HTTP/1.1 200 OK"` is treated as a successful feeder/kabu recovery signal. The current script only treats `token 200` and `unregister/all 200` as OK, so a recovered 401 can remain a false-positive NG when `register 200` is the latest meaningful state.

## 2026-06-01 production redeploy and pre-open recheck

Production reflection follow-up:

- Latest `main` was already at `origin/main` and pointed to `d95c7df` (`Merge pull request #74 from hiro88hyo/codex/reduce-observability-noise`).
- Ran GitHub Actions `Deploy Production` with `ref=main` and `dry_run=false`.
- First deploy run `26754348176` failed before restart because the LAN host root filesystem was full and the self-hosted runner could not write `_diag/Worker_20260601-121702-utc.log`.
- `docker system df` showed Docker build cache was the immediate pressure point (`Build Cache 28.78GB`, `22.58GB` reclaimable). Ran a one-time `docker builder prune -f`, freeing `22.58GB`; filesystem recovered to about `80%` used.
- Re-ran production deploy as GitHub Actions run `26754480684`; it succeeded.
- After deploy, production compose showed all main services recreated and Up: `feeder`, `feature-engine`, `strategy-rule`, `strategy-ai`, `aggregator`, `gateway`, `oms-paper`, `oms-live`. `otel-collector` stayed Up from the previous rollout.
- Supabase lightweight health check after deploy was clean: `OK 9 / NG 0`.

Pre-open recheck after redeploy:

- Ran `op run --env-file infra/env.production -- uv run python scripts/production-preopen-check.py --timeout 30` without `--kabu-offline`.
- Result: `OK 60 / WARN 0 / NG 0 / SKIP 0`.
- Confirmed key production settings: `TRADE_MODE=live`, `OMS_LIVE_DRY_RUN=false`, `AI_MAX_OUTPUT_TOKENS=2048`, `LIVE_DAY_NEW_BUY_START_TIME=09:15`, Aggregator thresholds `RULE_ONLY=0.5`, `AI_ONLY=0.5`, `CONSENSUS=0.3`.
- Confirmed Supabase state: `is_trading_allowed=true`, `trade_mode=live`, `trading_style=day`, `daily_pnl=4470.0`, `live positions` empty.
- Confirmed managed Pub/Sub topics/subscriptions and smoke publish/pull/ack.
- Confirmed feeder kabu logs had no recent kabu error.

Follow-up decision status:

- Disk management: the host machine has other Docker uses, so do not add `docker builder prune` as a roboinvest project routine. Treat the prune above as one-time emergency cleanup only. Future disk alerting/cleanup policy should consider the whole host, not this project alone.
- Handoff logging: record this deploy, pre-open result, and the follow-up decisions here.
- Cloud Logging market-event verification: defer to the next trading day. Check `signal_rejected`, `order_published`, OMS Live fill, and closeout searchability after fresh market data.
- Monitoring / notification work: defer until notification requirements are clearer.

## 2026-06-01 live close review

End-of-day system behavior summary:

- Production pre-open check was clean before market open: `OK 60 / WARN 0 / NG 0`.
- Market data flowed from `feeder` to `feature-engine`, then through `strategy-rule`, `strategy-ai`, `aggregator`, `gateway`, and `oms-live`.
- `09:00-09:15 JST` live/day BUY guard worked: Gateway rejected new BUY signals with `opening_live_buy`.
- AI calls did not explode. Gemini calls from market open to around `10:04 JST` were 6 total, with `AI_MIN_INTERVAL_SECONDS=300`.
- Live trading completed with `34` live trade rows and final realized daily PnL `+4,470円`.
- `14:50 JST` day closeout worked. OMS Live closeout precheck matched `['6635', '6969', '4100']`, submitted SELL orders, inserted closeout trades, deleted live positions, and logged `closeout: postcheck clear (no live positions remain)`.
- Post-close state: `positions(live)` empty, `is_trading_allowed=True`, all production compose services Up.

Log / observability topics to discuss next session:

- Cloud Logging query quality for production JSON logs:
  - Confirm `jsonPayload.event="signal_rejected"` and `jsonPayload.event="order_published"` are easy to filter.
  - Confirm OMS Live fills and closeout events are easy to filter, especially `live order filled`, `closeout: precheck`, and `closeout: postcheck clear`.
  - Decide whether some OMS Live messages should be promoted from generic `event="log"` to structured event names such as `order_filled`, `closeout_started`, `closeout_completed`, and `broker_order_failed`.
- Alert candidates:
  - `oms-live` broker error `Code 21: 可能額が不足しております` occurred at `2026-06-01 14:16:55 JST` on a duplicate/extra `4100` BUY attempt. OMS Live logged ERROR and continued, but Gateway capital/exposure estimate and actual kabu buying power differed.
  - Closeout failure or residual live positions after `14:50 JST` should become high-priority alert conditions.
  - Repeated `below_min_lot` / `same_day_reentry_after_sell` bursts are useful as diagnostic logs, but probably not alerts.
- Monitoring metrics candidates:
  - Daily realized PnL, open live positions count, live order count, broker order failures, closeout success flag, and AI call count.
  - Supabase should remain source of truth for trading metrics; Cloud Monitoring custom metrics via a future `metrics-exporter` remains the likely path.
