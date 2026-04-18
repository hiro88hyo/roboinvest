-- system_status: 稼働状態を管理するシングルトンテーブル (id=1 の固定行)
create table if not exists system_status (
    id int primary key default 1,
    is_trading_allowed boolean not null default true,
    trade_mode text not null default 'paper' check (trade_mode in ('live', 'paper')),
    trading_style text not null default 'day' check (trading_style in ('day', 'swing')),
    daily_pnl numeric not null default 0,
    weekly_pnl numeric not null default 0,
    monthly_pnl numeric not null default 0,
    daily_loss_limit numeric not null,
    weekly_loss_limit numeric not null,
    monthly_loss_limit numeric not null,
    updated_at timestamptz not null default now(),
    constraint system_status_singleton check (id = 1)
);
