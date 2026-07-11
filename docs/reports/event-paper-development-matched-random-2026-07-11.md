# Event Paper Development Matched-Random Diagnostic

Date: 2026-07-11

## Scope

This is a development-only reproducibility diagnostic for
`event_cluster_earnings_dividend_value_guard_fixed20_stop_v1_research`.
It is not target-paper evidence, does not authorize publication, and does not
alter the frozen selection definition.

The run used `--split development` with the frozen split manifest. It did not
pass `--include-locked-oos`, and it wrote temporary outputs only.

## Fixed comparison contract

- Entry: next-session open, unconditional
- Exit: 20th-session close, with catastrophic stop
- Catastrophic stop: `CAT_STOP_PCT=-0.10`
- Round-trip cost: `0.00298`
- Random baseline: `same_symbol_random_date`, 300 deterministic seeds
- Portfolio constraints: five positions maximum, 20% maximum notional per
  position, 100-share lots, no same-symbol overlap

`simulate-event-portfolio.py` passes the same `CandidateSpec` to selected and
random candidate construction. For this candidate, that spec has
`catastrophic_stop=True`; both sides therefore use the same -10% stop. This
corrects the simulator's future comparison contract, but it does not revise or
reinterpret the previously recorded locked-OOS result that used an 8% random
stop.

## Development result

The fixed manifest selected 73,380 development observations and 95 candidate
occurrences. All 95 random pools were matched without fallback.

| Capital | Selected net PnL | PF | Max DD | Random percentile |
| ---: | ---: | ---: | ---: | ---: |
| 1,000,000 | 354,289 | 3.390 | 31,158 | 0.983 |
| 2,000,000 | 911,462 | 3.451 | 100,797 | 0.993 |
| 5,000,000 | 2,493,653 | 3.335 | 288,155 | 0.980 |

## Interpretation and boundary

This confirms that the current development simulator applies a comparable -10%
catastrophic-stop rule to both selected and random paths. It does **not** make
the historical locked-OOS percentile valid, does not supply new locked-OOS
evidence, and does not lift the frozen-v1 paper-publication block.

Replacing the invalid locked-OOS comparison would require the explicit approval
and procedure in ADR-0005 before any locked-OOS data is rerun or inspected.
