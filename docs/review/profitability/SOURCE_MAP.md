# Profitability Source Map

## Data and Contracts

| Area | Paths | Review focus |
| --- | --- | --- |
| Shared models | `contracts/python/trade_contracts/` | timestamps, holding type, stop/target metadata, signal provenance |
| SQL contracts | `contracts/sql/` | trade/position fields, constraints, auditability, cost fields |
| Universe | `services/universe-scanner/` | look-ahead risk, liquidity filters, selection bias |
| Market features | `services/feature-engine/` | sampling, stale data, indicator warmup, archive fidelity |

## Signal and Execution Path

| Stage | Paths | Review focus |
| --- | --- | --- |
| Rule strategy | `services/strategy-rule/` | hypothesis fidelity, thresholds, duplicate/conflicting signals |
| AI strategy | `services/strategy-ai/` | prompt leakage, parser failures, placebo sensitivity |
| Aggregation | `services/aggregator/` | source thresholds, conflict handling, signal attribution |
| Risk/routing | `services/gateway/` | lot sizing, capital accounting, kill switch, mode isolation |
| Paper OMS | `services/oms-paper/` | fill realism, book age, no-fill handling, exit ordering |
| Live OMS | `services/oms-live/` | broker behavior, reconciliation, closeout, duplicate orders |

No service should bypass Pub/Sub to call another service directly. Gateway is
the single owner of risk validation and live/paper routing.

## Research Harnesses

| Purpose | Primary paths |
| --- | --- |
| Daily swing walk-forward | `scripts/backtest-swing-daily.py` |
| Event research dataset audit | `scripts/audit-event-research-data.py` |
| Event rule diagnostics | `scripts/diagnose-event-cluster-rules.py` |
| Event portfolio simulation | `scripts/simulate-event-portfolio.py` |
| Event AI evaluation/placebo | `scripts/evaluate-event-ai.py`, `scripts/compare-event-ai-placebo.py` |
| OMS archive replay | `scripts/run-paper-archive-backtest.py` |
| Intraday replay-set gate | `scripts/check-replay-report-set.py` |
| OMS report gate | `scripts/check-paper-backtest-report.py` |
| Random entry baseline | `scripts/run-random-entry-baseline-replay.sh` |

## Binding Decisions

- Project kill switch: `AGENTS.md`
- Intraday strategy rejection: `docs/adr/0003-strategy-layer-rebuild.md`
- Event AI research gates: `docs/adr/0004-event-ai-research-gates.md`
- Frozen event split: `docs/features/event-split-manifest-freeze.md`
- Swing research record: `docs/features/swing-rebuild-plan.md`
- Event-cluster paper scope: `docs/features/event-cluster-paper-observation-plan.md`

## High-Risk Review Questions

1. Can any feature or event field observe information published after entry?
2. Are delisted symbols, corporate actions, holidays, and missing bars handled
   without survivorship or calendar bias?
3. Do random baselines preserve symbol, date, universe, capacity, and holding
   constraints closely enough to be comparable?
4. Does the backtest execution order match production cash release and exit
   ordering?
5. Are no-fills and stale books included rather than silently discarded?
6. Are parameter sweeps and OOS inspections recorded so selection multiplicity
   can be assessed?
7. Can every reported trade be traced to an immutable input, strategy version,
   signal, order, fill, and exit?
8. Are costs, taxes, borrow assumptions, and lot-size/capacity constraints
   consistent with Japanese cash equities?
