# June 2026 Operations Log

## 2026-06-01 live close review

End-of-day system behavior summary:

- Production pre-open check was clean before market open: `OK 60 / WARN 0 / NG 0`.
- Market data flowed from `feeder` to `feature-engine`, then through `strategy-rule`, `strategy-ai`, `aggregator`, `gateway`, and `oms-live`.
- `09:00-09:15 JST` live/day BUY guard worked: Gateway rejected new BUY signals with `opening_live_buy`.
- AI calls did not explode. Gemini calls from market open to around `10:04 JST` were 6 total, with `AI_MIN_INTERVAL_SECONDS=300`.
- Live trading completed with `34` live trade rows and final realized daily PnL `+4,470円`.
- `14:50 JST` day closeout worked. OMS Live closeout precheck matched `['6635', '6969', '4100']`, submitted SELL orders, inserted closeout trades, deleted live positions, and logged `closeout: postcheck clear (no live positions remain)`.
- Post-close state: `positions(live)` empty, `is_trading_allowed=True`, all production compose services Up.

Log / observability topics to discuss next session:

- Cloud Logging query quality for production JSON logs:
  - Confirm `jsonPayload.event="signal_rejected"` and `jsonPayload.event="order_published"` are easy to filter.
  - Confirm OMS Live fills and closeout events are easy to filter, especially `live order filled`, `closeout: precheck`, and `closeout: postcheck clear`.
  - Decide whether some OMS Live messages should be promoted from generic `event="log"` to structured event names such as `order_filled`, `closeout_started`, `closeout_completed`, and `broker_order_failed`.
- Alert candidates:
  - `oms-live` broker error `Code 21: 可能額が不足しております` occurred at `2026-06-01 14:16:55 JST` on a duplicate/extra `4100` BUY attempt. OMS Live logged ERROR and continued, but Gateway capital/exposure estimate and actual kabu buying power differed.
  - Closeout failure or residual live positions after `14:50 JST` should become high-priority alert conditions.
  - Repeated `below_min_lot` / `same_day_reentry_after_sell` bursts are useful as diagnostic logs, but probably not alerts.
- Monitoring metrics candidates:
  - Daily realized PnL, open live positions count, live order count, broker order failures, closeout success flag, and AI call count.
  - Supabase should remain source of truth for trading metrics; Cloud Monitoring custom metrics via a future `metrics-exporter` remains the likely path.

