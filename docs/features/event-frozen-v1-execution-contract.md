# Event Frozen-v1 Paper Execution Contract

Date: 2026-07-12

Status: Frozen implementation contract; target publication remains prohibited.

## Scope

This contract defines the paper execution profile for
`event_cluster_earnings_dividend_value_guard_fixed20_stop_v1_research`.
It does not authorize target publication, reopen locked OOS, or change the
registered selection rule, cost, exit horizon, or catastrophic stop.

The existing `opening_transport_stress_v1` profile remains a transport and
idempotency test. Its fills and PnL remain
`comparable_to_registered_backtest=false`.

## Frozen Entry Contract

- Eligible date: the candidate artifact's `entry_date`, which must be the next
  TSE business day after `signal_date`.
- Entry capture window: `09:00:00 <= received_at < 09:01:00 JST`.
- Quote: the first valid, fresh `OrderBookSnapshot` for the selected occurrence
  observed in that window.
- Order reference price: the selected snapshot's best ask.
- The publisher must not replace a missing opening-window quote with a quote at
  or after `09:01:00 JST`.
- The publisher must not seek past the opening sequence and reinterpret a later
  quote as the opening quote.
- A missing, stale, future-dated, crossed, locked, empty, or incomplete opening
  book is an execution miss. It is not a strategy exclusion and must remain in
  the observation report denominator.
- Publication remains one occurrence per invocation, `PAPER_ONLY`, and requires
  the existing durable claim and single-attempt publication journal.

The observed ask is an executable paper reference, not the registered
`next_open_unconditional` price. The official session open must be attached
later from point-in-time daily OHLCV and the entry difference reported in basis
points:

```text
entry_slippage_vs_official_open_bps =
    (paper_entry_fill / official_open - 1) * 10,000
```

Until that reconciliation exists, a receipt must not claim full registered
backtest comparability.

## Frozen Risk And Holding Contract

- `holding_type=swing`
- `stop_loss_pct=0.10`
- The absolute stop is derived once by OMS Paper from the actual BUY fill.
- `max_hold_days=20`
- `scheduled_exit_date` is derived from the actual BUY fill using the TSE
  session calendar.
- `scheduled_exit_time=15:30:00 JST`
- Day closeout must not close this position.
- A stop exit and a scheduled exit must retain distinct exit reasons.

## Frozen Exit Contract

- On the 20th scheduled TSE session, the position remains open before 15:30 JST.
- At or after 15:30 JST, OMS Paper uses the first valid bounded fresh exit book.
- If no eligible close-session book arrives within the bounded retry policy,
  record a no-fill/manual-intervention outcome. Do not silently substitute an
  earlier price or move the scheduled date.
- Report exit slippage against the official 20th-session close when that daily
  bar becomes causally available.

## Comparability Gate

The frozen-v1 receipt/report may set
`comparable_to_registered_backtest=true` only when all of the following are
demonstrated together:

1. selection uses only the frozen disclosure-time feature vintage;
2. entry follows the opening-window contract above;
3. the actual fill anchors the 10% stop;
4. the position exits on the 20th session at the close-session profile unless
   the catastrophic stop fired first;
5. official-open and official-close reconciliation is present;
6. costs, no-fills, misses, and slippage remain in the report;
7. matched-random evidence uses the same 10% stop and execution assumptions.

Implementation or local E2E success alone does not satisfy item 7. Rerunning or
inspecting the frozen locked-OOS window still requires the explicit approval
defined by ADR-0005.

## Required Implementation Sequence

1. Add a distinct frozen-v1 execution profile; do not mutate the transport
   stress identity.
2. Enforce the one-minute opening capture window in configuration, claim
   validation, recovery, and final publication preflight.
3. Preserve `comparable_to_registered_backtest=false` in the initial receipt.
4. Extend the observation report with opening/closing reference prices,
   slippage, opening misses, close no-fills, and exit reason.
5. Prove the complete path with local Pub/Sub and Supabase E2E before any
   managed-Pub/Sub enablement is reviewed.

Prospective causal artifacts and their pending outcomes are chained using the
[Event Forward Evidence Ledger Protocol](event-forward-evidence-ledger.md).
