# Event Forward Evidence Bootstrap — Signal Date 2026-07-10

Recorded: 2026-07-12 JST

## Result

The first causal schema-v3 forward artifact was generated for signal date
2026-07-10 and appended to the forward-evidence hash chain.

- J-Quants financial-summary rows: 93
- daily OHLCV rows fetched for 2026-07-10: 4,196
- candidate count: 0
- incomplete required-OHLCV candidates: 0
- exclusions: 0
- publication count: 0
- status: `no_candidate_complete_artifact`

This is a valid zero-candidate observation, unlike the legacy July detector
outputs. It has complete export metadata, a source receipt inside the causal
coverage window, required 2026-07-10 OHLCV, and no T+1 entry-price dependency.

It is evidence that the frozen rule selected no occurrence for this signal
date. It is not evidence of profitability and creates no pending position or
exit outcome.

## Causality Checks

- `schema_version=3`
- `causality_verified=true`
- forward bars used by candidate features: false
- candidate artifact contains entry price: false
- entry date source: TSE business calendar
- receipt provenance: export metadata
- fetch completion and source coverage: verified
- paper publication: disabled

The financial export completed at
`2026-07-11T22:12:29.368512+00:00` (2026-07-12 07:12 JST), before the next TSE
session's 09:00 entry cutoff on 2026-07-13.

## Artifacts

- candidate JSON:
  `out/event-paper-observation/causal-candidates-2026-07-10.json`
  SHA-256 `120a81afada6fcddfd1db25f948612f6c8cfa0a2161e9e7300298a61012279d4`
- candidate CSV:
  `out/event-paper-observation/causal-candidates-2026-07-10.csv`
  SHA-256 `942d5f0fd083fb0f5ccfa9892802f2a5139ad678e1400d0600c9a8475f58ccc4`
- ledger row hash:
  `e51f28618a6122a7aab22feffc3bb7ddfcc7ffe44fb1f4db81025962692ff7a6`

No watchlist update, paper signal, managed Pub/Sub publication, or live action
was performed.
