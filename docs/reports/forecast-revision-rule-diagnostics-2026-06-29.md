# Forecast Revision Rule Diagnostics - 2026-06-29

This is a diagnostic-only report for AI-less event swing research. It does not register a
paper/live candidate and does not change any production route.

Inputs:

- Observations: `out/event-research-real-pit/observations.jsonl`
- Split policy: existing point-in-time split with 20 trading day purge
- Locked OOS: not inspected
- Main diagnostic output:
  - `out/event-research-forecast-revision-diagnostics/development-rule-diagnostics.json`
  - `out/event-research-forecast-revision-diagnostics/train-rule-diagnostics.json`
  - `out/event-research-forecast-revision-diagnostics/validation-rule-diagnostics.json`
- Exit-specific random baseline output:
  - `out/event-research-forecast-revision-diagnostics/development-rule-diagnostics-exit-random.json`
  - `out/event-research-forecast-revision-diagnostics/train-rule-diagnostics-exit-random.json`
  - `out/event-research-forecast-revision-diagnostics/validation-rule-diagnostics-exit-random.json`
  - `out/event-research-forecast-revision-diagnostics/locked-oos-rule-diagnostics-exit-random.json`

## Candidate Diagnostic

The strongest diagnostic rule was:

`fair_or_cheap_positive_revision_plus_technical`

Definition:

- existing positive revision rule passes
- forecast PER is valid and <= 25
- existing preregistered technical veto passes

This is a diagnostic rule only. It is not yet a registered strategy candidate.

## Results

### Development

| Exit | Trades | Net PnL | PF | Max DD |
|---|---:|---:|---:|---:|
| fixed_5d | 468 | 523,720 | 1.536 | 88,475 |
| fixed_10d | 468 | 388,209 | 1.259 | 229,835 |
| fixed_20d | 468 | 581,665 | 1.304 | 316,193 |

### Train

| Exit | Trades | Net PnL | PF | Max DD |
|---|---:|---:|---:|---:|
| fixed_5d | 358 | 466,421 | 1.634 | 87,924 |
| fixed_10d | 358 | 441,121 | 1.405 | 172,948 |
| fixed_20d | 358 | 540,974 | 1.353 | 316,193 |

### Validation

| Exit | Trades | Net PnL | PF | Max DD |
|---|---:|---:|---:|---:|
| fixed_5d | 110 | 57,299 | 1.237 | 88,475 |
| fixed_10d | 110 | -52,912 | 0.870 | 229,835 |
| fixed_20d | 110 | 40,691 | 1.106 | 202,993 |

## Interpretation

The signal improves substantially over broad forecast revision exposure, but the horizon is not
stable. The fixed_5d version is the most consistent across train and validation. The fixed_10d
version weakens in validation, so it should not be promoted based on the earlier all-period
fixed10 result alone.

Exit-specific same-symbol random-date baselines were then computed with 300 seeds. Coverage was
complete for the selected observations in all three development splits, with no fallback:

| Split | Exit | Selected PnL | PF | same_symbol_random_date percentile | Random median | Random p75 | Random p90 |
|---|---|---:|---:|---:|---:|---:|---:|
| development | fixed_5d | 523,720 | 1.536 | 1.000 | 37,902 | 121,190 | 210,903 |
| development | fixed_10d | 388,209 | 1.259 | 0.857 | 178,122 | 307,499 | 427,253 |
| development | fixed_20d | 581,665 | 1.304 | 0.630 | 498,902 | 668,871 | 841,214 |
| train | fixed_5d | 466,421 | 1.634 | 0.997 | 32,704 | 99,606 | 176,157 |
| train | fixed_10d | 441,121 | 1.405 | 0.960 | 126,114 | 239,802 | 360,711 |
| train | fixed_20d | 540,974 | 1.353 | 0.713 | 388,538 | 576,749 | 679,013 |
| validation | fixed_5d | 57,299 | 1.237 | 0.813 | 3,867 | 39,795 | 80,248 |
| validation | fixed_10d | -52,912 | 0.870 | 0.137 | 39,732 | 93,004 | 132,807 |
| validation | fixed_20d | 40,691 | 1.106 | 0.307 | 90,612 | 164,717 | 234,176 |

This supports the fixed_5d hypothesis only. The fixed_10d and fixed_20d variants fail the
validation random-date comparison and should remain diagnostic/rejected for now.

After preregistering the fixed_5d-only research-continuation candidate in
`docs/features/event-ai-swing-plan.md`, locked OOS was inspected once with the same fixed
definition and 300 seed baseline:

| Split | Exit | Trades | Selected PnL | PF | Max DD | same_symbol_random_date percentile | Random median | Random p75 | Random p90 |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| locked-oos | fixed_5d | 157 | 129,233 | 1.282 | 232,707 | 0.923 | 33,985 | 78,504 | 121,906 |
| locked-oos | fixed_10d | 157 | 380,622 | 1.780 | 203,652 | 0.997 | 86,444 | 150,375 | 214,120 |
| locked-oos | fixed_20d | 155 | 213,941 | 1.272 | 274,667 | 0.500 | 214,903 | 311,647 | 387,870 |

The fixed_5d alpha survived the one-shot locked OOS random-date check. The observation-level
drawdown is still large, and this report does not convert it into portfolio-level risk. The
locked-oos fixed_10d result must not revive fixed_10d because fixed_10d failed validation before
the locked OOS check.

## Portfolio-Level Simulation

A research-only portfolio simulation was added after the fixed_5d definition was locked:

- script: `scripts/simulate-event-portfolio.py`
- output:
  - `out/event-research-forecast-revision-diagnostics/train-fixed5-portfolio-simulation.json`
  - `out/event-research-forecast-revision-diagnostics/validation-fixed5-portfolio-simulation.json`
  - `out/event-research-forecast-revision-diagnostics/development-fixed5-portfolio-simulation.json`
  - `out/event-research-forecast-revision-diagnostics/locked-oos-fixed5-portfolio-simulation.json`
  - `out/event-research-forecast-revision-diagnostics/train-fixed5-portfolio-simulation-random.json`
  - `out/event-research-forecast-revision-diagnostics/validation-fixed5-portfolio-simulation-random.json`
  - `out/event-research-forecast-revision-diagnostics/locked-oos-fixed5-portfolio-simulation-random.json`
- entry: next open
- exit: fixed 5 trading sessions, close exit
- same-day exit cash reuse: disabled
- same-symbol overlap: disabled
- position cap: 5
- max notional per position: 20% of starting capital
- lot size: 100 shares
- cost per side: 0.149%

| Split | Capital | Candidates | Opened | Net PnL | PF | Max DD | Hit Rate | Position-cap skips | Lot-size skips |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| train | 1,000,000 | 358 | 146 | 343,829 | 1.698 | 93,313 | 0.575 | 29 | 183 |
| train | 2,000,000 | 358 | 208 | 999,861 | 1.649 | 163,624 | 0.567 | 102 | 48 |
| train | 5,000,000 | 358 | 234 | 2,689,635 | 1.606 | 507,897 | 0.556 | 123 | 1 |
| validation | 1,000,000 | 110 | 31 | 967 | 1.008 | 52,298 | 0.548 | 16 | 63 |
| validation | 2,000,000 | 110 | 60 | -48,579 | 0.897 | 178,095 | 0.533 | 36 | 14 |
| validation | 5,000,000 | 110 | 67 | -11,086 | 0.992 | 545,568 | 0.582 | 42 | 1 |
| development | 1,000,000 | 468 | 177 | 344,797 | 1.562 | 93,313 | 0.571 | 45 | 246 |
| development | 2,000,000 | 468 | 268 | 951,282 | 1.472 | 250,402 | 0.560 | 138 | 62 |
| development | 5,000,000 | 468 | 301 | 2,697,327 | 1.458 | 694,633 | 0.561 | 165 | 2 |
| locked-oos | 1,000,000 | 157 | 52 | 111,791 | 1.624 | 69,460 | 0.654 | 19 | 86 |
| locked-oos | 2,000,000 | 157 | 69 | 334,775 | 1.832 | 93,070 | 0.609 | 56 | 32 |
| locked-oos | 5,000,000 | 157 | 80 | 1,422,778 | 2.001 | 552,047 | 0.613 | 73 | 4 |

The portfolio-level result is weaker than the observation-level result. The 1M validation run is
only near breakeven, and the 2M/5M validation runs are negative. The locked OOS portfolio result is
good, but it cannot override the weaker validation portfolio stability. This candidate remains
research-continuation only and is not ready for paper observation.

Portfolio-level same-symbol random-date baselines were then run with 300 seeds and no fallback:

| Split | Capital | Selected PnL | PF | Max DD | Random percentile | Random median | Random p75 | Random p90 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| train | 1,000,000 | 343,829 | 1.698 | 93,313 | 0.967 | 39,518 | 113,038 | 202,083 |
| train | 2,000,000 | 999,861 | 1.649 | 163,624 | 0.983 | 99,726 | 313,867 | 549,465 |
| train | 5,000,000 | 2,689,635 | 1.606 | 507,897 | 0.990 | 146,022 | 909,398 | 1,440,725 |
| validation | 1,000,000 | 967 | 1.008 | 52,298 | 0.393 | 17,791 | 49,872 | 84,106 |
| validation | 2,000,000 | -48,579 | 0.897 | 178,095 | 0.310 | 32,899 | 144,184 | 240,406 |
| validation | 5,000,000 | -11,086 | 0.992 | 545,568 | 0.427 | 69,478 | 350,942 | 668,134 |
| locked-oos | 1,000,000 | 111,791 | 1.624 | 69,460 | 0.823 | 27,694 | 87,011 | 140,243 |
| locked-oos | 2,000,000 | 334,775 | 1.832 | 93,070 | 0.873 | 87,054 | 227,779 | 368,350 |
| locked-oos | 5,000,000 | 1,422,778 | 2.001 | 552,047 | 0.963 | 242,259 | 674,320 | 1,126,474 |

This blocks paper observation. Train and locked OOS are strong, but validation fails the
portfolio-level random median check at every tested capital level.

Same-day selection-order stress was also run. Validation remained weak across orderings:

| Split | Capital | Worst order PnL / PF / DD | Best order PnL / PF / DD |
|---|---:|---|---|
| train | 1,000,000 | 317,530 / 1.631 / 79,705 | 343,829 / 1.698 / 93,313 |
| train | 2,000,000 | 685,122 / 1.417 / 218,288 | 1,044,796 / 1.710 / 197,126 |
| train | 5,000,000 | 2,339,423 / 1.522 / 573,360 | 3,239,483 / 1.726 / 586,820 |
| validation | 1,000,000 | 967 / 1.008 / 52,298 | 13,167 / 1.111 / 49,918 |
| validation | 2,000,000 | -91,394 / 0.819 / 178,095 | -47,117 / 0.901 / 178,095 |
| validation | 5,000,000 | -316,617 / 0.793 / 545,568 | -8,480 / 0.994 / 545,568 |
| locked-oos | 1,000,000 | 111,691 / 1.623 / 71,267 | 111,791 / 1.624 / 69,460 |
| locked-oos | 2,000,000 | 91,583 / 1.157 / 199,918 | 334,775 / 1.832 / 93,070 |
| locked-oos | 5,000,000 | 285,549 / 1.131 / 1,193,416 | 1,422,778 / 2.001 / 552,047 |

## Next Step

Do not promote this candidate to paper observation. The next research step should not retune this
fixed_5d candidate against validation. Either wait for AI labels to provide an independently
pre-registered filter, or define a new hypothesis before looking at further validation/OOS detail.
