-- positions: 現在保有中のポジション (live と paper を trade_type で区別)
create table if not exists positions (
    symbol text not null,
    trade_type text not null check (trade_type in ('live', 'paper')),
    side text not null check (side in ('LONG')),
    quantity int not null check (quantity > 0),
    entry_price numeric not null,
    current_price numeric not null,
    unrealized_pnl numeric not null default 0,
    holding_type text not null check (holding_type in ('day', 'swing')),
    target_price numeric,
    stop_loss_price numeric,
    max_hold_days int,
    scheduled_exit_date date,
    trailing_stop_pct numeric,
    opened_at timestamptz not null default now(),
    primary key (symbol, trade_type)
);

create index if not exists positions_trade_type_idx on positions (trade_type);
