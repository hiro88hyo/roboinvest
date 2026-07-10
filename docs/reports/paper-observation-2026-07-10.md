# Paper Observation - 2026-07-10

Recorded after market close at `2026-07-10 15:09 JST` from production Supabase
rows and structured service logs.

## Scope

- `TRADE_MODE=paper`
- day strategy: `relative_momentum`
- event-cluster swing candidates for signal date `2026-07-09`: `0`
- live trades: `0`
- open paper/live positions after close: `0 / 0`

## Paper Fills

| Symbol | BUY | SELL | Quantity | Gross execution PnL |
| --- | ---: | ---: | ---: | ---: |
| 4894 | 4,790 | 4,825 | 100 | +3,500 JPY |
| 4419 | 1,539 | 1,544 | 100 | +500 JPY |
| 4413 | 3,240 | 3,220 | 100 | -2,000 JPY |
| 4722 | 1,803 | 1,811 | 100 | +800 JPY |
| **Total** | | | **4 closed** | **+2,800 JPY** |

The result is gross fill-price PnL. The `trades_paper` contract stores no
separate commission, slippage, or tax columns, so this is not directly
comparable with cost-adjusted research net PnL.

## Execution Notes

- Five BUY orders were not filled with reason `limit_not_crossed`.
- A stale-book SELL for `4894` retried and later filled.
- A duplicate `4722` SELL was safely rejected as `no_position_for_sell` after
  the position had already closed.
- No `ERROR`, `CRITICAL`, or traceback was observed in the close review.
- This observation does not restore `relative_momentum` to paper/live candidate
  status; its OOS rejection remains controlling.

## Reproduction Query Boundary

The source rows are production data and are not committed. A reviewer with
read-only access should query `trades_paper` for the JST day window
`2026-07-09T15:00:00Z <= executed_at < 2026-07-10T15:00:00Z`, order by
`executed_at`, and pair long-only fills by symbol using FIFO.
