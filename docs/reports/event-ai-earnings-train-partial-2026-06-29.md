# Event AI Earnings Train Partial - 2026-06-29

This report records the current partial local LLM development run. It is train-only and does not
promote any candidate.

Inputs:

- Jobs: `out/event-ai/jobs-earnings-development-all-gemma4-seed1.jsonl`
- Labels: `out/event-ai/labels-earnings-development-all-gemma4-seed1.jsonl`
- Diagnostics:
  - `out/event-ai/diagnostics-earnings-development-train-13003-gemma4-seed1.json`
  - `out/event-ai/eval-earnings-development-train-13003-gemma4-seed1/event-ai-report.json`

Run state:

- Total jobs: 62,247
- Train jobs: 46,757
- Validation jobs: 15,490
- Completed labels: 13,003
- Completed label split: train only
- Validation and locked OOS were not evaluated by this report.

Label distribution:

| Field | Counts |
|---|---|
| fundamental_direction | neutral 3,190; positive 4,579; negative 5,187; unclear 32; mixed 15 |
| fundamental_strength | 0: 325; 1: 8,735; 2: 3,096; 3: 847 |
| expected_horizon | avoid 9,783; 2d 1,548; 5d 1,489; unclear 183 |

## Train Diagnostic

The model is not useful as a standalone long filter on this train subset. `ai_pass` remains
negative across fixed exits:

| Group | Exit | Trades | Net PnL | PF | Max DD |
|---|---|---:|---:|---:|---:|
| all_labeled | fixed_2d | 12,959 | -7,646,595 | 0.729 | 7,692,401 |
| ai_pass | fixed_2d | 1,807 | -662,301 | 0.828 | 802,639 |
| ai_reject | fixed_2d | 11,159 | -6,992,657 | 0.713 | 6,997,137 |
| ai_pass | fixed_5d | 1,807 | -1,354,313 | 0.731 | 1,423,013 |
| ai_pass | fixed_10d | 1,806 | -2,032,599 | 0.678 | 2,156,399 |
| ai_pass | fixed_20d | 1,804 | -1,376,236 | 0.820 | 3,030,279 |

The only encouraging train-only result is that AI improves the existing earnings
`fundamental_and_technical` subset slightly for short exits:

| Group | Exit | Trades | Net PnL | PF | Max DD |
|---|---|---:|---:|---:|---:|
| fundamental_and_technical | fixed_2d | 443 | 223,880 | 1.310 | 168,772 |
| ai_fundamental_and_technical | fixed_2d | 317 | 175,673 | 1.328 | 125,219 |
| fundamental_and_technical | fixed_5d | 443 | 110,183 | 1.113 | 206,915 |
| ai_fundamental_and_technical | fixed_5d | 317 | 117,761 | 1.168 | 145,784 |
| fundamental_and_technical | fixed_10d | 443 | -323,820 | 0.779 | 435,368 |
| ai_fundamental_and_technical | fixed_10d | 317 | -77,911 | 0.922 | 209,077 |

This is not enough to inspect validation or promote anything. It is only a possible train-only
hypothesis: earnings events may need the existing rule-only filter first, with AI as a second-stage
quality filter, and only for short fixed exits.

## Interrupted Additional LLM Run

An attempted additional 1,500-job run was stopped because labels were not increasing and failures
were accumulating.

- Labels before stop: 13,003
- Failure rows written in the interrupted run: 358
- Failure reason: `LLMError: openai-compatible http error: All connection attempts failed`
- Configured base URL at the time: `http://192.168.2.168:1234/v1`
- `/models` check: connection failed

This is an external local LLM server availability issue, not a parser/schema failure. Resume the LLM
run only after `/models` responds.

## Next Step

Do not evaluate validation from these AI labels yet. Once the local LLM endpoint is reachable,
continue the train run in bounded resume chunks. After the train diagnostics are complete, write a
new preregistered earnings AI hypothesis before looking at validation.
