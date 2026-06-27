# Event AI Earnings Smoke Result - 2026-06-27

## Scope

This report records the pre-registered earnings-only local LLM smoke run.

- Branch: `strategy/swing-rebuild`
- Commit: `157473940e144acca34351ff4106876e852e9472`
- Dataset: `out/event-research-real-pit`
- Split evaluated: `development`
- Event filter: `event_type=earnings_result`
- Sample size: 300
- Prompt version: `event_ai_label_v0`
- Model provider: `openai_compatible`
- Model: `gemma-4-26b-a4b-it-qat`
- Temperature: `0`
- Model seed: `1`
- Sample seed: `21`
- Max concurrency: `1`

No paper/live route was enabled.

## Artifacts

- Real jobs: `out/event-ai/jobs-earnings300-gemma4-seed1.jsonl`
- Bundle placebo jobs: `out/event-ai/jobs-earnings300-bundle-placebo-gemma4-seed1.jsonl`
- Real labels: `out/event-ai/labels-earnings300-gemma4-seed1.jsonl`
- Bundle placebo labels: `out/event-ai/labels-earnings300-bundle-placebo-gemma4-seed1.jsonl`
- Real eval: `out/event-ai/eval-earnings300-gemma4-seed1/event-ai-report.json`
- Bundle placebo eval: `out/event-ai/eval-earnings300-bundle-placebo-gemma4-seed1/event-ai-report.json`
- Real diagnostics: `out/event-ai/smoke-diagnostics-earnings300-gemma4-seed1.json`
- Bundle placebo diagnostics: `out/event-ai/smoke-diagnostics-earnings300-bundle-placebo-gemma4-seed1.json`
- Real/placebo comparison: `out/event-ai/placebo-compare-earnings300-gemma4-seed1.json`
- Real/placebo comparison with true random-date pool:
  `out/event-ai/placebo-compare-earnings300-gemma4-seed1-randomdate.json`
- Official-numeric placebo jobs:
  `out/event-ai/jobs-earnings300-official-numeric-placebo-gemma4-seed1.jsonl`
- Official-numeric placebo job audit:
  `out/event-ai/jobs-earnings300-official-numeric-placebo-audit.json`
- Official-numeric placebo labels:
  `out/event-ai/labels-earnings300-official-numeric-placebo-gemma4-seed1.jsonl`
- Official-numeric placebo eval:
  `out/event-ai/eval-earnings300-official-numeric-placebo-gemma4-seed1/event-ai-report.json`
- Official-numeric placebo comparison:
  `out/event-ai/placebo-compare-earnings300-official-numeric-placebo-gemma4-seed1-randomdate.json`
- Feature-bundle proxy labels:
  `out/event-ai/labels-earnings300-feature-proxy-v0.jsonl`
- Feature-bundle proxy eval:
  `out/event-ai/eval-earnings300-feature-proxy-v0/event-ai-report.json`
- Feature-bundle proxy comparison:
  `out/event-ai/placebo-compare-earnings300-feature-proxy-v0-randomdate.json`

## Run Status

Real LLM run:

- attempted: 300
- completed: 300
- failed: 0
- cached: 0
- labels_total: 300

Bundle placebo LLM run:

- initial attempted: 300
- completed: 299
- failed: 1
- cached: 0
- labels_total: 299

The failed placebo job was retried once. The retry produced the same strict parser failure: the model returned multiple JSON blocks plus explanatory text. It remains rejected fail-closed.

## Main Development Metrics

Fixed 20 trading session exit:

| Arm | Labels | Trades | Net PnL | PF | Max DD | Hit Rate | Positive Month Ratio |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| real event_only | 300 | 300 | 274,539 | 1.344 | 203,228 | 0.507 | 0.545 |
| real event_plus_technical | 300 | 58 | 65,429 | 1.397 | 54,048 | 0.483 | 0.407 |
| real event_plus_ai | 300 | 37 | 104,547 | 2.385 | 21,669 | 0.541 | 0.524 |
| real event_plus_ai_plus_fundamental_plus_technical | 300 | 9 | -3,211 | 0.884 | 20,823 | 0.333 | 0.250 |
| bundle placebo event_only | 299 | 299 | 276,003 | 1.347 | 203,228 | 0.508 | 0.545 |
| bundle placebo event_plus_technical | 299 | 57 | 66,892 | 1.409 | 54,048 | 0.491 | 0.407 |
| bundle placebo event_plus_ai | 299 | 42 | 110,991 | 2.311 | 30,285 | 0.476 | 0.462 |
| bundle placebo event_plus_ai_plus_fundamental_plus_technical | 299 | 2 | -6,922 | 0.000 | 6,922 | 0.000 | 0.000 |

Fixed 10 trading session exit:

| Arm | Trades | Net PnL | PF | Max DD | Hit Rate |
| --- | ---: | ---: | ---: | ---: | ---: |
| real event_only | 300 | 95,410 | 1.142 | 149,131 | 0.487 |
| real event_plus_technical | 58 | -23,524 | 0.841 | 73,240 | 0.431 |
| real event_plus_ai | 37 | 16,273 | 1.212 | 21,446 | 0.541 |

## AI-Specific Checks

The real `event_plus_ai` fixed_20d arm beats:

- event_only on PF and drawdown, but not total PnL because it trades far fewer events
- rule-only technical on PF, PnL, and drawdown
- internal labels-shuffled placebo: shuffled `event_plus_ai` fixed_20d had PF 0.910 and PnL -8,358
- random-threshold placebo: random-threshold `event_plus_ai` fixed_20d had PF 1.090 and PnL 10,679

However, it does not beat the stronger external bundle placebo:

- real `event_plus_ai` fixed_20d: 37 trades, PnL 104,547, PF 2.385
- bundle placebo `event_plus_ai` fixed_20d: 42 trades, PnL 110,991, PF 2.311

The bundle placebo having similar or better results means the smoke does not yet prove that the real prompt's official numeric/event alignment adds value.

## Real Vs Bundle Placebo Comparison

The direct comparison uses only the common label cohort, excluding the one
bundle placebo parse failure.

- common labels: 299
- real AI pass: 37
- bundle placebo AI pass: 42
- both pass: 8
- real-only pass: 29
- placebo-only pass: 34
- neither pass: 228
- pass Jaccard: 0.113

Fixed 20 trading session exit on the common cohort:

| Cohort | Trades | Net PnL | PF | Max DD | Hit Rate | Positive Month Ratio |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| real AI pass | 37 | 104,547 | 2.385 | 21,669 | 0.541 | 0.524 |
| bundle placebo AI pass | 42 | 110,991 | 2.311 | 30,285 | 0.476 | 0.462 |
| both pass | 8 | 84,257 | 13.173 | 3,817 | 0.750 | 1.000 |
| real-only pass | 29 | 20,291 | 1.296 | 21,669 | 0.483 | 0.450 |
| placebo-only pass | 34 | 26,735 | 1.344 | 30,285 | 0.412 | 0.375 |
| neither pass | 228 | 144,721 | 1.225 | 190,657 | 0.518 | 0.463 |

True `same_symbol_random_date` coverage used the daily OHLCV pool:

- matched: 299
- fallback: 0
- fallback rate: 0.0
- candidate pool size median: 1,219

Fixed 20 trading session `same_symbol_random_date` percentiles:

| Cohort | Selected PnL | Random Median | Random P95 | Selected Percentile |
| --- | ---: | ---: | ---: | ---: |
| both pass | 84,257 | 3,027 | 62,656 | 0.980 |
| real-only pass | 20,291 | 23,113 | 125,725 | 0.487 |
| placebo-only pass | 26,735 | 8,676 | 96,032 | 0.640 |
| real AI pass | 104,547 | 30,503 | 139,587 | 0.910 |
| bundle placebo AI pass | 110,991 | 4,822 | 126,687 | 0.910 |
| neither pass | 144,721 | 1,575 | 323,767 | 0.767 |

Interpretation:

- The common 8 events explain most of the apparent AI-pass strength.
- Real-only and placebo-only selections are both only modestly positive.
- The pass overlap is low, so the model is not producing a stable, uniquely
  real-information-driven selection set.
- Both real and placebo confidence distributions collapsed into the `0.7..1.0`
  bucket, so confidence remains unusable as a threshold.
- The common 8 events are genuinely unusual versus same-symbol random dates,
  but this is not enough to attribute the edge to real prompt information
  because the bundle placebo selected the same 8 events.
- Prompt section audit showed why bundle placebo is not a full official-data
  placebo: for the common 8 events, `event` and `official_numeric_summary`
  remained identical while `fundamental_features_v0`, `valuation_features_v0`,
  and `technical_context_v0` all differed.
- A new `official_numeric_summary_shuffled` placebo job set was generated and
  audited. It preserves event metadata and feature bundles while changing
  `official_numeric_summary`; job audit passed with 300 jobs, no event-order
  mismatch, and 299 changed prompt hashes.

## Official Numeric Placebo Result

The official-numeric placebo used the same prompt version, model, temperature,
sample seed, model seed, and max concurrency as the real run. It completed
without parser failures.

- completed: 300
- failed: 0
- labels_total: 300
- model: `gemma-4-26b-a4b-it-qat`
- temperature: `0`
- seed: `1`
- max concurrency: `1`

Fixed 20 trading session exit:

| Arm | Labels | Trades | Net PnL | PF | Max DD | Hit Rate | Positive Month Ratio |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| official numeric placebo event_only | 300 | 300 | 274,539 | 1.344 | 203,228 | 0.507 | 0.545 |
| official numeric placebo event_plus_technical | 300 | 58 | 65,429 | 1.397 | 54,048 | 0.483 | 0.407 |
| official numeric placebo event_plus_ai | 300 | 38 | 85,750 | 2.131 | 16,315 | 0.553 | 0.500 |
| official numeric placebo event_plus_ai_plus_fundamental_plus_technical | 300 | 7 | 9,025 | 1.582 | 8,587 | 0.429 | 0.429 |

Direct comparison to real labels on the common 300-label cohort:

- real AI pass: 37
- official-numeric placebo AI pass: 38
- both pass: 25
- real-only pass: 12
- placebo-only pass: 13
- pass Jaccard: 0.500

Fixed 20 trading session exit by overlap cohort:

| Cohort | Trades | Net PnL | PF | Max DD | Hit Rate | `same_symbol_random_date` percentile |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| both pass | 25 | 72,113 | 2.284 | 20,148 | 0.560 | 0.823 |
| real-only pass | 12 | 32,434 | 2.680 | 9,610 | 0.500 | 0.797 |
| official-numeric placebo-only pass | 13 | 13,637 | 1.695 | 7,530 | 0.538 | 0.433 |
| official-numeric placebo AI pass | 38 | 85,750 | 2.131 | 16,315 | 0.553 | 0.857 |

Prompt section comparison for the 25 both-pass events:

- `event`: identical rate 1.0
- `official_numeric_summary`: identical rate 0.0
- `fundamental_features_v0`: identical rate 1.0
- `valuation_features_v0`: identical rate 1.0
- `technical_context_v0`: identical rate 1.0

Interpretation:

- Shuffling official disclosure numerics did not collapse the AI selection.
- The strong AI-pass behavior is therefore not explained by event-specific
  `official_numeric_summary` alone.
- Because feature bundles were unchanged and overlap rose to 25 events, the
  current label rule is likely driven substantially by engineered
  point-in-time feature bundles and/or event metadata.
- Confidence still collapsed into the `0.7..1.0` bucket for both real and
  official-numeric placebo labels, so confidence remains unusable for selection.

## Feature-Bundle Proxy Result

A deterministic `feature_bundle_proxy_v0` label set was generated from the
same point-in-time feature bundles, without LLM calls and without forward
returns. It applies the current AI-pass predicate to synthetic labels derived
from coarse feature thresholds.

Fixed 20 trading session exit:

| Arm | Labels | Trades | Net PnL | PF | Max DD | Hit Rate | Positive Month Ratio |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| feature proxy event_only | 300 | 300 | 274,539 | 1.344 | 203,228 | 0.507 | 0.545 |
| feature proxy event_plus_technical | 300 | 58 | 65,429 | 1.397 | 54,048 | 0.483 | 0.407 |
| feature proxy event_plus_ai | 300 | 11 | -1,796 | 0.948 | 17,006 | 0.364 | 0.300 |
| feature proxy event_plus_ai_plus_fundamental_plus_technical | 300 | 11 | -1,796 | 0.948 | 17,006 | 0.364 | 0.300 |

Direct comparison to real labels:

- real AI pass: 37
- feature proxy AI pass: 11
- both pass: 9
- real-only pass: 28
- feature-proxy-only pass: 2
- pass Jaccard: 0.231

Fixed 20 trading session exit by overlap cohort:

| Cohort | Trades | Net PnL | PF | Max DD | Hit Rate | `same_symbol_random_date` percentile |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| real AI pass | 37 | 104,547 | 2.385 | 21,669 | 0.541 | 0.910 |
| feature proxy AI pass | 11 | -1,796 | 0.948 | 17,006 | 0.364 | 0.287 |
| both pass | 9 | -3,211 | 0.884 | 20,823 | 0.333 | 0.233 |
| real-only pass | 28 | 107,758 | 3.258 | 15,803 | 0.607 | 0.960 |
| feature-proxy-only pass | 2 | 1,415 | 1.215 | 6,595 | 0.500 | 0.553 |

True `same_symbol_random_date` coverage:

- matched: 300
- fallback: 0
- fallback rate: 0.0
- candidate pool size median: 1,219

Interpretation:

- A simple deterministic feature-threshold proxy does not reproduce the real
  AI-pass strength.
- The official-numeric placebo result is therefore not explained by the exact
  coarse feature proxy implemented here.
- The remaining hypothesis is that the model is using feature bundles and/or
  event metadata in a more complex way than this proxy, or that another common
  cohort artifact is driving the pass set.
- This is still not sufficient to proceed to development-all, validation, or
  locked OOS.

## Semantic Diagnostics

Real fixed_20d label buckets:

- `fundamental_direction=positive`: 102 trades, PnL 165,784, PF 1.824
- `fundamental_direction=neutral`: 79 trades, PnL 141,717, PF 1.636
- `fundamental_direction=negative`: 119 trades, PnL -32,962, PF 0.912
- `fundamental_strength=2`: 65 trades, PnL 118,244, PF 1.911
- `fundamental_strength=3`: 13 trades, PnL 36,815, PF 2.385
- `technical_context=favorable`: 50 trades, PnL 90,573, PF 1.645
- `technical_context=extended`: 28 trades, PnL 71,396, PF 1.805
- `confidence`: all 300 labels fell into the `0.7..1.0` bucket

Interpretation:

- Fundamental direction is directionally useful in this smoke.
- Fundamental strength is not cleanly monotonic by count-adjusted stability, but the top bucket is positive.
- Technical context is not reliable as a gating label because `extended` outperformed `favorable`.
- Confidence cannot be used yet because the model assigned all labels to the highest bucket.

## Decision

Do not proceed to development-all, validation, or locked OOS from this smoke.

Reason:

- The real AI arm is promising versus internal shuffled/random placebos.
- But the bundle placebo and the official-numeric placebo both remain strong,
  so AI-specific incremental value is not established.
- The official-numeric placebo overlap suggests the current pass rule is
  substantially explainable by event metadata and/or engineered feature bundles,
  not by event-specific official disclosure numerics alone.
- The deterministic feature-bundle proxy does not reproduce the real AI-pass
  edge, so a simplistic rule-only explanation is also insufficient.
- Confidence is not discriminative.
- Technical-context labels are not semantically aligned with forward performance.

Next work should stay inside development-only diagnostics and should not inspect validation or locked OOS.

## Next Candidate Work

Before any larger LLM run:

1. Investigate why the shared real/placebo pass set is unusually strong.
2. Add a combined feature-bundle plus official-numeric shuffled placebo if
   another LLM diagnostic is needed.
3. Inspect real-only versus official-numeric-placebo-only prompt sections and
   top contributors for common event metadata patterns.
4. Keep `prompt_version=event_ai_label_v0`, model, temperature, and sample seed
   frozen for this recorded smoke; any prompt change must be treated as a new
   pre-registered experiment.
