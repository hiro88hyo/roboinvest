-- OMS Live Phase 3 走行前後で確認したい状態を一覧表示する。
-- 使い方:
--   psql "$SUPABASE_DB_URL" -f scripts/oms-live-phase3/check-state.sql
-- ローカル Supabase (CLI) なら:
--   psql 'postgresql://postgres:postgres@127.0.0.1:54322/postgres' \
--     -f scripts/oms-live-phase3/check-state.sql

\echo '=== system_status (id=1) ==='
select
    id,
    is_trading_allowed,
    trade_mode,
    trading_style,
    daily_pnl,
    weekly_pnl,
    monthly_pnl,
    daily_loss_limit,
    updated_at
from system_status
where id = 1;

\echo ''
\echo '=== positions (trade_type=live) ==='
select
    symbol,
    side,
    quantity,
    entry_price,
    current_price,
    unrealized_pnl,
    holding_type,
    opened_at
from positions
where trade_type = 'live'
order by opened_at desc;

\echo ''
\echo '=== trades_live (直近 10 件) ==='
select
    trade_id,
    symbol,
    side,
    quantity,
    price,
    signal_source,
    order_id,
    executed_at
from trades_live
order by executed_at desc
limit 10;
