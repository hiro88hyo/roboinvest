# IMOM6M top-5 fixed-20 V0: Phase 1 feature audit

Date: 2026-08-08  
Candidate: `imom6m_top5_fixed20_v0_research`  
Status: `OUTCOME_BLIND_FEATURE_BUILT_AUDITED_STOPPED_BEFORE_GATE_A`

## Scope

Phase 1 implemented and ran only the preregistered outcome-blind IMOM6M feature
builder. It did not calculate next-month returns, Gate A, Gate B, trades, PnL,
profit factor, drawdown, validation outcomes, or locked-OOS outcomes. It did not
modify paper or live trading.

The one-time build authority is recorded in
`research/imom/imom6m-top5-fixed20-v0-phase1-authorization.json`; its permitted
build has now been consumed. The immutable completion record is
`research/imom/imom6m-top5-fixed20-v0-phase1-completion.json`.

## Fixed inputs

| Input | SHA-256 |
|---|---|
| IMOM preregistration | `0ee3efb5a3a4629f70e17accb92663a7d14ece581939848612c63149f7c59d01` |
| Phase 1 authorization | `cd51b81482f76b5f2fd135cddad2fd2460251e38ceae7d54795172c03246b72d` |
| Trial registry | `81218b7e8d22d7d84c59ff592e0004772bc49e35d1865ae83ece30ce6094f0da` |
| Normalized archive manifest | `fb41af1ef19585dcaa2a962e7c8fca1b429ce01ac55d391949637e96a60a61d6` |

The builder verifies those bindings and every normalized archive partition hash
before creating output. Existing output or temporary output paths are never
overwritten.

## Implementation checks

The synthetic suite has 16 passing tests. It covers:

- the exact `IMOM6M = MOM6M - SUM6M` formula with six consecutive monthly returns;
- inclusion of the latest month, with no skip-month variant;
- rejection of a missing actual global month-end rather than stale-price filling;
- rejection of a gap in the six-month sequence;
- the fixed product, market, and median-turnover screens;
- descending rank, issue-code ascending tie break, and the registered decile formula;
- forbidden outcome-like columns and fail-closed config or authorization drift;
- non-overwrite behavior.

## Artifact audit

| Measure | Count |
|---|---:|
| Global month-end dates | 59 |
| Cohort rows | 254,567 |
| Dates with an available IMOM6M feature | 53 |
| Feature-available rows | 209,747 |
| Eligible rows | 95,561 |
| Decile-10 candidate rows | 9,581 |

The independent structural audit passed all of the following:

- no duplicate `(signal_date, code)` keys;
- no next-return, execution-price, trade-PnL, profit-factor, drawdown, or rank-IC columns;
- every eligible row has a complete rank and decile in `[1, 10]`;
- every ineligible row is unranked;
- decile 10 contains exactly `ceil(n / 10)` rows for each eligible cross-section;
- `IMOM6M = MOM6M - SUM6M` holds for every feature-available row;
- cohort and audit hashes match the manifest.

Artifact hashes:

| Artifact | SHA-256 |
|---|---|
| Builder | `3d1d8b9931e4474cc6873735979a186e0bba802056638131e7f163b4050a5c76` |
| Feature cohort | `7b6c691237eac160bccaa953074927130d7a8ce5b6c2f27ef14c6b641a4892b9` |
| Cohort audit | `702f8fe9ad84bea3cc0a9bfe4d130459f105d35f22597a44a04c65fe02efa091` |
| Feature manifest | `97c58071b5542c8ff8b3fdf55b232c5357cf213cf2f6bb83d3fee00eb4a0e6ee` |

Repository-wide verification also passed: `make lint-all`, 1,623 Python tests
(29 environment-dependent skips, 86% coverage), and 47 Dashboard tests.

## Stop boundary

Phase 1 is complete and stopped before Gate A. The artifact is research-only and
does not count as evidence for the 2026-09-30 project kill switch. A separate
explicit authorization is required before any next-month return, rank IC,
decile outcome, or Gate A calculation is implemented or run.
