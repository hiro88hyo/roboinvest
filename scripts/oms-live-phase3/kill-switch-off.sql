-- OMS Live Phase 3: 取引許可フラグを ON に戻す (kill switch OFF)。
-- 異常対応後の再開時に使う。再開前に daily_pnl が daily_loss_limit を
-- 超えていないか check-state.sql で確認すること。
--
-- 使い方:
--   psql "$SUPABASE_DB_URL" -f scripts/oms-live-phase3/kill-switch-off.sql

update system_status
set
    is_trading_allowed = true,
    updated_at = now()
where id = 1
returning id, is_trading_allowed, daily_pnl, updated_at;
