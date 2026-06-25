# ADR-0004: Event AI Swing Research Gates

Date: 2026-06-25

Status: Accepted

## Context

OHLCV-only swing candidates are frozen as rejected research baselines:

- `daily_trend_pullback_v0` through `daily_trend_pullback_v5`
- `daily_trend_pullback_exit_fixed10_v0`
- `daily_trend_pullback_fixed10_hash_v0`
- `daily_trend_pullback_fixed10_hash_v1_operational`
- `daily_breakout_continuation_v0`
- `daily_volatility_contraction_v0`
- `daily_volatility_contraction_hash_basket_v1`

They must not be revived by relaxing gates. The primary rejection reason is not
only PF/DD. Their selector, timing, and exit components did not reliably beat
matched random baselines, especially symbol/date matched baselines.

The next research direction is event-driven swing research that separates:

- event / fundamental catalyst
- fundamental and valuation context
- technical veto / sizing / exit context
- AI structural event labeling

AI labels must not directly emit BUY/SELL/HOLD or `StrategySignal`.

## Decision

Keep the project kill switch unchanged and add three earlier gates for event
research. These gates are additional filters, not replacements for the final
live acceptance criteria in `AGENTS.md`.

## Research Continuation Gate

Temporary minimum requirements:

- aggregate OOS net PnL > 0
- aggregate OOS PF > 1.05
- aggregate OOS max DD < capital * 0.15
- selected result at or above random median
- `symbol_matched_random_date` percentile > 0.50
- explainable event / fundamental / timing alpha hypothesis
- sample count, confidence interval, and period stability reported

Passing this gate only allows continued research. It does not allow paper
observation.

## Paper Observation Gate

Temporary minimum requirements:

- aggregate OOS PF > 1.10
- target PF is 1.15
- aggregate OOS max DD < capital * 0.12
- matched random p75 or better
- `symbol_matched_random_date` percentile >= 0.65
- execution stress does not materially break the result
- backtest data timing matches paper operational ordering
- prompt, model, and feature schema can be frozen

Passing this gate only allows paper observation. It does not allow live.

## Live Candidate Gate

The project kill switch remains unchanged:

- OOS PF > 1.2
- OOS max DD < capital * 0.10
- parameters and costs pre-registered
- materially above matched random baselines
- reproduced in paper observation

## Low-Frequency Block Diagnostics

For low-frequency event strategies, each 60-trading-day block is not required to
individually pass PF > 1.2. Aggregate deployment gates and block diagnostics are
reported separately.

Required block diagnostics:

- positive block ratio
- worst block PnL
- median block PnL
- block DD
- event count per block
- selected random percentile per block

These diagnostics may explain instability, but they must not hide aggregate
gate failure.

## Consequences

- Existing OHLCV-only candidates remain frozen rejected baselines.
- Event research reports must compare layers independently before building a
  combined score.
- AI labels are research features and must stay separate from production
  StrategySignal routes.
- Locked OOS should not be repeatedly inspected while prompt/schema/model
  selection is still changing.
