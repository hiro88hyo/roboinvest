# IMOM6M Top-5 Fixed-20 V0 Preregistration

Date: 2026-08-08

Status: preregistered design with no authority to implement, compute features, inspect outcomes,
or run a backtest. A separate explicit authorization is required.

Candidate: `imom6m_top5_fixed20_v0_research`

Research cycle: candidate 2 of 2 in `cross_sectional_adaptation_v0`.

Evidence class: `PAPER_INSPIRED_IMPLEMENTABLE_ADAPTATION`.

The authoritative machine-readable design is
`research/imom/imom6m-top5-fixed20-v0.json`.

## Prior Candidate And Trial Limit

`liqimp1m_logdiff_v0_research` failed its development gate and is immutably recorded as
`FROZEN_REJECTED_DEVELOPMENT`. Its validation and locked-OOS outcomes remain uninspected.

IMOM6M was identified as the fallback before the LIQIMP development result. It is the final
candidate in this research cycle. If it fails, the cycle stops; no immediate IMOM12M, skip-month,
sign-reversal, alternative quantile, regime, HTP, quality, or value variant is allowed.

This work remains separate from the unchanged 2026-09-30 Project Kill Switch and cannot count as
its evidence.

## Primary Sources And Evidence Boundary

The public author report, [モメンタム戦略の開発とその有効性の検証](https://www.yu-cho-f.jp/wp-content/uploads/2-4iwanaga-2024.pdf),
defines MOM, SUM, and IMOM on journal pages 251-252, equations (1)-(3). It reports a TSE universe
excluding the foreign section and ETF/ETN, six- and twelve-month signals without skipping the most
recent month, monthly decile formation, next-month holding, and equal weighting.

The peer-reviewed article is Iwanaga and Hirose,
[Illusion momentum and cross-sectional returns](https://doi.org/10.1016/j.pacfin.2026.103063),
*Pacific-Basin Finance Journal* 96 (2026), article 103063.

This candidate is not a replication. The available archive and implementable portfolio differ
from the source study in period, return provider, tradability guard, number of holdings, entry and
exit prices, lot sizing, costs, and stop handling. In particular, equivalence between archived
J-Quants adjusted price returns and the source provider's return series is not established.

## Frozen Signal

For six consecutive monthly returns ending at formation month `t`:

```text
r_i,m   = AdjClose_i,m / AdjClose_i,m-1 - 1
MOM6M   = 100 * (product(1 + r_i,m) - 1)
SUM6M   = 100 * sum(r_i,m)
IMOM6M  = MOM6M - SUM6M
```

- Use exactly six months and seven consecutive global-TSE-month-end adjusted closes.
- Do not skip the most recent month.
- Higher IMOM is better.
- Do not winsorize, reverse the sign, take absolute IMOM, or combine another signal.
- A symbol must have a valid adjusted close on the actual global TSE month-end. Never substitute
  its last available close.
- Rank descending by IMOM and then issue code ascending.

The 100 multiplier does not affect rank but is retained for source-formula fidelity.

## Frozen Adapted Universe

At each formation date:

- use the latest historical master snapshot at or before formation;
- require product category `011`;
- allow market codes `0101`, `0102`, `0104`, `0106`, `0107`, `0111`, `0112`, or `0113`;
- require the current 20-valid-session median turnover to be at least JPY 50,000,000;
- require six complete monthly returns and positive adjusted closes;
- exclude missing inputs rather than fill them;
- do not use current membership.

The turnover guard makes even Gate A an adapted tradable-universe diagnostic rather than a paper
replication.

The archive contains 35 global month ends from 2021-08-31 through 2024-06-28. Without inspecting
symbol returns, the theoretical development inventory has 28 boundary-complete formations from
2022-02-28 through 2024-05-31. The minimum Gate A requirement is fixed at 24 valid formations.

## Gate A: Source-Structure Development Diagnostic

Gate A asks whether the sign and cross-sectional ordering survive in the fixed tradable universe
before evaluating the concentrated top-five implementation.

For every boundary-complete development formation:

1. Require at least 100 eligible symbols.
2. Sort by IMOM descending and code ascending.
3. Assign deterministic deciles using
   `decile = 10 - floor((rank - 1) * 10 / eligible_count)`.
4. Compute each symbol's adjusted close-to-close return from formation month-end to the next global
   TSE month-end.
5. Calculate equal-weight decile 10, decile 1, and decile-10-minus-decile-1 returns without costs.
6. Calculate monthly Spearman rank IC. Exact feature and outcome ties receive average ranks; an
   undefined monthly IC makes Gate A incomplete and therefore failed.

All of these must pass:

- at least 24 boundary-complete monthly formations;
- mean decile-10 return greater than zero;
- mean decile-10-minus-decile-1 return greater than zero;
- mean monthly rank IC greater than zero;
- mean spread in the chronological first half greater than zero;
- mean spread in the chronological second half greater than zero;
- mean spread remains greater than zero after removing the single highest spread month.

For `n` monthly spreads, the first `floor(n/2)` form the first half and the rest form the second.
The removed month is the largest spread, with an earliest-date tie-break. These are sign gates, not
claims of statistical replication.

If any Gate A condition fails or is incomplete, freeze the candidate and do not execute Gate B,
validation, or locked OOS.

## Gate B: Implementable Development Portfolio

Gate B may run only after Gate A passes, but its complete specification is frozen before Gate A is
computed. Candidates are decile 10, ordered by IMOM descending and code ascending.

- starting capital JPY 2,000,000;
- long-only and maximum five positions;
- at most five new positions per formation;
- maximum 20% of starting capital per position;
- maximum 1% of current-window median turnover;
- 100-share entry lots;
- next-session raw open entry;
- 20th holding-session raw close exit, counting entry as session one;
- gap-aware 10% catastrophic stop;
- corporate-action treatment using archived `AdjFactor`;
- no fallback entry price and fail-closed missing scheduled close;
- no same-symbol overlap and no same-day exit cash reuse;
- round-trip cost 0.298%, split 0.149% per side;
- stress round-trip cost 0.500%;
- no sector, correlation, beta, quality, value, or regime constraint.

Development passes only if all conditions hold:

- at least 100 opened trades;
- PF greater than 1.2;
- maximum daily MTM drawdown below 10% of starting capital;
- stressed PF greater than 1.0;
- positive net PnL in at least 75% of calendar-year blocks.

These thresholds are reused without relaxation from LIQIMP V0.

## Splits And Later Stages

- development signals: 2021-08-10 through 2024-06-28;
- validation signals: 2024-07-01 through 2025-06-30;
- locked OOS signals: 2025-07-01 through 2026-06-30.

A diagnostic month or trade belongs to a split only if formation and its complete next-month or
stop/scheduled outcome finish by the split end. Passing development does not automatically permit
validation. Passing validation does not automatically permit locked-OOS inspection.

If separately authorized, validation retains the prior thresholds: at least 40 trades, PF greater
than 1.2, DD below 10%, stress PF greater than 1.0, and at least p75 against 300 same-date random
portfolios with no unmatched or fallback candidates. Locked OOS retains at least 40 trades, PF
greater than 1.2, and DD below 10%.

## Current Stop Point

No IMOM feature, decile, next-month return, rank IC, trade, PF, drawdown, or symbol-level outcome
has been computed. No implementation or computation is authorized by this document alone.
