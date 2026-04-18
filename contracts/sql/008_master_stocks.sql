-- master_stocks: 銘柄マスタ (J-Quants API から日次更新)
create table if not exists master_stocks (
    symbol text primary key,
    symbol_name text not null,
    market_segment text not null,
    sector text,
    is_active boolean not null default true,
    updated_at timestamptz not null default now()
);

create index if not exists master_stocks_is_active_idx on master_stocks (is_active);
create index if not exists master_stocks_market_segment_idx on master_stocks (market_segment);
