# Event AI Swing Research Plan

Created: 2026-06-25

This plan replaces the OHLCV-only candidate loop with a point-in-time,
event-driven research workflow. It does not enable paper/live routing.

## Frozen Rejected Baselines

The following candidates remain frozen rejected baselines:

- `daily_trend_pullback_v0` through `daily_trend_pullback_v5`
- `daily_trend_pullback_exit_fixed10_v0`
- `daily_trend_pullback_fixed10_hash_v0`
- `daily_trend_pullback_fixed10_hash_v1_operational`
- `daily_breakout_continuation_v0`
- `daily_volatility_contraction_v0`
- `daily_volatility_contraction_hash_basket_v1`

They are not revived by the new event research gates. Their key failure was
that selector / timing / exit did not stably beat matched random baselines.

## Architecture

Layer responsibilities:

1. Event / fundamental catalyst decides why a symbol is evaluated on a date.
2. Fundamental / valuation context evaluates whether the catalyst is
   underpriced or already reflected.
3. Technical context is limited to veto, overheating, liquidity, volatility,
   market regime, sizing, and exit diagnostics.
4. AI structures event meaning, quality, one-off risk, and expected horizon.
   AI does not output BUY/SELL/HOLD.
5. Strategy simulation compares layer arms under identical assumptions.

Initial event types:

- P0: `forecast_revision`, `dividend_revision`
- P1: `earnings_result`
- P2: `buyback_announcement` only when TDnet/archive data is available. Until
  then only schema/interface/fixture support is provided.

## Point-In-Time Rules

Every event and feature keeps:

- `disclosed_at`
- `data_available_at`
- `signal_date`
- `entry_date`
- `feature_cutoff_at`
- `source_record_id`
- `fetched_at`

Feature values carry source timing metadata through `FeatureValue`.

Initial execution mode is `next_open_unconditional`:

- post-close and unknown-time events enter at the next trading session open
- entry decision may use only data available by `feature_cutoff_at`
- next-day open/gap/09:15 data is not used as a pre-entry filter

`next_0915_conditional` is schema-only until minute data exists. Daily bars are
not used to invent 09:15 prices.

## Feature Versions

`fundamental_features_v0` includes forecast revision, EPS, profit, sales,
sign-change, missing-value, and accounting-standard fields. Percentage
revisions are invalid when the denominator is missing, near zero, or crosses
sign.

`valuation_features_v0` uses the last confirmed close at `data_available_at`.
PER is valid only when EPS is positive. Negative PER is not emitted.

`technical_context_v0` is built from pre-event daily bars and is not an
OHLCV-only entry reason. Initial vetoes are intentionally coarse:

- minimum 20d average turnover
- exclude extreme ATR
- exclude strongly pre-run events
- block broad downtrend

## Simulation Arms

Rule-only entry arms:

- `event_only`
- `event_plus_fundamental`
- `event_plus_technical`
- `event_plus_fundamental_plus_technical`

AI arms after labels exist:

- `event_plus_ai`
- `event_plus_ai_plus_fundamental`
- `event_plus_ai_plus_fundamental_plus_technical`

Exit arms:

- `fixed_2d`
- `fixed_5d`
- `fixed_10d`
- `fixed_20d`
- `fixed_10d_plus_catastrophic_stop`
- `fixed_20d_plus_catastrophic_stop`

The first catastrophic stop is fixed at a single coarse value. ATR/grid search
is not part of this plan.

## Gates

Research, paper, and live gates are defined in
[ADR-0004](../adr/0004-event-ai-research-gates.md). The project kill switch in
`AGENTS.md` remains unchanged.

## Commands

All commands use `uv`.

1. Export J-Quants financial summaries:

```bash
uv run python scripts/export-jquants-financial-summaries-jsonl.py \
  --start-date 2021-01-01 \
  --end-date 2026-12-31 \
  --output out/event-research/financial-summaries.jsonl \
  --resume \
  --concurrency 1 \
  --sleep-seconds 1.4 \
  --log-every-dates 50
```

2. Build the event dataset:

```bash
uv run python scripts/build-event-research-dataset.py \
  --financial-summary-jsonl scripts/tests/fixtures/event-research/jquants_fins_summary.jsonl \
  --ohlcv scripts/tests/fixtures/event-research/daily_ohlcv.csv \
  --master scripts/tests/fixtures/event-research/master_stocks.csv \
  --output-dir out/event-research
```

3. Evaluate event/fundamental/technical baselines:

```bash
uv run python scripts/evaluate-event-research.py \
  --observations out/event-research/observations.jsonl \
  --output-dir out/event-research \
  --split development \
  --random-seeds 300
```

`event-alpha-summary.csv` includes matched-random selected percentiles for each
`event_type` / `entry_arm` / `exit_arm` row. `random-baselines.json` keeps the
legacy aggregate fixed-10-day baseline for broad sanity checks only; gate
decisions must use the row-specific percentiles in the summary/report.

The default split is `development`, which includes train and validation while
excluding purge windows and locked OOS. Locked OOS details require an explicit
opt-in:

```bash
uv run python scripts/evaluate-event-research.py \
  --observations out/event-research/observations.jsonl \
  --output-dir out/event-research-locked-oos \
  --split locked-oos \
  --include-locked-oos \
  --random-seeds 300
```

4. Generate LLM jobs:

```bash
uv run python scripts/build-event-llm-jobs.py \
  --events out/event-research/events.jsonl \
  --observations out/event-research/observations.jsonl \
  --output out/event-ai/jobs.jsonl \
  --split development \
  --model-provider fixture \
  --model-id fixture-event-labeler-v0
```

For a local LLM smoke run, generate a deterministic development sample first:

```bash
uv run python scripts/build-event-llm-jobs.py \
  --events out/event-research/events.jsonl \
  --observations out/event-research/observations.jsonl \
  --output out/event-ai/jobs-sample100.jsonl \
  --split development \
  --sample-size 100 \
  --sample-seed 1 \
  --model-provider openai_compatible \
  --model-id local-model
```

To generate a numerical-field placebo prompt set for the same sample:

```bash
uv run python scripts/build-event-llm-jobs.py \
  --events out/event-research/events.jsonl \
  --observations out/event-research/observations.jsonl \
  --output out/event-ai/jobs-sample100-numerical-placebo.jsonl \
  --split development \
  --sample-size 100 \
  --sample-seed 1 \
  --placebo-mode numerical_fields_shuffled \
  --placebo-seed 1 \
  --model-provider openai_compatible \
  --model-id local-model
```

The numerical placebo shuffles only `FeatureValue.value` fields within each
event type. Feature timing metadata such as `available_at` and
`feature_cutoff_at` is preserved so the placebo prompt does not introduce
future timestamps.

5. Run fixture labels:

```bash
uv run python scripts/run-event-llm-jobs.py \
  --jobs out/event-ai/jobs.jsonl \
  --provider fixture \
  --output-labels out/event-ai/fixture-labels.jsonl \
  --output-failures out/event-ai/failures.jsonl \
  --output-manifest out/event-ai/run-manifest.json
```

6. Evaluate AI labels:

```bash
uv run python scripts/evaluate-event-ai.py \
  --observations out/event-research/observations.jsonl \
  --labels out/event-ai/fixture-labels.jsonl \
  --output-dir out/event-ai \
  --split development
```

The AI evaluator uses the same split guard as the rule-only event evaluator.
Locked OOS requires `--split locked-oos --include-locked-oos` after prompt,
feature schema, model, and thresholds are frozen.
The report includes label-shuffled, confidence-shuffled, and random-threshold
placebos within event type. Event-title and numerical-field shuffled placebos
are reported as unavailable until the evaluator consumes the full feature bundle
instead of labels-only input.

7. Run a local OpenAI-compatible model:

```bash
LLM_PROVIDER=openai_compatible \
LOCAL_LLM_BASE_URL=http://127.0.0.1:8000/v1 \
LOCAL_LLM_API_KEY=local \
LOCAL_LLM_MODEL=local-model \
LOCAL_LLM_TIMEOUT_SECONDS=60 \
LOCAL_LLM_MAX_CONCURRENCY=2 \
uv run python scripts/run-event-llm-jobs.py \
  --jobs out/event-ai/jobs-sample100.jsonl \
  --provider openai_compatible \
  --output-labels out/event-ai/labels-sample100.jsonl \
  --output-failures out/event-ai/failures-sample100.jsonl \
  --output-manifest out/event-ai/run-manifest-sample100.json \
  --max-jobs 100
```

`run-event-llm-jobs.py` resumes by default from existing successful labels
using a cache key derived from prompt hash, provider, model, temperature, and
seed. Use `--no-resume` only when intentionally overwriting a run. Failed jobs
are not cached and are retried on the next run.

## Data Limitations

This implementation supports J-Quants `/fins/summary` through the
`JQuantsClient`, but local tests use sanitized fixtures and mock transports. If
credentials or raw TDnet archives are unavailable, buyback announcements remain
fixture/interface-only and are not inferred.

The evaluator reports TOPIX/sector excess when the fields exist in the dataset.
It does not synthesize unavailable TOPIX or sector time series.

## Paper/Live Status

No event strategy is connected to `strategy-signals-b`, Gateway, OMS Paper, or
OMS Live. This is a research-only pipeline.
