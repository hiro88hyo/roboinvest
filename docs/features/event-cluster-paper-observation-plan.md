# Event Cluster Paper Observation Plan

Created: 2026-07-04

Status: Phase 0 was approved on 2026-07-04. On 2026-07-10 an external audit
found that operational detection depended on T+1 OHLCV and copied the future
open into the signal price and absolute stop. Phase 1 is now causal dry-run
detection only. Phase 2 paper publication is blocked regardless of environment
flags until fresh observed pricing, relative stop intent, and fill-anchored
absolute stops are implemented.

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
| Backtest data timing matches paper ordering | point-in-time event data, next open entry | Causal dry-run detection is implemented; executable next-open/fill path remains blocked and unaudited | DRY-RUN PASS / EXECUTION NEEDED |
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

The currently supported path stops at a causal artifact and watchlist capture:

```text
J-Quants /fins/summary
  -> event cluster paper batch
  -> causal candidate/exclusion artifact
  -> production-preopen-check artifact validation
  -> watchlist market-data capture
  -> no signal or order publication
```

If publication is restored, it must use the existing Pub/Sub route through
Aggregator, Gateway, and OMS Paper. It must not add service-to-service direct
calls or bypass Gateway. Gateway remains the only risk executor for 2% rule
sizing, lot calculation, kill switch state, duplicate-position checks, and
paper/live routing.

### Timing

Current causal dry-run sequence:

1. After the signal date has ended in JST, and before 09:00 JST on the next TSE
   business day, fetch that date's `/fins/summary` disclosures with
   exporter-recorded receipt provenance. Evaluate the frozen cluster v1 rule
   and write candidate and exclusion output without consulting T+1 OHLCV.
2. Next trading day pre-open preparation: ensure event symbols are in the
   watchlist for market-data capture only after
   `production-preopen-check.py --swing-candidates-json` validates causality,
   dates, receipt provenance, and the absence of executable price fields.
3. At the intended entry session, capture fresh market data. Do not publish a
   `StrategySignal`; `--publish-paper` remains fail closed.
4. Produce a candidate-only report. No event-cluster rows should appear in
   `strategy_logs`, `aggregator_logs`, `trades_paper`, or `positions`.

### Execution Contract Status and Remaining Gaps

`StrategySignal` already carries optional `holding_type`, and Aggregator uses
it when present while preserving the existing day-trading default otherwise.
That earlier contract gap is closed. The remaining execution gap is downstream:
`OrderRequest` does not carry `holding_type`, so OMS Paper currently uses a
process-level default for a new position.

The candidate artifact is intentionally non-executable. It contains the
valuation reference and frozen `CAT_STOP_PCT=-0.10`, but contains neither an
entry-price assumption nor an absolute `stop_loss_price`.

Paper publication may be restored only after a separate implementation:

- observes a fresh entry price with its timestamp and rejects stale or missing
  market data;
- propagates `holding_type=swing`, `max_hold_days=20`, and the relative 10% stop
  intent through `StrategySignal`, Aggregator, Gateway, and `OrderRequest`;
- uses the fresh observation for pre-fill risk validation without presenting it
  as an actual fill;
- has OMS Paper anchor the persisted absolute stop to the actual fill price as
  `fill_price * (1 + CAT_STOP_PCT)`; and
- covers the complete paper path with tests while leaving existing day signals
  unchanged.

Until those conditions are met, there is no supported event publisher.

### Aggregator

Aggregator already supports single-source passthrough after the pairing window:
RULE-only input becomes `signal_source=RULE` when confidence is above
`MIN_CONFIDENCE_RULE_ONLY`. No consensus requirement is needed for this paper
candidate.

Acceptance checks before publication can resume:

- RULE-only event signal emits exactly one `UnifiedTradeSignal`.
- `holding_type=swing`, relative stop intent, `max_hold_days`, and
  `scheduled_exit_date` survive aggregation once the execution contract is
  implemented.
- Below-threshold confidence is rejected by existing source-specific threshold
  behavior.

These are target execution checks, not authorization to publish in the current
phase.

### Gateway

Gateway must remain unchanged in responsibility:

- read kill-switch/system status
- reject if trading is disabled
- reject if the signal is stale
- reject duplicate same-symbol long positions
- calculate lot size and enforce risk limits
- route by `system_status.trade_mode`

The current detector refuses all publication. Any restored event publisher must
also add a preflight that refuses to publish when
`system_status.trade_mode != paper`. This event-batch safety check would not
replace Gateway's routing responsibility.

### OMS Paper

OMS Paper already supports swing positions, absolute-stop monitoring, and
`opening-swing-exits` for fixed-hold exits. Before publication can be restored,
the paper path must prove:

- New BUY position derives and persists `holding_type=swing` from the order,
  not a process-wide default.
- Absolute `stop_loss_price` is calculated from the actual paper fill using the
  frozen relative 10% intent and then persists.
- `max_hold_days=20` persists.
- `scheduled_exit_date` is set either from the signal/order or by OMS Paper
  from the fill date plus 20 TSE business days.
- `opening-swing-exits` closes due positions before new event entries.

## Operational Timeline

```text
T day through 23:59 JST
  J-Quants disclosures accumulate; no final zero-candidate conclusion is made

After T day ends, before next TSE business day 09:00 JST
  exporter records the complete signal-date response and receipt provenance
  event batch detects cluster v1 candidates without consulting T+1 OHLCV
  causal dry-run artifact is written; no order publish
  production-preopen-check validates the candidate artifact
  event candidates are added to watchlist for data capture
  no StrategySignal is published

T+1 open
  Feeder captures fresh market data for candidate symbols
  event --publish-paper remains fail closed
  no event order or position is created

After market-data capture
  candidate-only report records detections and exclusions
  event execution fields remain empty while the execution path is blocked
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
- valuation reference price, bar date, and availability timestamp; this is not
  an executable entry price
- fresh observed entry price and timestamp after the execution path is restored
- paper fill price, quantity, fill timestamp, fill reason, and slippage bps
- relative stop intent, fill-anchored absolute stop loss price, max hold days,
  and scheduled exit date
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
- The Phase 1 command writes JSON/CSV artifacts only and never calls Pub/Sub.
  Passing `--publish-paper` fails closed.

Dry-run command:

```bash
uv run python scripts/detect-event-cluster-paper-candidates.py \
  --financial-summary-jsonl out/event-research/financial-summaries-20210628-20260624-clean.jsonl \
  --ohlcv data/reference/daily_ohlcv_20210625_20260624_bydate.csv \
  --output-json out/event-paper-observation/candidates.json \
  --output-csv out/event-paper-observation/candidates.csv \
  --signal-date YYYY-MM-DD
```

This command writes candidate and exclusion artifacts only. It has no Pub/Sub
side effect and cannot route to Gateway, OMS Paper, or OMS Live.

Phase 2:

- Blocked on 2026-07-10. `--publish-paper` fails closed before any Supabase or
  Pub/Sub side effect.
- Restore only after a timestamped fresh-price path, relative stop intent,
  actual-fill stop anchoring, and `holding_type` propagation through
  `OrderRequest` are covered by paper-path tests.
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
