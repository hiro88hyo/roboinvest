# Reproducibility and Data Boundary

## Repository-Only Checks

These commands require no production credentials:

```bash
uv sync --locked
make lint-all
make test-all
make review-profitability
```

Useful CLI discovery commands:

```bash
uv run python scripts/backtest-swing-daily.py --help
uv run python scripts/check-replay-report-set.py --help
uv run python scripts/check-paper-backtest-report.py --help
uv run python scripts/diagnose-event-cluster-rules.py --help
uv run python scripts/simulate-event-portfolio.py --help
uv run python scripts/evaluate-event-ai.py --help
```

## Data Not Included in Git

The following are ignored or private and are not part of the source review:

- `data/`: J-Quants exports, fixtures derived from private datasets, OHLCV
- `out/`: generated reports, labels, replay results, paper artifacts
- production Supabase rows
- archived order books, ticks, and approved orders
- 1Password, broker, Supabase, and GCP credentials

This protects credentials, licensed data, and large generated artifacts, but it
means the headline numerical results cannot be independently recomputed from a
fresh clone today. A complete numerical audit requires a separate read-only
artifact bundle.

## Required Artifact Bundle

The owner should provide the following outside Git for a full review:

| Class | Required contents |
| --- | --- |
| Daily swing | point-in-time OHLCV input, selected result JSON, all matched-random outputs, stress and capital-sensitivity outputs |
| Event cluster | observation dataset, split manifest, candidate diagnostics, portfolio simulations, 300-seed random baselines |
| Event AI | frozen jobs, labels, parser failures, evaluation reports, shuffled/placebo comparisons |
| Intraday replay | orders JSONL, books JSONL, fills, rejects, positions, backtest reports, replay-set gate output |
| Operations | read-only exports of relevant strategy, aggregator, trade, and position rows with timestamps |

Every supplied file should have:

- SHA-256 hash
- byte size and row count
- generation command and Git commit
- source date range and timezone
- whether the file contains licensed or personal data
- schema or contract version

Example manifest generation:

```bash
sha256sum path/to/artifact
git rev-parse HEAD
wc -c path/to/artifact
wc -l path/to/artifact
```

## Reproduction Boundaries

- Do not rerun or inspect a frozen OOS split while tuning a candidate.
- Do not substitute current Supabase state for a dated export.
- Treat JST as the trading-date timezone.
- Preserve the exact strategy ID, cost model, random seeds, split manifest, and
  execution model recorded by the source report.
- Record failures and zero-candidate days; do not drop them from the sample.

## Known Reproducibility Gaps

1. Most generated result JSON files referenced by tracked reports live under
   ignored `out/` paths and are not remotely reviewable.
2. Historical paper/live reports are partly narrative snapshots rather than
   immutable signed exports.
3. Cost semantics differ between operational paper rows and research harnesses.
4. Some early backtests used shorter histories or simplified one-share sizing.
5. Broker-side fees and taxes are not uniformly represented across all reports.

These gaps should reduce confidence. They must not be filled by assuming the
most favorable interpretation.
