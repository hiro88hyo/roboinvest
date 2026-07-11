# Event Cluster Paper Observation Plan

Created: 2026-07-04

Status: Phase 0 was approved on 2026-07-04. On 2026-07-10 an external audit
found that operational detection depended on T+1 OHLCV and copied the future
open into the signal price and absolute stop. Phase 1 remains causal dry-run
detection only. A separate fresh-book paper publisher, the relative-stop/fill
contract, paper-only routing identity, and full local entry/partial/full-exit
E2E are now implemented as `opening_transport_stress_v1`. That E2E proves
transport, routing, idempotency, atomic persistence, and exit mechanics; it
does **not** reproduce the frozen `next_open_unconditional` / 20th-session-close
execution contract and is explicitly marked
`comparable_to_registered_backtest=false`. Phase 2 target activation remains
blocked until execution timing is aligned, the target database/RPC health and
managed subscription are verified, and the evidence mismatch below is
resolved. An environment flag alone never authorizes it.

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
| Matched random p75 or better | target p75 | Reported percentile: 1M `0.737`, 2M `0.853`, 5M `0.927`, but random rows used an 8% stop while selected rows used 10% | NOT COMPARABLE / BLOCKED |
| `same_symbol_random_date` percentile | `>= 0.65` | Same mismatched-stop percentile series | NOT COMPARABLE / BLOCKED |
| Execution stress does not materially break result | positive stressed result | entry10_exit25: 1M PF `1.784`, 2M `2.015`, 5M `2.655`; exit50: 1M PF `1.808`, 2M `1.975`, 5M `2.568` | PASS |
| Backtest data timing matches paper ordering | disclosure-time PIT features, next-open entry, 20th-session close exit | Detector now preserves the frozen disclosure-time feature vintage. The local fresh-ask/opening-exit E2E is a separate transport stress | SELECTION FIXED / EXECUTION REPRODUCTION PENDING |
| Prompt/model/feature schema can be frozen | required for AI path | This is LLM-free rule-only. Rule definition is frozen instead. | PASS BY SCOPE |

The earlier paper-observation argument is no longer sufficient: in addition to
the 1M p75 shortfall, the cited same-symbol random cohort used
`entry_price * 0.92` while selected observations used the frozen 10% stop. Do
not reinterpret those percentiles as gate evidence and do not inspect/rerun the
locked OOS window without the ADR-required approval. The simulator is corrected
for future runs, but that code fix does not retroactively validate the cited
locked report.

## Paper Observation Decision

Current decision:

- Do not start frozen-v1 paper observation with the current opening transport
  stress path.
- Do not treat transport-stress fills/PnL as the frozen candidate's paper/live
  evidence.
- Require next-open/20th-session-close implementation alignment and a valid
  matched-random comparison before a new activation decision.
- Keep all candidate parameters frozen.
- Do not inspect the frozen locked OOS window again.

Paper observation success and failure must be judged before any live discussion.
The initial observation horizon is the earlier of:

- 6 calendar months of eligible market days after activation, or
- 15 opened paper trades for this candidate.

Future frozen-v1 success criteria (not satisfied by the current stress E2E):

- Event detection, signal publication, Gateway approval/rejection, OMS Paper
  fill, position creation, and scheduled exit ordering are reproducible from
  logs.
- Every opened paper position has `holding_type=swing`, catastrophic
  `stop_loss_price`, `max_hold_days=20`, and a deterministic
  `scheduled_exit_date`.
- Entry fills are attributable to the intended next-session opening sequence,
  with entry slippage versus the publisher's selected fresh ask reported in
  basis points. The valuation reference remains non-executable context.
- Fixed-horizon exit fills are attributable to a close-session execution on the
  frozen 20th-session date; catastrophic stops and explicit no-fill/manual
  intervention remain separately attributable.
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

The detector path remains intentionally non-executable:

```text
J-Quants /fins/summary
  -> event cluster paper batch
  -> causal candidate/exclusion artifact
  -> production-preopen-check artifact validation
  -> watchlist market-data capture
  -> no signal or order publication
```

The separately gated path below is an operational stress profile, not frozen-v1
execution reproduction:

```text
schema v2 causal artifact + dedicated event-paper-raw-books
  -> strategy_rule event-paper-publish (opening_transport_stress_v1)
  -> durable first-quote claim + single-attempt CAS journal + separate receipt
  -> strategy-signals-a (RULE / SWING / PAPER_ONLY)
  -> Aggregator
  -> Gateway
  -> paper-orders only
  -> OMS Paper atomic fill + fill-anchored stop + scheduled exit
```

It uses the existing Pub/Sub route and never calls a downstream service
directly or bypasses Gateway. Gateway remains the only risk executor for 2%
rule sizing, lot calculation, kill switch state, duplicate-position checks,
and paper/live routing.

### Timing

Current causal dry-run sequence:

1. After the signal date has ended in JST, and before 09:00 JST on the next TSE
   business day, fetch that date's `/fins/summary` disclosures with
   exporter-recorded receipt provenance. Evaluate the frozen cluster v1 rule
   at each disclosure's original `data_available_at/feature_cutoff_at`; the
   later local `source_received_at` must not advance that feature vintage.
   Write candidate and exclusion output without consulting T+1 OHLCV. If the
   latest expected signal-date bar is absent after close, preserve the frozen
   selection with `feature_data_complete=false`; pre-open/watchlist/publisher
   reject execution rather than changing the research cohort.
2. Next trading day pre-open preparation: ensure event symbols are in the
   watchlist for market-data capture only after
   `production-preopen-check.py --swing-candidates-json` validates causality,
   dates, receipt provenance, and the absence of executable price fields.
3. In the current transport stress, process all due paper swing exits before
   entry. This capital-release ordering differs from the frozen 20th-session
   close contract and cannot count as v1 evidence.
4. At 09:00–09:30 JST, the separately gated publisher targets only
   `event-paper-raw-books` and accepts a best ask whose `received_at` is at most
   10 seconds old (future skew at most 5 seconds). The detector's own
   `--publish-paper` remains fail closed.
5. Claim the exact quote in `strategy_logs`, ack its raw book, recheck readiness,
   and atomically mark one attempt immediately before emitting the immutable
   PAPER_ONLY stress signal through Aggregator/Gateway/OMS Paper. The external
   Pub/Sub RPC is never retried. A success checkpoint yields a confirmed
   digest-bound receipt; an attempt without a checkpoint is retained as
   ambiguous and is never republished. Every receipt/report records
   `execution_profile=opening_transport_stress_v1` and
   `comparable_to_registered_backtest=false`.
6. Reconcile the receipt and downstream lineage in the observation report.

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

Identity-bearing event signals also carry `routing_intent=PAPER_ONLY`, the
separate execution key
`<frozen-selection-key>__opening_transport_stress_v1`, and a per-occurrence
`candidate_id` (the detector cluster/observation identity). This prevents the
stress path from being mistaken for frozen-v1 evidence while isolating the
Aggregator pairing bucket. Strategy, unified, and order IDs are deterministic
under redelivery. Gateway rejects PAPER_ONLY in live mode, and the OrderRequest
contract cannot represent PAPER_ONLY with `trade_mode=live`.

Live Feeder books carry `OrderBookSnapshot.received_at` separately from kabu's
`CurrentPriceTime`. OMS Paper evaluates freshness against its wall clock,
rejects excessive future skew, and requires `received_at` unconditionally for
PAPER_ONLY orders. `event-paper-raw-books` is defined as a dedicated filtered
subscription so the one-shot publisher does not consume another service's
stream.

The candidate artifact is intentionally non-executable. It contains the
valuation reference and frozen `CAT_STOP_PCT=-0.10`, but contains neither an
entry-price assumption nor an absolute `stop_loss_price`. In the publisher,
that frozen strategy value maps to the contract's positive
loss-distance representation `stop_loss_pct=0.10`.

The local plumbing and safety verification gates are complete:

- the publisher uses the dedicated subscription, fresh observed ask,
  paper/allowed/RPC/due-exit preflight, PAPER_ONLY identity, double latch, and a
  durable claim-before-publish protocol. It executes exactly one occurrence per
  invocation; multi-candidate artifacts require an explicit occurrence ID and
  separate receipt paths. A body-based CAS RPC owns the single external
  attempt, and Pub/Sub success is checkpointed back into the claim, allowing an
  atomic confirmed or ambiguous stress receipt to be reconstructed without
  another publish. Same-filesystem-namespace invocations are locked; operations
  must also use one designated coordinator because the cursor is shared;
- the real emulator + PostgREST path verifies publisher redelivery,
  Aggregator/Gateway duplication, exactly one BUY fill, fill-anchored stop,
  scheduled partial/full SELLs, position deletion, and no live message; and
- CI runs that focused path on pull requests alongside the actual atomic RPC
  tests.

These changes do not authorize target publication. Frozen-v1 activation first
requires an execution path matching next-open entry and a close-session exit on
the frozen 20th-session date, plus a valid matched-random comparison. Database
migrations/RPC health and the managed dedicated subscription remain additional,
not sufficient, gates.

OMS Paper atomic persistence is implemented: all fill paths use one
`oms_paper_apply_fill` transaction with order-ID idempotency, symbol-level
serialization, authoritative position results, rollback on trade failure, and
explicit partial-exit handling. Actual local PostgREST RPC tests cover these
properties; this completed item does not authorize publication by itself.

Until those target conditions are met, the command remains operationally
blocked even though its implementation exists.

### Aggregator

Aggregator already supports single-source passthrough after the pairing window:
RULE-only input becomes `signal_source=RULE` when confidence is above
`MIN_CONFIDENCE_RULE_ONLY`. No consensus requirement is needed for this paper
candidate.

Locally verified acceptance checks:

- RULE-only event signal emits exactly one `UnifiedTradeSignal`.
- `holding_type=swing`, relative stop intent, `max_hold_days`, and
  `scheduled_exit_date` survive aggregation; contract tests now cover this
  passthrough.
- Below-threshold confidence is rejected by existing source-specific threshold
  behavior.
- Candidate-specific strategy isolation and deterministic IDs prevent a
  redelivery or an unrelated AI/day signal from changing the unified result.

These checks are evidence, not target authorization.

### Gateway

Gateway must remain unchanged in responsibility:

- read kill-switch/system status
- reject if trading is disabled
- reject if the signal is stale
- reject duplicate same-symbol long positions
- calculate lot size and enforce risk limits
- route by `system_status.trade_mode`

The current detector refuses all publication. The separate publisher refuses
to publish unless `system_status.trade_mode=paper` and trading is allowed, then
rechecks mode after its durable claim and immediately before publish. That
preflight is not sufficient by itself: it emits explicit `PAPER_ONLY`, and
Gateway enforces it again at routing time. Gateway also rejects a live BUY
carrying a relative stop, but that guard does not replace the broader routing
intent.

### OMS Paper

OMS Paper already supports swing positions, absolute-stop monitoring, and
`opening-swing-exits` for its current fixed-hold stress path. The order path now
derives a new
position's `holding_type` from `OrderRequest`, resolves an absolute stop from the
actual BUY fill, and carries `max_hold_days` and `scheduled_exit_date`. Its 14:50
day closeout ignores swing positions.

OMS Paper persists `trades_paper` and the corresponding `positions` mutation in
one idempotent transaction. The emulator E2E now covers new entry, redelivery,
partial/full exit, and scheduled opening-exit ordering. These checks do not
prove the frozen 20th-session-close contract. Before any frozen-v1 activation,
add and verify a propagated close-session exit profile; migration/RPC health is
necessary but not sufficient. Its PAPER_ONLY path requires a fresh
wall-clock-checked `received_at`.

## Operational Timeline

```text
T day through 23:59 JST
  J-Quants disclosures accumulate; no final zero-candidate conclusion is made

After T day ends, before next TSE business day 09:00 JST
  exporter records the complete signal-date response and receipt provenance
  event batch detects cluster v1 candidates without consulting T+1 OHLCV
  schema v2 causal dry-run artifact is written; detector cannot publish
  production-preopen-check validates the candidate artifact
  event candidates are added to watchlist for data capture
  all due swing exits complete before entry preflight

T+1 09:00-09:30 JST (transport stress only; target use is not authorized)
  Feeder captures fresh market data for candidate symbols
  dedicated publisher targeted-seeks only event-paper-raw-books
  one explicit occurrence is selected for this invocation
  paper/RPC/exit preflight passes; first fresh ask is durably claimed
  RULE/SWING/PAPER_ONLY stress signal follows Aggregator -> Gateway -> OMS Paper
  receipt records opening_transport_stress_v1 / comparable=false

After execution
  observation report joins receipt signal ID to unified signal and paper fills
  fill-anchored stop and opening-exit mechanics are reconciled as plumbing stress
```

## Observation Log Design

The paper observation report needs enough information to reconcile each
selection, publication, and trade without reintroducing a future-price
assumption:

- `candidate_id`
- event cluster ID and source event IDs
- symbol and symbol name
- disclosure-time `data_available_at/feature_cutoff_at` and later local
  `source_received_at` for each source event
- signal generation timestamp and intended entry date
- rule pass/fail fields: earnings present, dividend increase present, minimum
  forecast PER, missing PER treatment
- exclusion reason for every detected but unpublished candidate
- publication status (`confirmed` or `ambiguous`), durable attempt ID/time, and
  confirmed Pub/Sub message ID/time only when checkpointed
- execution profile and `comparable_to_registered_backtest` (currently false)
- signal ID, unified signal ID, order ID, and paper trade IDs
- valuation reference price, bar date, and availability timestamp; this is not
  an executable entry price
- fresh observed entry price and timestamp after the execution path is restored
- paper fill price, quantity, fill timestamp, fill reason, and slippage bps
- relative stop intent, fill-anchored absolute stop loss price, max hold days,
  and scheduled exit date
- exit trigger (`opening_max_hold_days` stress, `stop_loss`, manual/no-fill)
- exit fill price and exit slippage bps
- open-position status and unrealized PnL for still-open positions

Write observation artifacts to `out/event-paper-observation/` locally and, when
the batch is connected to Supabase/PubSub, ensure the same IDs can be joined
from `aggregator_logs`, `trades_paper`, and `positions`. The reporter consumes
the separate publication receipt, verifies its exact artifact digest and
occurrence coverage, and never treats an unrelated same-symbol BUY or later
position generation as event evidence.

## Phase Plan After Approval

Phase 1:

- Implemented daily event detection batch in dry-run mode only:
  `scripts/detect-event-cluster-paper-candidates.py`.
- Reuses `event_research_common` cluster rule helpers rather than duplicating
  selection code.
- Added a 1-week J-Quants-shaped fixture test covering PASS, PER-guard
  exclusion, missing-PER PASS, and `--signal-date` filtering, plus a regression
  proving next-morning receipt does not replace the disclosure-time PER vintage.
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

- The detector's `--publish-paper` remains fail closed before any Supabase or
  Pub/Sub side effect. A separate `strategy_rule event-paper-publish` command
  is implemented with an explicit CLI latch plus
  `EVENT_CLUSTER_PAPER_PUBLISH_ENABLED=true`.
- The only callable stress configuration is explicit `--no-seek` with a
  loopback Pub/Sub emulator, a loopback Supabase URL, and an allowlisted
  development project ID. Managed Pub/Sub, remote emulators, and cloud Supabase
  are rejected before network clients open; Supabase and emulator gRPC proxy
  inheritance are off.
- The relative-stop contract, `OrderRequest` holding metadata, fill-anchored
  paper stop, day/swing closeout isolation, receive provenance, PAPER_ONLY
  enforcement, deterministic IDs, strategy isolation, and atomic OMS Paper
  persistence are implemented.
- The dedicated fresh-book publisher and complete local Pub/Sub/Supabase E2E,
  including redelivery and scheduled partial/full opening exits, pass under the
  separate `opening_transport_stress_v1` identity.
- Target activation remains blocked on next-open/20th-session-close alignment,
  valid matched-random evidence, target DB migration/RPC health, and managed
  dedicated subscription verification.
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
  `positions` through the receipt's deterministic signal ID.
- `--publish-receipt-json` validates artifact SHA-256, target date, complete
  `execution_candidate_id` coverage, fixed topic/strategy, and deterministic
  signal IDs. It distinguishes confirmed delivery from
  `publication_ambiguous` without authorizing a resend. Null-lineage
  scheduled/stop SELLs are attributed only after an exactly linked BUY and
  before a later BUY generation.
- Reports expose `execution_profile=opening_transport_stress_v1` and
  `comparable_to_registered_backtest=false`; their PnL/trades cannot satisfy
  frozen-v1 paper/live gates.

## Explicit Non-Goals

- No live enablement.
- No change to OMS Live or `live-orders`.
- No new locked OOS inspection.
- No retuning of PER, dividend, exit horizon, stop, or technical veto values.
- No AI prompt/model/schema changes.
- No direct service-to-service order routing.
