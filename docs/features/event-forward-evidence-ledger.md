# Event Forward Evidence Ledger Protocol

Date: 2026-07-12

Status: Active collection protocol. As of 2026-08-08 JST, the local ledger has
16 eligible artifacts: the two pre-window signal dates 2026-07-10 and
2026-07-17, plus all 14 expected clean-cohort signal dates from 2026-07-21
through 2026-08-07. The clean cohort has no missing dates and all 14 artifacts
are causally complete zero-candidate observations.

The clean project-deadline evaluation cohort begins on that signal date. Under
the frozen 20-session exit, only signal dates through 2026-08-27 can close by
2026-09-30. Complete coverage of all 27 TSE signal dates in that interval is
required by the
[Project Kill-Switch Readiness Contract](project-kill-switch-readiness.md).

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

After append, the same runner invokes the idempotent outcome finalizer and the
kill-switch readiness reporter. It writes the latest report to
`out/event-forward-evidence/kill-switch-readiness.json`.

The runner always appends a fresh financial-summary response for the explicit
signal date. It must not reuse a completed response fetched before the required
next-calendar-day coverage window. Daily OHLCV remains resumable.

Before any exporter or detector writes, preflight requires the current time to
be inside the causal collection window: from 00:00 JST on the calendar day
after the signal date, inclusive, until 09:00 JST on the next TSE business day,
exclusive. Weekend and Japanese exchange holidays extend the upper boundary;
they do not move the lower boundary. Early and late runs fail without fetching
data or creating an artifact.

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
available or its 10% catastrophic stop is observed. Run the offline finalizer
after the official daily-OHLCV archive has been updated:

```bash
uv run python scripts/finalize-event-forward-outcomes.py
```

The finalizer appends immutable rows to
`out/event-forward-evidence/outcomes.jsonl`. It never edits the source ledger.
Each outcome binds to the original `record_sha256`, artifact SHA-256, and
`execution_candidate_id`, then records:

- official next-session open;
- the official exit bar and either the 20th-session close, the 10% stop price,
  or the official open when price gaps through the stop;
- frozen 0.298% round-trip costs and net return;
- OHLCV archive path and SHA-256;
- the previous and current outcome SHA-256.

Source and outcome hash chains, artifact identity, signal date, strategy ID,
candidate IDs, and candidate counts are revalidated before append. A completed
session with a missing OHLCV row fails closed. An immature candidate remains
pending, and reruns do not duplicate a finalized outcome. Zero-candidate days
return without creating an outcome ledger or loading the OHLCV archive.

These rows have
`evidence_class=registered_backtest_shadow`,
`paper_execution_observed=false`, and
`execution_evidence_eligible=false`. Their
`comparable_to_registered_backtest=true` applies only to the per-candidate
modeled price path and frozen cost/stop calculation. It is not paper-fill,
no-fill, opening-book, 2M portfolio-allocation, or live evidence. A separate
aggregate report must still reproduce the registered portfolio constraints,
and paper execution reproduction remains required before activation.
