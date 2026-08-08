# Project Kill-Switch Readiness Contract

Date: 2026-07-18

Status: Active prospective adjudication protocol.

## Frozen Evaluation Window

The unchanged project deadline is 2026-09-30. For the frozen cluster-v1
strategy, a signal requires next-session entry plus 20 trading sessions before
its fixed close is observable. Therefore the complete deadline-evaluable
prospective cohort is:

- first signal date: 2026-07-21;
- last signal date: 2026-08-27;
- expected TSE signal dates: 27;
- final possible entry: 2026-08-28;
- final registered close: 2026-09-30 at 15:30 JST.

Signal dates after 2026-08-27 continue as research evidence but cannot be
silently included in the 2026-09-30 economic decision before their registered
exit is available.

## Automated Report

Run:

```bash
uv run python scripts/report-project-kill-switch-readiness.py \
  --output-json out/event-forward-evidence/kill-switch-readiness.json
```

The normal forward runner now performs outcome finalization and writes this
report after recording each signal date.

The reporter validates both append-only hash chains, source-artifact bindings,
candidate IDs and counts, source receipt time, outcome availability time, and
evidence class. It then reports:

- expected, recorded, and missing signal dates;
- complete, incomplete, finalized, and pending candidates;
- the frozen 2M portfolio under five-position, 20%-notional, 100-share-lot,
  same-symbol, cost, fixed-20, and 10% stop assumptions;
- PF, maximum drawdown, and their project gates;
- deadline state and fail-closed project status.

Before 2026-09-30 15:30 JST, status remains
`PENDING_UNTIL_DEADLINE`. At or after that time:

- complete 27-date coverage, complete outcomes, at least one opened trade, PF
  above 1.2, and maximum drawdown below 200,000 JPY produce
  `ECONOMIC_CONDITION_MET_NOT_ACTIVATION`;
- missing dates, incomplete feature data, missing outcomes, no demonstrated
  economic result, or a failed PF/DD condition produce
  `KILL_SWITCH_TRIGGERED`.

The project contract does not contain a numeric minimum-trade value, so the
reporter does not invent one. It always exposes trade count and coverage. A
passing economic condition is not paper execution evidence and does not
authorize paper/live activation.

## Current State

At the 2026-08-08 JST snapshot (through signal date 2026-08-07):

- expected dates to date: 14;
- recorded dates: 14;
- missing dates: 0;
- source coverage complete: true;
- total point-in-time event observations across those artifacts: 2,209;
- complete candidates: 0;
- incomplete candidates: 0;
- finalized candidates: 0;
- economic condition: `NOT_DEMONSTRATED`;
- project status: `PENDING_UNTIL_DEADLINE`;
- activation authorized: false.

The zero-candidate artifacts are complete observations and remain in the
adjudication cohort. They are not zero-PnL trades and must not be dropped or
converted into synthetic outcomes.
