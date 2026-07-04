# Event Cluster Paper Observation Plan

Created: 2026-07-04

Status: Phase 0 approved on 2026-07-04. Phase 1 dry-run detection, Phase 2
paper-only publication, and Phase 3 observation reporting are implemented.
Publication remains disabled by default unless
`EVENT_CLUSTER_PAPER_PUBLISH_ENABLED=true` and `--publish-paper` are both set.

This plan covers paper observation for
`event_cluster_earnings_dividend_value_guard_fixed20_stop_v1_research`.
It is not a live promotion. The live gate remains unchanged:
OOS `profit_factor > 1.2`, max drawdown `< capital * 0.10`, pre-registered
parameters and costs, materially above matched random baselines, and reproduced
paper behavior. The project kill switch in `AGENTS.md` remains unchanged.

No task in this plan requires a new locked OOS run. The current locked OOS
window is frozen by [ADR-0005](../adr/0005-locked-oos-inspection-freeze.md).

## Candidate

Candidate ID:
`event_cluster_earnings_dividend_value_guard_fixed20_stop_v1_research`

Fixed definition:

- same trade cluster contains `earnings_result`
- same trade cluster contains `dividend_revision` subtype `increase`
- if forecast PER is available point-in-time, cluster minimum forecast PER must
  be `<= 15`
- if forecast PER is unavailable point-in-time, the cluster is not rejected only
  for that absence
- entry mode: `next_open_unconditional`
- exit: `fixed_20d_plus_catastrophic_stop`
- catastrophic stop: `CAT_STOP_PCT=-0.10`
- cost: `ROUND_TRIP_COST_RATE=0.00298`
- random baseline: true `same_symbol_random_date`, 300 seeds

Do not change the PER threshold, missing-value treatment, exit horizon,
catastrophic stop, cost assumption, or cluster definition as part of paper
observation.

## Gate Assessment

ADR-0004 Paper Observation Gate:

| Condition | Required | Cluster v1 evidence | Status |
|---|---:|---|---|
| Aggregate OOS PF | `> 1.10`, target `1.15` | Locked OOS portfolio PF: 1M `2.036`, 2M `2.193`, 5M `2.904` | PASS |
| Aggregate OOS max DD | `< capital * 0.12` | 1M `41,194`, 2M `117,894`, 5M `161,253` | PASS |
| Matched random p75 or better | target p75 | Locked OOS portfolio percentile: 1M `0.737`, 2M `0.853`, 5M `0.927` | 1M FAIL, 2M/5M PASS |
| `same_symbol_random_date` percentile | `>= 0.65` | 1M `0.737`, 2M `0.853`, 5M `0.927` | PASS |
| Execution stress does not materially break result | positive stressed result | entry10_exit25: 1M PF `1.784`, 2M `2.015`, 5M `2.655`; exit50: 1M PF `1.808`, 2M `1.975`, 5M `2.568` | PASS |
| Backtest data timing matches paper ordering | point-in-time event data, next open entry | Research dataset has point-in-time fields; live/paper event detection path still needs implementation and audit | BORROWED/IMPLEMENTATION NEEDED |
| Prompt/model/feature schema can be frozen | required for AI path | This is LLM-free rule-only. Rule definition is frozen instead. | PASS BY SCOPE |

The only numerical gap is 1M matched-random p75: `0.737 < 0.75`. The argument
for paper observation is that 2M and 5M pass p75, all tested capital levels
pass the `>= 0.65` paper gate, and paper observation collects operational
evidence with zero capital risk. Because the latest cluster report still marks
v1 as research-continuation only, starting paper observation requires explicit
user approval.

## Paper Observation Decision

Proposed decision:

- Start paper observation only after this Phase 0 document is approved.
- Treat the 1M percentile shortfall as an accepted paper-observation exception,
  not as a live gate exception.
- Keep all candidate parameters frozen.
- Do not inspect the frozen locked OOS window again.

Paper observation success and failure must be judged before any live discussion.
The initial observation horizon is the earlier of:

- 6 calendar months of eligible market days after activation, or
- 15 opened paper trades for this candidate.

Success criteria:

- Event detection, signal publication, Gateway approval/rejection, OMS Paper
  fill, position creation, and scheduled exit ordering are reproducible from
  logs.
- Every opened paper position has `holding_type=swing`, catastrophic
  `stop_loss_price`, `max_hold_days=20`, and a deterministic
  `scheduled_exit_date`.
- Entry fills are attributable to the intended next-session opening sequence,
  with entry slippage versus the research assumption reported in basis points.
- Exit fills are attributable to `opening-swing-exits`, catastrophic stop, or
  an explicitly recorded no-fill/manual intervention reason.
- Fill rate, no-fill rate, entry slippage, exit slippage, skipped symbols, and
  sequencing errors are reported without changing the registered strategy.

Failure criteria:

- Any live route is touched or an event signal can route to `live-orders`.
- Event paper BUY can publish while `system_status.trade_mode != paper`.
- The implementation cannot reproduce the research selection rule from
  point-in-time inputs.
- `holding_type`, stop, or max-hold metadata is lost before `positions`.
- Operational ordering diverges from the backtest assumption and cannot be
  explained by logged market data.
- The observation report cannot reconcile paper trades with detected events.

These criteria must not be loosened after observing paper results.

## Architecture

### Data Flow

Use the existing Pub/Sub route. Do not add service-to-service direct calls.

```text
J-Quants /fins/summary
  -> event cluster paper batch
  -> StrategySignal(source=RULE) on strategy-signals-a
  -> aggregator
  -> UnifiedTradeSignal on trade-signals
  -> gateway risk checks and trade_mode routing
  -> paper-orders
  -> oms-paper
  -> positions / trades_paper
```

The event batch is a daily batch similar in shape to `universe-scanner`, but it
does not bypass aggregator or Gateway. Gateway remains the only risk executor
for 2% rule sizing, lot calculation, kill switch state, duplicate-position
checks, and paper/live routing.

### Timing

Separate event detection from order publication:

1. Evening detection batch, after J-Quants financial summaries are available:
   fetch the day's `/fins/summary` disclosures, evaluate the frozen cluster v1
   rule, and write dry-run/event-candidate output.
2. Next trading day pre-open preparation: ensure event symbols are in the
   watchlist for market-data capture and verify `system_status.trade_mode =
   paper`.
3. Opening exit batch: run `oms-paper opening-swing-exits` before new entries
   so due fixed-hold positions can close before same-day BUYs.
4. Entry publication: publish fresh `StrategySignal` messages shortly before or
   during the intended opening entry window. Do not publish the previous
   evening because Gateway rejects stale signals after 300 seconds by default
   and, more importantly, immediate downstream processing would no longer
   represent next-session entry.
5. Gateway routes only while `trade_mode=paper`; otherwise the event batch must
   refuse to publish and leave a dry-run report.

### StrategySignal Contract Gap

Current `StrategySignal` carries stop, target, trailing stop, `max_hold_days`,
and `scheduled_exit_date`, but it does not carry `holding_type`. Aggregator
sets `UnifiedTradeSignal.holding_type` from its global `default_holding_type`.
Setting the global default to `swing` would affect unrelated signals and is not
acceptable.

Phase 1 must therefore include a small contract change:

- Add `holding_type: TradingStyle | None = None` to `StrategySignal`.
- Update aggregator to use the signal's `holding_type` when present; otherwise
  keep `ConsensusConfig.default_holding_type`.
- Keep the default behavior unchanged for existing day-trading signals.

The event batch should publish:

- `source=RULE`
- `action=BUY`
- `holding_type=swing`
- `confidence >= MIN_CONFIDENCE_RULE_ONLY` (default threshold is `0.5`)
- `max_hold_days=20`
- `stop_loss_price` equal to `entry_price * (1 + CAT_STOP_PCT)`, using the
  frozen research value `CAT_STOP_PCT=-0.10`
  and current entry-price assumption
- `reasoning` with candidate ID, event IDs, disclosure timestamps, PER guard
  status, and intended entry date

`scheduled_exit_date` may be supplied by the batch when the TSE calendar is
available. If omitted, OMS Paper currently derives it from `max_hold_days` at
fill time via `nth_tse_business_day_after`.

### Aggregator

Aggregator already supports single-source passthrough after the pairing window:
RULE-only input becomes `signal_source=RULE` when confidence is above
`MIN_CONFIDENCE_RULE_ONLY`. No consensus requirement is needed for this paper
candidate.

Acceptance checks for Phase 1:

- RULE-only event signal emits exactly one `UnifiedTradeSignal`.
- `holding_type=swing`, stop, `max_hold_days`, and `scheduled_exit_date` fields
  survive aggregation.
- Below-threshold confidence is rejected by existing source-specific threshold
  behavior.

### Gateway

Gateway must remain unchanged in responsibility:

- read kill-switch/system status
- reject if trading is disabled
- reject if the signal is stale
- reject duplicate same-symbol long positions
- calculate lot size and enforce risk limits
- route by `system_status.trade_mode`

Event paper publication must add a preflight that refuses to publish when
`system_status.trade_mode != paper`. This is an event-batch safety check; it
does not replace Gateway's routing responsibility.

### OMS Paper

OMS Paper already supports swing positions, catastrophic stop monitoring, and
`opening-swing-exits` for fixed-hold exits. Required Phase 1/2 checks:

- New BUY position persists `holding_type=swing`.
- `stop_loss_price` persists.
- `max_hold_days=20` persists.
- `scheduled_exit_date` is set either from the signal/order or by OMS Paper
  from the fill date plus 20 TSE business days.
- `opening-swing-exits` closes due positions before new event entries.

## Operational Timeline

```text
T day 15:30-18:00 JST
  J-Quants summaries become available
  event batch detects cluster v1 candidates
  dry-run report written; no order publish

T+1 pre-open
  event candidates are added to watchlist for data capture
  Supabase health, Pub/Sub health, trade_mode=paper, and kill switch checked
  due swing exits are identified

T+1 open
  oms-paper opening-swing-exits runs first
  event batch publishes fresh RULE StrategySignal for approved candidates
  aggregator emits RULE UnifiedTradeSignal
  gateway validates and publishes paper-orders
  oms-paper simulates fills from current book

T+1 through scheduled exit
  OMS Paper monitors catastrophic stop on book updates
  opening-swing-exits closes due fixed-hold positions
  observation report reconciles detected events, orders, fills, positions, and exits
```

## Observation Log Design

The paper observation report needs enough information to reconcile each trade
against the research assumption:

- `candidate_id`
- event cluster ID and source event IDs
- symbol and symbol name
- disclosed_at and data_available_at for each source event
- signal generation timestamp and intended entry date
- rule pass/fail fields: earnings present, dividend increase present, minimum
  forecast PER, missing PER treatment
- exclusion reason for every detected but unpublished candidate
- signal ID, unified signal ID, order ID, and paper trade IDs
- intended entry price assumption from research
- Gateway entry price source (`signal`, `positions`, or `daily_ohlcv`)
- paper fill price, quantity, fill timestamp, fill reason, and slippage bps
- stop loss price, max hold days, scheduled exit date
- exit trigger (`opening_max_hold_days`, `stop_loss`, manual/no-fill)
- exit fill price and exit slippage bps
- open-position status and unrealized PnL for still-open positions

Write observation artifacts to `out/event-paper-observation/` locally and, when
the batch is connected to Supabase/PubSub, ensure the same IDs can be joined
from `aggregator_logs`, `trades_paper`, and `positions`.

## Phase Plan After Approval

Phase 1:

- Implemented daily event detection batch in dry-run mode only:
  `scripts/detect-event-cluster-paper-candidates.py`.
- Reuses `event_research_common` cluster rule helpers rather than duplicating
  selection code.
- Added a 1-week J-Quants-shaped fixture test covering PASS, PER-guard
  exclusion, missing-PER PASS, and `--signal-date` filtering.
- Added contract and aggregator tests for `StrategySignal.holding_type`.
- The Phase 1 command remains dry-run: without `--publish-paper`, it writes
  JSON/CSV artifacts only and never calls Pub/Sub.

Dry-run command:

```bash
uv run python scripts/detect-event-cluster-paper-candidates.py \
  --financial-summary-jsonl out/event-research-real-pit/financial-summaries.jsonl \
  --ohlcv data/reference/daily_ohlcv_20210625_20260624_bydate.csv \
  --output-json out/event-paper-observation/candidates.json \
  --output-csv out/event-paper-observation/candidates.csv \
  --signal-date YYYY-MM-DD
```

This command writes candidate and exclusion artifacts only. It has no Pub/Sub
side effect and cannot route to Gateway, OMS Paper, or OMS Live.

Phase 2:

- Implemented paper-only publish behind
  `EVENT_CLUSTER_PAPER_PUBLISH_ENABLED=true`; default is off.
- `--publish-paper` requires Supabase preflight
  `system_status.trade_mode = paper` before any Pub/Sub publish.
- Published messages go only to `strategy-signals-a` as `StrategySignal`
  `source=RULE`, `holding_type=swing`, `max_hold_days=20`.
- Runbook: [Event Cluster Paper Publish](../runbook/event-cluster-paper-publish.md).
- Live routes remain untouched.

Phase 3:

- Implemented `scripts/report-event-paper-observation.py` to reconcile detected
  events, emitted strategy signals, aggregator logs, paper trades, fill
  slippage, exits, and open paper positions.
- Confirmed paper execution rows and still-open position PnL are reported in
  separate fields. `position_unrealized_pnl` is not treated as confirmed
  realized PnL.
- Candidate-only reports are supported with `--skip-supabase`; Supabase-backed
  reports join `strategy_logs`, `aggregator_logs`, `trades_paper`, and
  `positions`.

## Explicit Non-Goals

- No live enablement.
- No change to OMS Live or `live-orders`.
- No new locked OOS inspection.
- No retuning of PER, dividend, exit horizon, stop, or technical veto values.
- No AI prompt/model/schema changes.
- No direct service-to-service order routing.
