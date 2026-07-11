# Event AI Earnings Train Freeze — 2026-07-11

## Decision

The earnings Event AI arm is frozen after failing the preregistered Train
Minimum Effect Gate. This is a train-only decision. Validation and locked OOS
were neither evaluated nor labeled as part of this decision.

## Inputs

- Jobs: `out/event-ai/jobs-earnings-development-all-gemma4-seed1.jsonl`
- Labels: `out/event-ai/labels-earnings-development-all-gemma4-seed1.jsonl`
- Train jobs and successful labels: 46,757 / 46,757
- Train report: `out/event-ai/train-report-earnings-development-all-gemma4-seed1-complete-2026-07-11.json`
- Human-adjudication ledger: `out/event-ai/manual-adjudications-earnings-train-2026-07-11.jsonl`

Two initially unparseable model responses were recorded as user-approved
hypothesis labels. Each ledger entry is bound to its exact event ID, prompt
hash, and raw-response SHA-256; it does not change general parser behavior.

## Gate Result

The required comparison was `ai_fundamental_and_technical`
(`event_plus_ai_plus_fundamental_plus_technical`) against
`fundamental_and_technical` (`event_plus_fundamental_plus_technical`). A
single `fixed_2d` or `fixed_5d` exit had to satisfy all of:

1. PF improvement of at least +0.10.
2. Net PnL no lower than rule-only.
3. The AI-rejected rule-pass subset having PF below 1.0.

| Exit | PF improvement | AI net PnL vs rule-only | Rejected subset PF | Result |
| --- | ---: | ---: | ---: | --- |
| fixed_2d | +0.051 | 489,569 vs 582,516 | 1.110 | FAIL |
| fixed_5d | +0.030 | 595,996 vs 786,040 | 1.203 | FAIL |

Neither exit meets any of the three continuation conditions as a whole. The
train report therefore records `train_minimum_effect_gate.status = FAIL`.

## Interpretation

For this frozen prompt, model, and earnings-train cohort, adding the AI label
as a second-stage trade selector did not provide the required economic value.
It modestly increased profit factor but reduced net PnL, while the trades it
rejected were not a demonstrably losing subset. This result is not evidence
that every possible AI approach is ineffective; it is a rejection of this
specific preregistered AI arm.

## Consequence

- Do not run or inspect validation for this AI arm.
- Do not use this AI arm for paper or live promotion.
- Future reconsideration requires a newly preregistered hypothesis and a
  separate evaluation plan; it must not tune this rejected arm against the
  hidden validation or locked-OOS periods.
