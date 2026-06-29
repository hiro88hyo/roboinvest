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
  --output out/event-ai/jobs-balanced100.jsonl \
  --split development \
  --balanced-sample-size 100 \
  --sample-seed 1 \
  --model-provider openai_compatible \
  --model-id local-model
```

For preregistered subset smokes, filter before sampling. For example,
earnings-only jobs:

```bash
uv run python scripts/build-event-llm-jobs.py \
  --events out/event-research/events.jsonl \
  --observations out/event-research/observations.jsonl \
  --output out/event-ai/jobs-earnings300.jsonl \
  --split development \
  --event-type earnings_result \
  --sample-size 300 \
  --sample-seed 21 \
  --model-provider openai_compatible \
  --model-id local-model \
  --temperature 0 \
  --seed 1
```

`--event-type`, `--event-subtype`, and `--event-subtype-prefix` may be repeated.
They must be set before seeing the next smoke result, and locked OOS remains
off limits.

To generate placebo prompt sets for the same sample:

```bash
uv run python scripts/build-event-llm-jobs.py \
  --events out/event-research/events.jsonl \
  --observations out/event-research/observations.jsonl \
  --output out/event-ai/jobs-balanced100-numerical-placebo.jsonl \
  --split development \
  --balanced-sample-size 100 \
  --sample-seed 1 \
  --placebo-mode numerical_fields_shuffled \
  --placebo-seed 1 \
  --model-provider openai_compatible \
  --model-id local-model

uv run python scripts/build-event-llm-jobs.py \
  --events out/event-research/events.jsonl \
  --observations out/event-research/observations.jsonl \
  --output out/event-ai/jobs-balanced100-bundle-placebo.jsonl \
  --split development \
  --balanced-sample-size 100 \
  --sample-seed 1 \
  --placebo-mode bundle_shuffled \
  --placebo-seed 1 \
  --model-provider openai_compatible \
  --model-id local-model

uv run python scripts/build-event-llm-jobs.py \
  --events out/event-research/events.jsonl \
  --observations out/event-research/observations.jsonl \
  --output out/event-ai/jobs-balanced100-official-numeric-placebo.jsonl \
  --split development \
  --balanced-sample-size 100 \
  --sample-seed 1 \
  --placebo-mode official_numeric_summary_shuffled \
  --placebo-seed 1 \
  --model-provider openai_compatible \
  --model-id local-model

uv run python scripts/build-event-llm-jobs.py \
  --events out/event-research/events.jsonl \
  --observations out/event-research/observations.jsonl \
  --output out/event-ai/jobs-balanced100-feature-and-official-placebo.jsonl \
  --split development \
  --balanced-sample-size 100 \
  --sample-seed 1 \
  --placebo-mode feature_and_official_numeric_shuffled \
  --placebo-seed 1 \
  --model-provider openai_compatible \
  --model-id local-model
```

The numerical placebo shuffles only `FeatureValue.value` fields within each
event type. Feature timing metadata such as `available_at` and
`feature_cutoff_at` is preserved so the placebo prompt does not introduce
future timestamps. The bundle placebo shuffles the fundamental, valuation, and
technical feature bundles within event type as a less adversarial numerical
placebo. The official-numeric placebo shuffles the prompt allowlisted
`official_numeric_summary` bundle within event type while leaving event metadata
and feature bundles unchanged. Use it when bundle placebo remains strong,
because bundle placebo intentionally preserves official disclosure numerics.
The combined placebo shuffles both feature bundles and official numeric
summary while preserving event metadata; use it only as a follow-up diagnostic
when the official-numeric placebo still leaves a strong common pass cohort.

Before running a local model, audit the baseline and placebo job files:

```bash
uv run python scripts/audit-event-llm-jobs.py \
  --jobs out/event-ai/jobs-balanced100.jsonl \
  --placebo-jobs out/event-ai/jobs-balanced100-bundle-placebo.jsonl \
  --observations out/event-research/observations.jsonl \
  --output out/event-ai/jobs-balanced100-audit.json \
  --provider openai_compatible \
  --model-id local-model \
  --split development
```

The audit fails on prompt hash mismatch, forbidden prompt keys such as forward
returns or PnL, provider/model mismatch, requested split mismatch, and broken
baseline/placebo event ordering.

External review on 2026-06-26 approved running the balanced-100 local LLM smoke
after the data-validity fixes. The execution order is fixed:

1. Data audit.
2. Rule-only 30-trading-day audit.
3. Balanced-100 baseline job generation.
4. Balanced-100 bundle placebo job generation.
5. Job audit.
6. Balanced-100 LLM run.
7. Bundle placebo LLM run.
8. AI evaluation.
9. Development full run only if smoke is promising.
10. Validation only if development full run remains promising.
11. Locked OOS last.

Do not tune prompts, thresholds, model choice, temperature, or sample seed after
seeing a good smoke result and then keep re-reading the same
development/validation periods. For the first smoke observation, freeze
`prompt_version`, model ID, temperature, and `sample_seed` and run it once.

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
  --split development \
  --allow-partial-labels
```

The AI evaluator uses the same split guard as the rule-only event evaluator.
Locked OOS requires `--split locked-oos --include-locked-oos` after prompt,
feature schema, model, and thresholds are frozen.
By default, partial labels are rejected for `development`, `validation`,
`locked-oos`, and `all`; `--allow-partial-labels` is only for explicit smoke
diagnostics. Do not use it for validation or locked OOS decision reports.
The report includes label-shuffled, confidence-shuffled, and random-threshold
placebos within event type. Numerical-field and bundle-shuffled prompt sets are
generated as external placebo job files and audited before local LLM execution.

For local LLM smoke results, run the semantic diagnostics before deciding
whether to continue to a larger development run:

```bash
uv run python scripts/diagnose-event-ai-smoke.py \
  --observations out/event-research/observations.jsonl \
  --labels out/event-ai/labels-balanced100.jsonl \
  --output-json out/event-ai/smoke-diagnostics-balanced100.json \
  --output-csv out/event-ai/smoke-diagnostics-balanced100.csv \
  --split development
```

The smoke diagnostics checks whether AI selection beats rule-only technical,
whether positive labels beat negative labels, whether stronger labels beat
weaker labels, and whether confidence buckets are monotonic. Failing these
checks blocks full development LLM execution with the current prompt/selection
rule.

When an external placebo run exists, compare the real and placebo labels on the
common label cohort before any larger run:

```bash
uv run python scripts/compare-event-ai-placebo.py \
  --observations out/event-research/observations.jsonl \
  --real-labels out/event-ai/labels-balanced100.jsonl \
  --placebo-labels out/event-ai/labels-balanced100-bundle-placebo.jsonl \
  --output-json out/event-ai/placebo-compare-balanced100.json \
  --output-csv out/event-ai/placebo-compare-balanced100.csv \
  --split development \
  --ohlcv data/reference/daily_ohlcv_20210625_20260624_bydate.csv \
  --random-seeds 300
```

The placebo comparison reports real-only, placebo-only, both-pass, and
neither-pass cohorts under the same exit arms. It also emits confidence
distribution warnings when confidence collapses into a single bucket. When
`--ohlcv` is supplied, it uses the true daily OHLCV random-date pool for
`same_symbol_random_date` and reports matched/fallback coverage. A strong real
AI arm that is matched by bundle placebo does not qualify for a larger
development run.

Supply `--real-jobs` and `--placebo-jobs` to compare prompt sections. This is
required when diagnosing why a placebo remains strong. For `bundle_shuffled`,
`official_numeric_summary` is expected to remain identical while feature
bundles differ. For `official_numeric_summary_shuffled`, the opposite should be
true. For `feature_and_official_numeric_shuffled`, only event metadata should
remain identical.

If external placebo remains strong, build a deterministic feature-bundle proxy
without LLM calls and compare it to the real labels before expanding the run:

```bash
uv run python scripts/build-event-ai-feature-proxy-labels.py \
  --observations out/event-research/observations.jsonl \
  --event-ids-from-labels out/event-ai/labels-balanced100.jsonl \
  --output out/event-ai/labels-balanced100-feature-proxy-v0.jsonl \
  --split development

uv run python scripts/evaluate-event-ai.py \
  --observations out/event-research/observations.jsonl \
  --labels out/event-ai/labels-balanced100-feature-proxy-v0.jsonl \
  --output-dir out/event-ai/eval-balanced100-feature-proxy-v0 \
  --split development \
  --allow-partial-labels

uv run python scripts/compare-event-ai-placebo.py \
  --observations out/event-research/observations.jsonl \
  --real-labels out/event-ai/labels-balanced100.jsonl \
  --placebo-labels out/event-ai/labels-balanced100-feature-proxy-v0.jsonl \
  --real-name real \
  --placebo-name feature_proxy \
  --output-json out/event-ai/placebo-compare-balanced100-feature-proxy-v0.json \
  --output-csv out/event-ai/placebo-compare-balanced100-feature-proxy-v0.csv \
  --split development \
  --ohlcv data/reference/daily_ohlcv_20210625_20260624_bydate.csv \
  --random-seeds 300
```

This proxy is a diagnostic only. Passing or failing it does not promote a
candidate; it only checks whether the current AI-pass behavior can be explained
by coarse pre-registered feature thresholds.

7. Run a local OpenAI-compatible model:

```bash
LLM_PROVIDER=openai_compatible \
LOCAL_LLM_BASE_URL=http://127.0.0.1:8000/v1 \
LOCAL_LLM_API_KEY=local \
LOCAL_LLM_MODEL=local-model \
LOCAL_LLM_TIMEOUT_SECONDS=60 \
LOCAL_LLM_MAX_CONCURRENCY=2 \
uv run python scripts/run-event-llm-jobs.py \
  --jobs out/event-ai/jobs-balanced100.jsonl \
  --provider openai_compatible \
  --output-labels out/event-ai/labels-balanced100.jsonl \
  --output-failures out/event-ai/failures-balanced100.jsonl \
  --output-manifest out/event-ai/run-manifest-balanced100.json \
  --max-jobs 100
```

`run-event-llm-jobs.py` resumes by default from existing successful labels
using a cache key derived from prompt hash, provider, model, temperature, and
seed. Use `--no-resume` only when intentionally overwriting a run. Failed jobs
are not cached and are retried on the next run.
For `openai_compatible`, the runner calls `/v1/models` before any job is sent.
If the local endpoint is down, no label is appended and no failed parser record
is created.

The local LLM is not assumed to be always available. For full train execution,
run bounded chunks only while the local endpoint is up:

```bash
LLM_PROVIDER=openai_compatible \
LOCAL_LLM_BASE_URL=http://127.0.0.1:8000/v1 \
LOCAL_LLM_API_KEY=local \
LOCAL_LLM_MODEL=local-model \
LOCAL_LLM_TIMEOUT_SECONDS=60 \
LOCAL_LLM_MAX_CONCURRENCY=2 \
uv run python scripts/run-event-llm-jobs.py \
  --jobs out/event-ai/jobs-earnings-train.jsonl \
  --provider openai_compatible \
  --output-labels out/event-ai/labels-earnings-train.jsonl \
  --output-failures out/event-ai/failures-earnings-train.jsonl \
  --output-manifest out/event-ai/run-manifest-earnings-train.json \
  --max-jobs 500 \
  --max-concurrency 2
```

Repeat the same command to resume. Do not evaluate validation or locked OOS
from partial train labels. After train labels are complete, write a train-only
report covering label distribution, parse failure rate, confidence buckets,
`ai_pass` versus `ai_reject`, rule-only versus rule-only+AI, fixed 2d/5d/10d
exits, and shuffled/placebo comparisons if available. If train supports it,
pre-register exactly one validation hypothesis before running validation once.

While train labeling is incomplete, use the train-only report as a monitoring
artifact only:

```bash
uv run python scripts/report-event-ai-train-labels.py \
  --observations out/event-research-real-pit/observations.jsonl \
  --jobs out/event-ai/jobs-earnings-train.jsonl \
  --labels out/event-ai/labels-earnings-train.jsonl \
  --failures out/event-ai/failures-earnings-train.jsonl \
  --output-json out/event-ai/train-report-earnings.json \
  --output-csv out/event-ai/train-report-earnings.csv
```

This command only accepts `--split train`. It reports partial train progress,
label distribution, parse failure rate, confidence buckets, `ai_pass` versus
`ai_reject`, rule-only versus rule-only+AI, and fixed 2d/5d/10d exits. It must
not be used as a validation or locked-OOS decision report.

Check train label completion after each chunk:

```bash
uv run python scripts/audit-event-llm-label-progress.py \
  --jobs out/event-ai/jobs-earnings-train.jsonl \
  --labels out/event-ai/labels-earnings-train.jsonl \
  --failures out/event-ai/failures-earnings-train.jsonl \
  --output out/event-ai/label-progress-earnings-train.json
```

Use `--require-complete` in automation when the next step must be blocked until
every train job has a successful label.

Build a retry queue for parser or endpoint failures without changing the
original prompt/model settings:

```bash
uv run python scripts/build-event-llm-retry-jobs.py \
  --jobs out/event-ai/jobs-earnings-train.jsonl \
  --failures out/event-ai/failures-earnings-train.jsonl \
  --output out/event-ai/jobs-earnings-train-retry-invalid-json.jsonl \
  --error-contains EventAiParseError
```

Run the resulting retry jobs through `run-event-llm-jobs.py` with the same
provider, model, temperature, seed, and output label/failure files. Successful
labels are appended/resumed by cache key; failed records remain diagnostics and
are not treated as completed labels.

For a bounded real-data validity audit before large LLM runs:

```bash
uv run python scripts/audit-event-research-data.py \
  --financial-summary-jsonl out/event-research/financial-summaries.jsonl \
  --events out/event-research/events.jsonl \
  --observations out/event-research/observations.jsonl \
  --ohlcv data/reference/daily_ohlcv.csv \
  --output out/event-research/data-audit-30d.json \
  --max-trading-days 30

uv run python scripts/evaluate-event-research.py \
  --observations out/event-research/observations.jsonl \
  --ohlcv data/reference/daily_ohlcv.csv \
  --output-dir out/event-research-eval-30d \
  --split all \
  --include-locked-oos \
  --max-trading-days 30 \
  --random-seeds 30
```

The true `same_symbol_random_date` baseline samples from daily OHLCV trading
dates, including non-event dates, excludes self event dates, and reports
matched/fallback coverage. The previous event-only sampling diagnostic is named
`same_symbol_random_event_date`.

For non-random block stability diagnostics:

```bash
uv run python scripts/scan-event-alpha-blocks.py \
  --observations out/event-research/observations.jsonl \
  --output-dir out/event-research-block-scan-60d \
  --block-trading-days 60 \
  --min-trades 30

uv run python scripts/scan-event-alpha-blocks.py \
  --observations out/event-research/observations.jsonl \
  --output-dir out/event-research-block-scan-120d \
  --block-trading-days 120 \
  --min-trades 50
```

This scan reports observation-level block diagnostics only. It deliberately
does not replace the matched-random gate.

The first post-audit hypothesis worth carrying into local LLM smoke testing is:

- event type: `forecast_revision`
- entry arm: `event_plus_fundamental_plus_technical`
- exit arms to compare: `fixed_2d`, `fixed_5d`, `fixed_10d`,
  `fixed_10d_plus_catastrophic_stop`
- reason: full-period rule-only evaluation with 300 random seeds showed
  positive net PnL, PF > 1.2, and high true `same_symbol_random_date`
  percentile for 2d/5d/10d. The unfiltered `forecast_revision` event-only arm
  remains rejected.

Example focused random check:

```bash
uv run python scripts/evaluate-event-research.py \
  --observations out/event-research/observations.jsonl \
  --ohlcv data/reference/daily_ohlcv.csv \
  --output-dir out/event-research-forecast-revision-random \
  --split all \
  --include-locked-oos \
  --event-type forecast_revision \
  --random-seeds 300
```

`event-alpha-report.json` PF/DD values are observation/trade-notional alpha
metrics, not portfolio-level PF/DD. Portfolio sizing, cash reuse, and OMS
sequencing remain out of scope for this research-only evaluator.

## Research-Continuation Candidate: `event_forecast_revision_fair_value_tech_fixed5_v0_research`

Date: 2026-06-29

Status: research-continuation only. This is not a paper/live candidate and does
not enable any route to `strategy-signals-b`, Gateway, OMS Paper, or OMS Live.

This candidate is preregistered from the development train/validation
diagnostic in
`docs/reports/forecast-revision-rule-diagnostics-2026-06-29.md`.

Fixed definition:

- event type: `forecast_revision`
- entry mode: `next_open_unconditional`
- fundamental rule: existing positive revision rule from the event research
  evaluator
- valuation filter: `forecast_per_valid == true` and `forecast_per <= 25`
- technical veto: existing preregistered event research veto
- exit: `fixed_5d` only
- cost: `ROUND_TRIP_COST_RATE=0.00298`
- trade notional for alpha metric: `DEFAULT_TRADE_NOTIONAL=100000`
- random baseline: true `same_symbol_random_date` from daily OHLCV trading
  dates, 300 seeds, no self event-date match

The fixed_10d and fixed_20d variants are explicitly not carried forward from
this diagnostic. Validation fixed_10d had negative net PnL and validation
fixed_20d was below same-symbol random-date median.

Development split diagnostic:

| Split | Trades | Net PnL | PF | Max DD | same_symbol_random_date percentile |
|---|---:|---:|---:|---:|---:|
| train | 358 | 466,421 | 1.634 | 87,924 | 0.997 |
| validation | 110 | 57,299 | 1.237 | 88,475 | 0.813 |
| development | 468 | 523,720 | 1.536 | 88,475 | 1.000 |

After this fixed definition was registered, locked OOS was inspected once:

| Split | Trades | Net PnL | PF | Max DD | same_symbol_random_date percentile |
|---|---:|---:|---:|---:|---:|
| locked-oos | 157 | 129,233 | 1.282 | 232,707 | 0.923 |

The fixed_5d alpha survived the one-shot locked OOS random-date check, but the
drawdown here is still an observation-level alpha metric. It is not a
portfolio-level risk result.

Portfolio-level simulation was then run with 100-share lots, 5 max positions,
20% max notional per position, fixed 5-session close exits, and no same-day
exit cash reuse:

| Split | Capital | Opened | Net PnL | PF | Max DD |
|---|---:|---:|---:|---:|---:|
| train | 1,000,000 | 146 | 343,829 | 1.698 | 93,313 |
| validation | 1,000,000 | 31 | 967 | 1.008 | 52,298 |
| locked-oos | 1,000,000 | 52 | 111,791 | 1.624 | 69,460 |
| train | 2,000,000 | 208 | 999,861 | 1.649 | 163,624 |
| validation | 2,000,000 | 60 | -48,579 | 0.897 | 178,095 |
| locked-oos | 2,000,000 | 69 | 334,775 | 1.832 | 93,070 |
| train | 5,000,000 | 234 | 2,689,635 | 1.606 | 507,897 |
| validation | 5,000,000 | 67 | -11,086 | 0.992 | 545,568 |
| locked-oos | 5,000,000 | 80 | 1,422,778 | 2.001 | 552,047 |

The portfolio simulation weakens the candidate materially. Locked OOS remains
strong, but validation is near breakeven at 1M and negative at 2M/5M. This
blocks paper observation until portfolio-level random baselines and same-day
selection-order stress are reported.

Portfolio-level random baseline and same-day selection-order stress were then
reported. The result blocks paper observation:

| Split | Capital | Selected PnL | PF | same_symbol_random_date percentile |
|---|---:|---:|---:|---:|
| train | 1,000,000 | 343,829 | 1.698 | 0.967 |
| train | 2,000,000 | 999,861 | 1.649 | 0.983 |
| train | 5,000,000 | 2,689,635 | 1.606 | 0.990 |
| validation | 1,000,000 | 967 | 1.008 | 0.393 |
| validation | 2,000,000 | -48,579 | 0.897 | 0.310 |
| validation | 5,000,000 | -11,086 | 0.992 | 0.427 |
| locked-oos | 1,000,000 | 111,791 | 1.624 | 0.823 |
| locked-oos | 2,000,000 | 334,775 | 1.832 | 0.873 |
| locked-oos | 5,000,000 | 1,422,778 | 2.001 | 0.963 |

Train and locked OOS are strong, but validation fails the portfolio-level
random median check for every tested capital level. Same-day order stress does
not rescue validation. This candidate remains research-continuation only and
must not be promoted to paper observation.

Next allowed action:

- wait for AI labels to provide an independently preregistered filter, or write
  a new hypothesis before any further validation/OOS inspection

Disallowed action:

- retune the `forecast_per <= 25` threshold, technical veto thresholds, or exit
  horizon after looking at validation or locked OOS
- promote this candidate to paper/live from observation-level alpha metrics
  alone

## Rejected Rule-Only Candidate: `event_earnings_quality_deep_value_tech_fixed20_v0_research`

Date: 2026-06-29

Status: rejected for paper observation. Locked OOS was not inspected.

Fixed definition before validation:

- event type: `earnings_result`
- entry mode: `next_open_unconditional`
- revised forecast EPS is positive
- forecast PER is valid and <= 15
- existing preregistered technical veto passes
- no EPS red flags
- exit: `fixed_20d`
- cost: `ROUND_TRIP_COST_RATE=0.00298`
- random baseline: true `same_symbol_random_date`, 300 seeds

Train looked strong, but validation failed the matched random-date test:

| Split | Trades | Net PnL | PF | Max DD | same_symbol_random_date percentile |
|---|---:|---:|---:|---:|---:|
| train | 3,247 | 5,089,212 | 1.638 | 2,154,819 | 0.980 |
| validation | 979 | 847,618 | 1.360 | 318,752 | 0.270 |

The validation result is profitable but below the random median. Do not retune
the PER threshold, technical veto, EPS flags, or exit horizon from this
validation result.

## Rejected Rule-Only Candidate: `event_dividend_increase_yield3_fixed2_v0_research`

Date: 2026-06-29

Status: rejected for paper observation.

Fixed definition before validation:

- event type: `dividend_revision`
- event subtype: `increase`
- entry mode: `next_open_unconditional`
- forecast dividend yield is valid and >= 3%
- exit: `fixed_2d`
- cost: `ROUND_TRIP_COST_RATE=0.00298`
- random baseline: true `same_symbol_random_date`, 300 seeds

Train and validation were strong, but locked OOS failed:

| Split | Trades | Net PnL | PF | Max DD | same_symbol_random_date percentile |
|---|---:|---:|---:|---:|---:|
| train | 246 | 103,400 | 1.288 | 88,907 | 0.990 |
| validation | 122 | 137,970 | 1.920 | 47,116 | 1.000 |
| locked-oos | 82 | -40,629 | 0.788 | 88,422 | 0.253 |

Portfolio-level locked OOS also failed at 2M and 5M capital, and was only near
breakeven at 1M. Do not retune the dividend yield threshold or exit horizon
from locked OOS.

## Research-Continuation Candidate: `event_cluster_earnings_dividend_increase_fixed20_stop_v0_research`

Date: 2026-06-29

Status: research-continuation only. This is not a paper/live candidate and does
not enable any route to `strategy-signals-b`, Gateway, OMS Paper, or OMS Live.

Fixed definition before validation and locked OOS:

- same trade cluster contains `earnings_result`
- same trade cluster contains `dividend_revision` subtype `increase`
- entry mode: `next_open_unconditional`
- exit: `fixed_20d_plus_catastrophic_stop`
- cost: `ROUND_TRIP_COST_RATE=0.00298`
- random baseline: true `same_symbol_random_date`, 300 seeds

Observation-level result:

| Split | Trades | Net PnL | PF | Max DD | same_symbol_random_date percentile |
|---|---:|---:|---:|---:|---:|
| train | 67 | 261,661 | 2.866 | 24,796 | 0.990 |
| validation | 38 | 149,309 | 3.662 | 20,298 | 0.987 |
| locked-oos | 25 | 91,968 | 1.944 | 68,852 | 0.947 |

Portfolio-level result:

| Split | Capital | Opened | Net PnL | PF | Max DD | same_symbol_random_date percentile |
|---|---:|---:|---:|---:|---:|---:|
| train | 1,000,000 | 36 | 292,788 | 3.369 | 31,158 | 0.980 |
| validation | 1,000,000 | 11 | 74,663 | 4.032 | 22,067 | 0.747 |
| locked-oos | 1,000,000 | 12 | 40,367 | 1.681 | 57,082 | 0.717 |
| train | 2,000,000 | 43 | 594,039 | 2.786 | 100,797 | 0.967 |
| validation | 2,000,000 | 20 | 234,448 | 3.074 | 59,924 | 0.800 |
| locked-oos | 2,000,000 | 17 | 188,165 | 1.949 | 154,965 | 0.847 |
| train | 5,000,000 | 47 | 1,567,148 | 2.556 | 288,155 | 0.953 |
| validation | 5,000,000 | 22 | 685,006 | 3.094 | 150,131 | 0.857 |
| locked-oos | 5,000,000 | 19 | 787,250 | 2.528 | 192,921 | 0.940 |

This is the first rule-only event candidate in this branch to survive train,
validation, and locked OOS at observation and portfolio levels. It remains
research-continuation only because locked OOS has only 25 observation-level
trades, stressed 1M locked-OOS PF is below 1.5, and low-frequency block
stability has weak blocks.

Execution stress and block stability diagnostics:

```bash
uv run python scripts/simulate-event-portfolio.py \
  --observations out/event-research-real-pit/observations.jsonl \
  --output-json out/event-research-cluster-rule-diagnostics/locked-oos-fixed20-stop-portfolio-stress-entry10-exit25-random.json \
  --output-csv out/event-research-cluster-rule-diagnostics/locked-oos-fixed20-stop-portfolio-stress-entry10-exit25-trades.csv \
  --split locked-oos \
  --include-locked-oos \
  --candidate-id event_cluster_earnings_dividend_increase_fixed20_stop_v0_research \
  --capital 1000000 \
  --capital 2000000 \
  --capital 5000000 \
  --ohlcv data/reference/daily_ohlcv_20210625_20260624_bydate.csv \
  --random-seeds 300 \
  --entry-additional-slippage-bps 10 \
  --exit-additional-slippage-bps 25

uv run python scripts/summarize-event-cluster-portfolio-stability.py \
  --observations out/event-research-real-pit/observations.jsonl \
  --ohlcv data/reference/daily_ohlcv_20210625_20260624_bydate.csv \
  --output-json out/event-research-cluster-rule-diagnostics/fixed20-stop-portfolio-block-stability-60d-random.json \
  --output-csv out/event-research-cluster-rule-diagnostics/fixed20-stop-portfolio-block-stability-60d-random.csv \
  --split all \
  --include-locked-oos \
  --block-trading-days 60 \
  --capital 1000000 \
  --capital 2000000 \
  --capital 5000000 \
  --random-seeds 300
```

Stress result summary:

| Stress | Split | Capital | Opened | Net PnL | PF | Max DD | same_symbol_random_date percentile |
|---|---|---:|---:|---:|---:|---:|---:|
| entry10_exit25 | locked-oos | 1,000,000 | 12 | 28,797 | 1.472 | 58,885 | 0.703 |
| entry10_exit25 | locked-oos | 2,000,000 | 17 | 162,829 | 1.791 | 161,432 | 0.853 |
| entry10_exit25 | locked-oos | 5,000,000 | 19 | 718,309 | 2.325 | 207,359 | 0.940 |
| exit50 | locked-oos | 1,000,000 | 12 | 30,397 | 1.484 | 59,576 | 0.720 |
| exit50 | locked-oos | 2,000,000 | 17 | 159,349 | 1.764 | 163,982 | 0.863 |
| exit50 | locked-oos | 5,000,000 | 19 | 695,285 | 2.257 | 216,569 | 0.943 |

60 trading-session block stability summary:

| Capital | Active blocks | Positive block ratio | Worst block PnL | Median block PnL | Worst block DD | Median random percentile |
|---:|---:|---:|---:|---:|---:|---:|
| 1,000,000 | 16 | 0.875 | -57,081 | 25,716 | 57,081 | 0.793 |
| 2,000,000 | 18 | 0.778 | -56,863 | 37,449 | 154,965 | 0.788 |
| 5,000,000 | 18 | 0.778 | -154,880 | 120,187 | 192,921 | 0.780 |

Weak 1M blocks were `2024-04-12` to `2024-07-09` and `2026-04-02` to
`2026-05-15`; the latter had random percentile 0.030. Do not retune the
candidate definition, horizon, or stop using these locked-OOS diagnostics.

Adding the already preregistered coarse technical veto is only a diagnostic:

| Split | Trades | Net PnL | PF | Max DD | same_symbol_random_date percentile |
|---|---:|---:|---:|---:|---:|
| train | 13 | 51,451 | 2.708 | 18,534 | 0.947 |
| validation | 14 | 73,429 | 3.749 | 18,257 | 0.967 |
| locked-oos | 8 | 38,584 | 2.140 | 33,850 | 0.817 |

This plausibly reduces tail risk, but the sample is too small to register as a
paper/live candidate. Treat it as a hypothesis for a future train-only
preregistration cycle.

## Research-Continuation Candidate: `event_cluster_earnings_dividend_value_guard_fixed20_stop_v1_research`

Date: 2026-06-29

Status: research-continuation only. This is not a paper/live candidate and does
not enable any route to `strategy-signals-b`, Gateway, OMS Paper, or OMS Live.

V1 was fixed after train/validation tail-risk profiling and before the single
locked-OOS run:

- same trade cluster contains `earnings_result`
- same trade cluster contains `dividend_revision` subtype `increase`
- if forecast PER is available point-in-time, cluster minimum forecast PER must be <= 15
- if forecast PER is unavailable point-in-time, the cluster is not rejected only for that absence
- entry mode: `next_open_unconditional`
- exit: `fixed_20d_plus_catastrophic_stop`
- cost: `ROUND_TRIP_COST_RATE=0.00298`
- random baseline: true `same_symbol_random_date`, 300 seeds

Observation-level result:

| Split | Trades | Net PnL | PF | Max DD | same_symbol_random_date percentile |
|---|---:|---:|---:|---:|---:|
| train | 63 | 267,587 | 3.194 | 24,796 | 0.997 |
| validation | 32 | 155,687 | 4.810 | 13,012 | 0.977 |
| locked-oos | 22 | 94,922 | 2.089 | 68,852 | 0.933 |

Portfolio-level result:

| Split | Capital | Opened | Net PnL | PF | Max DD | same_symbol_random_date percentile |
|---|---:|---:|---:|---:|---:|---:|
| train | 1,000,000 | 35 | 279,626 | 3.262 | 31,158 | 0.977 |
| validation | 1,000,000 | 11 | 74,663 | 4.032 | 22,067 | 0.793 |
| locked-oos | 1,000,000 | 9 | 44,936 | 2.036 | 41,194 | 0.737 |
| train | 2,000,000 | 40 | 588,495 | 2.971 | 100,797 | 0.963 |
| validation | 2,000,000 | 19 | 322,967 | 5.408 | 44,134 | 0.907 |
| locked-oos | 2,000,000 | 15 | 197,617 | 2.193 | 117,894 | 0.853 |
| train | 5,000,000 | 43 | 1,588,206 | 2.831 | 288,155 | 0.960 |
| validation | 5,000,000 | 20 | 905,446 | 5.510 | 114,616 | 0.907 |
| locked-oos | 5,000,000 | 17 | 810,179 | 2.904 | 161,253 | 0.927 |

Locked-OOS stress remains positive:

| Stress | Capital | Opened | Net PnL | PF | Max DD | same_symbol_random_date percentile |
|---|---:|---:|---:|---:|---:|---:|
| entry10_exit25 | 1,000,000 | 9 | 35,029 | 1.784 | 42,495 | 0.720 |
| entry10_exit25 | 2,000,000 | 15 | 175,229 | 2.015 | 123,190 | 0.853 |
| entry10_exit25 | 5,000,000 | 17 | 748,378 | 2.655 | 173,762 | 0.927 |
| exit50 | 1,000,000 | 9 | 37,334 | 1.808 | 42,994 | 0.760 |
| exit50 | 2,000,000 | 15 | 171,967 | 1.975 | 125,292 | 0.877 |
| exit50 | 5,000,000 | 17 | 727,351 | 2.568 | 178,892 | 0.930 |

60 trading-session block stability is improved but still not clean:

| Capital | Active blocks | Positive block ratio | Worst block PnL | Median block PnL | Worst block DD | Median random percentile |
|---:|---:|---:|---:|---:|---:|---:|
| 1,000,000 | 16 | 0.875 | -41,194 | 25,716 | 41,194 | 0.800 |
| 2,000,000 | 18 | 0.833 | -56,863 | 45,948 | 117,894 | 0.795 |
| 5,000,000 | 18 | 0.833 | -154,880 | 139,009 | 165,204 | 0.780 |

The 2026-04-02 to 2026-05-15 block remains weak at 1M with random percentile
`0.010`. V1 is the best rule-only event candidate so far, but it does not pass
paper observation yet.

Until a TOPIX or market-breadth series is available in the point-in-time
dataset, the per-symbol trend bucket is written as `symbol_regime`.
`market_regime` is retained only as a backward-compatible alias for older
artifacts and must not be interpreted as broad market breadth.

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
