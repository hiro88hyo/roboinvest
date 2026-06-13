# 2026-06-13 fable5 feedback fixes handoff

Context: user asked to carefully read `docs/handoff/2026-06-fable5-feedback.md` and proceed with
improvements. This session implemented the mechanically verifiable items that did not require
fabricating production data. No commit or deployment was made.

## Working tree note

Pre-existing/user changes to preserve:

- `AGENTS.md`
- `docs/handoff/2026-06-operations-log.md`
- `docs/handoff/2026-06-fable5-feedback.md` is still untracked in `git status`

New untracked files created by this session:

- `contracts/sql/015_gateway_kill_switch_rpc.sql`
- `scripts/parameter-sweep.py`
- `services/oms-paper/src/oms_paper/backtest/report.py`
- `services/oms-paper/tests/unit/test_paper_backtest_report.py`
- `docs/handoff/2026-06-13-fable5-fixes-handoff.md`

## Implemented checklist items

### CHK-01 / CHK-02: backtest PnL and report

Files:

- `services/oms-paper/src/oms_paper/backtest/runner.py`
- `services/oms-paper/src/oms_paper/backtest/report.py`
- `services/oms-paper/src/oms_paper/backtest/writer.py`
- `services/oms-paper/src/oms_paper/backtest/__init__.py`
- `services/oms-paper/src/oms_paper/__main__.py`
- `services/oms-paper/tests/unit/test_paper_backtest_report.py`
- `services/oms-paper/tests/unit/test_paper_backtest_runner.py`
- `services/oms-paper/tests/unit/test_paper_main_cli.py`

Summary:

- Added realized PnL to `BacktestSummary`.
- Added closed-trade capture for SELL fills.
- Added `backtest_report.json` output with:
  - gross/net PnL
  - commission at 0.099%
  - slippage at 0.05% per entry/exit notional
  - tax at 20.315% only when profitable
  - win rate, profit factor, max drawdown, Sharpe ratio, expectancy
- `oms-paper backtest` writes `backtest_report.json` beside `--output-positions` by default,
  with optional `--output-report`.

### CHK-03: parameter sweep

File:

- `scripts/parameter-sweep.py`

Summary:

- Standalone stdlib script.
- Input CSV/JSONL columns: `symbol,date,open,high,low,close,volume`.
- Sweeps:
  - RSI buy `{20,25,30}`
  - RSI sell `{70,75,80}`
  - SMA short `{5,10,20}`
  - SMA long `{25,50,75}`
  - Bollinger tolerance `{0.0,0.05,0.15}`
- Splits data by median date into train/validation.
- Outputs `sweep_results.csv` with trade count, net PnL, win rate, profit factor,
  max drawdown, Sharpe ratio, and expectancy for each split.

### CHK-04: AI strategy silence monitoring

Files:

- `services/strategy-ai/src/strategy_ai/config.py`
- `services/strategy-ai/src/strategy_ai/strategy.py`
- `services/strategy-ai/src/strategy_ai/streaming/runner.py`
- `services/strategy-ai/tests/unit/test_strategy.py`
- `services/strategy-ai/tests/unit/test_ai_stream_runner.py`
- `services/strategy-ai/tests/unit/test_config.py`

Summary:

- Added `AiStrategyStats` counters:
  - `llm_calls`
  - `llm_successes`
  - `llm_errors`
  - `parse_failures`
  - `hold_decisions`
  - `confidence_rejects`
  - `signals_emitted`
- Added `ai_silence_warn_seconds` setting, default `3600.0`.
- During JST market hours on weekdays, logs `AI_STRATEGY_SILENT` at error level when no valid AI
  signal is emitted for the configured silence window.
- Silence logging resets on a valid emitted signal and does not spam repeatedly for the same
  silence window.

### CHK-06: Gateway kill-switch atomicity

Files:

- `contracts/sql/015_gateway_kill_switch_rpc.sql`
- `services/gateway/src/gateway/clients/supabase.py`
- `services/gateway/src/gateway/streaming/runner.py`
- `services/gateway/tests/unit/test_supabase_client.py`
- `services/gateway/tests/unit/test_stream_runner.py`
- `services/gateway/tests/unit/test_main_cli.py`

Summary:

- Added Supabase RPC contract `public.gateway_check_kill_switch()`.
- The RPC execute privilege is explicitly revoked from `public`, `anon`, and `authenticated`,
  then granted only to `service_role`.
- The RPC locks `system_status.id=1` with `FOR UPDATE`, evaluates live-mode PnL limits, and flips
  `is_trading_allowed=false` in the same transaction.
- Gateway streaming now calls `SupabaseClient.check_kill_switch()` instead of doing
  `read_system_status()` followed by `disable_trading()`.
- Existing `disable_trading()` remains for compatibility.
- This is a safer scoped version of the fable5 `check_and_reserve_risk(amount)` instruction:
  no risk reservation table/lifecycle was added because stale reservations would require OMS
  release semantics on no-fill, cancel, partial fill, and crashes.

### CHK-07: OMS Live partial-fill remainder visibility

Files:

- `services/oms-live/src/oms_live/streaming/runner.py`
- `services/oms-live/tests/unit/test_live_stream_runner.py`
- `services/oms-live/tests/unit/test_live_config.py`
- `services/oms-live/tests/unit/test_live_main_cli.py`

Summary:

- Partial fills now log the abandoned remainder explicitly with:
  - `event="partial_fill_abandoned"`
  - `reason="partial_abandoned"`
  - requested quantity
  - filled quantity
  - remaining quantity
  - order ids and phase
- No re-order/re-submit behavior was added.

### CHK-08: Universe Scanner lookahead removal

Files:

- `services/universe-scanner/src/universe_scanner/ingest/daily_ohlcv.py`
- `services/universe-scanner/src/universe_scanner/filters/dynamic.py`
- `services/universe-scanner/src/universe_scanner/pipeline.py`
- `services/universe-scanner/tests/unit/test_dynamic_filter.py`
- `services/universe-scanner/tests/unit/test_ingest_parsing.py`

Summary:

- `ingest_daily_ohlcv()` now ends at `previous_business_day(as_of)`.
- `score_candidates(..., as_of=...)` filters OHLCV through `previous_business_day(as_of)`.
- Pipeline passes `target_date` into scoring.
- Tests verify same-day data is not used for scoring.

## Verification run

Full repository checks:

- `make lint-all` passed.
- `make test-all` passed:
  - Python: `988 passed, 21 skipped`
  - Dashboard: `8 files passed`, `47 tests passed`

2026-06-13 follow-up review:

- Re-reviewed Gateway RPC / OMS Live partial-fill / backtest report / AI silence / Universe
  Scanner lookahead changes.
- Tightened `contracts/sql/015_gateway_kill_switch_rpc.sql` function grants so the public RPC is
  executable only by `service_role`.
- Added `docs/runbook/gateway-kill-switch-rpc.md` with DB-first apply, verification, app deploy,
  and rollback order.
- Updated `docs/runbook/adr-0001-supabase-cloud.md` schema application order through
  `contracts/sql/015_gateway_kill_switch_rpc.sql`.
- Re-ran `make lint-all` successfully.
- Re-ran `make test-all` successfully:
  - Python: `988 passed, 21 skipped`
  - Dashboard: `8 files passed`, `47 tests passed`

Important individual checks also run during the session:

- `services/oms-paper`: `uv run pytest` -> `149 passed, 4 skipped`
- `services/strategy-ai`: `uv run pytest` -> `83 passed, 2 skipped`
- `services/universe-scanner`: `uv run pytest` -> `43 passed`
- `services/oms-live`: `uv run pytest` -> `159 passed, 6 skipped`
- `services/gateway`: `uv run pytest` -> `159 passed, 3 skipped`
- Service-level ruff and strict mypy checks passed for touched services.
- `scripts/parameter-sweep.py` passed ruff/mypy and a synthetic run produced 243 parameter rows.

## Not completed

### CHK-05: dynamic capital from kabu wallet/cash

Not implemented.

Reason:

- Gateway currently owns Pub/Sub/Supabase risk routing and does not own kabu API connectivity.
- Direct Gateway -> kabu coupling would break the existing responsibility boundary where kabu
  access is in Feeder/OMS Live.
- A safer design is to have OMS Live or a dedicated account-state publisher write available cash
  into Supabase, then let Gateway read that dynamic value. That schema and lifecycle are not
  present yet.

### CHK-09 / CHK-10: real backtest report and disabling losers

Not implemented.

Reason:

- No local `docs/reports` directory exists.
- No local OHLCV CSV/JSONL dataset for the last two years was found with `rg --files`.
- Do not fabricate report metrics or remove strategies without real validation data.

Next recommended work:

1. Decide the dynamic-capital architecture:
   - preferred: account-state table in Supabase populated by OMS Live or a dedicated service;
   - avoid direct Gateway -> kabu unless the architecture is explicitly changed.
2. Provide/export recent `daily_ohlcv` data, then run `scripts/parameter-sweep.py`.
3. Create `docs/reports/backtest-YYYY-MM.md` from real sweep output.
4. Only after a real report exists, evaluate CHK-10 and remove PF < 1.0 strategies from
   `services/strategy-rule/src/strategy_rule/config.py`.

## Commit / PR packaging notes

Suggested commit scope:

- Include:
  - `contracts/sql/015_gateway_kill_switch_rpc.sql`
  - `docs/runbook/adr-0001-supabase-cloud.md`
  - `docs/runbook/gateway-kill-switch-rpc.md`
  - `docs/handoff/2026-06-13-fable5-fixes-handoff.md`
  - `scripts/parameter-sweep.py`
  - all touched `services/gateway`, `services/oms-live`, `services/oms-paper`,
    `services/strategy-ai`, and `services/universe-scanner` files
- Keep separate unless intentionally committing operational notes:
  - `AGENTS.md`
  - `docs/handoff/2026-06-operations-log.md`
  - `docs/handoff/2026-06-fable5-feedback.md`

PR summary:

- Add cost-aware paper backtest reporting and a standalone rule-parameter sweep script.
- Add AI strategy silence counters/logging for market-hours no-signal windows.
- Move Gateway kill-switch evaluation into a Supabase RPC with row locking and service-role-only
  execute privilege.
- Log OMS Live partial-fill abandoned remainders explicitly.
- Prevent Universe Scanner lookahead by ending OHLCV ingest/scoring at the previous business day.
- Document Gateway RPC production apply / rollback order.

PR verification:

- `make lint-all`
- `make test-all` (`988 passed, 21 skipped`; dashboard `47 passed`)
