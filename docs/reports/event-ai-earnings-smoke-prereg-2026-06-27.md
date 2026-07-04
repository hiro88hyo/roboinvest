# Event AI Earnings-Only Smoke Preregistration

Date: 2026-06-27
Branch: `strategy/swing-rebuild`

This preregisters the next local LLM smoke run after the fixed balanced500
diagnostics. The previous smoke showed that broad event mixing is not good
enough for full development LLM execution, but `earnings_result` was the only
event type with positive observation-level `fixed_20d` performance.

This is still research-only. It does not enable paper/live routing and does not
touch locked OOS.

## Frozen Inputs

- Dataset: `out/event-research-real-pit`
- Split: `development`
- Event filter: `event_type=earnings_result`
- Prompt version: `event_ai_label_v0`
- Model provider: `openai_compatible`
- Model ID: `gemma-4-26b-a4b-it-qat`
- Temperature: `0`
- Model seed: `1`
- Sample seed: `21`
- Sample size: `300`
- Runner concurrency: `1`
- Placebo: `bundle_shuffled`, `placebo_seed=21`

No prompt, schema, model, threshold, or sample seed changes are allowed after
observing this smoke result.

## Commands

Baseline jobs:

```bash
uv run python scripts/build-event-llm-jobs.py \
  --events out/event-research-real-pit/events.jsonl \
  --observations out/event-research-real-pit/observations.jsonl \
  --output out/event-ai/jobs-earnings300-gemma4-seed1.jsonl \
  --split development \
  --event-type earnings_result \
  --sample-size 300 \
  --sample-seed 21 \
  --model-provider openai_compatible \
  --model-id gemma-4-26b-a4b-it-qat \
  --temperature 0 \
  --seed 1
```

Bundle placebo jobs:

```bash
uv run python scripts/build-event-llm-jobs.py \
  --events out/event-research-real-pit/events.jsonl \
  --observations out/event-research-real-pit/observations.jsonl \
  --output out/event-ai/jobs-earnings300-bundle-placebo-gemma4-seed1.jsonl \
  --split development \
  --event-type earnings_result \
  --sample-size 300 \
  --sample-seed 21 \
  --placebo-mode bundle_shuffled \
  --placebo-seed 21 \
  --model-provider openai_compatible \
  --model-id gemma-4-26b-a4b-it-qat \
  --temperature 0 \
  --seed 1
```

Audit:

```bash
uv run python scripts/audit-event-llm-jobs.py \
  --jobs out/event-ai/jobs-earnings300-gemma4-seed1.jsonl \
  --placebo-jobs out/event-ai/jobs-earnings300-bundle-placebo-gemma4-seed1.jsonl \
  --observations out/event-research-real-pit/observations.jsonl \
  --output out/event-ai/jobs-earnings300-audit.json \
  --provider openai_compatible \
  --model-id gemma-4-26b-a4b-it-qat \
  --split development
```

Run real labels and bundle placebo labels with `scripts/run-event-llm-jobs.py`
using the same provider, model, temperature, seed, and concurrency.

## Continuation Criteria

This smoke is promising only if all of the following hold on the fixed
earnings-only cohort:

- `event_plus_ai fixed_20d` beats `event_only fixed_20d`.
- `event_plus_ai fixed_20d` beats `event_plus_technical fixed_20d`.
- `event_plus_ai fixed_20d` beats bundle placebo `event_plus_ai fixed_20d`.
- label-shuffled and confidence-shuffled placebos do not beat the real AI row.
- confidence or strength buckets show usable monotonicity.
- selected AI trade count is large enough to inspect, target at least 30.

If these fail, do not run full development LLM. Return to data/prompt
diagnostics and preregister a new hypothesis before any further LLM run.

