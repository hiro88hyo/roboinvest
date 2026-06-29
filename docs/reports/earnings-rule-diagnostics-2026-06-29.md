# Earnings Rule Diagnostics - 2026-06-29

This is an LLM-free event/fundamental/valuation/technical diagnostic. It does not register a
paper/live candidate and does not change any production route.

Inputs:

- Observations: `out/event-research-real-pit/observations.jsonl`
- Train diagnostic:
  - `out/event-research-earnings-rule-diagnostics/train-rule-diagnostics.json`
  - `out/event-research-earnings-rule-diagnostics/train-rule-diagnostics-random.json`
- Validation diagnostic:
  - `out/event-research-earnings-rule-diagnostics/validation-rule-diagnostics-random.json`
- Locked OOS: not inspected

## Train Candidate

The strongest train-only hypothesis was:

`earnings_quality_deep_value_plus_technical`

Definition:

- event type: `earnings_result`
- revised forecast EPS is positive
- forecast PER is valid and <= 15
- existing preregistered technical veto passes
- no EPS red flags

Train results with 300 seed true `same_symbol_random_date` baseline:

| Exit | Trades | Net PnL | PF | Max DD | Random percentile | Random median | Random p75 | Random p90 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| fixed_2d | 3,247 | 179,176 | 1.033 | 466,967 | 0.987 | -463,706 | -309,350 | -147,145 |
| fixed_5d | 3,247 | 1,070,192 | 1.181 | 974,401 | 0.983 | 251,176 | 496,889 | 733,153 |
| fixed_10d | 3,247 | 2,418,435 | 1.362 | 1,797,501 | 0.953 | 1,421,987 | 1,730,912 | 2,105,122 |
| fixed_20d | 3,247 | 5,089,212 | 1.638 | 2,154,819 | 0.980 | 3,638,596 | 4,126,059 | 4,557,856 |
| fixed_10d_plus_catastrophic_stop | 3,247 | 2,242,505 | 1.331 | 1,750,230 | 0.967 | 1,139,584 | 1,467,905 | 1,802,619 |
| fixed_20d_plus_catastrophic_stop | 3,247 | 4,208,273 | 1.496 | 2,204,316 | 0.980 | 2,674,836 | 3,095,025 | 3,534,948 |

## Preregistered Research-Continuation Candidate

Candidate ID:

`event_earnings_quality_deep_value_tech_fixed20_v0_research`

Fixed definition before validation:

- event type: `earnings_result`
- entry mode: `next_open_unconditional`
- fundamental/valuation filter: `earnings_quality_deep_value_plus_technical`
- exit: `fixed_20d` only
- cost: `ROUND_TRIP_COST_RATE=0.00298`
- random baseline: true `same_symbol_random_date`, 300 seeds
- paper/live route: disabled

The fixed_2d, fixed_5d, fixed_10d, and catastrophic-stop variants are not part of this
candidate. They remain diagnostics only.

## Next Step

The fixed candidate was then run once on validation:

| Split | Exit | Trades | Net PnL | PF | Max DD | Random percentile | Random median | Random p75 | Random p90 |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| validation | fixed_20d | 979 | 847,618 | 1.360 | 318,752 | 0.270 | 1,065,173 | 1,295,450 | 1,443,980 |

The result is profitable but fails the matched random-date test. It is below the random median, so
`event_earnings_quality_deep_value_tech_fixed20_v0_research` is rejected for paper observation.

Do not retune the PER threshold, technical veto, EPS flags, or exit horizon from validation. Locked
OOS remains uninspected for this earnings candidate.
