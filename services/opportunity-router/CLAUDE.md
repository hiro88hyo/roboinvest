# Opportunity Router Development Guide

## Current Authority

Phase 1 only: pure routing functions, version/SHA-256 validation, deterministic decision identity,
and a local append-only JSONL decision ledger.

The package is library-only. Do not add a CLI, daemon, Pub/Sub, Supabase, Dashboard, Aggregator,
Gateway, OMS, paper, or live connection without a separate authorization.

The candidate-independent Phase 2 design draft does not expand this package's implementation
authority. Candidate selection remains prohibited until after the 2026-09-30 adjudication and a
separate authorization.

## Boundaries

- admitted playbooks remain zero outside unit-test fixtures
- no historical or forward outcome computation
- no prospective shadow collection
- no `trade-signals` publication
- no changes to the 2026-09-30 Project Kill Switch evidence pipeline
- fail closed on missing, ambiguous, stale, invalid, or hash-mismatched inputs
- all records are append-only and hash chained; correction is a later record, never mutation

## Tests

- fixtures belong in `src/opportunity_router/_testing.py`
- tests use the `test_opportunity_router_*` prefix
- pure routing tests must not touch the network or ambient environment
