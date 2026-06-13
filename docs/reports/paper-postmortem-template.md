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

## Validation Questions

This paper run is a validation run, not a live-size increase signal by itself.
Answer these before any live change proposal:

- Safety routing: were live trades/orders unchanged for the trading date?
- Mode consistency: did production env, Gateway container env, and
  `system_status.trade_mode` all stay on `paper`?
- Archive coverage: did Gateway write approved paper orders and did
  Feature Engine write matching book snapshots for the same JST date?
- Replayability: did `run-paper-postmortem.sh` complete from exported archives
  without manual data repair?
- Execution quality: were fill ratio, no-fill count, partial count, spread bps,
  and slippage within the gate thresholds?
- Strategy quality: did total net PnL, profit factor, max drawdown, Sharpe ratio,
  and expectancy support continuing paper observation?
- Candidate filter: if `ENTRY_VOLUME_RATIO_MIN=2.0` was enabled, did it reduce
  weak entries without eliminating useful signals?
- AI liveness: were AI parse/call failures visible, and was there no unexplained
  `AI_STRATEGY_SILENT` condition during market hours?
- Decision discipline: does the result justify `no live change`,
  `continue paper`, or a separate live-change proposal with rollback?

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
