-- daily_ohlcv: 日次 OHLCV データ (J-Quants API から取得、バックテスト / Universe Scanner 入力)
create table if not exists daily_ohlcv (
    symbol text not null,
    date date not null,
    open numeric not null,
    high numeric not null,
    low numeric not null,
    close numeric not null,
    volume bigint not null check (volume >= 0),
    turnover numeric not null check (turnover >= 0),
    primary key (symbol, date)
);

create index if not exists daily_ohlcv_date_idx on daily_ohlcv (date desc);
