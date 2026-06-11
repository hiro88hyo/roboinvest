-- Server-side roboinvest services use the Supabase service role / secret key
-- through PostgREST. Keep browser-facing access governed by RLS policies, but
-- grant the service role explicit table privileges so local Supabase and newer
-- secret-key based deployments can read/write operational tables.

grant select, insert, update, delete on system_status to service_role;
grant select, insert, update, delete on positions to service_role;
grant select, insert, update, delete on strategy_logs to service_role;
grant select, insert, update, delete on aggregator_logs to service_role;
grant select, insert, update, delete on trades_live to service_role;
grant select, insert, update, delete on trades_paper to service_role;
grant select, insert, update, delete on watchlist to service_role;
grant select, insert, update, delete on master_stocks to service_role;
grant select, insert, update, delete on daily_ohlcv to service_role;
grant select, insert, update, delete on market_regime to service_role;

grant usage, select on all sequences in schema public to service_role;
