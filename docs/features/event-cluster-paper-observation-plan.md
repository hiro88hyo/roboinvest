# Event Cluster Paper Observation Plan

Created: 2026-07-04

Status: Phase 0 was approved on 2026-07-04. On 2026-07-10 an external audit
found that operational detection depended on T+1 OHLCV and copied the future
open into the signal price and absolute stop. Phase 1 is now causal dry-run
detection only. The relative-stop/fill contract, paper-only routing identity,
and truthful live-book receive timestamp are implemented, but Phase 2 paper
publication remains blocked regardless of environment flags until the publisher,
target-database migration check, and end-to-end safety requirements are complete.

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
| Backtest data timing matches paper ordering | point-in-time event data, next open entry | Causal dry-run detection and the relative-stop/fill contract are implemented; fresh-quote publication and end-to-end ordering remain blocked | DRY-RUN PASS / EXECUTION NEEDED |
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

`StrategySignal`, `UnifiedTradeSignal`, and `OrderRequest` now carry
`stop_loss_pct`, constrained to `0 < stop_loss_pct < 1`. A relative stop and an
absolute `stop_loss_price` are mutually exclusive. `OrderRequest` also carries
`holding_type`, `max_hold_days`, and `scheduled_exit_date`; Gateway preserves
those fields and Aggregator includes the relative stop in its order-field
passthrough.

Gateway uses `entry_price * (1 - stop_loss_pct)` for pre-fill risk sizing, but
keeps the relative intent on the paper order instead of treating that estimate
as an actual stop fill. A live BUY carrying `stop_loss_pct` is rejected with
`relative_stop_live_unsupported`. OMS Paper resolves the persisted absolute
stop for a new BUY from the actual paper fill as
`fill_price * (1 - stop_loss_pct)`. Existing positions keep their existing
holding and exit metadata on an add-on fill. The 14:50 day closeout now creates
orders only for `holding_type=day`; swing positions are not closed by that job.

Identity-bearing event signals also carry `routing_intent=PAPER_ONLY`, a stable
`strategy_key`, and a per-occurrence `candidate_id` (the detector cluster or
observation identity, not the strategy definition ID). Those fields isolate the
Aggregator pairing bucket. Strategy, unified, and order IDs are deterministic
under redelivery. Gateway rejects PAPER_ONLY in live mode, and the OrderRequest
contract cannot represent PAPER_ONLY with `trade_mode=live`.

Live Feeder books carry `OrderBookSnapshot.received_at` separately from kabu's
`CurrentPriceTime`. OMS Paper evaluates freshness against its wall clock,
rejects excessive future skew, and requires `received_at` unconditionally for
PAPER_ONLY orders. `event-paper-raw-books` is defined as a dedicated filtered
subscription so a future publisher does not consume another service's stream.

The candidate artifact is intentionally non-executable. It contains the
valuation reference and frozen `CAT_STOP_PCT=-0.10`, but contains neither an
entry-price assumption nor an absolute `stop_loss_price`. When the path is
eventually enabled, that frozen strategy value maps to the contract's positive
loss-distance representation `stop_loss_pct=0.10`.

These changes do not authorize publication. Paper publication may be restored
only after the remaining implementation and deployment gates:

- consumes the dedicated subscription, chooses a fresh observed entry quote,
  performs the paper-mode preflight, and emits the implemented identity/routing
  contract without reintroducing a future-price assumption;
- passes the complete event-to-fill path in the Pub/Sub emulator while leaving
  existing day signals unchanged; and
- confirms `contracts/sql/018_oms_paper_apply_fill_rpc.sql` (infra migration
  019) and both atomic OMS Paper RPCs are available in the target Supabase
  environment via the canonical health check.

OMS Paper atomic persistence is implemented: all fill paths use one
`oms_paper_apply_fill` transaction with order-ID idempotency, symbol-level
serialization, authoritative position results, rollback on trade failure, and
explicit partial-exit handling. Actual local PostgREST RPC tests cover these
properties; this completed item does not authorize publication by itself.

Until those conditions are met, there is no supported event publisher.

### Aggregator

Aggregator already supports single-source passthrough after the pairing window:
RULE-only input becomes `signal_source=RULE` when confidence is above
`MIN_CONFIDENCE_RULE_ONLY`. No consensus requirement is needed for this paper
candidate.

Acceptance checks before publication can resume:

- RULE-only event signal emits exactly one `UnifiedTradeSignal`.
- `holding_type=swing`, relative stop intent, `max_hold_days`, and
  `scheduled_exit_date` survive aggregation; contract tests now cover this
  passthrough.
- Below-threshold confidence is rejected by existing source-specific threshold
  behavior.
- Candidate-specific strategy isolation and deterministic IDs prevent a
  redelivery or an unrelated AI/day signal from changing the unified result.

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
`system_status.trade_mode != paper`. That preflight is not sufficient by itself:
the publisher must emit an explicit `PAPER_ONLY` intent and Gateway must enforce
it at routing time. Gateway already rejects a live BUY carrying a relative stop,
but that guard does not replace the broader routing intent.

### OMS Paper

OMS Paper already supports swing positions, absolute-stop monitoring, and
`opening-swing-exits` for fixed-hold exits. The order path now derives a new
position's `holding_type` from `OrderRequest`, resolves an absolute stop from the
actual BUY fill, and carries `max_hold_days` and `scheduled_exit_date`. Its 14:50
day closeout ignores swing positions.

OMS Paper persists `trades_paper` and the corresponding `positions` mutation in
one idempotent transaction. Before publication can be restored, apply migration
018 to the target Supabase project, require the RPC health probe to pass, and
complete an emulator E2E covering new entry, redelivery, partial/full exit, and
scheduled exit ordering. Its PAPER_ONLY path already requires a fresh
wall-clock-checked `received_at`.

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
- Added contract and aggregator tests for `holding_type`, mutually exclusive
  absolute/relative stops, and relative-stop metadata passthrough.
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
- The relative-stop contract, `OrderRequest` holding metadata, fill-anchored
  paper stop, day/swing closeout isolation, receive provenance, PAPER_ONLY
  enforcement, deterministic IDs, strategy isolation, and atomic OMS Paper
  persistence are implemented.
- Restore only after a publisher consumes the dedicated fresh-book
  subscription, the target DB passes migration/RPC health checks, and the
  Pub/Sub emulator E2E passes.
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
