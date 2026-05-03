-- OMS Live Phase 3: 指定 symbol の positions(live) と trades_live を削除する。
-- e2e 後の後始末や、異常終了で残ったゴミ行を消すときに使う。
-- aggregator_logs は signal_id がわからないと特定できないので別途。
-- trade_type='live' のみ削除するため paper には影響しない。
--
-- 使い方:
--   psql "$SUPABASE_DB_URL" -v symbol=7203 \
--     -f scripts/oms-live-phase3/cleanup-symbol.sql
--
-- WARNING: 本番 DB に向けて実行する場合は対象 symbol を慎重に確認すること。

\echo '=== before: positions(live) ==='
select symbol, side, quantity, entry_price
from positions
where symbol = :'symbol' and trade_type = 'live';

\echo '=== before: trades_live ==='
select trade_id, symbol, side, quantity, price, executed_at
from trades_live
where symbol = :'symbol'
order by executed_at desc;

\echo ''
\echo '=== deleting trades_live ==='
delete from trades_live
where symbol = :'symbol'
returning trade_id, symbol, side, quantity, price;

\echo ''
\echo '=== deleting positions(live) ==='
delete from positions
where symbol = :'symbol' and trade_type = 'live'
returning symbol, side, quantity, entry_price;
