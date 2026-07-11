# Swing Paper Opening Exit Gate

Purpose: validate the research assumption behind `exit_before_entry_at_open=true`.
This is a paper-only operational gate. It is not a live procedure and does not
promote any swing candidate to paper/live trading by itself.

This runbook is for positions whose exit is due at the opening. It must not be
used as the event-cluster fixed-hold exit procedure: positions carrying
`scheduled_exit_time=15:30 JST` are intentionally not due during this command's
morning window.

## Scope

Validate this sequence:

1. Opening book data is available for symbols with due swing exits.
2. `oms-paper opening-swing-exits` closes fixed-hold swing positions whose
   `scheduled_exit_date` has arrived.
3. One atomic RPC commits each SELL fill and its `positions` update/delete.
4. Gateway BUY processing reads the updated `positions` state.
5. BUY budget is calculated after exited positions are no longer counted in
   `capital_in_use`.

Do not connect this to a scheduler until the sequence has been observed manually.
Do not run this against live positions.

## Preconditions

- `system_status.trade_mode = paper`.
- Target positions have `trade_type = paper` and `holding_type = swing`.
- Target fixed-hold positions have `scheduled_exit_date <= current JST date`.
- The strategy under review is still research-only.
- `oms-paper` has access to:
  - `SUPABASE_URL`
  - `SUPABASE_SECRET_KEY`
  - `PUBSUB_PROJECT_ID` when book warmup is enabled
  - `PUBSUB_EMULATOR_HOST` or managed Pub/Sub configuration, matching the environment
- Supabase schema health check passes, including `positions.scheduled_exit_date`,
  `positions.scheduled_exit_time`, `trades_paper.order_id`, `rpc:oms_paper_apply_fill`, and
  `rpc:oms_paper_update_stop_loss`.
- Raw book subscription for `oms-paper` is receiving order book messages.
- Gateway is not processing the corresponding BUY signals before this command completes.

Schema preflight:

```bash
cd /home/hiroyuki/workspaces/roboinvest
set -a && . infra/.op.service-account.env && set +a
op run --env-file infra/env.production -- \
  uv run python scripts/health-check.py --check supabase --timeout 30
```

If `positions.scheduled_exit_date` is `NG`, apply migration 017. If
`positions.scheduled_exit_time` is `NG`, apply
`contracts/sql/022_positions_scheduled_exit_time.sql`. If
`trades_paper.order_id` or either OMS Paper RPC is `NG`, apply
`contracts/sql/018_oms_paper_apply_fill_rpc.sql`. Do not run this gate against a
target where any of these checks fail.

## Command

Local/host execution:

```bash
cd /home/hiroyuki/workspaces/roboinvest
set -a && . infra/.op.service-account.env && set +a
op run --env-file infra/env.production -- \
  uv run python -m oms_paper opening-swing-exits --book-warmup-batches 3
```

Container execution, if using production compose images:

```bash
cd /home/hiroyuki/workspaces/roboinvest
set -a && . infra/.op.service-account.env && set +a
op run --env-file infra/env.production -- \
  docker compose -f infra/docker-compose.prod.yml run --rm oms-paper \
  python -m oms_paper opening-swing-exits --book-warmup-batches 3
```

Use `--book-warmup-batches 0` only when a test harness already seeded the
runner's book cache. In normal paper observation, keep warmup enabled.

## Expected Logs

Look for:

```text
opening swing exits: book warmup pulled=N applied=M acked=K symbols=S
opening swing exits: positions_seen=P due=D closed=C partial_exits=R no_fills=F write_errors=0
```

For JSON logs, verify this `event=opening_swing_exit_sequence` stage order for
the same business day:

```text
stage=sell_fill
stage=position_delete or stage=position_update
stage=capital_in_use_recalculated
stage=buy_order_published
```

Interpretation:

- `due=0`: no fixed-hold swing positions reached `scheduled_exit_date`. The gate cannot be
  observed that day.
- `closed=due` and `write_errors=0`: opening exit batch wrote all due exits.
- `partial_exits>0`: SELL fills were committed but shares remain. Do not process
  dependent BUY signals; wait for a fresh book and reconcile/retry.
- `no_fills>0`: some due symbols had no cached executable bid. Do not treat the
  backtest assumption as reproduced for those symbols.
- `write_errors>0`: stop. Do not process dependent BUY signals until positions
  are reconciled.

## Supabase Checks

After the command and before BUY signal processing, confirm:

```sql
select symbol, quantity, holding_type, opened_at, max_hold_days, scheduled_exit_date,
       scheduled_exit_time
from positions
where trade_type = 'paper'
  and holding_type = 'swing'
order by symbol;
```

Due symbols that logged `closed` should no longer be present.

Confirm corresponding SELL fills:

```sql
select order_id, symbol, side, quantity, price, executed_at
from trades_paper
where side = 'SELL'
order by executed_at desc
limit 20;
```

## Gateway Observation

Then allow or replay the BUY signal path. Confirm Gateway logs show the BUY after
the opening exit command completed:

```text
entry_price resolved: symbol=... source=...
buy budget recalculated: symbol=... trade_mode=paper exposure=... remaining_capital=...
order approved: symbol=... side=BUY qty=... trade_mode=paper
```

If BUY is rejected by `insufficient_live_budget`, inspect `positions` again.
That likely means the exit position was still counted in `capital_in_use`, so the
`exit_before_entry_at_open` assumption was not reproduced.

## Pass / Fail

Pass for a symbol/day only when:

- due swing position was present before the command,
- `opening-swing-exits` closed it with `write_errors=0`,
- `positions` no longer contains the exited paper position,
- `trades_paper` has the SELL fill,
- Gateway BUY processing happens after that state is visible,
- BUY budget is calculated without the exited position.

Fail or inconclusive when:

- no due position exists,
- no bid / no fill occurs,
- Supabase write errors occur,
- BUY is processed before the exit command completes,
- Gateway still budgets as if the exited position is open.

## Observation Log

- 2026-06-25: schema preflight passed against production Supabase
  (`positions.scheduled_exit_date` OK). Production paper `positions` had no rows,
  so there were no due `holding_type='swing'` paper positions and this gate was
  inconclusive. Do not count this as an `open_exit_then_entry` operational pass.
