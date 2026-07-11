-- Optional close-session time for a fixed-hold paper position.  NULL retains
-- the legacy "exit as soon as the scheduled date is due" behaviour.
alter table public.positions
    add column if not exists scheduled_exit_time time;

-- Keep the existing 16-argument apply-fill RPC intact for already deployed
-- callers.  The 17-argument overload delegates all fill and lineage work to
-- it, then stores this new-BUY-only metadata under the same transaction.
create or replace function public.oms_paper_apply_fill(
    p_order_id uuid,
    p_trade_id uuid,
    p_symbol text,
    p_side text,
    p_filled_quantity integer,
    p_fill_price numeric,
    p_signal_source text,
    p_unified_signal_id uuid,
    p_executed_at timestamptz,
    p_expected_position_opened_at timestamptz,
    p_new_holding_type text,
    p_new_target_price numeric,
    p_new_stop_loss_price numeric,
    p_new_max_hold_days integer,
    p_new_scheduled_exit_date date,
    p_new_trailing_stop_pct numeric,
    p_new_scheduled_exit_time time
)
returns table (
    outcome text,
    reason text,
    committed_trade_id uuid,
    position_action text,
    resulting_position jsonb
)
language plpgsql
security invoker
set search_path = ''
as $$
declare
    delegated record;
    refreshed jsonb;
begin
    select * into delegated
    from public.oms_paper_apply_fill(
        p_order_id, p_trade_id, p_symbol, p_side, p_filled_quantity,
        p_fill_price, p_signal_source, p_unified_signal_id, p_executed_at,
        p_expected_position_opened_at, p_new_holding_type, p_new_target_price,
        p_new_stop_loss_price, p_new_max_hold_days, p_new_scheduled_exit_date,
        p_new_trailing_stop_pct
    );

    if delegated.position_action = 'inserted' and p_new_scheduled_exit_time is not null then
        update public.positions
        set scheduled_exit_time = p_new_scheduled_exit_time
        where positions.symbol = p_symbol
          and positions.trade_type = 'paper'
        returning pg_catalog.to_jsonb(positions) into refreshed;
    end if;

    return query select delegated.outcome, delegated.reason,
        delegated.committed_trade_id, delegated.position_action,
        coalesce(refreshed, delegated.resulting_position);
end;
$$;
