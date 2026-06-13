-- Gateway risk reservations: atomically reserve worst-case live BUY risk
-- before publishing an OrderRequest. This prevents concurrent Gateway workers
-- from all reading the same daily_pnl and overshooting loss limits.

create table if not exists gateway_risk_reservations (
    order_id uuid primary key,
    trade_mode text not null check (trade_mode in ('live', 'paper')),
    trading_date date not null,
    symbol text not null,
    side text not null check (side in ('BUY', 'SELL')),
    risk_amount numeric not null check (risk_amount >= 0),
    notional_amount numeric not null check (notional_amount >= 0),
    status text not null default 'active' check (status in ('active', 'released')),
    reason text,
    reserved_at timestamptz not null default now(),
    released_at timestamptz
);

create index if not exists gateway_risk_reservations_active_idx
    on gateway_risk_reservations (trade_mode, trading_date, status);

create or replace function public.gateway_check_and_reserve_risk(
    p_order_id uuid,
    p_trade_mode text,
    p_trading_date date,
    p_symbol text,
    p_side text,
    p_risk_amount numeric,
    p_notional_amount numeric
)
returns table (
    passed boolean,
    reason text,
    reserved boolean,
    active_risk_before numeric,
    active_risk_after numeric,
    daily_pnl numeric,
    daily_loss_limit numeric,
    weekly_pnl numeric,
    weekly_loss_limit numeric,
    monthly_pnl numeric,
    monthly_loss_limit numeric
)
language plpgsql
as $$
declare
    status_row public.system_status%rowtype;
    active_risk numeric := 0;
    rejection_reason text := null;
    did_reserve boolean := false;
begin
    if p_order_id is null then
        raise exception 'p_order_id is required';
    end if;
    if p_trade_mode not in ('live', 'paper') then
        raise exception 'invalid p_trade_mode: %', p_trade_mode;
    end if;
    if p_side not in ('BUY', 'SELL') then
        raise exception 'invalid p_side: %', p_side;
    end if;
    if p_risk_amount < 0 or p_notional_amount < 0 then
        raise exception 'risk_amount and notional_amount must be non-negative';
    end if;

    select *
    into status_row
    from public.system_status
    where system_status.id = 1
    for update;

    if not found then
        raise exception 'system_status row (id=1) not found';
    end if;

    select coalesce(sum(risk_amount), 0)
    into active_risk
    from public.gateway_risk_reservations
    where trade_mode = p_trade_mode
      and trading_date = p_trading_date
      and status = 'active';

    if not status_row.is_trading_allowed then
        rejection_reason := 'kill_switch_off';
    elsif p_trade_mode = 'live'
        and p_side = 'BUY'
        and status_row.daily_pnl - active_risk - p_risk_amount <= -status_row.daily_loss_limit then
        rejection_reason := 'daily_loss_reservation_limit';
    elsif p_trade_mode = 'live'
        and p_side = 'BUY'
        and status_row.weekly_pnl - active_risk - p_risk_amount <= -status_row.weekly_loss_limit then
        rejection_reason := 'weekly_loss_reservation_limit';
    elsif p_trade_mode = 'live'
        and p_side = 'BUY'
        and status_row.monthly_pnl - active_risk - p_risk_amount <= -status_row.monthly_loss_limit then
        rejection_reason := 'monthly_loss_reservation_limit';
    end if;

    if rejection_reason is null and p_trade_mode = 'live' and p_side = 'BUY' then
        insert into public.gateway_risk_reservations (
            order_id,
            trade_mode,
            trading_date,
            symbol,
            side,
            risk_amount,
            notional_amount,
            status
        )
        values (
            p_order_id,
            p_trade_mode,
            p_trading_date,
            p_symbol,
            p_side,
            p_risk_amount,
            p_notional_amount,
            'active'
        )
        on conflict (order_id) do nothing;
        did_reserve := true;
    end if;

    return query
    select
        rejection_reason is null,
        rejection_reason,
        did_reserve,
        active_risk,
        case
            when did_reserve then active_risk + p_risk_amount
            else active_risk
        end,
        status_row.daily_pnl,
        status_row.daily_loss_limit,
        status_row.weekly_pnl,
        status_row.weekly_loss_limit,
        status_row.monthly_pnl,
        status_row.monthly_loss_limit;
end;
$$;

create or replace function public.gateway_release_risk_reservation(
    p_order_id uuid,
    p_reason text default 'released'
)
returns table (
    released boolean,
    order_id uuid,
    status text
)
language plpgsql
as $$
declare
    updated_row public.gateway_risk_reservations%rowtype;
begin
    update public.gateway_risk_reservations
    set status = 'released',
        reason = p_reason,
        released_at = now()
    where gateway_risk_reservations.order_id = p_order_id
      and gateway_risk_reservations.status = 'active'
    returning *
    into updated_row;

    if not found then
        return query
        select false, p_order_id, null::text;
        return;
    end if;

    return query
    select true, updated_row.order_id, updated_row.status;
end;
$$;

revoke all on gateway_risk_reservations from anon;
revoke all on gateway_risk_reservations from authenticated;
grant select, insert, update on gateway_risk_reservations to service_role;

revoke all on function public.gateway_check_and_reserve_risk(
    uuid, text, date, text, text, numeric, numeric
) from public;
revoke all on function public.gateway_check_and_reserve_risk(
    uuid, text, date, text, text, numeric, numeric
) from anon;
revoke all on function public.gateway_check_and_reserve_risk(
    uuid, text, date, text, text, numeric, numeric
) from authenticated;
grant execute on function public.gateway_check_and_reserve_risk(
    uuid, text, date, text, text, numeric, numeric
) to service_role;

revoke all on function public.gateway_release_risk_reservation(uuid, text) from public;
revoke all on function public.gateway_release_risk_reservation(uuid, text) from anon;
revoke all on function public.gateway_release_risk_reservation(uuid, text) from authenticated;
grant execute on function public.gateway_release_risk_reservation(uuid, text) to service_role;
