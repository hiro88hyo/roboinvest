-- Dashboard authenticated admin access.
--
-- This replaces the temporary anon read policies from
-- 011_dashboard_anon_read_policies.sql. Dashboard users must be signed in with
-- Supabase Auth and present in dashboard_admins. Authorization is enforced in
-- RLS using a private security-definer helper so dashboard_admins is not
-- directly listable from browser clients.

create schema if not exists private;
revoke all on schema private from public;

create table if not exists dashboard_admins (
    user_id uuid primary key references auth.users(id) on delete cascade,
    created_at timestamptz not null default now()
);

alter table dashboard_admins enable row level security;
revoke all on dashboard_admins from anon, authenticated;

create or replace function private.is_dashboard_admin()
returns boolean
language sql
stable
security definer
set search_path = public, auth
as $$
    select exists (
        select 1
        from public.dashboard_admins
        where user_id = auth.uid()
    );
$$;

revoke all on function private.is_dashboard_admin() from public;
grant usage on schema private to authenticated;
grant execute on function private.is_dashboard_admin() to authenticated;

drop policy if exists dashboard_anon_select_system_status on system_status;
drop policy if exists dashboard_anon_select_positions on positions;
drop policy if exists dashboard_anon_select_trades_live on trades_live;
drop policy if exists dashboard_anon_select_trades_paper on trades_paper;
drop policy if exists dashboard_anon_select_strategy_logs on strategy_logs;
drop policy if exists dashboard_anon_select_aggregator_logs on aggregator_logs;

drop policy if exists dashboard_admin_select_system_status on system_status;
create policy dashboard_admin_select_system_status
    on system_status
    for select
    to authenticated
    using ((select private.is_dashboard_admin()));

drop policy if exists dashboard_admin_update_system_status on system_status;
create policy dashboard_admin_update_system_status
    on system_status
    for update
    to authenticated
    using ((select private.is_dashboard_admin()))
    with check ((select private.is_dashboard_admin()));

drop policy if exists dashboard_admin_select_positions on positions;
create policy dashboard_admin_select_positions
    on positions
    for select
    to authenticated
    using ((select private.is_dashboard_admin()));

drop policy if exists dashboard_admin_select_trades_live on trades_live;
create policy dashboard_admin_select_trades_live
    on trades_live
    for select
    to authenticated
    using ((select private.is_dashboard_admin()));

drop policy if exists dashboard_admin_select_trades_paper on trades_paper;
create policy dashboard_admin_select_trades_paper
    on trades_paper
    for select
    to authenticated
    using ((select private.is_dashboard_admin()));

drop policy if exists dashboard_admin_select_strategy_logs on strategy_logs;
create policy dashboard_admin_select_strategy_logs
    on strategy_logs
    for select
    to authenticated
    using ((select private.is_dashboard_admin()));

drop policy if exists dashboard_admin_select_aggregator_logs on aggregator_logs;
create policy dashboard_admin_select_aggregator_logs
    on aggregator_logs
    for select
    to authenticated
    using ((select private.is_dashboard_admin()));

revoke all on system_status from anon;
revoke all on positions from anon;
revoke all on trades_live from anon;
revoke all on trades_paper from anon;
revoke all on strategy_logs from anon;
revoke all on aggregator_logs from anon;

grant select on system_status to authenticated;
grant update (is_trading_allowed, trade_mode, updated_at) on system_status to authenticated;
grant select on positions to authenticated;
grant select on trades_live to authenticated;
grant select on trades_paper to authenticated;
grant select on strategy_logs to authenticated;
grant select on aggregator_logs to authenticated;
