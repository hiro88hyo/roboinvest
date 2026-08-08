# Liquidity Improvement V0 Feature Audit

Date: 2026-08-08

Status: Feature/cohort artifact built. No forward returns or portfolio outcomes computed.

Candidate: `liqimp1m_logdiff_v0_research`

## Bound Inputs And Code

- research config SHA-256:
  `c803bcbd6405b24cad1c754b0e32e26632e0483fa91116ba1e41590c29cf24bd`
- normalized manifest SHA-256:
  `fb41af1ef19585dcaa2a962e7c8fca1b429ce01ac55d391949637e96a60a61d6`
- feature builder SHA-256:
  `52c183c453a6ec3b9e6a40054558f3f5c21c0408a8636ef1258320f47e7af6c6`
- feature manifest SHA-256:
  `478ea180f2d8e4a9bc80f9b7d5d1fa4f52181974767776d94858e119eb669066`
- feature cohort SHA-256:
  `8d16d1eefa3657a5c9a3704c5c97c088681545bfb6d1a38c2c41a2176c86f5e1`

The authoritative artifact manifest is
`data/liquidity-research-features-v0/feature-manifest.json`.

## Outcome-Blind Audit

- master/cohort rows: 254,567
- feature-available rows: 235,511
- fixed-universe eligible rows: 103,538
- top-20% candidate-pool rows: 20,731
- signal dates with a nonempty eligible cross-section: 57
- rank/top-20 rounding rule failures: 0
- outcome-like columns: 0
- forward returns computed: false
- portfolio outcomes computed: false
- locked-OOS outcomes inspected: false

Eligibility exclusions are retained rather than dropped:

- below 50M JPY current-window median turnover: 109,793
- disallowed product category: 24,745
- insufficient valid fixed feature window: 10,497
- disallowed market: 5,994

The counts above establish feasibility only. They contain no evidence that the factor predicts
returns or passes PF/drawdown gates.

## Split Inventory Without Outcomes

| Split | Signal dates | Master rows | Feature available | Eligible | Top-20% pool |
|---|---:|---:|---:|---:|---:|
| development | 35 | 148,569 | 134,735 | 58,587 | 11,731 |
| validation | 12 | 52,836 | 50,377 | 21,303 | 4,265 |
| locked OOS | 12 | 53,162 | 50,399 | 23,648 | 4,735 |

Validation and locked-OOS entries in this table are only feature/cohort counts. No future price,
return, selected-trade PnL, PF, drawdown, or symbol-level outcome has been joined or inspected.

## Next Gate

Before any development outcome run:

1. implement the registered raw-price/split-aware portfolio simulator;
2. test daily mark-to-market drawdown, gap-aware 10% stop, 100-share sizing, 1% turnover cap,
   costs, same-symbol overlap, and same-day cash rules;
3. bind the simulator and feature artifact hashes in a one-run development execution record;
4. run development only and apply the frozen decision contract.

Validation and locked OOS remain prohibited at this stage.

