# Liquidity Improvement V0 Development Result

Date: 2026-08-08

Decision: **DEVELOPMENT FAIL — candidate frozen; validation and locked OOS prohibited.**

Candidate: `liqimp1m_logdiff_v0_research`

Evidence class: `PAPER_INSPIRED_NOT_REPLICATION`

This result is research-only. It does not authorize paper/live routing and does not count as
evidence for the 2026-09-30 project kill switch. The existing project strategy and kill-switch
contract are unchanged.

## Registered Gate Result

| Gate | Observed | Required | Pass |
|---|---:|---:|:---:|
| Opened trades | 141 | at least 100 | yes |
| Base profit factor | 0.3677 | greater than 1.2 | no |
| Daily MTM max drawdown | 58.55% of starting capital | less than 10% | no |
| Stress profit factor | 0.3674 | greater than 1.0 | no |
| Positive calendar years | 1 / 4 = 25% | at least 75% | no |

The candidate passed only the minimum trade-count gate. The frozen conjunction therefore failed.

## Development Metrics

Base cost is 0.149% per side:

- starting capital: JPY 2,000,000;
- opened trades: 141;
- net PnL: JPY -1,061,841.03;
- ending equity: JPY 938,158.97;
- profit factor: 0.367689;
- maximum daily mark-to-market drawdown: JPY 1,170,951.05, or 58.55% of starting capital;
- winning/losing trades: 29 / 112;
- exits: 75 scheduled close, 62 intraday stop, 4 gap stop.

Stress cost is 0.250% per side:

- opened trades: 139;
- net PnL: JPY -1,075,531.70;
- profit factor: 0.367441;
- maximum daily mark-to-market drawdown: JPY 1,194,225.63, or 59.71%.

Base calendar-year net PnL was negative in 2021, 2022, and 2024 and positive only in 2023.

## Boundary And Audit

- development top-20 candidate-pool rows: 11,731;
- rows excluded because the full 20-session outcome crossed the split end: 356;
- boundary-complete candidate rows: 11,375;
- output contains 141 base trades and 709 daily equity observations;
- all entry opens, scheduled closes, stop fills, 100-share entry lots, 20-session dates,
  20%-of-capital sizing, 1%-of-turnover sizing, costs, same-symbol non-overlap, PnL sums, final
  equity, and five-position maximum passed an independent output audit;
- `validation_outcomes_inspected=false`;
- `locked_oos_outcomes_inspected=false`.

Authoritative local result:
`out/liquidity-improvement-v0-development-2026-08-08/development-result.json`

Result SHA-256:
`63386c7c442ced2b478cf682434e3d3e66811154e108c176e8ecfd76b0066bc1`

Run registration SHA-256:
`751ff4d913c9f296ee9047762ddd81b0a579a17b666f42fd547e2baead18f0b0`

## Operational Fail-closed Record

The initial process attempt produced no result artifact and stopped on source Float64 precision in
`AdjFactor=0.3333333333333333`. Before any metric was emitted or inspected, a corrective simulator
was separately hash-bound. It rounds an adjusted share count only when it is within `1e-8` shares
of an integer; genuine fractional shares still fail closed. Synthetic tests cover both cases. No
strategy parameter, feature, selection, price, stop, cost, split, or gate was changed.

## Decision Contract Applied

Under the preregistered development-fail clause, this candidate ID is frozen. Do not inspect its
validation outcomes and do not alter its window, sign, threshold, stop, or costs under the same
candidate ID. Any future liquidity research would require a distinct hypothesis and candidate ID,
registered without using validation or locked-OOS outcomes from this candidate.
