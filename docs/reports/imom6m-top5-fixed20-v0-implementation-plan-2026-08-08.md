# IMOM6M Top-5 Fixed-20 V0 Implementation Plan

Date: 2026-08-08

Status: plan only. No implementation or outcome computation authorized.

Candidate: `imom6m_top5_fixed20_v0_research`

## Safety Boundary

Implementation must begin only after explicit review and authorization of
`research/imom/imom6m-top5-fixed20-v0.json`. The implementation may not change the feature,
universe, decile rule, gates, portfolio rules, costs, or trial limit.

Development-only CLIs must have no validation or locked-OOS selector. Validation and locked-OOS
prices must not be loaded by a development evaluator. All artifacts must state:

```text
research_only=true
paper_live_enabled=false
validation_outcomes_inspected=false
locked_oos_outcomes_inspected=false
counts_as_2026_09_30_kill_switch_evidence=false
```

## Phase 1: Outcome-Blind Monthly Feature Artifact

Proposed files:

- `scripts/build-imom6m-features.py`
- `scripts/tests/test_build_imom6m_features.py`
- output: `data/imom6m-features-v0/`

The builder should:

1. Verify the preregistration and normalized-manifest hashes.
2. Derive the global TSE month-end calendar from normalized bars.
3. Require the actual month-end adjusted close for every symbol-month; never use a stale close.
4. Build six returns from seven consecutive month ends.
5. Compute only MOM6M, SUM6M, IMOM6M, universe eligibility, deterministic rank, and decile.
6. Retain excluded rows with machine-readable reasons.
7. Write no next-month return, entry/exit price, stop result, PnL, PF, drawdown, or random baseline.
8. Write an atomic, non-overwriting Parquet artifact and manifest with hashes and split inventory.

Required synthetic tests include consecutive-month enforcement, missing actual month-end handling,
formula equality, no-skip timing, historic-master join, turnover guard, deterministic ties, exact
decile allocation for nonmultiples of ten, no winsorization, forbidden outcome columns, hash drift,
and non-overwrite behavior.

The builder implementation and output artifact must be hash-bound before Gate A is authorized.

## Phase 2: Gate A Evaluator

Proposed files:

- `scripts/evaluate-imom6m-development-gate-a.py`
- `scripts/tests/test_evaluate_imom6m_development_gate_a.py`
- output: `out/imom6m-gate-a-development-v0-<registration-date>/`

Before the one authorized Gate A run:

- finalize and test the evaluator with synthetic returns only;
- bind config, normalized manifest, feature artifact, evaluator, and output path hashes in a
  development Gate A execution record;
- independently audit that the evaluator filters development before loading outcome prices;
- fail if any outcome-like field already exists in the feature artifact.

The evaluator should output monthly decile returns, monthly rank IC, aggregate checks, artifact
hashes, and a single `PASS`, `FAIL`, or `INCOMPLETE` decision. It must not compute Gate B trades.

If Gate A is not `PASS`, write an immutable candidate disposition and stop the research cycle.

## Phase 3: Gate B Simulator, Only After Gate A Passes

The complete Gate B behavior is already preregistered, but it must not execute unless a separately
bound Gate A result is `PASS`.

Proposed files:

- `scripts/simulate-imom6m-development-gate-b.py`
- `scripts/tests/test_simulate_imom6m_development_gate_b.py`
- output: `out/imom6m-gate-b-development-v0-<registration-date>/`

Reuse the tested semantics of the LIQIMP simulator for raw-price entry, corporate actions,
gap/intraday stop order, fixed-20 exit, lot sizing, turnover cap, costs, same-day cash, overlap,
missing marks, daily MTM drawdown, and fail-closed scheduled exit. Shared logic may be extracted only
before any Gate B outcome run and must retain synthetic parity tests against LIQIMP behavior.

Bind the Gate A result, simulator, config, feature cohort, normalized manifest, and unique output
path before exactly one Gate B development execution.

## Phase 4: Independent Development Audit And Disposition

For a completed Gate B result, independently verify:

- every selected symbol was eligible and in decile 10;
- rank/order and backfill were deterministic;
- entry and exit prices match normalized raw bars;
- holding-session counts, stops, corporate actions, lots, costs, cash, and position limits;
- trade PnL sum, daily equity, PF, annual blocks, and MTM drawdown;
- validation and locked-OOS access flags remain false.

Apply the frozen gate once. Failure creates `FROZEN_REJECTED_DEVELOPMENT` and ends candidate 2 of 2.
Passing only permits a separately reviewed validation registration; it does not run validation.

## Verification Before Any Future Computation

Run targeted synthetic tests, `make lint-all`, and `make test-all`. Confirm a clean hash chain and
that raw, normalized, feature, and output artifacts remain research-only and outside paper/live
routing.

## Not Authorized Now

- creating either proposed script;
- computing IMOM values or ranks;
- computing any next-month return or development trade;
- inspecting validation or locked OOS;
- modifying the existing strategy, Project Kill Switch, paper route, or live route.
