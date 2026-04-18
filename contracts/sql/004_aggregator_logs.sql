-- aggregator_logs: Aggregator が出力した統合シグナルのログ (約定テーブルの参照元)
create table if not exists aggregator_logs (
    signal_id uuid primary key,
    symbol text not null,
    action text not null check (action in ('BUY', 'SELL', 'HOLD')),
    confidence numeric not null check (confidence >= 0 and confidence <= 1),
    signal_source text not null check (signal_source in ('RULE', 'AI', 'CONSENSUS')),
    strategy_signal_id_a uuid references strategy_logs (signal_id),
    strategy_signal_id_b uuid references strategy_logs (signal_id),
    created_at timestamptz not null default now()
);

create index if not exists aggregator_logs_symbol_created_at_idx
    on aggregator_logs (symbol, created_at desc);
