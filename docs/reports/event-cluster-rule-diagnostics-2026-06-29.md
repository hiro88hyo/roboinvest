# Event Cluster Rule Diagnostics - 2026-06-29

This is an LLM-free multi-event cluster diagnostic. It does not register a paper/live candidate
and does not change any production route.

> **Erratum added 2026-07-10:** portfolio-level matched-random rows in this
> historical report used an 8% catastrophic stop (`entry_price * 0.92`), while
> selected observations used the registered 10% stop (`CAT_STOP_PCT=-0.10`).
> Portfolio random percentiles, including execution-stress and block-stability
> random percentiles, are therefore non-comparable and must not be used as gate
> evidence. Selected-path trade counts, PnL, PF, and drawdown remain recorded
> below as historical calculations; their presence does not authorize paper
> observation. The simulator is corrected for future preregistered runs, but
> the frozen locked-OOS window will not be rerun or reinspected without the
> explicit approval required by ADR-0005.

Inputs:

- Observations: `out/event-research-real-pit/observations.jsonl`
- Train diagnostic:
  - `out/event-research-cluster-rule-diagnostics/train-rule-diagnostics.json`
  - `out/event-research-cluster-rule-diagnostics/train-rule-diagnostics-random.json`
- Validation diagnostic:
  - `out/event-research-cluster-rule-diagnostics/validation-rule-diagnostics-random.json`
- Locked OOS diagnostic:
  - `out/event-research-cluster-rule-diagnostics/locked-oos-rule-diagnostics-random.json`
- Portfolio diagnostics:
  - `out/event-research-cluster-rule-diagnostics/train-fixed20-stop-portfolio-simulation-random.json`
  - `out/event-research-cluster-rule-diagnostics/validation-fixed20-stop-portfolio-simulation-random.json`
  - `out/event-research-cluster-rule-diagnostics/locked-oos-fixed20-stop-portfolio-simulation-random.json`

Train summary:

- trade clusters: 53,257
- multi-event clusters: 2,178

## Train Candidate

The strongest train-only hypothesis was:

`earnings_plus_dividend_increase`

Definition:

- same trade cluster contains `earnings_result`
- same trade cluster contains `dividend_revision` with subtype `increase`

Train results with 300 seed true `same_symbol_random_date` baseline:

| Exit | Trades | Net PnL | PF | Max DD | Random percentile | Random median | Random p75 | Random p90 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| fixed_2d | 67 | 44,123 | 1.415 | 29,900 | 0.970 | -14,632 | 5,239 | 25,169 |
| fixed_5d | 67 | 109,897 | 2.065 | 21,177 | 0.987 | 182 | 25,238 | 50,727 |
| fixed_10d | 67 | 203,256 | 2.784 | 24,737 | 0.997 | 11,831 | 53,446 | 93,536 |
| fixed_20d | 67 | 254,993 | 2.736 | 29,366 | 0.983 | 45,553 | 102,326 | 156,756 |
| fixed_20d_plus_catastrophic_stop | 67 | 261,661 | 2.866 | 24,796 | 0.990 | 29,511 | 83,077 | 136,006 |

## Preregistered Research-Continuation Candidate

Candidate ID:

`event_cluster_earnings_dividend_increase_fixed20_stop_v0_research`

Fixed definition before validation:

- cluster contains `earnings_result`
- cluster contains `dividend_revision` subtype `increase`
- entry mode: `next_open_unconditional`
- exit: `fixed_20d_plus_catastrophic_stop`
- cost: `ROUND_TRIP_COST_RATE=0.00298`
- random baseline: true `same_symbol_random_date`, 300 seeds
- paper/live route: disabled

The 2d, 5d, 10d, and fixed_20d-no-stop variants are not part of this candidate. They remain
diagnostics only.

## Next Step

The fixed candidate was then run once on validation and locked OOS:

| Split | Trades | Net PnL | PF | Max DD | Random percentile |
|---|---:|---:|---:|---:|---:|
| train | 67 | 261,661 | 2.866 | 24,796 | 0.990 |
| validation | 38 | 149,309 | 3.662 | 20,298 | 0.987 |
| locked-oos | 25 | 91,968 | 1.944 | 68,852 | 0.947 |

Portfolio-level simulation with 100-share lots, 5 max positions, 20% max notional per position,
fixed 20-session catastrophic-stop exits, and no same-day exit cash reuse:

| Split | Capital | Opened | Net PnL | PF | Max DD | Random percentile |
|---|---:|---:|---:|---:|---:|---:|
| train | 1,000,000 | 36 | 292,788 | 3.369 | 31,158 | 0.980 |
| train | 2,000,000 | 43 | 594,039 | 2.786 | 100,797 | 0.967 |
| train | 5,000,000 | 47 | 1,567,148 | 2.556 | 288,155 | 0.953 |
| validation | 1,000,000 | 11 | 74,663 | 4.032 | 22,067 | 0.747 |
| validation | 2,000,000 | 20 | 234,448 | 3.074 | 59,924 | 0.800 |
| validation | 5,000,000 | 22 | 685,006 | 3.094 | 150,131 | 0.857 |
| locked-oos | 1,000,000 | 12 | 40,367 | 1.681 | 57,082 | 0.717 |
| locked-oos | 2,000,000 | 17 | 188,165 | 1.949 | 154,965 | 0.847 |
| locked-oos | 5,000,000 | 19 | 787,250 | 2.528 | 192,921 | 0.940 |

This is the first rule-only event candidate in the current research branch to survive train,
validation, and locked OOS at both observation and portfolio levels. It remains research-only
because locked OOS has only 25 observation-level trades and paper observation requires additional
stress and low-frequency stability diagnostics.

Next steps:

- run paper-only sequence/audit only after the stress and block diagnostics are accepted
- do not retune the cluster definition, exit horizon, or stop from locked OOS

## Execution Stress

The fixed candidate was re-run without changing selection, exit horizon, stop, cost, or random
baseline. These are execution-only stress diagnostics:

- `entry10_exit25`: entry price worsened by 10 bps and exit price worsened by 25 bps
- `exit50`: exit price worsened by 50 bps as a conservative gap-stop/additional-slippage proxy

Portfolio-level result, 300 seed true `same_symbol_random_date` baseline:

| Stress | Split | Capital | Opened | Net PnL | PF | Max DD | Random percentile |
|---|---|---:|---:|---:|---:|---:|---:|
| entry10_exit25 | train | 1,000,000 | 36 | 272,905 | 3.078 | 32,142 | 0.980 |
| entry10_exit25 | train | 2,000,000 | 43 | 542,380 | 2.525 | 107,441 | 0.970 |
| entry10_exit25 | train | 5,000,000 | 47 | 1,416,198 | 2.317 | 309,297 | 0.960 |
| entry10_exit25 | validation | 1,000,000 | 11 | 68,339 | 3.543 | 23,186 | 0.763 |
| entry10_exit25 | validation | 2,000,000 | 20 | 212,472 | 2.752 | 64,383 | 0.837 |
| entry10_exit25 | validation | 5,000,000 | 22 | 615,605 | 2.740 | 162,576 | 0.870 |
| entry10_exit25 | locked-oos | 1,000,000 | 12 | 28,797 | 1.472 | 58,885 | 0.703 |
| entry10_exit25 | locked-oos | 2,000,000 | 17 | 162,829 | 1.791 | 161,432 | 0.853 |
| entry10_exit25 | locked-oos | 5,000,000 | 19 | 718,309 | 2.325 | 207,359 | 0.940 |
| exit50 | train | 1,000,000 | 36 | 263,966 | 2.962 | 32,519 | 0.980 |
| exit50 | train | 2,000,000 | 43 | 519,392 | 2.422 | 110,650 | 0.970 |
| exit50 | train | 5,000,000 | 47 | 1,349,266 | 2.223 | 319,333 | 0.960 |
| exit50 | validation | 1,000,000 | 11 | 65,521 | 3.357 | 23,634 | 0.783 |
| exit50 | validation | 2,000,000 | 20 | 202,719 | 2.625 | 66,208 | 0.853 |
| exit50 | validation | 5,000,000 | 22 | 584,883 | 2.602 | 167,695 | 0.873 |
| exit50 | locked-oos | 1,000,000 | 12 | 30,397 | 1.484 | 59,576 | 0.720 |
| exit50 | locked-oos | 2,000,000 | 17 | 159,349 | 1.764 | 163,982 | 0.863 |
| exit50 | locked-oos | 5,000,000 | 19 | 695,285 | 2.257 | 216,569 | 0.943 |

Interpretation: the candidate is not only a no-slippage artifact at aggregate level. However, the
1M locked-OOS result remains thin: 12 opened trades, PF below 1.5 under stress, and random
percentile around 0.70-0.72. This is still research-continuation evidence, not a paper/live gate
pass.

## Low-Frequency Block Stability

60 trading-session block diagnostic:

- output: `out/event-research-cluster-rule-diagnostics/fixed20-stop-portfolio-block-stability-60d-random.json`
- split: `all`
- candidate count: 130
- block count: 19
- capital is reset per block; this is a stability diagnostic, not the aggregate deployment gate

| Capital | Active blocks | Positive block ratio | Worst block PnL | Median block PnL | Worst block DD | Median random percentile | Opened trades |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 1,000,000 | 16 | 0.875 | -57,081 | 25,716 | 57,081 | 0.793 | 59 |
| 2,000,000 | 18 | 0.778 | -56,863 | 37,449 | 154,965 | 0.788 | 82 |
| 5,000,000 | 18 | 0.778 | -154,880 | 120,187 | 192,921 | 0.780 | 90 |

Weak blocks at 1M:

| Block | Period | Opened | Net PnL | PF | Max DD | Random percentile |
|---|---|---:|---:|---:|---:|---:|
| block_010 | 2024-04-12 to 2024-07-09 | 5 | -23,011 | 0.135 | 23,202 | 0.140 |
| block_018 | 2026-04-02 to 2026-05-15 | 4 | -57,081 | 0.000 | 57,081 | 0.030 |

Interpretation: aggregate and stress results are promising for research continuation, but block
stability is not clean. The 2026 spring block is especially weak versus same-symbol random dates.
Do not promote this to paper observation without first understanding whether that block is event
quality, regime, liquidity, or execution-path related. Do not change the registered candidate using
locked-OOS feedback.

## Technical Veto Diagnostic

Weak locked-OOS losses were concentrated in catastrophic-stop exits with low liquidity and missing
or expensive valuation context. To avoid locked-OOS retuning, the only additional diagnostic run
used the already preregistered coarse `technical_veto_allows` rule.

This is not a new registered candidate.

| Split | Rule | Trades | Net PnL | PF | Max DD | Random percentile |
|---|---|---:|---:|---:|---:|---:|
| train | earnings_plus_dividend_increase | 67 | 261,661 | 2.866 | 24,796 | 0.990 |
| train | earnings_plus_dividend_increase_plus_technical | 13 | 51,451 | 2.708 | 18,534 | 0.947 |
| validation | earnings_plus_dividend_increase | 38 | 149,309 | 3.662 | 20,298 | 0.987 |
| validation | earnings_plus_dividend_increase_plus_technical | 14 | 73,429 | 3.749 | 18,257 | 0.967 |
| locked-oos | earnings_plus_dividend_increase | 25 | 91,968 | 1.944 | 68,852 | 0.947 |
| locked-oos | earnings_plus_dividend_increase_plus_technical | 8 | 38,584 | 2.140 | 33,850 | 0.817 |

Interpretation: technical veto plausibly removes some tail risk, but the sample collapses below a
usable low-frequency threshold. It is a research hypothesis for the next train-only preregistration
cycle, not evidence to patch the current fixed candidate.

## Value Guard V1 Preregistration

Before re-running locked OOS, the train/validation tail-risk profile suggested one coarse,
point-in-time valuation guard:

`event_cluster_earnings_dividend_value_guard_fixed20_stop_v1_research`

Fixed definition:

- same trade cluster contains `earnings_result`
- same trade cluster contains `dividend_revision` subtype `increase`
- if forecast PER is available point-in-time, cluster minimum forecast PER must be <= 15
- if forecast PER is unavailable point-in-time, the cluster is not rejected only for that absence
- entry mode: `next_open_unconditional`
- exit: `fixed_20d_plus_catastrophic_stop`
- cost: `ROUND_TRIP_COST_RATE=0.00298`
- random baseline: true `same_symbol_random_date`, 300 seeds
- paper/live route: disabled

Train/validation observation-level result before locked-OOS evaluation:

| Split | Trades | Net PnL | PF | Max DD | Random percentile |
|---|---:|---:|---:|---:|---:|
| train | 63 | 267,587 | 3.194 | 24,796 | 0.997 |
| validation | 32 | 155,687 | 4.810 | 13,012 | 0.977 |

Train/validation portfolio-level result before locked-OOS evaluation:

The selected-path columns remain historical results. The `Random percentile`
column in this and the later portfolio tables is subject to the erratum above
and is not comparable.

| Split | Capital | Opened | Net PnL | PF | Max DD | Random percentile |
|---|---:|---:|---:|---:|---:|---:|
| train | 1,000,000 | 35 | 279,626 | 3.262 | 31,158 | 0.977 |
| train | 2,000,000 | 40 | 588,495 | 2.971 | 100,797 | 0.963 |
| train | 5,000,000 | 43 | 1,588,206 | 2.831 | 288,155 | 0.960 |
| validation | 1,000,000 | 11 | 74,663 | 4.032 | 22,067 | 0.793 |
| validation | 2,000,000 | 19 | 322,967 | 5.408 | 44,134 | 0.907 |
| validation | 5,000,000 | 20 | 905,446 | 5.510 | 114,616 | 0.907 |

This v1 is registered for a single locked-OOS check. Do not alter the PER threshold, missing-value
treatment, exit horizon, stop, or cost after seeing locked OOS.

Locked-OOS result:

| Level | Split | Capital | Trades/Open | Net PnL | PF | Max DD | Random percentile |
|---|---|---:|---:|---:|---:|---:|---:|
| observation | locked-oos | n/a | 22 | 94,922 | 2.089 | 68,852 | 0.933 |
| portfolio | locked-oos | 1,000,000 | 9 | 44,936 | 2.036 | 41,194 | 0.737 |
| portfolio | locked-oos | 2,000,000 | 15 | 197,617 | 2.193 | 117,894 | 0.853 |
| portfolio | locked-oos | 5,000,000 | 17 | 810,179 | 2.904 | 161,253 | 0.927 |

V1 execution stress:

| Stress | Split | Capital | Opened | Net PnL | PF | Max DD | Random percentile |
|---|---|---:|---:|---:|---:|---:|---:|
| entry10_exit25 | locked-oos | 1,000,000 | 9 | 35,029 | 1.784 | 42,495 | 0.720 |
| entry10_exit25 | locked-oos | 2,000,000 | 15 | 175,229 | 2.015 | 123,190 | 0.853 |
| entry10_exit25 | locked-oos | 5,000,000 | 17 | 748,378 | 2.655 | 173,762 | 0.927 |
| exit50 | locked-oos | 1,000,000 | 9 | 37,334 | 1.808 | 42,994 | 0.760 |
| exit50 | locked-oos | 2,000,000 | 15 | 171,967 | 1.975 | 125,292 | 0.877 |
| exit50 | locked-oos | 5,000,000 | 17 | 727,351 | 2.568 | 178,892 | 0.930 |

V1 60 trading-session block stability:

The `Median random percentile` values below are also affected by the mismatched
random stop and are retained only as historical output.

| Capital | Active blocks | Positive block ratio | Worst block PnL | Median block PnL | Worst block DD | Median random percentile | Opened trades |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 1,000,000 | 16 | 0.875 | -41,194 | 25,716 | 41,194 | 0.800 | 55 |
| 2,000,000 | 18 | 0.833 | -56,863 | 45,948 | 117,894 | 0.795 | 76 |
| 5,000,000 | 18 | 0.833 | -154,880 | 139,009 | 165,204 | 0.780 | 82 |

V1 improves aggregate locked OOS and reduces the 1M worst block loss from `-57,081` to `-41,194`,
but the 2026-04-02 to 2026-05-15 block remains weak with 1M random percentile `0.010`.
The selected-path results remain research context, but the random percentile
claims in this report are non-comparable under the erratum above. Frozen-v1
paper observation is **BLOCKED** pending a valid matched-random comparison and
execution reproduction. Do not rerun the frozen locked-OOS window to repair the
historical comparison without ADR-0005 approval.
