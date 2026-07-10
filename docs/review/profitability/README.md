# Profitability Review Package

Cutoff: 2026-07-10 JST

This is the entry point for an independent review of whether the repository has
demonstrated a durable, executable trading edge. It deliberately separates
software reliability from strategy profitability and separates live, paper,
replay, and research evidence.

## Current Verdict

**Durable profitability has not been demonstrated.**

The strongest positive results are useful research evidence, but none currently
satisfies the complete promotion contract:

- The May live result was positive, but covered a short period and the AI path
  was effectively silent. It is evidence about the legacy RULE-heavy system,
  not the intended hybrid strategy.
- Four June paper sessions lost `41,300 JPY` in aggregate. The associated
  intraday strategy family was removed from live-candidate status.
- Several swing and event candidates show positive OOS aggregates, but formal
  gates still fail on block stability, matched-random comparison, execution
  assumptions, sample size, or paper reproduction.
- July day-paper observations are operational observations of a previously
  rejected intraday candidate. They do not restore candidate status.

The project-level falsification contract remains:

- deadline: `2026-09-30`
- OOS profit factor: `> 1.2`
- OOS max drawdown: `< capital * 0.10`
- only pre-registered strategy parameters and cost assumptions count

See the root [AGENTS.md](../../../AGENTS.md) for the binding kill-switch text.

## Review Order

1. [EVIDENCE.md](EVIDENCE.md): claims, counter-evidence, and known limitations.
2. [evidence-ledger.csv](evidence-ledger.csv): machine-readable evidence index.
3. [METHODOLOGY.md](METHODOLOGY.md): metric definitions and acceptance rules.
4. [REPRODUCIBILITY.md](REPRODUCIBILITY.md): commands, data boundaries, and gaps.
5. [SOURCE_MAP.md](SOURCE_MAP.md): code paths for strategy and execution review.

Validate the package with:

```bash
make review-profitability
```

## Scope Boundaries

This package does not claim that a backtest is independently reproducible from
the Git repository alone. Raw J-Quants data, production Supabase rows, market
data archives, and generated `out/` results are intentionally not committed.
The missing artifact bundle is a material review limitation, documented in
[REPRODUCIBILITY.md](REPRODUCIBILITY.md).

This package also does not authorize live trading, capital increases, parameter
changes, or exceptions to the project kill switch.
