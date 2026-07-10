# Profitability Review Methodology

## Decision Hierarchy

The binding decision is the project kill switch in `AGENTS.md`:

```text
OOS profit_factor > 1.2
OOS max_drawdown < capital * 0.10
deadline = 2026-09-30
```

Parameters, universe rules, exits, cost assumptions, and evaluation windows
must be registered before the judged OOS run. Post-hoc exceptions do not count.

Additional research gates may be stricter. A relaxed diagnostic or a paper-only
exception cannot weaken the project-level gate.

## Metric Semantics

- `net_pnl`: realized PnL after the costs implemented by that harness.
- `gross_execution_pnl`: fill-price difference times quantity, before costs not
  represented in the production paper table.
- `profit_factor`: gross profit divided by absolute gross loss. Undefined when
  no losses exist; it must not be represented as an arbitrarily large pass.
- `max_drawdown`: maximum peak-to-trough decline of the evaluated equity curve.
- `hit_rate`: winning closed trades divided by all closed trades.
- `random_percentile`: selected result's percentile among the declared matched
  random baselines. Baseline construction must be reported with the result.

All monetary values are JPY unless a source explicitly states otherwise.

## Cost Models

The repository contains multiple harnesses with different scopes:

- Daily swing backtest defaults to commission `0.099%` and slippage `0.05%`
  per side. See `BacktestParams` in `scripts/backtest-swing-daily.py`.
- Event portfolio simulation uses round-trip cost rate `0.00298`, split equally
  across entry and exit. See `scripts/event_research_common.py` and
  `scripts/simulate-event-portfolio.py`.
- OMS Paper backtest accounts for commission and slippage in its report layer.
- Production `trades_paper` rows store fill price and quantity, not explicit
  commission, slippage, or tax columns.

Reviewers should not combine PnL across these harnesses without normalizing the
cost model.

## Minimum Acceptance Evidence

For an intraday candidate:

- multi-day OOS or replay set
- positive cost-adjusted net PnL
- sufficient closed trades and positive days
- bounded no-fill and partial-fill rates
- spread and BUY-price stress
- comparable random-entry baseline
- production-paper observation before live discussion

For a low-frequency swing/event candidate:

- frozen point-in-time split manifest
- aggregate OOS PF and drawdown thresholds
- positive-period and worst-period diagnostics
- multiple block-length sensitivity checks
- same-symbol and universe/date matched-random baselines
- capital and execution-order sensitivity
- paper reproduction of entry, exit, stop, and scheduled-exit ordering

## Non-Evidence

The following do not establish profitability:

- services staying `Up`
- Pub/Sub delivery or Supabase writes
- positive feature-level forward returns without fill simulation
- train-only results
- the best row from a parameter sweep
- a single profitable day
- improved fill rate without improved net PnL
- paper results from a strategy already rejected by its OOS gate
- a placebo-insensitive LLM selection result
