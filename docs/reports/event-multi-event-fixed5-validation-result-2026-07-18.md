# Multi-Event Fundamental/Technical Fixed-5 Validation Result

Date: 2026-07-18

Status: `INCONCLUSIVE`

## Decision

The one preregistered validation run completed. The primary 2M portfolio passed
the economic, drawdown, and matched-random thresholds, but opened `29` trades
against the preregistered minimum of `30`. The result is therefore
`INCONCLUSIVE`, not `PASS`.

Do not lower the sample threshold, inspect another exit variant on validation,
retune the selection rule, inspect locked OOS, or activate paper/live routing.
This candidate is frozen for the current data cycle.

The preregistration is
[event-multi-event-fixed5-validation-prereg-2026-07-18.md](event-multi-event-fixed5-validation-prereg-2026-07-18.md).

## Primary 2M Gate

Candidate:
`event_multi_event_fundamental_technical_fixed5_v0_research`

| Condition | Required | Result | Gate |
|---|---:|---:|---|
| Opened trades | `>= 30` | `29` | **INCONCLUSIVE** |
| Cost-adjusted net PnL | `> 0` | `+141,337` JPY | PASS |
| Profit factor | `> 1.2` | `2.037` | PASS |
| Maximum drawdown | `< 200,000` JPY | `41,059` JPY | PASS |
| Same-symbol random percentile | `>= 0.75` | `0.797` | PASS |
| Random unmatched / fallback | `0 / 0` | `0 / 0` | PASS |

The 2M maximum drawdown was about `2.05%` of starting capital. The selected
PnL exceeded the random p75 (`110,195` JPY) but not random p90
(`194,627` JPY).

## Capital Diagnostics

| Capital | Candidates | Opened | Net PnL | PF | Max DD | Random percentile |
|---:|---:|---:|---:|---:|---:|---:|
| 1,000,000 | 77 | 20 | 87,541 | 3.282 | 23,190 | 0.937 |
| 2,000,000 | 77 | 29 | 141,337 | 2.037 | 41,059 | 0.797 |
| 5,000,000 | 77 | 34 | 338,743 | 1.746 | 115,306 | 0.750 |

Random coverage was complete for all `77` candidates. Pool sizes ranged from
`1,015` to `1,227` eligible same-symbol dates across 300 seeds.

## Selection-Order Diagnostic

All six preregistered diagnostic orderings remained positive at 2M:

- net PnL range: `141,337` to `201,601` JPY;
- PF range: `1.988` to `2.329`;
- maximum drawdown range: `29,541` to `62,659` JPY.

This is encouraging robustness evidence, but it cannot override the minimum
sample contract.

## Frozen Outputs

- JSON:
  `out/event-research-multi-event-fixed5-validation-2026-07-18/portfolio-simulation.json`
  - SHA-256: `bbf20c0caa39a043dc9f71b00dd6b5350b209b3fd58f00881a2f53df6e50437b`
- trades CSV:
  `out/event-research-multi-event-fixed5-validation-2026-07-18/portfolio-trades.csv`
  - SHA-256: `22074dcc4e7c388e5c218784c30796323a897cf4daf03cb01950ccb7c2cf062c`

The run used the fixed validation boundary 2024-07-22 through 2025-06-20 and
did not request or include locked OOS.

The preregistered simulator hash
`01fd8e58dec95aee857735b1c7afd99285c3057273fe7c3ce9d2aeffe6878cfb`
is the exact code used for this run. Afterward, Ruff reformatted only the
multi-line return tuple in `random_candidate_pools`; no logic changed and the
validation was not rerun. The formatted working-tree simulator hash is
`5b4bc1c79c61b9e1f9ff8e53f05ab3dc344dcd955929f2d2ea2efac6bbe552d4`.

## Consequence

- Keep this candidate research-only and frozen for the current data cycle.
- Do not validate the fixed-2 alternative after seeing this result.
- Do not activate event paper or live publication.
- Continue the already approved 2M cluster-v1 shadow-forward evidence process.
- A future reconsideration requires genuinely new forward data and a separately
  preregistered cycle; it must not reuse this validation window as a tuning set.
