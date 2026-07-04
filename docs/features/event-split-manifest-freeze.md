# Event Split Manifest Freeze

Created: 2026-07-04

This memo records the split-manifest issue for event swing research history
expansion.

## Problem

`scripts/event_research_common.py` originally computed split boundaries from
the observed signal-date distribution:

- `train_end`: 60% date quantile
- `validation_end`: 80% date quantile
- `validation_start` and `locked_oos_start`: 20 trading-day purge offsets

When older J-Quants history is added, those quantiles can move even if no new
forward data has been added. That would silently change the validation and
locked OOS windows, conflicting with the locked OOS freeze in
[ADR-0005](../adr/0005-locked-oos-inspection-freeze.md).

## Decision

Event research scripts that build or evaluate split-sensitive artifacts accept
`--split-manifest path/to/manifest.json`.

When supplied, these fields are frozen from the external manifest:

- `train_end`
- `validation_start`
- `validation_end`
- `locked_oos_start`
- `purge_days`

The current dataset hash, observation count, symbol count, `train_start`, and
`locked_oos_end` are still recomputed from the current observations. This keeps
artifact identity honest while holding promotion boundaries fixed.

Past-direction history expansion therefore increases train coverage only. It
must not move validation or locked OOS boundaries for promotion decisions.

## Supported Scripts

- `scripts/build-event-research-dataset.py`
- `scripts/evaluate-event-research.py`
- `scripts/evaluate-event-ai.py`
- `scripts/build-event-llm-jobs.py`
- `scripts/simulate-event-portfolio.py`

Example:

```bash
uv run python scripts/evaluate-event-research.py \
  --observations out/event-research-expanded/observations.jsonl \
  --split development \
  --split-manifest out/event-research-real-pit/dataset-manifest.json \
  --output-dir out/event-research-expanded-fixed-split
```

Do not use `--split locked-oos --include-locked-oos` merely to test this
plumbing. Locked OOS remains frozen unless explicitly approved.
