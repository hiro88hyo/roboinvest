# Event AI Local LLM Smoke Report

Date: 2026-06-27
Branch: `strategy/swing-rebuild`
Commit: `56d9039`

This report records the first fixed-cohort local LLM smoke result for the
event AI research foundation. It does not enable paper/live routing and does
not change any production signal stream.

## Setup

- Dataset: `out/event-research-real-pit`
- Split: `development` only
- Cohort: balanced 500 event jobs, `sample_seed=1`
- Prompt: `event_ai_label_v0`
- Model provider: `openai_compatible`
- Model: `gemma-4-26b-a4b-it-qat`
- Temperature: `0`
- Model seed: `1`
- Runner concurrency: `1`
- Real labels: 499/500 completed, 1 fail-closed parse failure
- Bundle placebo labels: 497/500 completed, 3 fail-closed parse failures

The fixed cohort matches the earlier balanced500 `sample_seed=1` event set.
The run adds model seed metadata and sends `seed` to the OpenAI-compatible
payload when configured.

## Key Results

Observation-level metrics, after costs, using the fixed cohort:

| Run | Arm | Exit | Trades | Net PnL | PF |
| --- | --- | --- | ---: | ---: | ---: |
| real | `event_only` | `fixed_20d` | 498 | -545,968 | 0.779 |
| real | `event_plus_technical` | `fixed_20d` | 102 | 110,227 | 1.319 |
| real | `event_plus_ai` | `fixed_20d` | 66 | 32,647 | 1.129 |
| real | `event_plus_ai_plus_fundamental_plus_technical` | `fixed_20d` | 11 | 47,719 | 4.810 |
| bundle placebo | `event_plus_ai` | `fixed_20d` | 92 | -105,252 | 0.738 |
| bundle placebo | `event_plus_ai_plus_fundamental_plus_technical` | `fixed_20d` | 12 | 15,468 | 1.396 |

The AI-only arm beat the bundle placebo on this fixed cohort. It did not beat
the rule-only technical arm. The strongest combined AI/fundamental/technical
row has only 11-12 trades, so it is diagnostic only.

## Diagnostics

The label semantics are not yet reliable enough to advance to full development
execution:

- `fundamental_direction=negative` outperformed `positive` on this cohort.
- `fundamental_strength=3` underperformed `fundamental_strength=2`.
- `technical_context=extended` outperformed `favorable`.
- Confidence buckets were not monotonic.
- `event_plus_ai` worked mainly at `fixed_20d`; `fixed_10d` stayed below PF 1.
- `ai_without_technical` was weak, while `ai_and_technical` was strong but tiny.
- `event_plus_technical` alone remained stronger than `event_plus_ai`.

This does not satisfy the AI value minimum conditions in the plan:

- AI filtered arm did not beat the rule-only technical arm.
- Confidence/strength monotonicity was not stable.
- The positive combined row was too small to treat as a continuation candidate.

## Decision

Do not run the full development LLM job set yet.

The correct next step is not prompt/threshold tuning against the same
development/validation sample. The next acceptable work is diagnostic:

- compare AI label semantics against event type and document subtype
- investigate why dividend revisions and forecast revisions are weak in this
  cohort
- decide whether a new prompt/schema hypothesis must be pre-registered before
  any further non-smoke LLM run
- keep locked OOS untouched

Paper/live routes remain disabled.

