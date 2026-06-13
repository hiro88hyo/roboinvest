# June 2026 Operations Log

## 2026-06-13 Fable5 feedback status and remaining TODO

Reviewed `docs/handoff/2026-06-fable5-feedback.md` and
`docs/handoff/2026-06-fable5-additional-feedback.md` against the current working tree.

Status by original checklist:

- CHK-01 revenue metrics: done. OMS Paper backtest now reports net PnL, win rate,
  profit factor, max drawdown, Sharpe ratio, expectancy, costs, tax, and slippage.
- CHK-02 backtest PnL: done. `BacktestSummary.realized_pnl` exists and is covered by tests.
- CHK-03 parameter sweep: done. `scripts/parameter-sweep.py` supports the required
  strategy grid and the later 500-business-day focused sweep with walk-forward folds.
- CHK-04 AI strategy silence monitoring: done. `AI_STRATEGY_SILENT` is logged during
  market-hours silence and has tests.
- CHK-05 dynamic capital: done for the Fable5 checklist. Gateway reads live
  capital from the kabu wallet, caches it, and falls back to configured capital
  only when wallet reads fail and no cached value exists. Residual policy work
  remains before increasing live size: explicitly decide whether the fallback
  behavior is acceptable or should become paper/backtest-only.
- CHK-06 kill-switch/risk reservation atomicity: implemented in the working tree,
  and `contracts/sql/016_gateway_risk_reservations.sql` was applied to production
  Supabase by the user. RLS is enabled on `gateway_risk_reservations`. Service-role
  verification passed for `gateway_risk_reservations` `limit=0`,
  `gateway_check_and_reserve_risk` with a no-reserve `paper`/`SELL` payload, and
  `gateway_release_risk_reservation` with a nonexistent order id. A live BUY verification
  reservation with `risk_amount=1` also passed and was immediately released with
  `reason="verify_cleanup"`. The SQL adds
  `gateway_risk_reservations`, `gateway_check_and_reserve_risk`, and
  `gateway_release_risk_reservation`. Gateway reserves worst-case live BUY risk before
  Pub/Sub publish and releases it if publish fails.
- CHK-07 OMS Live partial fill visibility: done. Partial-fill abandoned remainder is
  logged as `partial_fill_abandoned` with `reason="partial_abandoned"` and tested.
- CHK-08 look-ahead removal: done. Universe Scanner OHLCV ingestion/scoring uses
  `previous_business_day(as_of)` and tests verify same-day data is excluded.
- CHK-09 backtest report: done. `docs/reports/backtest-2026-06.md` records the
  core metrics and long-horizon interpretation.
- CHK-10 losing strategy stop: done. `sma_crossover` was removed from default
  strategy enablement after validation PF < 1.0; explicit opt-in remains possible.

Additional feedback status:

- Project kill switch: done in `AGENTS.md` as Codex-facing policy. It pre-registers
  the 2026-09-30 out-of-sample PF/DD condition and forbids silent weakening.
- Codex migration note: done. `AGENTS.md` is the primary agent memo; old `AGENT.md`
  references were corrected where found.
- 1Password vault correction: done. Agent notes explicitly state `op://roboinvest/...`,
  not `op://Trade AI/...`.
- Capital-scale / purpose-function issue: not fully done. The kill switch captures
  when to stop, but there is not yet a separate capital scaling plan or explicit
  "this is research/platform value, not profit-maximization" decision document.

Remaining TODO:

1. Close the residual CHK-05 policy explicitly. Decide whether kabu wallet plus cached fallback is
   sufficient, or whether configured `capital` should become paper/backtest-only.
2. Write a short capital-scale / project-purpose note before increasing live size.
   This should state whether the project is optimizing profit, research data, or
   AI-assisted engineering reference value.
3. After the next paper session, run `scripts/run-paper-postmortem.sh` on real
   `/data/orders` and `/data/books`, then compare execution quality gates before
   considering any live parameter change.

CHK-06 implementation note:

- Risk reservations are conservative: active live BUY risk remains reserved for the
  trading date unless publish fails and Gateway releases it immediately. This prevents
  worst-case concurrent overshoot, but can under-use capital if an order fills cleanly.
- A future enhancement can release or settle reservations from OMS Live terminal order
  state (`filled`, `partial`, `cancelled`, `broker rejected`) once that event path is
  designed end-to-end.
- Verification after this change: `make lint-all` passed, `make test-all` passed with
  Python `1004 passed, 21 skipped` and dashboard `47 passed`.
- Production env compose rendering passed:
  `op run --env-file infra/env.production -- docker compose ... config --quiet`.
- Production pre-open check passed in no-smoke / kabu-offline mode after applying
  `contracts/sql/016_gateway_risk_reservations.sql`:
  `OK 62 / WARN 0 / NG 0 / SKIP 0`. The run used a temporary readable host
  credential at `/tmp/roboinvest-gcp-pubsub-sa.json`, then removed it. The normal
  tmpfs credential path `/dev/shm/roboinvest/gcp-pubsub-sa.json` was root-owned
  (`root:root`, `0600`) at verification time, so host-side checks could not read it
  without sudo; containers were still already running from the existing mount.
- Follow-up hardening: `production-preopen-check.py` now reports an unreadable
  GCP credential path as a clean `NG` instead of surfacing a Python traceback.
  `scripts/deploy-production.sh` can pass `--gcp-credentials` and
  `--no-pubsub-smoke` through to the post-check. Syntax/help verification passed
  for both scripts, and `make lint-all` plus `make test-all` passed afterward
  (`1004 passed, 21 skipped`; dashboard `47 passed`).
- Production deploy completed after PR #90 was merged to `main`.
  Deploy Production run `27467929469` deployed `ffb618d` with
  `dry_run=false`; the workflow concluded `success`. Production compose
  recreated all trading services and showed `strategy-ai`, `aggregator`,
  `gateway`, `feature-engine`, `strategy-rule`, `oms-live`, `oms-paper`, and
  `feeder` all `Up`. Post-check passed with
  `OK 62 / WARN 0 / NG 0 / SKIP 0`; live positions were empty, `trade_mode`
  was `live`, `OMS_LIVE_DRY_RUN=false`, managed Pub/Sub topic/subscription
  checks passed, and feeder kabu logs ended at `unregister/all 200`.

## 2026-06-13 paper backtest reliability follow-up

Backtest confidence work after the long-horizon parameter sweep:

- Kept `ENTRY_VOLUME_RATIO_MIN=2.0` as a paper-only candidate. Do not enable it for live
  until live-like paper archive postmortems show stable fills and execution quality.
- Added paper postmortem archive tooling so approved Gateway orders and feature-engine
  order books can be replayed through OMS Paper after a session.
- `run-paper-archive-backtest.py` now fails fast when the archived orders file exists
  but contains zero nonblank orders, and writes `metadata.json` with order/book/fill/no-fill counts.
- `summarize-paper-backtest.py` can include that metadata so a Markdown summary clearly
  states the replay input size, not just PnL and gate status.
- Gateway order archive partitions are based on the configured trading timezone
  (`day_closeout_timezone`, default `Asia/Tokyo`) so UTC boundary timestamps do not
  land in the wrong trading date.

Verification:

- `make lint-all` passed.
- `make test-all` passed: Python `1002 passed, 21 skipped`; dashboard `47 passed`.
- Postmortem smoke using `/tmp/roboinvest-paper-archive-smoke` passed with
  `orders=1`, `books=1`, `fills=1`, `no_fills=0`, gate `PASS`.
- Local daily OHLCV CSV archive dry-run passed:
  `2,109,772` rows from `2024-05-27` through `2026-06-12`.
- Targeted Gateway archive timezone tests passed after the JST partition fix.

## 2026-06-10 full trading halt due to kabu station login failure

Local kabu station login was unavailable, so kabu API was considered unavailable for the full day.
Decision: stop all trading for `2026-06-10`.

Actions taken around `22:43 UTC`:

- Patched production Supabase `system_status.id=1` to `is_trading_allowed=false`.
- Confirmed pre-halt state was `trade_mode=live`, `trading_style=day`, `daily_pnl=-40,310円`,
  `daily_loss_limit=100,000円`.
- Confirmed `positions` was empty before and after the halt.
- Stopped production compose trading services:
  `feeder`, `feature-engine`, `strategy-rule`, `strategy-ai`, `aggregator`, `gateway`,
  `oms-live`, `oms-paper`.
- Verified production compose then only showed `otel-collector` running.

Resume checklist for the next trading day:

1. Start kabu station / Windows proxy and confirm kabu API login.
2. Start production trading services intentionally; do not rely on restart policy.
3. Run `scripts/production-preopen-check.py --timeout 30 --refresh-kabu-token` without
   `--kabu-offline`.
4. Only after checks pass, set `system_status.is_trading_allowed=true` for the intended mode.

## 2026-06-08 risk-off paper close review and parity fixes

Monday `2026-06-08` was intentionally run in production paper mode because the user expected
a sharp down market. The outcome confirmed that live should not have been used.

End-of-day results checked around `15:04 JST`:

- `system_status.trade_mode=paper`, `is_trading_allowed=true`.
- Live side was clean: `live_trade_count=0`, open live positions `[]`, live realized PnL `0`.
- Paper side:
  - `trades_paper` rows for the JST date: `198`.
  - Recomputed paper realized PnL from fills: `-100,900円`.
  - One paper position remained open: `4092 LONG 100`, entry `5230`, current around `5080`, unrealized around `-15,000円`.
  - Mark-to-market including the remaining paper position: about `-115,900円`.
- The remaining `4092` position was opened at `2026-06-08 14:50:19 JST`, immediately after the
  scheduled `14:50` paper closeout. It was not closed because OMS Paper closeout runs once per
  day and the Gateway had allowed a new paper/day BUY after closeout.

Important finding: live and paper were not fully parity-equivalent at the Gateway risk layer.
Some day-trading safety gates were implemented only for `TradeMode.LIVE`, even though paper
is intended to exercise the same risk/decision path with only the execution venue swapped.

Differences observed before the fixes:

- `09:00-09:15 JST` new day BUY block was live-only.
- `14:30 JST` late new day BUY block was live-only.
- `14:50 JST` day session closed block was live-only.
- Same-symbol same-day reentry after a SELL was live-only.
- Paper PnL is not reflected in `system_status.daily_pnl`, so paper losses can be missed if
  only the standard live daily performance script is used.

Fixes already applied in the working tree during the close review:

- `services/gateway/src/gateway/clients/supabase.py`
  - Added `has_sell_since(symbol, trade_mode, since)` to read either `trades_live` or
    `trades_paper`.
  - Kept `has_live_sell_since` as a compatibility wrapper.
- `services/gateway/src/gateway/config.py`
  - Added `day_same_symbol_reentry_block_enabled: bool = True`.
- `services/gateway/src/gateway/streaming/runner.py`
  - Extended same-symbol same-day reentry blocking to both paper and live.
  - Extended day session closed / late BUY / opening BUY guards to paper and live.
  - Rejection reason strings remain the existing values (`same_day_reentry_after_sell`,
    `market_closed`, `late_live_buy`, `opening_live_buy`) for log compatibility.
- `infra/docker-compose.prod.yml`
  - Added `DAY_SAME_SYMBOL_REENTRY_BLOCK_ENABLED: ${DAY_SAME_SYMBOL_REENTRY_BLOCK_ENABLED:-true}`
    for `gateway`.
  - Also contains the earlier pre-open fix `MAX_HOLD_MINUTES: ${MAX_HOLD_MINUTES:-45}` for
    `feature-engine`.
- Tests added/updated:
  - Paper/day BUY after same-day SELL rejects before position reads.
  - Reentry block can be disabled by config.
  - Paper/day BUY after new-buy cutoff rejects before position reads.
  - Paper/day BUY before start time rejects before position reads.
  - Existing paper stale-signal test was moved to a normal session time so it does not conflict
    with the newly shared market-closed guard.

Verification already run:

- `uv run pytest services/gateway/tests/unit` -> `145 passed`.
- `uv run ruff format --check ...` on touched Gateway files -> OK.
- `uv run ruff check ...` on touched Gateway files -> OK.
- `uv run mypy services/gateway/src/gateway services/gateway/tests/unit/test_stream_runner.py services/gateway/tests/unit/test_config.py` -> OK for the first reentry change. Re-run after the final market-closed parity patch in the next session if desired.

Production reflection already done:

- Rebuilt/recreated production `gateway` twice:
  - once after the paper/live same-day reentry parity fix,
  - once after the paper/live session-closed/opening/late BUY parity fix.
- `docker compose -f infra/docker-compose.prod.yml ps gateway` showed `gateway` Up after recreate.
- Gateway logs after the final recreate showed paper/day BUY rejected with `reason=market_closed`,
  e.g. `6997`, `3905` at around `15:09 JST`.

Known current state / dirty working tree:

- `AGENTS.md` is modified from user-provided session context. Do not revert unless explicitly asked.
- `docs/features.md` and `docs/features/market-regime-filter.md` document the planned market
  regime / 地合い filter.
- `infra/docker-compose.prod.yml` has both the pre-open `MAX_HOLD_MINUTES` addition and Gateway
  `DAY_SAME_SYMBOL_REENTRY_BLOCK_ENABLED`.
- Gateway source and tests are modified as described above.
- `4092` paper position still exists and is a simulated residual from the 14:50:19 BUY. Decide
  in the next session whether to manually close/delete it for clean paper state or keep it as
  evidence. It is not a live position.

Recommended next-session work:

1. Review and commit the Gateway parity fixes separately from the market-regime planning doc if
   possible.
2. Add/adjust docs/runbooks to state the invariant: Gateway day-session safety guards apply to
   both paper and live; only the destination topic and execution adapter should differ.
3. Add a paper PnL reporting utility or extend the daily performance skill/script so paper
   realized/unrealized PnL is first-class and not hidden behind `system_status.daily_pnl=0`.
4. Add a parity-focused Gateway test group for day-session guards:
   - opening BUY block,
   - late BUY block,
   - market closed block,
   - same-day reentry block.
5. Consider an OMS Paper defense-in-depth guard to reject or no-fill day BUY after closeout time,
   even if Gateway regresses.
6. Continue with the `market_regime` / 地合い filter design:
   - Universe Scanner pre-open regime score,
   - AI summary judgment as brake-only input,
   - Gateway fail-close for `RISK_OFF` / `CRASH`,
   - strategy-rule suppression of RSI-style reverse BUY under risk-off conditions.

## 2026-06-06 risk-off paper plan

Friday US market selloff and weak Nikkei futures made Monday a risk-off session.
Decision: do not run live trading; use production paper trading to observe how the
pipeline behaves under a sharp down-open.

Prepared state:

- Added `docs/runbook/risk-off-paper-day.md`.
- Added `scripts/prepare-risk-off-paper-day.py` to verify live/paper positions and
  switch `system_status` to `trade_mode=paper`, `trading_style=day`,
  `is_trading_allowed=true`.
- Local `infra/env.production` was changed to `TRADE_MODE=paper` and
  `OMS_LIVE_DRY_RUN=true`.
- Ran the prepare script with `--apply`. Cloud Supabase now has
  `trade_mode=paper`, `trading_style=day`, `is_trading_allowed=true`.
- Confirmed `positions(live)` and `positions(paper)` were both empty at preparation time.

Monday focus:

- Observe whether the strategy stack buys into the sharp down-open in paper mode.
- Check `strategy_logs`, `aggregator_logs`, Gateway `signal_rejected` /
  `order_published`, `trades_paper`, and paper `positions`.
- Confirm 14:50 paper closeout clears day positions if any are opened.

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
