# Event AI Smoke Diagnostics

Date: 2026-06-27
Branch: `strategy/swing-rebuild`

This follow-up decomposes the fixed balanced500 local LLM smoke run by event
type, event subtype, AI label fields, and point-in-time feature buckets. It is
diagnostic only. It does not change the prompt, thresholds, paper routing, live
routing, or the locked OOS policy.

## Artifacts

- Real labels:
  `out/event-ai/smoke-diagnostics-balanced500-fixed-seed1-gemma4-seed1.json`
- Real CSV:
  `out/event-ai/smoke-diagnostics-balanced500-fixed-seed1-gemma4-seed1.csv`
- Bundle placebo labels:
  `out/event-ai/smoke-diagnostics-balanced500-fixed-seed1-bundle-placebo-gemma4-seed1.json`
- Bundle placebo CSV:
  `out/event-ai/smoke-diagnostics-balanced500-fixed-seed1-bundle-placebo-gemma4-seed1.csv`

## Mechanical Findings

On the real fixed cohort, all smoke semantic checks failed:

- `event_plus_ai fixed_20d` did not beat `event_plus_technical fixed_20d`.
- `fundamental_direction=positive` did not beat `negative`.
- `fundamental_strength=3` did not beat `strength=2`.
- `technical_context=favorable` did not beat `extended`.
- confidence buckets were not monotonic.

This is enough to block full development LLM execution with the current prompt
and selection rule.

## Event Decomposition

Observation-level `fixed_20d` results:

| Group | Trades | Net PnL | PF |
| --- | ---: | ---: | ---: |
| `earnings_result` | 166 | 207,542 | 1.359 |
| `forecast_revision` | 166 | -263,819 | 0.659 |
| `dividend_revision` | 166 | -489,691 | 0.562 |
| `3QFinancialStatements_Consolidated_JP` | 37 | 99,561 | 2.149 |
| `FYFinancialStatements_Consolidated_JP` | 35 | 126,255 | 1.823 |
| `1QFinancialStatements_Consolidated_JP` | 32 | -56,728 | 0.594 |
| `2QFinancialStatements_Consolidated_JP` | 32 | 4,614 | 1.041 |
| dividend `increase` | 93 | -329,719 | 0.469 |
| dividend `invalid` | 62 | -199,472 | 0.476 |
| dividend `decrease` | 11 | 39,500 | 1.339 |

The dividend decrease row is not a long hypothesis. It is a reminder that
dividend revisions must remain separated by subtype and not be treated as one
long catalyst.

## Feature Buckets

The forecast revision features were directionally sensible but not strong
enough on this cohort:

| Feature Bucket | Trades | Net PnL | PF |
| --- | ---: | ---: | ---: |
| `profit_revision_pct=positive` | 59 | 25,576 | 1.106 |
| `profit_revision_pct=zero` | 51 | 63,484 | 1.536 |
| `profit_revision_pct=negative` | 24 | -79,106 | 0.318 |
| `operating_profit_revision_pct=positive` | 54 | 28,763 | 1.136 |
| `forecast_eps_revision_absolute=positive` | 69 | 25,493 | 1.095 |

Positive revisions alone are not enough to justify a new candidate. Missing or
invalid revision values dominate the sample and should not be imputed as zero.

## Next Hypotheses

Do not tune the current prompt or thresholds against the same smoke output.

The next pre-registered research hypotheses should be:

1. `earnings_result` deserves a separate rule-only and AI smoke path.
2. Earnings subtypes should be separated at least into `1Q`, `2Q`, `3Q`, and
   `FY`; combining them hides very different behavior.
3. `dividend_revision` should not be a generic long catalyst. `increase`,
   `decrease`, and `invalid` must remain separate, and `decrease` remains
   excluded from long candidate rules.
4. `forecast_revision` needs a quality gate before AI evaluation. Positive
   revision fields are weakly positive, but broad forecast revision event-only
   exposure is poor.
5. The current AI label schema should not be used for selection until label
   monotonicity improves on a fixed smoke cohort and beats rule-only technical.

Locked OOS remains untouched.

