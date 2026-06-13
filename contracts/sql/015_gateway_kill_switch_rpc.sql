-- Gateway kill-switch check: lock the singleton status row, evaluate live-mode
-- loss limits, and flip trading off in the same database transaction.
create or replace function public.gateway_check_kill_switch()
returns table (
    id int,
    is_trading_allowed boolean,
    trade_mode text,
    trading_style text,
    daily_pnl numeric,
    weekly_pnl numeric,
    monthly_pnl numeric,
    daily_loss_limit numeric,
    weekly_loss_limit numeric,
    monthly_loss_limit numeric,
    updated_at timestamptz,
    passed boolean,
    reason text,
    disabled boolean
)
language plpgsql
as $$
declare
    status_row public.system_status%rowtype;
    rejection_reason text := null;
    did_disable boolean := false;
begin
    select *
    into status_row
    from public.system_status
    where system_status.id = 1
    for update;

    if not found then
        raise exception 'system_status row (id=1) not found';
    end if;

    if not status_row.is_trading_allowed then
        rejection_reason := 'kill_switch_off';
    elsif status_row.trade_mode = 'live'
        and status_row.daily_pnl <= -status_row.daily_loss_limit then
        rejection_reason := 'daily_loss_limit';
    elsif status_row.trade_mode = 'live'
        and status_row.weekly_pnl <= -status_row.weekly_loss_limit then
        rejection_reason := 'weekly_loss_limit';
    elsif status_row.trade_mode = 'live'
        and status_row.monthly_pnl <= -status_row.monthly_loss_limit then
        rejection_reason := 'monthly_loss_limit';
    end if;

    if rejection_reason in ('daily_loss_limit', 'weekly_loss_limit', 'monthly_loss_limit') then
        update public.system_status
        set is_trading_allowed = false,
            updated_at = now()
        where system_status.id = 1
        returning
            system_status.id,
            system_status.is_trading_allowed,
            system_status.trade_mode,
            system_status.trading_style,
            system_status.daily_pnl,
            system_status.weekly_pnl,
            system_status.monthly_pnl,
            system_status.daily_loss_limit,
            system_status.weekly_loss_limit,
            system_status.monthly_loss_limit,
            system_status.updated_at
        into status_row;
        did_disable := true;
    end if;

    return query
    select
        status_row.id,
        status_row.is_trading_allowed,
        status_row.trade_mode,
        status_row.trading_style,
        status_row.daily_pnl,
        status_row.weekly_pnl,
        status_row.monthly_pnl,
        status_row.daily_loss_limit,
        status_row.weekly_loss_limit,
        status_row.monthly_loss_limit,
        status_row.updated_at,
        rejection_reason is null,
        rejection_reason,
        did_disable;
end;
$$;

revoke all on function public.gateway_check_kill_switch() from public;
revoke all on function public.gateway_check_kill_switch() from anon;
revoke all on function public.gateway_check_kill_switch() from authenticated;
grant execute on function public.gateway_check_kill_switch() to service_role;
