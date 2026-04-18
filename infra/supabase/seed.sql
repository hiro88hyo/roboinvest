-- infra/supabase/seed.sql
-- 開発用シードデータ。`supabase db reset` 時にマイグレーション適用後に実行される。

-- system_status のシングルトン行。loss_limit の値は開発検証用のダミー。
insert into system_status (
    id,
    is_trading_allowed,
    trade_mode,
    trading_style,
    daily_loss_limit,
    weekly_loss_limit,
    monthly_loss_limit
)
values (
    1,
    true,
    'paper',
    'day',
    100000,
    500000,
    2000000
)
on conflict (id) do nothing;
