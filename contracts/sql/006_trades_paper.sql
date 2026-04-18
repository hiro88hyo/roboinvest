-- trades_paper: 擬似約定の履歴
create table if not exists trades_paper (
    trade_id uuid primary key,
    symbol text not null,
    side text not null check (side in ('BUY', 'SELL')),
    quantity int not null check (quantity > 0),
    price numeric not null,
    signal_source text not null check (signal_source in ('RULE', 'AI', 'CONSENSUS')),
    unified_signal_id uuid references aggregator_logs (signal_id),
    executed_at timestamptz not null default now()
);

create index if not exists trades_paper_symbol_executed_at_idx
    on trades_paper (symbol, executed_at desc);
create index if not exists trades_paper_executed_at_idx
    on trades_paper (executed_at desc);
