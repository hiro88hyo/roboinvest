# Liquidity Improvement Log-Difference V0 Preregistration

Date: 2026-08-08

Status: Registered before factor, forward-return, ranking, signal, or portfolio outcome
computation.

Candidate ID: `liqimp1m_logdiff_v0_research`

Machine-readable contract:
`research/liquidity/liqimp1m-logdiff-v0.json`

SHA-256:
`c803bcbd6405b24cad1c754b0e32e26632e0483fa91116ba1e41590c29cf24bd`

## Purpose And Boundary

Test exactly one long-only monthly hypothesis: stocks whose price impact per JPY traded
decreased over the latest 20 valid sessions relative to the preceding 20 sessions may
continue to outperform over the following month.

This is `PAPER_INSPIRED_NOT_REPLICATION`. The public abstract of Iwanaga and Hirose reports
next-month under-reaction to liquidity changes, but the exact paper definition of `LIQC` and
its decomposition has not been verified from the paid full text. Consequently, this candidate
must never be described as an implementation or replication of `LIQC1M`.

Primary paper reference:
<https://doi.org/10.1016/j.pacfin.2023.102115>

The two previously shared practitioner articles motivate risk decomposition, outlier exclusion,
position sizing, execution constraints, and drawdown control. They do not define this factor and
are not evidence that it is profitable:

- <https://zenn.dev/gamella/articles/7e1af53f19d94d>
- <https://qiita.com/tikeda123/items/15af9ecbc0c9767ba446>

This research lane is separate from the current strategy, existing shadow-forward evidence, and
the 2026-09-30 Project Kill Switch adjudication. It cannot be counted as a rescue result for that
contract and does not authorize paper or live routing.

## Frozen Input

Raw archive manifest:
`data/liquidity-research-v0/manifest.json`

Manifest SHA-256:
`6572cdf3a0c1d5cad6f0f0acb4f4dd31109d27ed13e0543e9664fbcbab5b047a`

The raw archive covers 1,196 completed daily fetches and 59 historical month-end master
snapshots. Its two raw file hashes are fixed inside that manifest. The old CSV without adjusted
fields must not be mixed into this candidate.

Normalization is outcome-blind. It may validate source payload hashes, types, nulls,
date/code uniqueness, OHLC consistency, and historical master fields, then write typed Parquet.
It may not compute the registered factor, returns, labels, ranks, or outcomes.

## Frozen Feature

At each historical month-end after the close, for each eligible issue:

1. Calculate daily adjusted-close log return.
2. Calculate daily Amihud-style price impact as
   `abs(log_return) / (turnover_jpy / 1,000,000,000)`.
3. Take the arithmetic mean for the current 20 valid symbol sessions and the immediately
   preceding 20 valid symbol sessions.
4. Define `LIQIMP1M_LOGDIFF_V0` as
   `ln(prior_20_mean) - ln(current_20_mean)`.

A positive value means measured illiquidity fell. A non-positive window mean is missing; no
epsilon, winsorization, imputation, alternative sign, or alternative lookback is allowed.
Exactly 41 valid adjusted closes and 40 valid price-impact observations are required.
Rows with null adjusted close or null/non-positive turnover are skipped and never filled; windows
count valid symbol sessions rather than substituting a calendar-day or stale-price observation.

## Frozen Universe And Selection

- historical master snapshot at or before the signal date only;
- product category `011`;
- market codes `0101`, `0102`, `0104`, `0106`, `0107`, `0111`, `0112`, or `0113`;
- current-window median turnover at least 50,000,000 JPY;
- no missing/non-positive turnover in either feature window;
- exclude TOKYO PRO and `other` market codes;
- descending cross-sectional factor rank, issue code ascending as tie-breaker;
- only the top 20% are selection candidates, with the count equal to
  `ceil(eligible cross-section count * 0.20)` and a minimum of one;
- fill at most five executable positions per signal date.

No sector neutralization, momentum filter, volatility filter, valuation filter, AI label, or
second feature is authorized in V0.

## Frozen Execution And Risk

- starting capital: 2,000,000 JPY;
- long-only, maximum five positions;
- maximum 20% of starting capital per position;
- maximum 1% of the current-window median turnover;
- 100-share lots;
- entry at the next TSE session's unadjusted open;
- scheduled exit at the 20th holding session's unadjusted close;
- catastrophic stop at 10% below entry, gap-aware using daily open/low;
- split/reverse-split quantity and raw-price references change on the ex-date using `AdjFactor`;
- no fallback price when entry open is missing;
- missing scheduled exit is an incomplete observation and fails closed;
- no same-symbol overlap and no same-day exit cash reuse;
- round-trip cost 0.298%, allocated 0.149% per side;
- stress round-trip cost 0.500%.

Maximum drawdown must use daily mark-to-market portfolio equity including open positions and
costs. A realized-PnL-only drawdown is invalid.

## Frozen Splits And Gates

Development signal dates end 2024-06-28. Validation is 2024-07-01 through 2025-06-30.
Locked OOS is 2025-07-01 through 2026-06-30. A trade belongs to a split only when its signal,
entry, and stop or scheduled exit all finish by the split end.

Development must have at least 100 opened trades, PF greater than 1.2, daily mark-to-market
drawdown below 10%, stressed PF greater than 1.0, and positive net PnL in at least 75% of calendar
year blocks. Failure freezes the candidate and prohibits validation inspection.

Validation must have at least 40 opened trades, PF greater than 1.2, drawdown below 10%, stressed
PF greater than 1.0, and at least p75 against 300 same-signal-date random portfolios with zero
unmatched/fallback candidates. Failure or insufficient trades freezes the candidate and prohibits
locked-OOS inspection.

Locked OOS, if eventually authorized, requires at least 40 opened trades, PF greater than 1.2,
and drawdown below 10%. Passing permits only a separate prospective shadow-forward proposal.

## Authorized Sequence

1. Validate and normalize the archive without computing the factor or outcomes.
2. Bind the normalized manifest hash into the feature-builder implementation.
3. Build an outcome-free feature/cohort artifact and audit causal timing.
4. Bind the feature artifact and simulator hashes before one development run.
5. Apply the decision contract. Do not automatically proceed to validation or locked OOS.

Normalization command:

```bash
uv run python scripts/normalize-liquidity-research-archive.py \
  --input-dir data/liquidity-research-v0 \
  --output-dir data/liquidity-research-normalized-v0
```
