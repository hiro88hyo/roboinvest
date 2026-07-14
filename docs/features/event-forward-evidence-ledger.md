# Event Forward Evidence Ledger Protocol

Date: 2026-07-12

Status: Active collection protocol. The first eligible artifact, for signal
date 2026-07-10, is recorded in the local ledger.

Daily service operation and data-capture boundaries are fixed in the
[Data-Capture And 2M Shadow-Forward Operating Rule](../runbook/data-capture-shadow-forward-operations.md).

## Purpose

Collect prospective evidence for the frozen event-cluster rule from signal dates
on or after 2026-07-01 without reopening the historical locked-OOS window.

The ledger is operational/research evidence only. It does not authorize paper or
live publication and does not override the failed 1M matched-random p75 gate.

## Record Contract

Use `scripts/record-event-forward-evidence.py` with a causal schema-v3 detector
artifact. Each row records:

- frozen strategy identity and signal date;
- artifact path and SHA-256;
- completed source receipt timestamp;
- complete and total candidate counts;
- concrete occurrence IDs;
- previous record SHA-256 and current record SHA-256;
- `economic_outcome_status=pending_forward_exit` when candidates exist, or
  `no_candidate_complete_artifact` for a causally complete zero-candidate day;
- `comparable_to_registered_backtest=false`.

Rows must be appended in strictly increasing signal-date order. Duplicate dates,
modified historical rows, broken hash chains, pre-July signal dates, unsafe
artifacts, and non-causal detector output are rejected.

Example:

```bash
uv run python scripts/record-event-forward-evidence.py \
  --artifact out/event-paper-observation/causal-candidates-YYYY-MM-DD.json \
  --ledger out/event-forward-evidence/ledger.jsonl
```

For a new completed signal date, run the full fail-closed sequence through
1Password secret injection:

```bash
set -a && . infra/.op.service-account.env && set +a
op run --env-file infra/env.production -- \
  uv run python scripts/run-event-forward-evidence.py \
    --signal-date YYYY-MM-DD
```

The runner requires an explicit TSE business date and refuses to overwrite an
existing causal artifact. It does not publish, update a watchlist, or touch a
live route.

## Eligibility Boundary

The existing candidate outputs for 2026-07-03 through 2026-07-09 predate the
causality repair and do not contain the required schema-v3 causality metadata.
Their zero-candidate results are unreliable/inconclusive and must not be copied
into this ledger.

The first ledger row must come from a newly generated causal artifact after a
complete J-Quants export receipt. A zero-candidate artifact is recordable only
when schema-v3 validation succeeds; absence of candidates must not be inferred
from a legacy detector run.

## Outcome Finalization

A ledger row remains pending until its registered 20th-session exit is causally
available. A future finalizer must bind outcome data to the existing
`record_sha256`; it must not edit or replace the original row. Official-open
and official-close reconciliation, costs, stops, misses, and no-fills remain
required before any aggregate forward-performance report can claim
comparability.
