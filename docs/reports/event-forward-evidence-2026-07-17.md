# Event Forward Evidence — Signal Date 2026-07-17

Recorded: 2026-07-18 JST

## Result

A causal schema-v3 forward artifact was generated for signal date 2026-07-17
and appended as the second row of the forward-evidence hash chain.

- J-Quants financial-summary rows: 10
- candidate count: 0
- incomplete required-OHLCV candidates: 0
- exclusions: 0
- publication count: 0
- status: `no_candidate_complete_artifact`

This is a valid zero-candidate observation. It does not create a pending
position or economic outcome and is not evidence of profitability.

## Causality Checks

- `schema_version=3`
- `causality_verified=true`
- forward bars used by candidate features: false
- candidate artifact contains entry price: false
- entry date source: TSE business calendar
- receipt provenance: export metadata
- fetch completion and source coverage: verified
- paper publication: disabled

The accepted financial export completed at
`2026-07-18T05:17:13.377522+00:00` (2026-07-18 14:17 JST), after the complete
2026-07-17 JST calendar day and before the next TSE session's 09:00 entry
cutoff.

## Fail-Closed Recovery

Two unsafe artifacts were rejected before this successful record:

- Signal date 2026-07-13 was fetched on 2026-07-18. All 49 events were marked
  `late_data_receipt`; the artifact remains outside the ledger at
  `out/event-paper-observation/causal-candidates-2026-07-13.json`, SHA-256
  `4c7b1284fd80e2685c68b3d4bcde49530f5c82b894d9331005221ce5e11249aa`.
- The first 2026-07-17 snapshot had completed at 23:03 JST on the signal date,
  57 minutes before the required next-calendar-day coverage window. It was
  rejected and retained as
  `out/event-paper-observation/causal-candidates-2026-07-17.invalid-early-fetch-20260717T140315Z.json`,
  SHA-256
  `c6f19693c0ed2467eb12e6ef75d5104ef087fe78d263a123d3d6863d5b3c3093`.

`scripts/run-event-forward-evidence.py` previously passed `--resume` to the
financial-summary exporter, which caused a completed but too-early fetch to
block the required later snapshot. The runner now always appends a fresh
financial-summary response for its explicit signal date. Daily OHLCV remains
resumable. It also rejects early or late execution before API access and any
artifact write. Export, detector, runner, and ledger tests passed (`37` tests),
including exact window boundaries and the 2026-07-17 weekend/holiday interval.

## Artifacts

- candidate JSON:
  `out/event-paper-observation/causal-candidates-2026-07-17.json`
  SHA-256 `41b8b850040665c00bef85d03492f10842f9e360635dba8cf0f5f329e63d093c`
- candidate CSV:
  `out/event-paper-observation/causal-candidates-2026-07-17.csv`
  SHA-256 `942d5f0fd083fb0f5ccfa9892802f2a5139ad678e1400d0600c9a8475f58ccc4`
- previous ledger row:
  `e51f28618a6122a7aab22feffc3bb7ddfcc7ffe44fb1f4db81025962692ff7a6`
- new ledger row:
  `5998bac0bbfe0d3c887ab66801f4e0eebc11d2390df730d6c103a1300d94c88c`
- ledger file after append:
  SHA-256 `901a4ec3b5bcc7628591add3be4aa0ad6e84a32c8dcad446babaa9adbd484b2d`

The ledger chain validated with two rows. No watchlist update, paper signal,
managed Pub/Sub publication, or live action was performed.
