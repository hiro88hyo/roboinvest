# Event Cluster Matched-Random Corrective Inspection — 2026-07-12

## Decision

The approved one-time locked-OOS corrective inspection completed. It fixes only
the historical 8%/10% catastrophic-stop mismatch in the portfolio-level
`same_symbol_random_date` comparison.

The result does **not** clear the paper-observation gate. The 1M portfolio,
which is the conservative capital case, reached matched-random percentile
`0.713` and did not reach p75. Target publication remains prohibited.

## Approval And Scope

Approval is recorded in
[ADR-0005](../adr/0005-locked-oos-inspection-freeze.md). The run was limited to:

- candidate:
  `event_cluster_earnings_dividend_value_guard_fixed20_stop_v1_research`
- frozen locked-OOS split ending 2026-06-23
- unchanged `next_open_unconditional` entry
- unchanged 20-session exit and 10% catastrophic stop
- unchanged round-trip cost `0.00298`
- unchanged portfolio constraints and `feature_time_symbol` selection order
- true `same_symbol_random_date`, 300 seeds
- capital 1M, 2M, and 5M JPY

No threshold, cohort, exit, cost, or strategy parameter was changed.

## Result

| Capital | Opened | Selected net PnL | PF | Max DD | Random median | Random p75 | Selected percentile | p75 gate |
|---:|---:|---:|---:|---:|---:|---:|---:|---|
| 1,000,000 | 9 | 44,936 | 2.036 | 41,194 | 12,601 | 53,522 | 0.713 | FAIL |
| 2,000,000 | 15 | 197,617 | 2.193 | 117,894 | 41,923 | 142,264 | 0.837 | PASS |
| 5,000,000 | 17 | 810,179 | 2.904 | 161,253 | 160,385 | 415,288 | 0.937 | PASS |

Random coverage was complete: 22 matched candidates, zero unmatched, zero
fallbacks.

The selected portfolio metrics are unchanged from the historical calculation;
only the corrected matched-random distributions and percentiles are new.

| Capital | Historical invalid percentile | Corrected percentile |
|---:|---:|---:|
| 1,000,000 | 0.737 | 0.713 |
| 2,000,000 | 0.853 | 0.837 |
| 5,000,000 | 0.927 | 0.937 |

## Frozen Inputs And Outputs

Inputs:

- observations SHA-256:
  `4e8cefbfb0521d50ea00a0c9742e1e56746f7a4dec79eb6d5b6ac67ce2e3c63c`
- daily OHLCV SHA-256:
  `74b2a6449e11d1a9c0115f5328bdc36b081a7ccd9437a80229373bc36e962166`
- simulator SHA-256:
  `c2d1b12c1848b6e6c1b255ff5dab9cd58f166295d85f74d0477b0c8f48b26ce0`

New, non-overwriting outputs:

- `out/event-research-cluster-rule-diagnostics/locked-oos-value-guard-fixed20-stop-portfolio-matched-random-10pct-approved-20260712.json`
  SHA-256 `d29dcdb936af2fbbae6482cb3d89e3b3fa15703c6fe8bc83d324be1f99616516`
- `out/event-research-cluster-rule-diagnostics/locked-oos-value-guard-fixed20-stop-portfolio-matched-random-10pct-approved-20260712.csv`
  SHA-256 `2097e21623bbdd0912a4d91eb6d38f99f82270a59bb38db37f0605d2e4fc0f84`

## Consequence

- Keep `frozen_opening_close_v1` disabled for target publication.
- Do not rerun or retune against this locked-OOS window.
- Preserve the local execution E2E as infrastructure evidence only.
- The next economic evidence must come from forward data under the frozen
  strategy, or from a separately preregistered strategy cycle.
