# IMOM6M top-5 fixed-20 V0: Gate A development result

Date: 2026-08-08  
Candidate: `imom6m_top5_fixed20_v0_research`  
Decision: `GATE_A_FAIL_CANDIDATE_FROZEN_GATE_B_AND_LATER_SPLITS_PROHIBITED`

## Outcome

Gate A failed. The candidate is frozen at the development source-structure
diagnostic and did not advance to Gate B, validation, or locked OOS.

| Registered check | Result | Gate |
|---|---:|---:|
| Complete formation months | 5 of 28 attempted | FAIL: at least 24 |
| Mean decile-10 return | 3.5350% | PASS: `> 0` |
| Mean decile-10 minus decile-1 return | -1.0583% | FAIL: `> 0` |
| Mean monthly rank IC | -0.03249 | FAIL: `> 0` |
| Mean spread, first half | 0.1751% | PASS: `> 0` |
| Mean spread, second half | -1.8805% | FAIL: `> 0` |
| Mean spread after largest month removal | -2.0844% | FAIL: `> 0` |

The five complete formation dates were 2022-05-31, 2022-06-30, 2022-09-30,
2022-10-31, and 2024-01-31. These five-month metrics are secondary evidence;
the diagnostic already fails because the required 24 complete months were not
available.

## Missing-outcome boundary

Before outcomes were read, the authorization fixed the following fail-closed
rule: every formation-time eligible symbol must have positive adjusted closes
on both the exact formation month-end and exact next global TSE month-end. A
prior or later close is never substituted, and a symbol is not removed merely
because its outcome is missing.

Twenty-three months were incomplete under that rule. They contained 57 missing
symbol outcomes in total, with one to five missing outcomes per affected month.
Recomputing with complete cases only would be an unregistered post-outcome rule
change and is prohibited.

## Scope and audit

The evaluator read price partitions only through the development end date,
2024-06-28. It did not calculate Gate B trades, PnL, profit factor, drawdown,
validation outcomes, or locked-OOS outcomes. Individual symbol outcomes were
not persisted.

The consistency audit passed:

- all 28 formation dates were unique and chronological;
- valid plus missing outcomes equaled the eligible count in every month;
- all complete months had zero missing outcomes;
- all incomplete months had the preregistered exact-endpoint reason;
- aggregate means, chronological halves, largest-spread removal, gates, and
  artifact hashes were independently recomputed from the result;
- the output contains only `gate-a-result.json` and `run-manifest.json`.

| Artifact | SHA-256 |
|---|---|
| Gate A authorization | `06fd36c0cb55b9ffc8b7844763cd05e26b59add9660068a3d6b7548cc54d4aef` |
| Evaluator | `6134b04dee9b5f6f68574d322661192f15b8c3ddbbf9f6c23f3af724aec862d3` |
| Gate A result | `ac09dd99f972b706b7ac0eb565046830743c5bc29ba4e36421a988d91483d045` |
| Run manifest | `942df7281d364b27e51d2db51dd28fbeb07eff0c356f82d99ed07ccb4fd0ac87` |
| Final disposition | `02543c2dfefc0f40957e36c158b2443b202774346677804f816329d5045f7fb9` |

Repository verification passed: `make lint-all`, 1,637 Python tests (29
environment-dependent skips, 86% coverage), and 47 Dashboard tests.

## Research-cycle closure

This was candidate 2 of the preregistered maximum of 2. Candidate 1, LIQIMP,
was already rejected in development, and candidate 2 is now rejected at Gate A.
`cross_sectional_adaptation_v0` is therefore closed. IMOM12M, skip-month, sign
reversal, quantile changes, regime filters, HTP, quality, and value combinations
are not authorized as immediate rescue variants.

This result is separate from the 2026-09-30 project kill switch and does not
change its criteria, deadline, or current live strategy.
