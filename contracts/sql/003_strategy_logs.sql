-- strategy_logs: Strategy A/B の出力ログ (Dashboard 分析用)
create table if not exists strategy_logs (
    signal_id uuid primary key,
    source text not null check (source in ('RULE', 'AI')),
    symbol text not null,
    action text not null check (action in ('BUY', 'SELL', 'HOLD')),
    confidence numeric not null check (confidence >= 0 and confidence <= 1),
    reasoning text,
    created_at timestamptz not null default now()
);

create index if not exists strategy_logs_symbol_created_at_idx
    on strategy_logs (symbol, created_at desc);
create index if not exists strategy_logs_source_created_at_idx
    on strategy_logs (source, created_at desc);
