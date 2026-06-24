# ADR-0003: Strategy Layer Rebuild After Intraday Edge Failure

Date: 2026-06-24

Status: Accepted

## Context

The June 2026 paper and archive reviews showed that the current intraday BUY
strategy stack is not robust enough to continue paper/live trading.

Observed failures:

- 2026-06-23 paper trading closed 21 trades with realized paper PnL
  `-12,500 yen`.
- Recent paper observations around 2026-06-16, 2026-06-19, 2026-06-22,
  and 2026-06-23 were materially negative in aggregate.
- 2026-06-24 out-of-sample archive replay rejected `relative_momentum`.
- Replacement intraday hypotheses such as VWAP reclaim, oversold reclaim,
  RSI/VWAP recovery, and RSI+MACD reversal did not survive OMS Paper replay
  after costs, fills, and stress checks.
- Raising BUY aggressiveness by `+1 tick` often improved fill rate but worsened
  realized PnL, which means the issue is not simply passive execution.
- Feature-level forward returns repeatedly looked better than OMS-realizable
  results, so feature-level success is not sufficient evidence.

The project kill switch remains unchanged: by 2026-09-30, accepted
out-of-sample strategies must meet `profit_factor > 1.2` and
`max_drawdown < capital * 0.10` using pre-registered strategy parameters and
cost assumptions.

## Decision

Stop treating the current intraday strategy stack as a live or paper candidate.

The strategy layer is now considered a rebuild target:

- Keep production/paper `strategy-rule` BUY disabled unless a new strategy
  passes explicit acceptance gates.
- Do not revive the old RULE BUY stack, AI judge entry stack, or rejected
  intraday replacement plugins by small threshold changes.
- Shift new research away from small intraday reversal/momentum edges and
  toward strategies with wider expected move and longer holding horizon, such
  as daily OHLCV based swing candidates.
- Require OMS-realizable validation before any paper/live route is enabled.

Infrastructure remains valid and should be reused:

- feeder
- feature-engine data pipeline
- Pub/Sub service boundaries
- aggregator
- gateway risk checks
- oms-paper / oms-live
- closeout and kill-switch controls
- Supabase/Dashboard observability
- archive replay, random baseline, and replay acceptance gates

## Required Acceptance Gates

Any new strategy candidate must pass, at minimum:

- multi-day replay, not a single favorable day
- total net PnL above zero after OMS Paper costs
- enough closed trades to avoid tiny-sample acceptance
- positive day count threshold
- no-fill rate threshold
- stress replay such as more aggressive BUY pricing
- random baseline comparison under comparable execution constraints
- documented parameter set before out-of-sample evaluation

The helper `scripts/check-replay-report-set.py` is the current multi-day
acceptance gate for OMS Paper replay sets.

## Consequences

- Production remains intentionally no-op for strategy-rule BUY while the
  rebuild is underway.
- The next strategy implementation should be a new candidate, not another
  parameter tweak of the rejected intraday family.
- Historical rejected experiments are documented in
  `docs/handoff/2026-06-24-relative-momentum-failure.md`, but rejected
  strategy plugin code is not kept in the production strategy registry.
- If a future intraday idea is revisited, it must start from a new hypothesis
  and pass the same acceptance gates; it is not grandfathered by earlier
  partial feature-level positives.
