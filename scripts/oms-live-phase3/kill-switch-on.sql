-- OMS Live Phase 3: 取引許可フラグを OFF にする (kill switch ON)。
-- 以降、Gateway 経路の全注文が reject される。Phase 3 e2e は Gateway を介さず
-- live-orders に直接 publish するため、kill switch は次の publish 以降にしか
-- 効かない点に注意 (走行中の Runner を止めるには pytest プロセス自体を停止)。
--
-- 使い方:
--   psql "$SUPABASE_DB_URL" -f scripts/oms-live-phase3/kill-switch-on.sql

update system_status
set
    is_trading_allowed = false,
    updated_at = now()
where id = 1
returning id, is_trading_allowed, daily_pnl, updated_at;
