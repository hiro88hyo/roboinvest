# Multi-Event Fundamental/Technical Fixed-5 Validation Preregistration

Date: 2026-07-18

Status: Registered before validation. Validation results have not been inspected.

## Purpose

Register exactly one validation hypothesis selected from the 2026-07-17
train-only rule screen. This is a research-continuation experiment only. It
does not authorize paper publication, live routing, a locked-OOS inspection,
or any capital change.

Candidate ID:
`event_multi_event_fundamental_technical_fixed5_v0_research`

The fixed-2, fixed-10, fixed-20, and catastrophic-stop variants are not part of
this validation. They must not be inspected as alternatives after this result
is known.

## Frozen Selection Rule

Group observations by `trade_group_id`, falling back to `event_cluster_id` and
then `observation_id`. Select one deterministic representative from a group
only when all of the following are true:

- the group contains more than one observation;
- at least one member passes the existing fundamental rule:
  - `dividend_revision` subtype `increase`; or
  - positive profit revision, operating-profit revision, or absolute forecast
    EPS revision;
- at least one member passes the existing technical veto:
  - 20-session average turnover is at least 200,000,000 JPY;
  - 14-session ATR is between 0.5% and 8%, inclusive;
  - 20-session return is missing or below 30%;
  - the point-in-time per-symbol regime is not `broad_downtrend`.

The fundamental and technical passes may come from different members of the
same group. No PER, dividend-yield, event-subtype, or AI-label condition is
added.

## Frozen Execution And Portfolio Assumptions

- evaluation split: validation only, 2024-07-22 through 2025-06-20;
- entry: next-session open, unconditional after selection;
- exit: fifth trading-session close;
- catastrophic stop: none for this candidate;
- round-trip cost: `0.00298` (`0.00149` per side);
- primary capital: 2,000,000 JPY;
- diagnostic capitals: 1,000,000 and 5,000,000 JPY;
- maximum simultaneous positions: 5;
- maximum notional per position: 20% of starting capital;
- lot size: 100 shares;
- same-symbol overlap: prohibited;
- same-day close-exit cash reuse: prohibited;
- primary same-day ordering: `feature_time_symbol`;
- same-symbol random-date baseline: 300 seeds using the same symbol, frozen
  fixed-5 exit and cost logic, excluding registered event dates;
- selection-order variants are diagnostics only and cannot be used to replace
  the primary ordering after results are known.

## Validation Decision Contract

The primary 2M validation result is `PASS` only when all conditions hold:

- opened trades >= 30;
- cost-adjusted net PnL > 0;
- profit factor > 1.2;
- maximum drawdown < 200,000 JPY;
- same-symbol random-date percentile >= 0.75;
- random coverage has zero unmatched and zero fallback candidates.

If opened trades are below 30, the result is `INCONCLUSIVE`; it is not a pass.
If any other primary condition fails, the result is `FAIL`.

A pass permits only continued research and prospective shadow-forward
collection. It does not permit paper/live activation or locked-OOS inspection.
A fail or inconclusive result freezes this candidate for the current data
cycle. Do not retune it, inspect the fixed-2 variant on validation, or rename it
as a new candidate.

## Frozen Inputs And Implementation

- observations:
  `out/event-research-real-pit/observations.jsonl`
  - SHA-256: `4e8cefbfb0521d50ea00a0c9742e1e56746f7a4dec79eb6d5b6ac67ce2e3c63c`
- daily OHLCV:
  `data/reference/daily_ohlcv_20210625_20260624_bydate.csv`
  - SHA-256: `fb96851d2e6fdc58368ccb9942f40a09c6f7506a6ab0807225ad173bf139a7a2`
- fixed split manifest:
  `out/event-research-real-pit/dataset-manifest.json`
  - observation count: 92,185
  - validation boundary: 2024-07-22 through 2025-06-20
  - purge: 20 days
- train-only selection artifact:
  `out/event-research-rule-only-train-scan-2026-07-17/rule-only-train-scan.json`
  - SHA-256: `0bb783af8aae35e16de86e7291bdf7d19de8a80254582d52c44d1141f8a01ad1`
- simulator:
  `scripts/simulate-event-portfolio.py`
  - SHA-256: `01fd8e58dec95aee857735b1c7afd99285c3057273fe7c3ce9d2aeffe6878cfb`
- shared event-research logic:
  `scripts/event_research_common.py`
  - SHA-256: `89d560e666ebe68fb4e666d782f03afa5b4756415bb673f52e9628536367fae3`

Before registration, the focused event-research test set passed `36` tests and
targeted Ruff checks passed.

## One Authorized Command

Run exactly once:

```bash
uv run python scripts/simulate-event-portfolio.py \
  --observations out/event-research-real-pit/observations.jsonl \
  --split validation \
  --split-manifest out/event-research-real-pit/dataset-manifest.json \
  --candidate-id event_multi_event_fundamental_technical_fixed5_v0_research \
  --capital 1000000 \
  --capital 2000000 \
  --capital 5000000 \
  --ohlcv data/reference/daily_ohlcv_20210625_20260624_bydate.csv \
  --random-seeds 300 \
  --selection-order feature_time_symbol \
  --include-selection-order-stress \
  --output-json out/event-research-multi-event-fixed5-validation-2026-07-18/portfolio-simulation.json \
  --output-csv out/event-research-multi-event-fixed5-validation-2026-07-18/portfolio-trades.csv
```

The locked-OOS split must not be run or included.
