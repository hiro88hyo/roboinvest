# ADR-0006: Primary Evaluation Capital Amendment

Date: 2026-07-12

Status: Accepted, cooling-off in progress. Effective no earlier than 2026-07-19
JST.

## Context

The frozen event-cluster portfolio is sensitive to Japanese 100-share lot
granularity and the 20% maximum-notional-per-position constraint.

In the corrected locked-OOS calculation:

| Capital | Opened | Lot skips | Position-cap skips | Net PnL | Return on capital | PF | DD ratio | Matched-random percentile |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 1M | 9 | 13 | 0 | 44,936 | 4.49% | 2.036 | 4.12% | 0.713 |
| 2M | 15 | 6 | 1 | 197,617 | 9.88% | 2.193 | 5.89% | 0.837 |
| 5M | 17 | 0 | 5 | 810,179 | 16.20% | 2.904 | 3.23% | 0.937 |

At 1M, 13 of 22 selected candidates could not open a single board lot under
the frozen sizing rules. The 1M matched-random failure therefore mixes strategy
quality with a capital/lot feasibility constraint.

## Decision

Adopt 2M JPY as the primary evaluation capital for this candidate.

- 2M is the pass/fail capital for the paper-observation research gate.
- 1M remains a mandatory small-capital sensitivity diagnostic, but is no longer
  a veto on a strategy intended for 2M deployment.
- 5M remains a capacity and concentration diagnostic; it is not the assumed
  initial live allocation.
- All existing strategy parameters, costs, 20% position cap, five-position cap,
  100-share lot, exit horizon, and 10% stop remain unchanged.
- This amendment does not itself authorize paper publication or live trading.
- Official-open/close reconciliation, forward evidence, paper execution
  reproduction, and the project kill switch remain required.

Because this changes a gate after results were observed, a one-week cooling-off
period applies. No activation decision may take effect before 2026-07-19 JST.

## Capital-Efficiency Interpretation

Among the three tested points, 5M has the highest observed return on capital and
lowest drawdown ratio. Marginal selected PnL was approximately:

- 1M to 2M: +152,681 JPY on +1M capital, or 15.27%;
- 2M to 5M: +612,563 JPY on +3M capital, or 20.42%.

This does not prove that efficiency rises monotonically between 2M and 5M. The
portfolio composition changes as high-priced board lots become feasible, while
the five-position cap causes more capacity skips at 5M. No new locked-OOS
capital grid is authorized by this ADR.

The apparent efficiency is also concentrated: the largest winning trade
accounts for about 64% / 67% / 70% of total selected PnL at 1M / 2M / 5M.
Therefore 5M is the best observed point, not a reliable optimum or a basis for
immediate capital scaling.

## Consequences

After cooling-off, cluster v1 may be described as passing the corrected
matched-random capital gate at its adopted 2M primary capital. It must still be
described as a multi-look survivor with only 15 opened locked-OOS trades.

Any initial live-capital proposal requires a separate authorization and staged
loss budget. The project-level deadline and
`profit_factor > 1.2` / maximum drawdown below 10% contract are unchanged.
