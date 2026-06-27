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
- But the bundle placebo remains equally strong, so AI-specific incremental value is not established.
- Confidence is not discriminative.
- Technical-context labels are not semantically aligned with forward performance.

Next work should stay inside development-only diagnostics and should not inspect validation or locked OOS.

## Next Candidate Work

Before any larger LLM run:

1. Add a strict label distribution audit to fail or warn when confidence collapses into one bucket.
2. Add explicit comparison reporting between real labels and external bundle placebo labels.
3. Investigate why bundle-shuffled jobs still select profitable earnings events.
4. Keep `prompt_version=event_ai_label_v0`, model, temperature, and sample seed frozen for this recorded smoke; any prompt change must be treated as a new pre-registered experiment.
