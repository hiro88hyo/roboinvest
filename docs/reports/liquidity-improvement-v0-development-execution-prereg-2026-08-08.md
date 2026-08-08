# Liquidity Improvement V0 Development Execution Registration

Date: 2026-08-08

Status: registered before development outcome computation. Exactly one development run is
authorized. Validation and locked-OOS outcomes remain prohibited.

Candidate: `liqimp1m_logdiff_v0_research`

This execution registration does not alter the factor, universe, portfolio constraints, costs,
stops, split boundaries, gates, or decision contract in the V0 preregistration. It binds the
remaining mechanical portfolio-accounting choices required to run the frozen candidate.

## Bound Artifacts

- research config SHA-256:
  `c803bcbd6405b24cad1c754b0e32e26632e0483fa91116ba1e41590c29cf24bd`
- normalized manifest SHA-256:
  `fb41af1ef19585dcaa2a962e7c8fca1b429ce01ac55d391949637e96a60a61d6`
- feature manifest SHA-256:
  `478ea180f2d8e4a9bc80f9b7d5d1fa4f52181974767776d94858e119eb669066`
- feature cohort SHA-256:
  `8d16d1eefa3657a5c9a3704c5c97c088681545bfb6d1a38c2c41a2176c86f5e1`
- development simulator SHA-256:
  `aac9e4d74c4efc37af98939bffd6228fe65a3ddb8bde2b342f43d01d669d9533`

The machine-readable authority is
`research/liquidity/liqimp1m-logdiff-v0-development-run.json`.

## Mechanical Bindings

- The trading calendar is the sorted unique date set in normalized J-Quants bars. Entry is the
  next date, and scheduled exit is the 20th holding date including entry.
- All open entries are processed before that session's exits. Cash and a position slot released
  at the close cannot be reused at the same open.
- For a carried position, the day's positive `AdjFactor` changes shares, the stop, and the prior
  raw mark before stop/close processing. It is not reapplied on entry day.
- A corporate action producing fractional shares fails closed.
- Stop processing precedes scheduled-close processing: gap at raw open, otherwise stop when raw
  low crosses it.
- A missing intermediate close carries the prior raw mark after the day's split adjustment for
  MTM only. It does not fill the bar and cannot supply an execution price.
- A missing scheduled close fails the complete run closed.
- Maximum drawdown is peak-to-trough daily MTM JPY divided by starting capital for the registered
  10%-of-capital gate.
- The positive-year denominator is 2021, 2022, 2023, and 2024. A year with no exits has zero PnL
  and is non-positive.
- PF with gains and no losses passes a finite exclusive threshold. PF with neither gains nor
  losses is undefined and fails.

## Authorized Command

```bash
env UV_CACHE_DIR=/tmp/uv-cache uv run python \
  scripts/simulate-liquidity-improvement-development.py \
  --normalized-dir data/liquidity-research-normalized-v0 \
  --feature-dir data/liquidity-research-features-v0 \
  --config research/liquidity/liqimp1m-logdiff-v0.json \
  --run-registration research/liquidity/liqimp1m-logdiff-v0-development-run.json \
  --output-dir out/liquidity-improvement-v0-development-2026-08-08
```

The executable accepts no split selector, verifies all bound hashes, filters only development
feature rows, writes to a new output path atomically, and refuses reruns at that path.

## Pre-result Operational Correction

The first authorized process stopped before creating an output directory or serializing any
metric. Source `AdjFactor=0.3333333333333333` for code 65730 made a theoretically integral
100-to-300 share conversion differ from an exact Decimal integer by Float64 representation noise.
No PF, drawdown, yearly PnL, or gate result was emitted or inspected.

The correction permits rounding only when adjusted shares are within `0.00000001` shares of an
integer. A genuine fractional result still fails closed. Synthetic tests cover both cases. No
feature, universe, selection, price, stop, cost, split, or gate changed. The corrected simulator
SHA-256 is
`60939f5791d16a8da6248ad465c18004444beca3672e4839ec47341475a8044e`, and the replacement
machine-readable execution authority is
`research/liquidity/liqimp1m-logdiff-v0-development-run-v2.json`.
