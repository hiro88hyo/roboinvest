# Prospective High-Frequency Event Development Screen Result

Date: 2026-07-18

Status: `NO_CANDIDATE`

## Decision

None of the three preregistered variants simultaneously passed the frequency,
economic, drawdown, execution-stress, period-stability, and matched-random
gates. Do not add a fourth event variant, lower the gates, or implement a new
causal/paper/live route from this screen.

The preregistration is
[event-prospective-high-frequency-development-screen-prereg-2026-07-18.md](event-prospective-high-frequency-development-screen-prereg-2026-07-18.md).

All 92,185 historical observations were treated as contaminated development
data. The prospective interval beginning 2026-07-21 was not read or evaluated.

## Result

Shared input produced 3,846 eligible observation rows and 3,831 unique trade
groups.

| Variant | Groups | Opened | Net PnL | PF | Max DD | Stress PF | Stress DD | Jul-Sep median opened | Random percentile | Decision |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| broad feature-time fixed2 | 3,831 | 937 | +362,161 | 1.062 | 544,891 (27.2%) | 0.897 | 876,646 | 30 | 0.987 | FAIL |
| broad quality-priority fixed2 | 3,831 | 937 | +654,330 | 1.109 | 527,062 (26.4%) | 0.937 | 737,975 | 30 | 0.993 | FAIL |
| quality tiers 0-2 fixed2 | 817 | 538 | +1,106,371 | 1.353 | 263,798 (13.2%) | 1.154 | 302,439 | 9 | 0.997 | FAIL |

The broad variants met the historical deadline-window frequency requirement,
but failed PF, drawdown, execution stress, and calendar-year stability. Quality
priority improved aggregate PnL but did not repair the economic gate.

Removing tier-3 events raised PF above 1.3, but drawdown remained above 10%,
execution-stress PF remained below 1.2, stressed drawdown remained above 10%,
and the median comparable July-21-through-September-30 window fell to only nine
opened trades. This is the frozen frequency/quality trade-off; it must not be
resolved by inspecting an unregistered tier cutoff or exit.

All variants had complete same-symbol random coverage with zero unmatched and
zero fallback candidates. High random percentiles do not override absolute PF,
drawdown, stress, and frequency failures.

## Deadline Interpretation

The screen confirms that increasing event-trade count is technically possible,
but the additional trades do not retain an executable edge. The current event
family therefore cannot be made into a credible 2026-09-30 primary merely by
broadening the cohort.

- Keep cluster v1 as a separate low-frequency shadow-forward lane.
- Keep the multi-event fixed5 candidate frozen and `INCONCLUSIVE`.
- Do not revive the rejected OHLCV-only or intraday families.
- Do not treat the deadline as permission to weaken PF/DD, stress, random, or
  sample contracts.
- If no materially independent, preregistered family exists in time, apply the
  unchanged project kill switch rather than extending the deadline.

## Reproduction

```bash
./.venv/bin/python scripts/screen-event-prospective-high-frequency.py \
  --observations out/event-research-real-pit/observations.jsonl \
  --ohlcv data/reference/daily_ohlcv_20210625_20260624_bydate.csv \
  --output-json out/event-prospective-high-frequency-development-2026-07-18/screen.json \
  --output-csv out/event-prospective-high-frequency-development-2026-07-18/screen.csv
```

Frozen output hashes:

- JSON: `420afab98361181482607bcd146cbf1c323a291966457670e776a218016214ed`
- CSV: `9217262e06c8ae39eeecc7343fce53488180372bec99130b9dac68563fe2e247`
- screen implementation:
  `42a9bbf94d8fae722d3b0d0fc947fd243ad89f7aa43be4e15a80b5f2e4b347d4`

The implementation preserves quality priority when matched random candidates
are assigned to random dates, so crowded-day ordering uses the same frozen
quality identity on selected and random paths.
