# Dividend Rule Diagnostics - 2026-06-29

This is an LLM-free event/fundamental/valuation/technical diagnostic. It does not register a
paper/live candidate and does not change any production route.

Inputs:

- Observations: `out/event-research-real-pit/observations.jsonl`
- Train diagnostic:
  - `out/event-research-dividend-rule-diagnostics/train-rule-diagnostics.json`
  - `out/event-research-dividend-rule-diagnostics/train-rule-diagnostics-random.json`
- Validation diagnostic:
  - `out/event-research-dividend-rule-diagnostics/validation-rule-diagnostics-random.json`
- Locked OOS diagnostic:
  - `out/event-research-dividend-rule-diagnostics/locked-oos-rule-diagnostics-random.json`
- Portfolio diagnostics:
  - `out/event-research-dividend-rule-diagnostics/train-fixed2-portfolio-simulation-random.json`
  - `out/event-research-dividend-rule-diagnostics/validation-fixed2-portfolio-simulation-random.json`
  - `out/event-research-dividend-rule-diagnostics/locked-oos-fixed2-portfolio-simulation-random.json`

Train dividend distribution:

- total dividend_revision observations: 1,259
- increase: 572
- decrease: 78
- invalid: 609

Decrease and invalid subtypes are not eligible long candidates, even when their train returns look
good.

## Train Candidate

The strongest eligible train-only hypothesis was:

`increase_yield_3pct`

Definition:

- event type: `dividend_revision`
- event subtype: `increase`
- point-in-time forecast dividend yield is valid and >= 3%

Train results with 300 seed true `same_symbol_random_date` baseline:

| Exit | Trades | Net PnL | PF | Max DD | Random percentile | Random median | Random p75 | Random p90 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| fixed_2d | 246 | 103,400 | 1.288 | 88,907 | 0.990 | -46,669 | -8,647 | 17,736 |
| fixed_5d | 246 | 70,864 | 1.145 | 139,642 | 0.823 | -4,066 | 55,726 | 90,770 |
| fixed_10d | 246 | 23,329 | 1.035 | 233,156 | 0.340 | 64,596 | 126,138 | 185,402 |
| fixed_20d | 246 | 265,552 | 1.366 | 154,027 | 0.737 | 178,458 | 277,649 | 367,225 |
| fixed_20d_plus_catastrophic_stop | 246 | 204,736 | 1.276 | 146,115 | 0.727 | 128,878 | 214,122 | 309,440 |

## Preregistered Research-Continuation Candidate

Candidate ID:

`event_dividend_increase_yield3_fixed2_v0_research`

Fixed definition before validation:

- event type: `dividend_revision`
- event subtype: `increase`
- entry mode: `next_open_unconditional`
- forecast dividend yield is valid and >= 3%
- exit: `fixed_2d` only
- cost: `ROUND_TRIP_COST_RATE=0.00298`
- random baseline: true `same_symbol_random_date`, 300 seeds
- paper/live route: disabled

The fixed_5d, fixed_10d, fixed_20d, and technical-veto variants are not part of this candidate.
They remain diagnostics only.

## Next Step

The fixed candidate was then run once on validation and locked OOS:

| Split | Trades | Net PnL | PF | Max DD | Random percentile | Random median | Random p75 | Random p90 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| train | 246 | 103,400 | 1.288 | 88,907 | 0.990 | -46,669 | -8,647 | 17,736 |
| validation | 122 | 137,970 | 1.920 | 47,116 | 1.000 | -17,433 | 4,611 | 27,434 |
| locked-oos | 82 | -40,629 | 0.788 | 88,422 | 0.253 | -21,320 | 4,132 | 24,010 |

Portfolio-level simulation with 100-share lots, 5 max positions, 20% max notional per position,
fixed 2-session close exits, and no same-day exit cash reuse:

| Split | Capital | Opened | Net PnL | PF | Max DD | Random percentile |
|---|---:|---:|---:|---:|---:|---:|
| train | 1,000,000 | 163 | 213,426 | 1.617 | 77,352 | 0.997 |
| train | 2,000,000 | 217 | 502,987 | 1.513 | 219,599 | 0.997 |
| train | 5,000,000 | 228 | 1,193,740 | 1.406 | 606,698 | 0.997 |
| validation | 1,000,000 | 75 | 92,381 | 1.555 | 66,923 | 0.980 |
| validation | 2,000,000 | 96 | 281,218 | 1.663 | 176,219 | 0.993 |
| validation | 5,000,000 | 103 | 1,052,178 | 1.864 | 428,677 | 1.000 |
| locked-oos | 1,000,000 | 52 | 639 | 1.005 | 58,851 | 0.683 |
| locked-oos | 2,000,000 | 72 | -59,604 | 0.842 | 165,257 | 0.497 |
| locked-oos | 5,000,000 | 82 | -154,609 | 0.902 | 656,017 | 0.533 |

The candidate fails locked OOS and is rejected for paper observation. Do not retune the dividend
yield threshold or exit horizon from locked OOS.
