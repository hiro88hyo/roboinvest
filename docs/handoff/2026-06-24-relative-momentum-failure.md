# 2026-06-24 Relative Momentum Failure

## Decision

`relative_momentum` is no longer a paper observation candidate. Production
`strategy-rule` now runs in no-op mode with `STRATEGIES_ENABLED=` so it keeps
consuming processed features but emits no RULE BUY signals.

This explicitly withdraws the 2026-06-23 observation candidate. Do not resume it
by threshold tuning alone.

Cleanup note: several rejected intraday candidates were temporarily implemented
as non-default plugins or replay wrappers during this investigation. After
ADR-0003, those rejected plugin files and candidate-specific replay wrappers
were removed from the production source tree. The results below are retained as
evidence, not as active strategy code.

## Evidence

2026-06-24 paper result:

- DB paper trades: BUY 1 / SELL 1
- FIFO paper PnL: `-1,100`
- open positions after close: paper 0 / live 0
- no-fill: `3186 stale_book`, `6668 limit_not_crossed`
- Gateway reject: `6668 AI BUY paper_symbol_order_cooldown`

Exported archive:

- `out/paper-archive-2026-06-24/features.jsonl`: 44,626 rows
- Actual order replay: closed 2, total net PnL `-5,713.332`, PF `0`, gate `FAIL`
- Replay showed `3186` would likely have filled and then lost more; the live no-fill
  reduced damage rather than hiding profit.

Feature-level forward return diagnostics:

| Condition | Candidates | Avg 15m return | Avg 30m return | Positive 30m |
| --- | ---: | ---: | ---: | ---: |
| baseline new-high, no momentum | 12 | `-18.001 bps` | `-43.111 bps` | `25.0%` |
| base relative momentum `100/0.80/+20` | 10 | `-8.150 bps` | `-25.525 bps` | `50.0%` |
| strict `150/0.90/+30` | 6 | `-53.477 bps` | `-93.765 bps` | `0.0%` |
| strict `300/0.90/+30` | 5 | `-50.798 bps` | `-95.399 bps` | `0.0%` |

The stricter thresholds concentrated short-term exhaustion rather than alpha.

## Root Cause

Scanner was not the main failure. The watchlist included strong symbols
(`6752`, `6668`, `7282`, `9247`, `8233`). The intraday algorithm turned usable
symbols into bad trades through:

- high-chase entry
- VWAP-as-stop with too little noise tolerance
- 15-minute holding assumption that does not fit momentum behavior
- insufficient veto for overheated/failed candidates such as `3186`

`6752` is the clearest example: actual paper exited at `-1,100`, but feature
path after entry reached positive 30m/60m/EOD returns. The position was cut by a
too-tight stop before the move developed.

## Production Change

Changed and deployed:

- `infra/env.production`: `STRATEGIES_ENABLED=`
- `infra/docker-compose.prod.yml`: `STRATEGIES_ENABLED` interpolation now honors
  an explicitly empty value.
- `strategy-rule` stream can run with no enabled strategies instead of exiting.
- `scripts/production-preopen-check.py` now expects empty `STRATEGIES_ENABLED`.

Verification:

- `uv run pytest services/strategy-rule/tests/unit`: `110 passed`
- Production `strategy-rule` rebuilt/recreated.
- Container env confirmed: `STRATEGIES_ENABLED=''`
- Production preopen check with `--kabu-offline --expected-trade-mode paper`:
  `OK 130 / WARN 0 / NG 0 / SKIP 0`

## Next Work

Do not turn RULE BUY back on until a replacement strategy beats a random
baseline under the same time/execution constraints.

Attempted replacement: `vwap_reclaim`

- implemented as a new non-default strategy plugin
- registered as `STRATEGIES_ENABLED=vwap_reclaim` only
- added focused unit coverage and replay script
- production remains no-op; do not enable this strategy

Archive replay through `strategy-rule -> aggregator -> gateway -> oms-paper`
with `ENTRY_MAX_SPREAD_BPS=30`, `ENTRY_MAX_SPREAD_TICKS=2`,
`ENTRY_MIN_ASK_DEPTH_5=1000`, and `BUY_LIMIT_OFFSET_TICKS=0`:

| Date | BUY orders | Closed | Net PnL | PF | No-fill rate |
| --- | ---: | ---: | ---: | ---: | ---: |
| 2026-06-18 | 14 | 4 | `-4,176.231` | `0.338` | `55.6%` |
| 2026-06-22 | 12 | 4 | `-18,093.911` | `0.201` | `43.8%` |
| 2026-06-23 | 14 | 6 | `-873.863` | `0.974` | `35.0%` |
| 2026-06-24 | 9 | 5 | `-78,880.457` | `0` | `28.6%` |

Combined net PnL is `-102,024.462`. This fails for the same practical reason as
`relative_momentum`: fills increase exactly when the candidate is wrong, and
6/24 dominates the result. Do not rescue it by cherry-picking the one parameter
set that produced a single winning candidate.

Attempted replacement: `oversold_reclaim`

- implemented as a new non-default strategy plugin
- registered as `STRATEGIES_ENABLED=oversold_reclaim` only
- production remains no-op; do not enable this strategy
- added replay helpers:
  - `scripts/filter-order-books-for-orders.py`
  - `scripts/cap-order-notional.py`
  - `scripts/run-oversold-reclaim-replay.sh`

Feature-level diagnostics were better than momentum strategies. With common
parameters (`rsi_arm=25`, `rsi_reclaim=35`, `reclaim_bps=20`,
`max_spread_bps=15`, `max_spread_ticks=1`, `min_ask_depth_5=300`,
`min_book_imbalance_5=0`, `max_price=3000`), forward returns were positive on
all four checked days:

| Date | Candidates | Avg 15m | Avg 30m | Avg 60m |
| --- | ---: | ---: | ---: | ---: |
| 2026-06-18 | 12 | `+40.421 bps` | `+55.190 bps` | `-70.127 bps` |
| 2026-06-22 | 16 | `+30.508 bps` | `+15.891 bps` | `+10.512 bps` |
| 2026-06-23 | 17 | `+19.349 bps` | `+17.357 bps` | `+16.213 bps` |
| 2026-06-24 | 13 | `+7.045 bps` | `+27.054 bps` | `+56.167 bps` |

Raw gateway/OMS replay exposed a separate backtest sizing issue: Gateway
backtest uses risk-per-share sizing without the stream runner's capital/exposure
rebudgeting, so tight stops generated multi-million-yen orders against
`capital=1,000,000`. Those raw results are not a valid capital-constrained
strategy evaluation.

After capping BUY orders to `200,000` yen notional per order for diagnostic
purposes, OMS Paper results were:

| Date | Closed | Net PnL | PF | No-fill rate |
| --- | ---: | ---: | ---: | ---: |
| 2026-06-18 | 3 | `+280.422` | `1.808` | `45.5%` |
| 2026-06-22 | 1 | `-838.081` | `0` | `75.0%` |
| 2026-06-23 | 1 | `+564.884` | n/a | `77.8%` |
| 2026-06-24 | 1 | `-1,837.464` | `0` | `66.7%` |

Combined capped net PnL is `-1,830.237`. This is materially less bad than the
momentum/reclaim attempts but still fails after execution and cost.

Follow-up implementation:

- Gateway `RiskConfig` now supports `max_notional_per_order_pct`.
- `gateway backtest` accepts `--max-notional-per-order-pct`.
- `lot_calculator` caps BUY quantity by both risk-per-share and per-order
  notional.
- `scripts/run-oversold-reclaim-replay.sh` now passes
  `--max-notional-per-order-pct 0.20` by default and filters books to order
  symbols before OMS Paper replay to avoid OOM.

Verification:

- `uv run pytest services/gateway/tests/unit services/strategy-rule/tests/unit`:
  `324 passed`

With the formal Gateway cap (`capital=1,000,000`,
`max_notional_per_order_pct=0.20`), `oversold_reclaim` target/stop replay
matched the earlier diagnostic cap:

| Date | Gateway approved/rejected | Total net PnL |
| --- | ---: | ---: |
| 2026-06-18 | `8 / 4` | `+280.422` |
| 2026-06-22 | `7 / 9` | `-838.081` |
| 2026-06-23 | `8 / 9` | `+564.884` |
| 2026-06-24 | `5 / 8` | `-1,837.464` |

30-minute fixed-exit diagnostic:

- built with `scripts/build-time-exit-orders.py`
- BUY orders use formal Gateway cap
- BUY target/stop/trailing fields are stripped for this diagnostic
- MARKET SELL is appended 30 minutes after each BUY
- this is not production-ready because SELL no-fill can leave a position open

| Date | Closed | Total net PnL | No-fill rate |
| --- | ---: | ---: | ---: |
| 2026-06-18 | 2 | `+1,264.154` | `68.8%` |
| 2026-06-22 | 1 | `-538.528` | `85.7%` |
| 2026-06-23 | 1 | `-489.316` | `87.5%` |
| 2026-06-24 | 1 | `-1,038.656` | `80.0%` |

Combined 30-minute fixed-exit net PnL is `-802.346`, better than target/stop
but still below zero. The bottleneck is now execution: no-fill rates are too
high and too few closed trades remain for a robust edge.

Additional execution diagnostics:

`+1 tick` BUY crossing with the same Gateway cap materially improved fill rates
but made PnL worse on every day:

| Date | No-fill rate | Total net PnL |
| --- | ---: | ---: |
| 2026-06-18 | `15.4%` | `-1,223.718` |
| 2026-06-22 | `27.3%` | `-7,006.774` |
| 2026-06-23 | `6.7%` | `-2,652.330` |
| 2026-06-24 | `11.1%` | `-6,976.357` |

Combined `+1 tick` net PnL is `-17,859.179`. This rejects the hypothesis that
the strategy only fails because passive orders do not fill. When made more
aggressive, it fills the wrong trades.

Stricter liquidity filters with passive orders (`ENTRY_MAX_SPREAD_BPS=10`,
`ENTRY_MAX_SPREAD_TICKS=1`, `ENTRY_MIN_ASK_DEPTH_5=1000`,
`ENTRY_MIN_BOOK_IMBALANCE_5=0.1`) also failed:

| Date | Gateway approved/rejected | No-fill rate | Total net PnL |
| --- | ---: | ---: | ---: |
| 2026-06-18 | `5 / 4` | `25.0%` | `-166.074` |
| 2026-06-22 | `5 / 7` | `42.9%` | `-2,044.532` |
| 2026-06-23 | `7 / 7` | `55.6%` | `-617.691` |
| 2026-06-24 | `4 / 8` | `60.0%` | `-1,436.868` |

Combined stricter-liquidity net PnL is `-4,265.165`. Liquidity filters reduce
some execution damage but do not rescue the strategy.

Attempted replacement: `rsi_vwap_recovery`

- added as a new non-default strategy plugin
- registered as `STRATEGIES_ENABLED=rsi_vwap_recovery` only
- production remains no-op; do not enable this strategy
- added feature-rule exploration:
  - `scripts/explore-feature-rule-grid.py`
  - `scripts/explore-liquid-trend-pullback.py`
- added replay helper:
  - `scripts/run-rsi-vwap-recovery-replay.sh`

Feature grid search found one relatively less-bad idea under strict execution
filters (`ENTRY_MAX_SPREAD_BPS=10`, `ENTRY_MAX_SPREAD_TICKS=1`,
`ENTRY_MIN_ASK_DEPTH_5=1000`): buy neutral RSI recovery only when
`35 <= rsi <= 50` and price is above VWAP but not more than `160 bps` above it.
Feature-level 60-minute forward return was positive on average (`+13.64 bps`)
and did not collapse on 2026-06-24, but the edge was too thin for trading costs.

Replay through `strategy-rule -> aggregator -> gateway -> oms-paper` with
`capital=1,000,000`, `max_notional_per_order_pct=0.20`, and passive BUY limits:

| Date | Gateway approved/rejected | Closed | Total net PnL | No-fill rate |
| --- | ---: | ---: | ---: | ---: |
| 2026-06-18 | `7 / 7` | 4 | `-1,338.297` | `27.3%` |
| 2026-06-22 | `5 / 13` | 2 | `-1,246.022` | `42.9%` |
| 2026-06-23 | `6 / 14` | 0 | `0` | `100.0%` |
| 2026-06-24 | `4 / 14` | 2 | `-1,076.439` | `33.3%` |

Combined passive net PnL is `-3,660.758`.

`+1 tick` BUY crossing was spot-checked on the two most relevant days and again
made results worse:

| Date | Closed | Total net PnL | No-fill rate |
| --- | ---: | ---: | ---: |
| 2026-06-18 | 7 | `-3,674.764` | `0.0%` |
| 2026-06-24 | 3 | `-1,741.915` | `14.3%` |

Conclusion: this rejects the weak-recovery premise. Even when feature-level
returns look mildly positive, the expected move is smaller than realistic
commission/slippage/stop conversion. Scanner is still not the primary failure:
the failure is that the intraday entry edge is too small after execution.

Random-entry baseline:

- added `scripts/generate-random-entry-signals.py`
- added `scripts/run-random-entry-baseline-replay.sh`
- baseline randomizes entry timing/symbol selection only after applying the same
  practical execution constraints used by `rsi_vwap_recovery`
- default baseline constraints:
  - `ENTRY_MAX_SPREAD_BPS=10`
  - `ENTRY_MAX_SPREAD_TICKS=1`
  - `ENTRY_MIN_ASK_DEPTH_5=1000`
  - `RANDOM_ENTRY_MAX_PRICE=2000`
  - price above VWAP, no more than `160 bps` above VWAP
  - stop at VWAP, target `1.5R`
  - Gateway `max_notional_per_order_pct=0.20`

The `RANDOM_ENTRY_MAX_PRICE=2000` constraint is required for a meaningful
baseline under `capital=1,000,000` and `max_notional_per_order_pct=0.20`: a
100-share lot above 2,000 yen cannot pass the 200,000-yen per-order cap.
Without this, random samples can be rejected entirely as `below_min_lot`, which
is not a useful trading comparison.

Random baseline results:

| Seed | 2026-06-18 | 2026-06-22 | 2026-06-23 | 2026-06-24 | Total net PnL |
| --- | ---: | ---: | ---: | ---: | ---: |
| 1 | `-1,110.604` | `-3,529.869` | `-362.838` | `-2,511.817` | `-7,515.128` |
| 2 | `+210.966` | `-3,529.869` | `-527.183` | `-2,511.817` | `-6,357.903` |
| 3 | `+210.966` | `-3,529.869` | `-627.332` | `-2,511.817` | `-6,458.052` |

For comparison, `rsi_vwap_recovery` passive total over the same four days was
`-3,660.758`. It beats this small random baseline sample, but still fails the
absolute requirement (`net > 0`). A strategy cannot be accepted merely because
it loses less than random.

Attempted diagnostic: RSI oversold + MACD golden cross

- added `scripts/generate-rsi-macd-reversal-signals.py`
- added `scripts/run-rsi-macd-reversal-replay.sh`
- this is an archive-only diagnostic; production `ProcessedFeatures` does not
  yet include MACD fields
- default entry:
  - aggregate archived feature ticks to 1-minute closes
  - compute MACD `12/26/9`
  - require RSI `<= 30` within the last 30 minutes
  - require MACD line crossing above signal line
  - require price between `-20 bps` and `+120 bps` from VWAP
  - require `price <= 2000`, spread/depth filters, and Gateway
    `max_notional_per_order_pct=0.20`

Target/stop replay was still negative:

| Date | Signals | Closed | Total net PnL | No-fill rate |
| --- | ---: | ---: | ---: | ---: |
| 2026-06-18 | 4 | 2 | `-2,542.914` | `33.3%` |
| 2026-06-22 | 3 | 1 | `-880.674` | `25.0%` |
| 2026-06-23 | 5 | 1 | `+1,147.714` | `66.7%` |
| 2026-06-24 | 3 | 1 | `+191.511` | `50.0%` |

Combined target/stop net PnL is `-2,084.363`.

Time-exit grid on the same approved BUY orders:

| Exit | Total net PnL | Closed | Open positions |
| --- | ---: | ---: | ---: |
| 5 min | `-2,791.422` | 6 | 0 |
| 10 min | `-231.108` | 6 | 0 |
| 15 min | `+757.574` | 6 | 0 |
| 30 min | `-148.397` | 6 | 0 |
| 60 min | `-4,150.339` | 6 | 0 |

15-minute fixed exit is the first diagnostic to produce positive four-day net
PnL after OMS Paper costs. However, the result is not live/paper-ready:

- no-fill rates remain high (`50%`, `33%`, `80%`, `66.7%` by day)
- total closed trades are only 6 over four days
- `+1 tick` BUY crossing with the same 15-minute exit turns negative:
  - 2026-06-18 `+2,759.187`
  - 2026-06-22 `-1,021.884`
  - 2026-06-23 `-1,649.602`
  - 2026-06-24 `-1,198.023`
  - combined `-1,110.322`

Conclusion: RSI+MACD reversal with 15-minute exit is worth further research,
but the current positive result depends too much on passive no-fills and a tiny
closed-trade sample. Do not add MACD to production contracts/feature-engine or
enable a strategy plugin until this is validated on more archived days and with
a fill model that does not rely on favorable missed fills.

Composite indicator grid:

- added `scripts/explore-composite-indicator-grid.py`
- combines archive-computed 1-minute MACD with RSI, VWAP, Bollinger lower touch,
  return-from-open, peer percentile, and the same execution filters
- top feature-level rule was roughly:
  - RSI `<= 35`
  - oversold lookback `15 min`
  - MACD cross
  - price above/reclaiming VWAP
  - no useful extra contribution from Bollinger in the top rows
- feature-level stats for the top rows looked better than the stricter
  RSI<=30 diagnostic: `n=18`, avg 15m forward return about `+42.76 bps`,
  positive 15m rate `55.6%`, with all four days non-negative on feature-level
  day means.

OMS replay rejected the loosened RSI<=35 variant:

| Variant | 2026-06-18 | 2026-06-22 | 2026-06-23 | 2026-06-24 | Total net PnL |
| --- | ---: | ---: | ---: | ---: | ---: |
| target/stop | `-3,582.783` | `+730.428` | `-1,847.789` | `+479.685` | `-4,220.459` |
| 15m time exit | `-1,685.614` | `-81.866` | `-250.173` | `-396.533` | `-2,414.186` |

This is an important failure mode: feature-level forward return can improve
while OMS-realizable PnL worsens because the added signals change which trades
actually fill and how costs hit. Keep the composite grid as a discovery tool,
but do not accept any rule without OMS replay and random-baseline comparison.

Follow-up with additional feature days and MACD histogram mode:

- Added `--macd-mode` to `scripts/generate-rsi-macd-reversal-signals.py` and
  `RSI_MACD_MODE` support to `scripts/run-rsi-macd-reversal-replay.sh`.
- Added `scripts/check-replay-report-set.py` to prevent accepting a candidate
  from a single favorable day or a feature-level forward-return result.
- Extended `scripts/check-paper-backtest-report.py` so CLI callers can pass
  `--max-no-fill-rate`, `--max-average-spread-ticks`, and `--max-spread-ticks`.
- Re-ran the composite grid with extra feature archives:
  - `2026-06-16`
  - `2026-06-17`
  - `2026-06-18`
  - `2026-06-19`
  - `2026-06-22`
  - `2026-06-23`
  - `2026-06-24`
- Top feature-level rule shifted to:
  - RSI `<= 30`
  - oversold lookback `15 min`
  - MACD histogram positive and rising
  - price near or above VWAP
  - optional Bollinger lower touch did not change the result
- Feature-level stats for that rule were:
  - `n=30`
  - avg 15m forward return `+26.59 bps`
  - positive 15m rate `56.7%`
  - day means: `2026-06-17 +97.6`, `2026-06-18 +63.3`,
    `2026-06-19 +10.6`, `2026-06-22 +35.5`,
    `2026-06-23 +8.0`, `2026-06-24 -20.9`

OMS replay for the histogram-positive/rising variant was still not acceptable:

| Variant | 2026-06-16 | 2026-06-18 | 2026-06-22 | 2026-06-23 | 2026-06-24 | Total net PnL |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 15m time exit, passive BUY | `0` | `-1,390.061` | `-20.096` | `+401.611` | `+111.945` | `-896.602` |
| 15m time exit, BUY `+1 tick` | `0` | `+694.110` | `-1,495.620` | `-1,079.914` | `-2,037.892` | `-3,919.316` |

The `+1 tick` run materially improved fills but worsened PnL. That means the
main problem is not simply missed fills or an overly passive execution
algorithm. The signal is still admitting too many trades whose edge disappears
once they are actually filled. This also supports the earlier conclusion that
scanner selection is not the primary failure; the intraday entry/exit premise
still lacks a robust, OMS-realizable edge.

The multi-day acceptance gate now rejects this candidate explicitly:

```bash
uv run python scripts/check-replay-report-set.py \
  --label rsi-macd-hist-rising-15m \
  --report /tmp/rsi-macd-hist-rising-replay-2026-06-{16,18,22,23,24}/backtest-report-15m.json \
  --stress-report /tmp/rsi-macd-hist-rising-plus1-replay-2026-06-{16,18,22,23,24}/backtest-report-15m.json
```

Gate result:

- status `FAIL`
- base net `-896.6015183000`
- closed trades `7 < 20`
- positive days `2/5 < 3`
- weighted no-fill rate `0.65 > 0.30`
- stress net `-3919.3162610500 < 0`

The next experiment should be a fresh entry/exit design, not another threshold
tune:

- entry: do not continue `oversold_reclaim` by simple execution tuning; it is
  better than momentum diagnostics but still fails under realistic execution
- stop: avoid tiny stops that create unrealistic risk-based order sizes; use a
  minimum risk width or explicit notional cap
- exit: compare 15/30/60 minute time exits with an explicit closeout fallback;
  current target/stop conversion loses the forward-return edge
- execution: `+1 tick` and stricter liquidity filters have now been tested and
  failed; next candidate needs a different entry premise, not just better fills
- mandatory diagnostics: random baseline, MFE/MAE, no-fill, and per-symbol
  concentration
