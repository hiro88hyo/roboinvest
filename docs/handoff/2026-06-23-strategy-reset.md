# 2026-06-23 Strategy Reset Decision

## Decision

As of the 2026-06-23 paper session, the existing intraday strategy should no
longer be treated as a live candidate. We will stop trying to rescue the current
RULE/AI judge stack with incremental gates and rebuild the strategy from a new
hypothesis.

This is a strategy decision, not an execution-platform decision. Production
services stayed healthy and live trading remained flat. The problem is that the
current entry/judge logic has shown persistent negative expectancy in paper.

## 2026-06-23 Session Summary

Production state:

- `TRADE_MODE=paper`
- live trades: `0`
- live open positions after checks: `0`
- live realized daily PnL: `0`
- all production compose services were `Up`
- watchlist had 30 rows for `valid_date=2026-06-23`

Paper result from `trades_paper` using long-only FIFO pairing:

- BUY fills: `21`
- SELL fills: `21`
- closed pairs: `21`
- open paper positions: `0`
- winners / losers / flat: `4 / 16 / 1`
- realized paper PnL: `-12,500`

Source attribution by BUY source:

- `RULE`: `-11,500`
- `CONSENSUS`: `-1,000`

The session was not dominated by flat exits. It was dominated by small stop-outs:
16 losing closed pairs versus 4 winners and 1 flat.

## Recent Paper Results

Long-only FIFO PnL from recent `trades_paper` rows:

| Date | Closed | W/L/F | Paper PnL | Notes |
| --- | ---: | --- | ---: | --- |
| 2026-06-16 | 3 | 0/3/0 | -12,200 | RULE only |
| 2026-06-19 | 13 | 6/7/0 | -10,100 | RULE only |
| 2026-06-22 | 9 | 2/6/1 | -6,500 | RULE only; separate paper execution anomaly existed |
| 2026-06-23 | 21 | 4/16/1 | -12,500 | RULE -11,500; CONSENSUS -1,000 |

Total for these observed paper sessions: `-41,300`.

## Interpretation

The current system is not failing because it lacks one more entry filter. The
working assumption is now:

- The existing RULE entry stack is structurally negative in current paper
  observation.
- The aggregator is allowing too many RULE-only BUY candidates through.
- The current judge behaves more like an anti-alpha source than a live entry
  signal.
- Same-day re-entry blocking and paper stop exits are limiting damage, but they
  are not creating expectancy.
- A quick gate such as "wait until 09:15" is not the answer; that constraint is
  already active through `ENTRY_MIN_MINUTES_FROM_OPEN=15` and
  `LIVE_DAY_NEW_BUY_START_TIME=09:15`.

Therefore, do not frame the next work as threshold tuning or small hardening.
Frame it as a strategy reset.

## Operational Stance Until Rebuild

- Existing RULE BUY should be removed from live-candidate status.
- Keep production in `TRADE_MODE=paper` unless explicitly changed after a fresh
  strategy review.
- Do not enable live BUY based on the current judge stack.
- If the system is run on the next session, treat it as observation only.
- Prefer disabling new BUY generation/routing for the old strategy over adding
  another small filter.

## Rebuild Requirements

Before a new strategy becomes a live candidate, it needs an explicit hypothesis
and offline/paper evidence. Candidate families to evaluate from scratch:

1. Opening range breakout:
   - form a 5-15 minute range
   - require high/low breakout confirmation
   - require VWAP/volume/liquidity confirmation
   - stop from range low/VWAP/ATR, not arbitrary tight exits
2. VWAP continuation or VWAP mean reversion:
   - choose one regime; do not mix both without a regime classifier
   - exclude material/news-driven adverse moves for mean reversion
3. Relative momentum:
   - score against TOPIX/sector/peer basket
   - require volume expansion and VWAP alignment

Minimum process:

- Define the strategy in plain language before coding.
- Define pass/fail metrics before testing.
- Test on archived/paper data without changing parameters after seeing results.
- Promote only if the evidence beats the project kill-switch style bar for
  out-of-sample evaluation.

## Do Not Do

- Do not keep adding small gates to the current RULE strategy and call it a live
  improvement.
- Do not treat "reverse the judge" as immediately tradable. For long-only現物,
  the safe inverse of a bad BUY signal is usually `no trade`, not an automatic
  opposite position.
- Do not use today's stable infrastructure behavior as evidence that the strategy
  itself is improving.
