-- Dashboard read-only access for browser clients.
--
-- The dashboard uses the anon key in client components for Realtime
-- subscriptions. Server-side rendering and writes continue to use the
-- service-role key, so this policy grants SELECT only.

alter table system_status enable row level security;
alter table positions enable row level security;
alter table trades_live enable row level security;
alter table trades_paper enable row level security;
alter table strategy_logs enable row level security;
alter table aggregator_logs enable row level security;

do $$
begin
    if not exists (
        select 1 from pg_publication_tables
        where pubname = 'supabase_realtime'
          and schemaname = 'public'
          and tablename = 'system_status'
    ) then
        alter publication supabase_realtime add table system_status;
    end if;

    if not exists (
        select 1 from pg_publication_tables
        where pubname = 'supabase_realtime'
          and schemaname = 'public'
          and tablename = 'positions'
    ) then
        alter publication supabase_realtime add table positions;
    end if;

    if not exists (
        select 1 from pg_publication_tables
        where pubname = 'supabase_realtime'
          and schemaname = 'public'
          and tablename = 'trades_live'
    ) then
        alter publication supabase_realtime add table trades_live;
    end if;

    if not exists (
        select 1 from pg_publication_tables
        where pubname = 'supabase_realtime'
          and schemaname = 'public'
          and tablename = 'trades_paper'
    ) then
        alter publication supabase_realtime add table trades_paper;
    end if;

    if not exists (
        select 1 from pg_publication_tables
        where pubname = 'supabase_realtime'
          and schemaname = 'public'
          and tablename = 'strategy_logs'
    ) then
        alter publication supabase_realtime add table strategy_logs;
    end if;

    if not exists (
        select 1 from pg_publication_tables
        where pubname = 'supabase_realtime'
          and schemaname = 'public'
          and tablename = 'aggregator_logs'
    ) then
        alter publication supabase_realtime add table aggregator_logs;
    end if;
end $$;

drop policy if exists dashboard_anon_select_system_status on system_status;
create policy dashboard_anon_select_system_status
    on system_status
    for select
    to anon
    using (true);

drop policy if exists dashboard_anon_select_positions on positions;
create policy dashboard_anon_select_positions
    on positions
    for select
    to anon
    using (true);

drop policy if exists dashboard_anon_select_trades_live on trades_live;
create policy dashboard_anon_select_trades_live
    on trades_live
    for select
    to anon
    using (true);

drop policy if exists dashboard_anon_select_trades_paper on trades_paper;
create policy dashboard_anon_select_trades_paper
    on trades_paper
    for select
    to anon
    using (true);

drop policy if exists dashboard_anon_select_strategy_logs on strategy_logs;
create policy dashboard_anon_select_strategy_logs
    on strategy_logs
    for select
    to anon
    using (true);

drop policy if exists dashboard_anon_select_aggregator_logs on aggregator_logs;
create policy dashboard_anon_select_aggregator_logs
    on aggregator_logs
    for select
    to anon
    using (true);
