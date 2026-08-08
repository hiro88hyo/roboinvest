# Evidence Assessment

## What Counts

Evidence is classified by execution realism:

1. `live`: broker-routed production executions.
2. `paper`: production signals with simulated fills from observed books.
3. `replay`: archived orders and books processed through OMS Paper logic.
4. `research`: OHLCV or event-data simulation with explicit costs.

Results from one class are not substitutes for another. A healthy data pipeline,
a positive feature-level forward return, or a profitable research aggregate is
not evidence of executable live profitability by itself.

## Evidence For Profitability

### May Live Operations

The reported May live period (`2026-05-21` through `2026-05-29`) produced:

- net PnL: `+46,766 JPY`
- trades: `123`
- profit factor: `1.34`
- max drawdown: `69,230 JPY`

This is genuine live evidence, but it is not sufficient proof of the intended
hybrid strategy. The AI path was effectively silent after `2026-05-21`, the
sample covers only several sessions, and `2026-05-29` lost `45,540 JPY`.

Primary tracked source:
[2026-05 Performance Review](../../handoff/2026-05-performance-review.md).

### Event-Cluster Research

The frozen event-cluster candidate has positive locked-OOS portfolio results
and remains the strongest research-continuation candidate. At `1,000,000 JPY`
capital, the historical selected-path calculation reports locked-OOS PF `2.036`
and max drawdown `41,194 JPY`. The selected-path execution stresses also remain
positive as historical calculations.

**Erratum (2026-07-10):** the cited portfolio matched-random comparison is not
valid evidence. Selected observations used the frozen `CAT_STOP_PCT=-0.10`,
while random portfolio candidates were generated with an 8% stop
(`entry_price * 0.92`). The reported `1M/2M/5M` random percentiles
`0.737/0.853/0.927` are therefore non-comparable to the selected path. The
simulator is corrected for future preregistered runs, but that code correction
does not retroactively validate these locked-OOS percentiles.

**Corrective inspection (2026-07-12):** after explicit user approval under
ADR-0005, the same locked-OOS portfolio comparison was run once with the 10%
stop applied to selected and matched-random paths. Corrected 1M/2M/5M
percentiles are `0.713/0.837/0.937`. The 1M case remains below the required p75,
so the paper-observation gate still fails. No further locked-OOS run or tuning
is authorized. See the
[corrective inspection report](../../reports/event-cluster-matched-random-corrective-inspection-2026-07-12.md).

Frozen-v1 paper observation is **BLOCKED**, not the next evidence-collection
step. The corrected matched-random evidence fails at 1M, and official-open/close
execution reconciliation is still required before any new activation decision.
Do not rerun or inspect the frozen locked-OOS window again.

Primary tracked source:
[Event Cluster Paper Observation Plan](../../features/event-cluster-paper-observation-plan.md).

### Daily Swing Research

The `daily_trend_pullback_fixed10_hash_v1_operational` open-exit model reports
OOS net PnL `+257,750.440 JPY`, PF `1.5589`, and max drawdown `67,697.220 JPY`
at `1M` capital. Its low-frequency block gate passes in one configuration.

The formal research gate still fails. The conservative execution model is much
weaker, selected OOS does not beat the best matched-random result, and results
are sensitive to block length and capital. This candidate is not a paper/live
candidate.

Primary tracked source:
[Swing Rebuild Plan](../../features/swing-rebuild-plan.md).

## Counter-Evidence

### June Intraday Paper Losses

Four observed paper sessions produced `46` closed trades and aggregate PnL of
`-41,300 JPY`:

| Date | Closed trades | Paper PnL |
| --- | ---: | ---: |
| 2026-06-16 | 3 | -12,200 JPY |
| 2026-06-19 | 13 | -10,100 JPY |
| 2026-06-22 | 9 | -6,500 JPY |
| 2026-06-23 | 21 | -12,500 JPY |

This led to the strategy-layer reset. Threshold tuning of that family is not a
valid path back to live status.

Primary tracked sources:
[Strategy Reset Decision](../../handoff/2026-06-23-strategy-reset.md) and
[ADR-0003](../../adr/0003-strategy-layer-rebuild.md).

### Intraday Replay Failures

Relative momentum and several replacement intraday hypotheses failed OOS or
OMS-realizable replay after costs and fills. Increasing BUY aggressiveness often
increased fill rate while worsening PnL. This is evidence that the deficit was
not only passive execution.

Primary tracked source:
[Relative Momentum Failure](../../handoff/2026-06-24-relative-momentum-failure.md).

### Event AI Placebos

The event AI smoke produced attractive PF for an AI-selected subset, but strong
placebos selected similar or better cohorts. Shuffling official disclosure
numerics did not collapse selection. The smoke does not demonstrate unique LLM
alpha.

Primary tracked source:
[Event AI Earnings Smoke Result](../../reports/event-ai-earnings-smoke-result-2026-06-27.md).

### High-Frequency Event Deadline Screen

The preregistered 2026-07-18 development screen tested whether a broader
fixed-2 event cohort could supply enough closed trades before 2026-09-30. Broad
variants reached the historical deadline-window frequency target but produced
PF at most `1.109`, drawdown at least `26.4%`, and stressed PF at most `0.937`.
The quality-filtered variant produced PF `1.353`, but drawdown was `13.2%`,
stressed PF was `1.154`, and historical deadline-window median opened trades
fell to `9`. The decision is `NO_CANDIDATE`; frequency does not compensate for
the missing executable edge.

Primary tracked source:
[High-Frequency Event Development Screen Result](../../reports/event-prospective-high-frequency-development-screen-result-2026-07-18.md).

## Recent Operational Observations

These rows demonstrate end-to-end behavior only. They do not override the OOS
rejections above.

- `2026-07-09`: 2 closed day-paper trades, gross execution PnL `-1,300 JPY`.
- `2026-07-10`: 4 closed day-paper trades, gross execution PnL `+2,800 JPY`.
- Both sessions ended with no open paper or live positions.
- The legacy event-cluster detector reported zero candidates for signal dates
  `2026-07-08` and `2026-07-09`, but the 2026-07-10 causality audit showed that
  it required T+1 OHLCV before it could construct a candidate. Those zero rows
  are unreliable and inconclusive because of structural false-zero risk; they
  establish neither candidate absence nor presence. No swing signals were
  published.

The July paper PnL is calculated from `trades_paper` fill prices and excludes a
separate commission/tax field because that production table does not store one.
It must not be compared directly with cost-adjusted research net PnL.

Tracked sources:
[2026-07-09 Swing Paper Plan](../../handoff/2026-07-09-swing-paper-plan.md) and
[2026-07-10 Paper Observation](../../reports/paper-observation-2026-07-10.md).

## Reviewer Conclusion Checklist

A favorable conclusion requires all of the following, not a selected subset:

- pre-registered parameters and costs
- untouched OOS evaluation
- PF and drawdown kill-switch thresholds
- adequate trade count and time coverage
- matched-random and placebo comparison
- execution stress and no-fill sensitivity
- OMS-realizable or paper reproduction
- no unresolved data leakage or point-in-time timing defect
- evidence artifacts with hashes and provenance

At the current cutoff, this checklist is incomplete.
