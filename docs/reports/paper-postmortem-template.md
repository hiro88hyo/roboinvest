# Paper Postmortem YYYY-MM-DD

## Run Context

- date: `YYYY-MM-DD`
- strategy experiment: `ENTRY_VOLUME_RATIO_MIN=2.0`
- trade mode: `paper`
- archive output: `out/paper-archive-YYYY-MM-DD`
- command:

```bash
bash scripts/run-paper-postmortem.sh \
  --date YYYY-MM-DD \
  --output-dir out/paper-archive-YYYY-MM-DD
```

## Gate Result

- gate status: `PASS | FAIL`
- decision: `no live change | continue paper | prepare live change proposal`
- decision reason:
  - `...`

## Summary

Paste `out/paper-archive-YYYY-MM-DD/backtest/summary.md` below.

```md
...
```

## Operator Notes

- archive export issues:
  - `none`
- order archive coverage:
  - `orders.jsonl rows: ...`
- book archive coverage:
  - `books.jsonl rows: ...`
- observed service issues:
  - `none`

## Follow-Up

- [ ] If gate failed, keep live unchanged and record the failed metrics.
- [ ] If gate passed, compare against long-horizon walk-forward result before any live proposal.
- [ ] If both gate and walk-forward pass, write a separate live-change plan with rollback.
