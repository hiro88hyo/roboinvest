-- watchlist: Universe Scanner が日次生成する監視銘柄リスト
create table if not exists watchlist (
    symbol text not null,
    valid_date date not null,
    symbol_name text not null,
    score numeric not null,
    selected_reasons jsonb not null default '{}'::jsonb,
    created_at timestamptz not null default now(),
    primary key (symbol, valid_date)
);

create index if not exists watchlist_valid_date_score_idx
    on watchlist (valid_date desc, score desc);
